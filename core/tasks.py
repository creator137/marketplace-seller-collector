"""Resumable streaming discovery -> details -> DaData collection pipeline."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.adapters import get_adapter
from core.adapters.base import CollectionError, MarketplaceAdapter, SellerData
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
    message = "Требуется обновить сессию маркетплейса" if status == "blocked" else str(exc)[:2000]
    job.status = CollectionJob.Status.PAUSED if status != "failed" else CollectionJob.Status.FAILED
    job.source_status = status
    job.last_error = message
    job.error_message = message
    if status in ("blocked", "rate_limited", "temporary_error"):
        backoff = {"blocked": 300, "rate_limited": 60, "temporary_error": 30}[status]
        job.retry_after = timezone.now() + timedelta(seconds=backoff)
    else:
        job.retry_after = None
    fields = ["status", "source_status", "last_error", "error_message", "retry_after", "checkpoint"]
    job.save(update_fields=fields)


def _adapter_page(adapter):
    value = getattr(adapter, "last_page", 0)
    return value if isinstance(value, int) else 0


def _search_cities(job, cities):
    # Marketplace city selection is a WB delivery destination only. Ozon and
    # Yandex discovery is global; legal city is resolved after details.
    return cities if job.marketplace == "wildberries" else [None]


def _discovery_key(job, category, city):
    return f"{job.marketplace}:{category.id}:{city.id if city else 0}"


def _iter_adapter(adapter, category, city, remaining, state):
    if isinstance(adapter, MarketplaceAdapter):
        return adapter.iter_sellers(
            category,
            city=city,
            max_sellers=remaining,
            start_page=state["page"],
            start_cursor=state.get("cursor"),
        )

    # Compatibility for small custom/test adapters that only implement the
    # original list API.
    def fallback():
        for data in adapter.discover_sellers(category, city=city, limit=remaining):
            yield data, {"page": 1, "cursor": None, "finished": True}
        yield None, {"page": 1, "cursor": None, "finished": True}

    return fallback()


def _save_discovery_checkpoint(job, checkpoint, job_count):
    job.checkpoint = checkpoint
    job.total = job_count
    job.found = job.job_sellers.exclude(seller=None).count()
    job.save(update_fields=["checkpoint", "total", "found"])


def _discover(job, adapter, categories, cities):
    """Consume adapter pages incrementally; no full discovery list is held."""
    checkpoint = job.checkpoint or {}
    states = checkpoint.setdefault("discovery", {})
    completed = set(checkpoint.get("discovery_done", []))
    max_total = settings.COLLECT_MAX_SELLERS
    job_count = job.job_sellers.count()

    for category in categories:
        for city in _search_cities(job, cities):
            key = _discovery_key(job, category, city)
            state = dict(states.get(key) or {})
            if state.get("finished") or key in completed:
                continue
            remaining = max_total - job_count if max_total else 0
            if max_total and remaining <= 0:
                state.update({"finished": True, "reason": "max_sellers", "discovered_count": job_count})
                states[key] = state
                completed.add(key)
                continue

            state.setdefault("page", 1)
            state.setdefault("cursor", None)
            state.setdefault("discovered_count", 0)
            checkpoint.update({"current_key": key, "current_page": state["page"], "current_cursor": state.get("cursor")})
            job.checkpoint = checkpoint
            job.save(update_fields=["checkpoint"])

            iterator = _iter_adapter(adapter, category, city, remaining, state)
            last_item_checkpoint = None
            page_boundary_seen = False
            try:
                for data, page_checkpoint in iterator:
                    last_item_checkpoint = page_checkpoint
                    if data is not None:
                        if max_total and job_count >= max_total:
                            break
                        seller = upsert_seller(data)
                        _, created = CollectionJobSeller.objects.get_or_create(
                            job=job,
                            external_seller_id=str(data.external_seller_id),
                            defaults={"seller": seller, "category": category, "city": city},
                        )
                        if created:
                            job_count += 1
                        state["discovered_count"] = state.get("discovered_count", 0) + (1 if created else 0)
                        continue

                    # Page boundary: all seller refs from that page have been
                    # persisted, so this cursor is safe for resume.
                    state.update(page_checkpoint or {})
                    states[key] = state
                    page_boundary_seen = True
                    checkpoint.update({"current_key": key, "current_page": state.get("page", 1), "current_cursor": state.get("cursor")})
                    if state.get("finished"):
                        completed.add(key)
                        checkpoint["discovery_done"] = sorted(completed)
                    _save_discovery_checkpoint(job, checkpoint, job_count)
                    if max_total and job_count >= max_total:
                        state.update({"finished": True, "reason": "max_sellers"})
                        completed.add(key)
                        checkpoint["discovery_done"] = sorted(completed)
                        _save_discovery_checkpoint(job, checkpoint, job_count)
                        break
            except Exception:
                # If a page yielded sellers but failed before its boundary,
                # its page checkpoint is still safe because the page was fully
                # parsed before yielding. Persist it for an exact retry point.
                if last_item_checkpoint and not page_boundary_seen:
                    state.update(last_item_checkpoint)
                    states[key] = state
                    checkpoint.update({"current_key": key, "current_page": state.get("page", 1), "current_cursor": state.get("cursor")})
                    _save_discovery_checkpoint(job, checkpoint, job_count)
                raise
            if not state.get("finished") and max_total and job_count >= max_total:
                state.update({"finished": True, "reason": "max_sellers"})
                completed.add(key)
                checkpoint["discovery_done"] = sorted(completed)
                _save_discovery_checkpoint(job, checkpoint, job_count)
    return checkpoint


def _process_details(job):
    """Fetch detail chunks concurrently while persisting every link status."""
    job.job_sellers.filter(detail_status="processing").update(detail_status="retry")
    links_qs = job.job_sellers.select_related("seller").filter(
        detail_status__in=("pending", "retry", "blocked"),
    )
    local = threading.local()
    batch_size = max(1, settings.COLLECT_DETAIL_CHUNK)
    concurrency = max(1, settings.COLLECT_CONCURRENCY)
    blocked_exc = None

    def fetch(ref):
        if not hasattr(local, "adapter"):
            local.adapter = get_adapter(job.marketplace)
        return local.adapter.fetch_seller(ref)

    links = list(links_qs.iterator(chunk_size=max(50, batch_size)))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for offset in range(0, len(links), batch_size):
            chunk = links[offset:offset + batch_size]
            ids = [link.id for link in chunk]
            CollectionJobSeller.objects.filter(id__in=ids).update(detail_status="processing", detail_error="")
            futures = {link.id: pool.submit(fetch, link.external_seller_id) for link in chunk}
            for link in chunk:
                try:
                    detail = futures[link.id].result()
                    if isinstance(detail, SellerData):
                        link.seller = upsert_seller(detail)
                        link.detail_status = "done"
                        link.detail_error = ""
                        enrich_with_dadata(link.seller)
                    else:
                        link.detail_status = "partial"
                        link.detail_error = "detail unavailable"
                except Exception as exc:
                    status = _classify_error(exc)
                    link.detail_status = {
                        "blocked": "blocked",
                        "rate_limited": "retry",
                        "temporary_error": "retry",
                    }.get(status, "failed")
                    link.detail_error = str(exc)[:1000]
                    if status in ("blocked", "rate_limited") and blocked_exc is None:
                        blocked_exc = exc
                link.save(update_fields=["seller", "detail_status", "detail_error"])

            job.processed = job.job_sellers.filter(detail_status__in=("done", "partial", "failed")).count()
            job.errors_count = job.job_sellers.filter(detail_status__in=("retry", "blocked", "failed")).count()
            job.found = job.job_sellers.exclude(seller=None).count()
            job.checkpoint = {**(job.checkpoint or {}), "detail_processed": job.processed}
            job.save(update_fields=["processed", "errors_count", "found", "checkpoint"])
            if blocked_exc:
                raise blocked_exc

    job.processed = job.job_sellers.filter(detail_status__in=("done", "partial", "failed")).count()
    job.errors_count = job.job_sellers.filter(detail_status__in=("retry", "blocked", "failed")).count()
    job.found = job.job_sellers.exclude(seller=None).count()
    job.save(update_fields=["processed", "errors_count", "found"])


def run_collection_job(job_id: int):
    job = CollectionJob.objects.get(pk=job_id)
    job.status = CollectionJob.Status.RUNNING
    job.source_status = "running"
    job.started_at = job.started_at or timezone.now()
    job.finished_at = None
    job.error_message = ""
    job.save(update_fields=["status", "source_status", "started_at", "finished_at", "error_message"])
    adapter = get_adapter(job.marketplace)
    try:
        categories = list(job.categories.all())
        cities = list(job.cities.all())
        if not categories:
            job.fail("Нет выбранных категорий", "empty")
            return
        _discover(job, adapter, categories, cities)
        _process_details(job)
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
    job.processed = job.job_sellers.filter(detail_status__in=("done", "partial", "failed")).count()
    job.finished_at = timezone.now()
    if not job.job_sellers.exists() and not job.error_message:
        job.error_message = "Источник успешно ответил, но продавцы не найдены."
    job.save(update_fields=["status", "source_status", "total", "found", "processed", "finished_at", "error_message"])
