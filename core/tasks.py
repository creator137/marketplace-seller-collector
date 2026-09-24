"""Resumable discovery -> details -> DaData collection pipeline."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.utils import timezone

from core.adapters import get_adapter
from core.adapters.base import CollectionError, SellerData
from core.httpclient import HttpError
from core.models import CollectionJob, CollectionJobSeller
from core.services import enrich_with_dadata, upsert_seller

log = logging.getLogger("core.tasks")


def _classify_error(exc):
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


def _pause(job, exc, status):
    message = str(exc)[:2000]
    job.status = CollectionJob.Status.PAUSED if status != "failed" else CollectionJob.Status.FAILED
    job.source_status = status
    job.last_error = message
    job.error_message = message
    job.save(update_fields=["status", "source_status", "last_error", "error_message", "checkpoint"])


def _job_key(category_id, city_id):
    return f"{category_id}:{city_id or 0}"


def _adapter_page(adapter):
    value = getattr(adapter, "last_page", 0)
    return value if isinstance(value, int) else 0


def _discover(job, adapter, categories, cities):
    checkpoint = job.checkpoint or {}
    completed = set(checkpoint.get("discovery_done", []))
    max_total = settings.COLLECT_MAX_SELLERS
    job_count = job.job_sellers.count()
    for category in categories:
        for city in (cities or [None]):
            key = _job_key(category.id, city.id if city else None)
            if key in completed:
                continue
            remaining = max_total - job_count if max_total else 0
            if max_total and remaining <= 0:
                completed.add(key)
                continue
            checkpoint["current_category"] = category.id
            checkpoint["current_city"] = city.id if city else None
            checkpoint["current_page"] = _adapter_page(adapter)
            job.checkpoint = checkpoint
            job.save(update_fields=["checkpoint"])
            data_list = adapter.discover_sellers(category, city=city, limit=remaining)
            for data in data_list:
                if max_total and job_count >= max_total:
                    break
                if not isinstance(data, SellerData) or not data.external_seller_id:
                    continue
                seller = upsert_seller(data)
                _, created = CollectionJobSeller.objects.get_or_create(
                    job=job,
                    external_seller_id=str(data.external_seller_id),
                    defaults={"seller": seller, "category": category, "city": city},
                )
                if created:
                    job_count += 1
            completed.add(key)
            checkpoint["discovery_done"] = sorted(completed)
            checkpoint["phase"] = "details"
            job.checkpoint = checkpoint
            job.total = job_count
            job.found = job.job_sellers.exclude(seller=None).count()
            job.save(update_fields=["checkpoint", "total", "found"])
    return checkpoint


def _process_details(job, adapter):
    processed = job.processed
    errors = job.errors_count
    batch = max(1, settings.COLLECT_PROGRESS_BATCH)
    qs = job.job_sellers.select_related("seller").filter(detail_status__in=("pending", "retry"))
    local = threading.local()

    def fetch(ref):
        if not hasattr(local, "adapter"):
            local.adapter = get_adapter(job.marketplace)
        return local.adapter.fetch_seller(ref)

    with ThreadPoolExecutor(max_workers=max(1, settings.COLLECT_CONCURRENCY)) as pool:
        links = list(qs.iterator(chunk_size=max(50, settings.COLLECT_DETAIL_CHUNK)))
        for offset in range(0, len(links), max(1, settings.COLLECT_DETAIL_CHUNK)):
            chunk = links[offset:offset + max(1, settings.COLLECT_DETAIL_CHUNK)]
            futures = {link.id: pool.submit(fetch, link.external_seller_id) for link in chunk}
            for link in chunk:
                try:
                    detail = futures[link.id].result()
                    if isinstance(detail, SellerData):
                        link.seller = upsert_seller(detail)
                    elif link.seller_id is None:
                        link.detail_status = "partial"
                    if link.seller_id:
                        enrich_with_dadata(link.seller)
                    link.detail_status = "done" if isinstance(detail, SellerData) else "partial"
                    link.detail_error = "" if isinstance(detail, SellerData) else "detail unavailable"
                except Exception as exc:
                    status = _classify_error(exc)
                    link.detail_status = "retry" if status in ("temporary_error", "rate_limited") else "failed"
                    link.detail_error = str(exc)[:1000]
                    errors += 1
                    link.save(update_fields=["seller", "detail_status", "detail_error"])
                    if status in ("blocked", "rate_limited"):
                        raise
                link.save(update_fields=["seller", "detail_status", "detail_error"])
                processed += 1
                if processed % batch == 0:
                    job.processed = processed
                    job.errors_count = errors
                    job.found = job.job_sellers.exclude(seller=None).count()
                    job.checkpoint = {**(job.checkpoint or {}), "detail_processed": processed}
                    job.save(update_fields=["processed", "errors_count", "found", "checkpoint"])
    job.processed = processed
    job.errors_count = errors
    job.found = job.job_sellers.exclude(seller=None).count()
    job.save(update_fields=["processed", "errors_count", "found"])


def run_collection_job(job_id: int):
    job = CollectionJob.objects.get(pk=job_id)
    job.status = CollectionJob.Status.RUNNING
    job.source_status = "running"
    job.started_at = job.started_at or timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "source_status", "started_at", "error_message"])
    adapter = get_adapter(job.marketplace)
    try:
        categories = list(job.categories.all())
        cities = list(job.cities.all())
        if not categories:
            job.fail("Нет выбранных категорий", "empty")
            return
        _discover(job, adapter, categories, cities)
        _process_details(job, adapter)
    except Exception as exc:
        status = _classify_error(exc)
        log.exception("Job %s stopped with %s", job_id, status)
        job.checkpoint = {**(job.checkpoint or {}), "current_page": _adapter_page(adapter)}
        _pause(job, exc, status)
        return
    job.status = CollectionJob.Status.COMPLETED
    job.source_status = "empty" if not job.job_sellers.exists() else "success"
    job.total = job.job_sellers.count()
    job.found = job.job_sellers.exclude(seller=None).count()
    job.finished_at = timezone.now()
    if not job.job_sellers.exists() and not job.error_message:
        job.error_message = "Источник успешно ответил, но продавцы не найдены."
    job.save(update_fields=["status", "source_status", "total", "found", "finished_at", "error_message"])
