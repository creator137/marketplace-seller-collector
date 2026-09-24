"""Yandex Market adapter: search/category HTML -> productSnippet zone-data JSON.

Live status 2026-09: search pages return HTTP 200 with snippets without
cookies; each snippet carries supplierId/shopId in data-zone-data.
Seller profile pages (/seller/<id>) are protected and may require
YANDEX_MARKET_COOKIES; the adapter degrades gracefully (returns partial data).
"""
import html as html_lib
import json
import logging
import re

from django.conf import settings

from core.adapters.base import CollectionBlocked, CollectionParseError, MarketplaceAdapter, SellerData
from core.httpclient import HttpClient, HttpError

log = logging.getLogger("core.adapters.ym")

BASE = "https://market.yandex.ru"
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

ZONE_DATA_RE = re.compile(r'data-zone-name="productSnippet"[^>]*data-zone-data="([^"]+)"')


def parse_snippets(html_text: str) -> list[dict]:
    """Extract productSnippet zone-data dicts from a YM page."""
    out = []
    for m in ZONE_DATA_RE.finditer(html_text):
        try:
            out.append(json.loads(html_lib.unescape(m.group(1))))
        except (ValueError, TypeError):
            continue
    return out


class YandexMarketAdapter(MarketplaceAdapter):
    code = "yandex_market"

    def __init__(self):
        self.last_page = 0
        self.client = HttpClient(
            cookies=HttpClient.parse_cookies(settings.YANDEX_MARKET_COOKIES),
            referer=BASE,
            headers=HEADERS,
        )

    # Categories are admin-managed: external_id = search text or hid value.
    def get_categories(self):
        return []

    def discover_sellers(self, category, city=None, limit=0, max_sellers=None):
        limit = max_sellers if max_sellers is not None else limit
        params = {"text": category.external_id, "page": "1"}
        if category.external_id.isdigit():
            params = {"hid": category.external_id, "page": "1"}
        found = {}
        seen_items = set()
        page = 1
        while True:
            self.last_page = page
            params["page"] = str(page)
            try:
                resp = self.client.get(f"{BASE}/search", params=params)
                if resp.status_code != 200:
                    log.warning("YM search HTTP %s", resp.status_code)
                    break
                snippets = parse_snippets(resp.text)
            except HttpError as exc:
                if exc.blocked:
                    raise CollectionBlocked(str(exc)) from exc
                raise
            except Exception as exc:
                log.warning("YM search failed: %s", exc)
                raise
            if not snippets and re.search(r"captcha|робот|доступ ограничен|проверка", resp.text, re.I):
                raise CollectionBlocked("Yandex Market returned a protection page")
            if not snippets:
                break
            page_new = 0
            for sn in snippets:
                item_key = str(sn.get("marketSku") or sn.get("sku") or sn.get("title") or "")
                if item_key and item_key in seen_items:
                    continue
                if item_key:
                    seen_items.add(item_key)
                    page_new += 1
                sid = sn.get("supplierId") or sn.get("shopId")
                if not sid:
                    continue
                sid = str(sid)
                if sid not in found:
                    rating = ""
                    if isinstance(sn.get("rating"), dict):
                        rating = str(sn["rating"].get("rating", "") or "")
                    found[sid] = SellerData(
                        marketplace=self.code,
                        external_seller_id=sid,
                        seller_url=f"{BASE}/seller/{sid}/",
                        name=str(sn.get("supplierName") or sn.get("shopName") or ""),
                        rating=rating,
                        category_refs=[category.external_id] if category else [],
                        raw={"marketSku": sn.get("marketSku"), "title": sn.get("title")},
                    )
                if limit and len(found) >= limit:
                    return list(found.values())
            if page > 1 and page_new == 0:
                break
            page += 1
            if page > 10000:
                log.warning("Yandex Market pagination safety stop at page %s", page)
                break
        return list(found.values())

    def fetch_seller(self, ref: str) -> SellerData | None:
        try:
            resp = self.client.get(f"{BASE}/seller/{ref}/")
        except Exception as exc:
            log.warning("YM seller %s failed: %s", ref, exc)
            return None
        if resp.status_code != 200:
            log.info("YM seller page %s HTTP %s (needs cookies)", ref, resp.status_code)
            return None
        t = resp.text
        name_m = re.search(r'"(?:sellerName|shopName|businessName)"\s*:\s*"([^"]{2,200})"', t)
        inn_m = re.search(r'"(?:inn|INN)"\s*:\s*"?(\d{10,12})"?', t)
        ogrn_m = re.search(r'"ogrn"\s*:\s*"?(\d{13,15})"?', t)
        addr_m = re.search(r'"(?:legalAddress|address)"\s*:\s*"([^"]{5,300})"', t)
        return SellerData(
            marketplace=self.code,
            external_seller_id=str(ref),
            seller_url=f"{BASE}/seller/{ref}/",
            name=name_m.group(1) if name_m else "",
            inn=inn_m.group(1) if inn_m else "",
            ogrn=ogrn_m.group(1) if ogrn_m else "",
            legal_address=addr_m.group(1) if addr_m else "",
            raw={"page_bytes": len(t)},
        )
