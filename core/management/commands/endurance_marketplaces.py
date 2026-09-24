"""Measured live crawl for one marketplace; never fabricates successful data."""
import time

from django.core.management.base import BaseCommand

from core.adapters import get_adapter
from core.adapters.base import SellerData
from core.models import Category, Marketplace


class Command(BaseCommand):
    help = "Live endurance crawl with discovery/detail and HTTP statistics"

    def add_arguments(self, parser):
        parser.add_argument("--marketplace", required=True, choices=[m.value for m in Marketplace])
        parser.add_argument("--query", default="наушники")
        parser.add_argument("--target", type=int, default=100)

    def handle(self, *args, **options):
        code = options["marketplace"]
        target = max(1, options["target"])
        category = Category(marketplace=code, external_id=options["query"], title="endurance")
        adapter = get_adapter(code)
        started = time.monotonic()
        discovery_error = ""
        try:
            discovered = adapter.discover_sellers(category, limit=target)
        except Exception as exc:
            discovered = []
            discovery_error = str(exc)
        unique = {str(item.external_seller_id): item for item in discovered if isinstance(item, SellerData)}
        details_ok = 0
        inn = 0
        address = 0
        parse_errors = 0
        duplicates = max(0, len(discovered) - len(unique))
        for ref in unique.values():
            try:
                detail = adapter.fetch_seller(ref.external_seller_id)
            except Exception:
                parse_errors += 1
                continue
            if not isinstance(detail, SellerData):
                continue
            details_ok += 1
            inn += bool(detail.inn)
            address += bool(detail.legal_address)
            # DaData is called only when configured and an INN is available;
            # command output still reports marketplace coverage independently.
        elapsed = max(time.monotonic() - started, 0.001)
        stats = getattr(getattr(adapter, "client", None), "stats", {})
        self.stdout.write(f"marketplace={code} query={options['query']!r} target={target}")
        if discovery_error:
            self.stdout.write(f"discovery_status=failed error={discovery_error[:500]}")
        self.stdout.write(f"discovered_unique={len(unique)} duplicate_sellers={duplicates}")
        self.stdout.write(f"requests={stats.get('requests', 0)} duration={elapsed:.2f}s requests/sec={stats.get('requests', 0) / elapsed:.2f}")
        self.stdout.write(
            f"2xx={stats.get('2xx', 0)} 403={stats.get('403', 0)} "
            f"429={stats.get('429', 0)} 5xx={stats.get('5xx', 0)} "
            f"parse_errors={stats.get('parse_errors', parse_errors)}"
        )
        self.stdout.write(f"seller_details_success={details_ok} inn_coverage={inn}/{len(unique)} address_coverage={address}/{len(unique)}")
