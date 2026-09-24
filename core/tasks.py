"""Background collection jobs executed by RQ worker."""
import logging

from django.conf import settings
from django.utils import timezone

from core.adapters import get_adapter
from core.models import CollectionJob
from core.services import enrich_with_dadata, upsert_seller

log = logging.getLogger("core.tasks")

ZERO_RESULT_HINTS = {
    "ozon": "Ozon вернул 403 (антибот). Заполните OZON_COOKIES в .env cookies из браузера и перезапустите.",
    "wildberries": "Wildberries вернул 403/498. Заполните WB_COOKIES в .env cookies из браузера и перезапустите.",
    "yandex_market": "Яндекс.Маркет не вернул сниппеты: возможно сработала защита. Попробуйте YANDEX_MARKET_COOKIES или другую категорию позже.",
}


def _cookies_missing(marketplace: str) -> bool:
    return not {
        "ozon": settings.OZON_COOKIES,
        "wildberries": settings.WB_COOKIES,
        "yandex_market": settings.YANDEX_MARKET_COOKIES,
    }.get(marketplace)


def run_collection_job(job_id: int):
    """Worker entry point: collect sellers for one job. One seller's failure
    never kills the whole job."""
    job = CollectionJob.objects.select_related().get(pk=job_id)
    job.status = CollectionJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "error_message"])

    adapter = get_adapter(job.marketplace)
    cities = list(job.cities.all())
    categories = list(job.categories.all())
    if not categories:
        job.fail("Нет выбранных категорий")
        return

    total = found = errors = 0
    http_blocked = False
    try:
        for category in categories:
            sellers = adapter.discover_sellers(category, city=cities[0] if cities else None)
            total += len(sellers)
            job.total = total
            job.save(update_fields=["total"])
            for data in sellers:
                try:
                    seller = upsert_seller(data)
                    found += 1
                    enrich_with_dadata(seller)
                except Exception as exc:
                    errors += 1
                    log.warning("Job %s: seller %s failed: %s", job_id, data.external_seller_id, exc)
                job.processed += 1
                job.found = found
                job.errors_count = errors
                job.save(update_fields=["processed", "found", "errors_count"])
    except Exception as exc:
        msg = str(exc)
        log.exception("Job %s crashed", job_id)
        if "403" in msg or "429" in msg:
            http_blocked = True
        job.fail(_final_notice(job.marketplace, msg, http_blocked))
        return

    job.status = CollectionJob.Status.COMPLETED
    job.finished_at = timezone.now()
    job.total = total or found
    if found == 0:
        job.error_message = _final_notice(job.marketplace, "", total == 0 and _cookies_missing(job.marketplace))
    job.save(update_fields=["status", "finished_at", "total", "error_message"])


def _final_notice(marketplace: str, exc_message: str, blocked: bool) -> str:
    parts = []
    if exc_message:
        parts.append(f"Ошибка: {exc_message[:500]}")
    if blocked:
        parts.append(ZERO_RESULT_HINTS.get(marketplace, "Источник недоступен (403/429)."))
    return " ".join(parts)


def fail(self, message):
    self.status = CollectionJob.Status.FAILED
    self.error_message = message[:2000]
    self.finished_at = timezone.now()
    self.save(update_fields=["status", "error_message", "finished_at"])


CollectionJob.fail = fail


def fail(self, message):
    self.status = CollectionJob.Status.FAILED
    self.error_message = message[:2000]
    self.finished_at = timezone.now()
    self.save(update_fields=["status", "error_message", "finished_at"])


CollectionJob.fail = fail
