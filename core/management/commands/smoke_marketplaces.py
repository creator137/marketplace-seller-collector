"""Live smoke-test for marketplace adapters (requires network; optional cookies)."""
from django.core.management.base import BaseCommand

from core.adapters import get_adapter
from core.models import Category, Marketplace


class Command(BaseCommand):
    help = "Проверка живых endpoint'ов маркетплейсов без запуска всей системы"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=5)
        parser.add_argument("--marketplace", type=str, default="", choices=[m.value for m in Marketplace])

    def handle(self, *args, **options):
        limit = options["limit"]
        codes = [options["marketplace"]] if options["marketplace"] else [m.value for m in Marketplace]
        for code in codes:
            self.stdout.write(self.style.MIGRATE_HEADING(f"=== {code} ==="))
            adapter = get_adapter(code)
            category = Category(marketplace=code, external_id=self._default_query(code), title="smoke")
            try:
                sellers = adapter.discover_sellers(category, limit=limit)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"discover failed: {exc}"))
                continue
            self.stdout.write(f"discovered: {len(sellers)}")
            for data in sellers[:limit]:
                self.stdout.write(f"  id={data.external_seller_id} name={data.name!r} rating={data.rating!r}")
            if sellers:
                ref = sellers[0].external_seller_id
                detail = adapter.fetch_seller(ref)
                if detail:
                    self.stdout.write(
                        f"detail id={ref}: name={detail.name!r} inn={detail.inn!r} addr={detail.legal_address[:60]!r}"
                    )
                else:
                    self.stdout.write(self.style.WARNING(f"detail for {ref}: None (endpoint/cookies)"))
            else:
                self.stdout.write(self.style.WARNING("0 sellers — endpoint заблокирован или нужны cookies"))

    @staticmethod
    def _default_query(code):
        return {
            "ozon": "/search/?text=наушники&from_global=true",
            "wildberries": "наушники",
            "yandex_market": "наушники",
        }[code]
