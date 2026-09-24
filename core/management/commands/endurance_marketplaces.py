"""Measured live crawl for one marketplace; never fabricates successful data."""
import time

from django.core.management.base import BaseCommand, CommandError

from core.adapters import get_adapter
from core.adapters.base import CollectionError, SellerData
from core.httpclient import HttpError
from core.models import Category, Marketplace


class Command(BaseCommand):
    help = "Live endurance crawl with streaming discovery/detail statistics"

    def add_arguments(self, parser):
        parser.add_argument("--marketplace", required=True, choices=[m.value for m in Marketplace])
        parser.add_argument("--query", default="наушники")
        parser.add_argument("--target", type=int, default=100)

    @staticmethod
    def _error_status(exc):
        if isinstance(exc, CollectionError):
            return exc.status
        if isinstance(exc, HttpError):
            if exc.blocked:
                return "blocked"
            if exc.rate_limited:
                return "rate_limited"
            if exc.status and exc.status >= 500:
                return "temporary_error"
        return "failed"

    def handle(self, *args, **options):
        code = options["marketplace"]
        target = max(1, options["target"])
        category = Category(marketplace=code, external_id=options["query"], title="endurance")
        adapter = get_adapter(code)
        started = time.monotonic()
        discovered_ids = set()
        pages = 0
        cursors = []
        detail_success = 0
        detail_failed = 0
        inn = 0
        address = 0
        discovery_error = None
        detail_error = None

        try:
            for data, checkpoint in adapter.iter_sellers(category, max_sellers=target):
                if data is None:
                    if checkpoint and checkpoint.get("page") is not None:
                        pages = max(pages, int(checkpoint.get("page", 1)) - 1)
                    if checkpoint and checkpoint.get("cursor") is not None:
                        cursors.append(str(checkpoint["cursor"]))
                    continue
                seller_id = str(data.external_seller_id)
                if seller_id in discovered_ids:
                    continue
                discovered_ids.add(seller_id)
                try:
                    detail = adapter.fetch_seller(seller_id)
                    if not isinstance(detail, SellerData):
                        detail_failed += 1
                        continue
                    detail_success += 1
                    inn += bool(detail.inn)
                    address += bool(detail.legal_address)
                except Exception as exc:
                    detail_failed += 1
                    detail_error = exc
                    if self._error_status(exc) in ("blocked", "rate_limited"):
                        break
        except Exception as exc:
            discovery_error = exc

        elapsed = max(time.monotonic() - started, 0.001)
        stats = getattr(getattr(adapter, "client", None), "stats", {})
        metrics = getattr(adapter, "metrics", {})
        error = discovery_error or detail_error
        status = self._error_status(error) if error else "success"
        self.stdout.write(f"marketplace={code} query={options['query']!r} target={target}")
        self.stdout.write(f"result={status}")
        self.stdout.write(
            f"pages={metrics.get('pages', pages)} checkpoint={cursors[-1] if cursors else '-'} "
            f"products={metrics.get('products', 0)} seller_refs={len(discovered_ids)} "
            f"unique_sellers={len(discovered_ids)} duplicates={metrics.get('duplicates', 0)}"
        )
        self.stdout.write(
            f"detail_success={detail_success} detail_failed={detail_failed} "
            f"inn_coverage={inn}/{len(discovered_ids)} address_coverage={address}/{len(discovered_ids)}"
        )
        self.stdout.write(
            f"requests={stats.get('requests', 0)} 2xx={stats.get('2xx', 0)} "
            f"403={stats.get('403', 0)} 429={stats.get('429', 0)} 5xx={stats.get('5xx', 0)} "
            f"duration={elapsed:.2f}s requests/sec={stats.get('requests', 0) / elapsed:.2f}"
        )
        if error:
            raise CommandError(f"{status}: {str(error)[:1000]}")
