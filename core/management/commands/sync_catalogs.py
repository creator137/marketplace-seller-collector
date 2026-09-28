"""Synchronize cities and marketplace categories from real upstream catalogs."""
import csv
import io
import json
import os
import re
import time
import zipfile

import xlrd
from curl_cffi import requests
from django.core.management.base import BaseCommand, CommandError

from core.citymatch import normalize_city_name
from core.models import Category, City, Marketplace


ROSSTAT_PAGE = "https://rosstat.gov.ru/opendata/7708234640-oktmo?source=subscribe"
WB_MENU = "https://static-basket-01.wbbasket.ru/vol0/data/main-menu-ru-ru-v3.json"
YANDEX_CATEGORIES = "https://download.cdn.yandex.net/market/market_categories.xls"
OZON_CATEGORIES = "https://codeload.github.com/welel/ozon-scraper/zip/refs/heads/main"


def parse_oktmo_cities(content):
    """Unique city names from section 1/2 city rows in the official OKTMO CSV."""
    names = set()
    rows = csv.reader(io.StringIO(content.decode("utf-8-sig")), delimiter=";")
    for row in rows:
        if len(row) < 8:
            continue
        value = row[6] if row[6].startswith("г ") else row[7] if row[7].startswith("г ") else ""
        name = value[2:].strip()
        if name:
            names.add(name)
    return sorted(names)


def flatten_wb_categories(tree):
    result = {}

    def walk(nodes):
        for node in nodes or []:
            query = str(node.get("query") or "").strip()
            shard = str(node.get("shard") or "").strip()
            title = str(node.get("name") or "").strip()
            if query and title:
                external_id = f"{shard}|{query}" if shard else query
                result.setdefault(external_id[:255], title[:255])
            walk(node.get("childs"))

    walk(tree)
    return result


def flatten_ozon_categories(archive_bytes):
    result = {}

    def walk(value):
        if isinstance(value, dict):
            title = str(value.get("title") or "").strip()
            url = str(value.get("url") or "").strip()
            if title and url.startswith("/category/"):
                result.setdefault(url.split("?", 1)[0][:255], title[:255])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            if "/data/categories/" in name and name.endswith(".json"):
                with archive.open(name) as source:
                    walk(json.load(source))
    return result


def flatten_yandex_categories(xls_bytes):
    workbook = xlrd.open_workbook(file_contents=xls_bytes)
    result = {}
    for sheet in workbook.sheets():
        for index in range(sheet.nrows):
            path = str(sheet.cell_value(index, 0) or "").strip()
            parts = [part.strip() for part in path.split("/") if part.strip()]
            if len(parts) < 2:
                continue
            query = parts[-1]
            title = " / ".join(parts[1:])
            result.setdefault(query[:255], title[:255])
    return result


def upsert_categories(marketplace, items):
    existing = set(Category.objects.filter(marketplace=marketplace).values_list("external_id", flat=True))
    created = Category.objects.bulk_create([
        Category(marketplace=marketplace, external_id=external_id, title=title, is_active=True)
        for external_id, title in items.items()
        if external_id not in existing
    ], ignore_conflicts=True, batch_size=500)
    return len(created), Category.objects.filter(marketplace=marketplace, is_active=True).count()


class Command(BaseCommand):
    help = "Sync all Russian cities (Rosstat OKTMO) and real marketplace categories"

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=int(os.getenv("CATALOG_SYNC_INTERVAL", "86400")))
        parser.add_argument("--cities-only", action="store_true")
        parser.add_argument("--categories-only", action="store_true")

    def _get(self, url, *, verify=True, timeout=120):
        response = requests.get(url, timeout=timeout, verify=verify, impersonate="chrome")
        response.raise_for_status()
        return response

    def sync_cities(self):
        page = self._get(ROSSTAT_PAGE, verify=False).text
        links = re.findall(r'href=["\']([^"\']+/data-[^"\']+\.csv)', page)
        if not links:
            raise CommandError("Rosstat OKTMO data link was not found")
        content = self._get(links[0], verify=False, timeout=180).content
        names = parse_oktmo_cities(content)
        existing = set(City.objects.values_list("normalized", flat=True))
        rows = []
        for name in names:
            normalized = normalize_city_name(name)
            if normalized and normalized not in existing:
                rows.append(City(name=name, normalized=normalized, is_active=True))
                existing.add(normalized)
        City.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)
        self.stdout.write(self.style.SUCCESS(
            f"cities: created={len(rows)}, active={City.objects.filter(is_active=True).count()}, source=Rosstat OKTMO"
        ))

    def sync_categories(self):
        sources = (
            (Marketplace.WB, flatten_wb_categories(self._get(WB_MENU).json()), "WB menu"),
            (Marketplace.YM, flatten_yandex_categories(self._get(YANDEX_CATEGORIES).content), "Yandex daily XLS"),
            (Marketplace.OZON, flatten_ozon_categories(self._get(OZON_CATEGORIES).content), "Ozon catalog snapshot"),
        )
        for marketplace, items, source in sources:
            created, active = upsert_categories(marketplace, items)
            self.stdout.write(self.style.SUCCESS(
                f"{marketplace}: created={created}, active={active}, source={source}"
            ))

    def handle(self, *args, **options):
        if options["interval"] < 1:
            raise CommandError("--interval must be positive")
        while True:
            try:
                if not options["categories_only"]:
                    self.sync_cities()
                if not options["cities_only"]:
                    self.sync_categories()
            except Exception as exc:
                if not options["loop"]:
                    raise
                self.stderr.write(self.style.ERROR(f"catalog sync failed: {exc}"))
            if not options["loop"]:
                return
            time.sleep(options["interval"])
