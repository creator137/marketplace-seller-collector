"""Operational readiness check without marketplace traffic by default."""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.adapters import get_adapter
from core.models import Category, City, Marketplace


class Command(BaseCommand):
    help = "Check DB, Redis/RQ, credentials and local reference data"

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="also make one live discovery request per marketplace")

    def handle(self, *args, **options):
        failures = []
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            self.stdout.write(self.style.SUCCESS("DB: ok"))
        except Exception as exc:
            failures.append("DB")
            self.stdout.write(self.style.ERROR(f"DB: failed ({exc})"))

        try:
            import django_rq

            queue = django_rq.get_queue("default")
            queue.connection.ping()
            self.stdout.write(self.style.SUCCESS("Redis/RQ: ok"))
        except Exception as exc:
            failures.append("Redis/RQ")
            self.stdout.write(self.style.WARNING(f"Redis/RQ: unavailable ({exc})"))

        self.stdout.write(f"DaData token: {'configured' if settings.DADATA_TOKEN else 'not configured'}")
        cookie_settings = {
            "ozon": settings.OZON_COOKIES,
            "wildberries": settings.WB_COOKIES,
            "yandex_market": settings.YANDEX_MARKET_COOKIES,
        }
        for code, value in cookie_settings.items():
            runtime = Path(settings.BASE_DIR) / "runtime" / "sessions" / f"{code}.json"
            configured = bool(value) or runtime.exists()
            source = "env/runtime" if value and runtime.exists() else ("env" if value else "runtime" if runtime.exists() else "missing")
            self.stdout.write(f"{code} cookies: {'configured' if configured else 'missing'} ({source})")
        self.stdout.write(f"categories: {Category.objects.filter(is_active=True).count()}")
        self.stdout.write(f"cities: {City.objects.filter(is_active=True).count()}")

        if options["live"]:
            from core.models import Category as CategoryModel

            for code in Marketplace.values:
                category = CategoryModel(marketplace=code, external_id="наушники", title="health check")
                try:
                    iterator = get_adapter(code).iter_sellers(category, max_sellers=1)
                    first = next(iterator, (None, {"finished": True}))
                    self.stdout.write(self.style.SUCCESS(f"{code} live: {'seller ref' if first[0] else 'empty'}"))
                except Exception as exc:
                    failures.append(code)
                    self.stdout.write(self.style.ERROR(f"{code} live: failed ({exc})"))

        if failures:
            raise CommandError("Health check failed: " + ", ".join(failures))
