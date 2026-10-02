"""Resumable streaming discovery -> details -> DaData collection pipeline.

When the job has selected cities, ``max_sellers`` means “sellers from those
cities”, not raw marketplace refs. Discovery continues (with oversampling)
until the city goal is reached or the source is exhausted.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.adapters import get_adapter
from core.adapters.base import CollectionError, MarketplaceAdapter, SellerData
from core.httpclient import HttpError
from core.models import CollectionJob, CollectionJobSeller, Seller
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
    if job.marketplace != "wildberries":
        return [None]
    # Selecting the whole Russian city catalog must not multiply every WB
    # category by 1,000+ delivery locations. Discovery is global in that case;
    # legal city matching still happens after seller details.
    if len(cities) > 20:
        return [None]
    result = []
    seen_dest = set()
    has_global = False
    for city in cities:
        dest = (city.dest_code or "").strip()
        if not dest:
            has_global = True
        elif dest not in seen_dest:
            seen_dest.add(dest)
            result.append(city)
    if has_global or not result:
        result.append(None)
    return result


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

    def fallback():
        for data in adapter.discover_sellers(category, city=city, limit=remaining):
            yield data, {"page": 1, "cursor": None, "finished": True}
        yield None, {"page": 1, "cursor": None, "finished": True}

    return fallback()


def _target(job) -> int:
    value = int(getattr(job, "max_sellers", 0) or 0)
    if settings.COLLECT_MAX_SELLERS:
        value = min(value, settings.COLLECT_MAX_SELLERS) if value else settings.COLLECT_MAX_SELLERS
    return value


def _goal_count(job, cities) -> int:
    """Sellers that count toward the job goal."""
    qs = Seller.objects.filter(collection_links__job=job)
    if cities:
        qs = qs.filter(city__in=cities)
    return qs.distinct().count()


def _refresh_progress(job, cities):
    job.total = _target(job) or job.job_sellers.count()
    job.found = _goal_count(job, cities)
    job.processed = job.job_sellers.filter(
        detail_status__in=("done", "partial", "failed", "processing"),
    ).count()
    job.errors_count = job.job_sellers.filter(
        detail_status__in=("retry", "blocked", "failed"),
    ).count()
    job.save(update_fields=["total", "found", "processed", "errors_count", "checkpoint"])


def _discovery_exhausted(job, categories, cities) -> bool:
    """Return true only when every requested category/destination is finished.

    Checkpoint states are created lazily. Looking only at existing states makes
    the first exhausted category look like the whole multi-category job is
    exhausted, which used to finish large WB jobs prematurely.
    """
    checkpoint = job.checkpoint or {}
    states = checkpoint.get("discovery") or {}
    expected_keys = {
        _discovery_key(job, category, city)
        for category in categories
        for city in _search_cities(job, cities)
    }
    if not expected_keys:
        return False
    done = set(checkpoint.get("discovery_done") or [])
    return all((states.get(key) or {}).get("finished") or key in done for key in expected_keys)


def _raw_safety_cap(job, cities) -> int:
    """Optional global hard stop; zero means no artificial discovery limit."""
    return int(settings.COLLECT_MAX_SELLERS or 0)


def _save_discovery_checkpoint(job, checkpoint, job_count, cities):
    job.checkpoint = checkpoint
    job.total = _target(job) or job_count
    job.found = _goal_count(job, cities)
    job.processed = job_count
    job.save(update_fields=["checkpoint", "total", "found", "processed"])


def _discover(job, adapter, categories, cities, raw_cap):
    """Discover until ``raw_cap`` job_sellers exist or sources finish.

    Hitting ``raw_cap`` pauses this round without marking discovery finished,
    so city-targeted jobs can resume after details.
    """
    checkpoint = job.checkpoint or {}
    states = checkpoint.setdefault("discovery", {})
    completed = set(checkpoint.get("discovery_done", []))
    job_count = job.job_sellers.count()
    created_this_round = 0

    for category in categories:
        for city in _search_cities(job, cities):
            if raw_cap and job_count >= raw_cap:
                return created_this_round
            key = _discovery_key(job, category, city)
            state = dict(states.get(key) or {})
            if state.get("finished") or key in completed:
                continue
            remaining = max(0, raw_cap - job_count) if raw_cap else 0
            if raw_cap and remaining <= 0:
                continue

            state.setdefault("page", 1)
            state.setdefault("cursor", None)
            state.setdefault("discovered_count", 0)
            checkpoint.update({
                "current_key": key,
                "current_page": state["page"],
                "current_cursor": state.get("cursor"),
            })
            job.checkpoint = checkpoint
            job.save(update_fields=["checkpoint"])

            iterator = _iter_adapter(adapter, category, city, remaining, state)
            last_item_checkpoint = None
            page_boundary_seen = False
            try:
                for data, page_checkpoint in iterator:
                    last_item_checkpoint = page_checkpoint
                    if data is not None:
                        if raw_cap and job_count >= raw_cap:
                            break
                        seller = upsert_seller(data)
                        _, created = CollectionJobSeller.objects.get_or_create(
                            job=job,
                            external_seller_id=str(data.external_seller_id),
                            defaults={"seller": seller, "category": category, "city": city},
                        )
                        if created:
                            job_count += 1
                            created_this_round += 1
                        state["discovered_count"] = state.get("discovered_count", 0) + (1 if created else 0)
                        continue

                    state.update(page_checkpoint or {})
                    states[key] = state
                    page_boundary_seen = True
                    checkpoint.update({
                        "current_key": key,
                        "current_page": state.get("page", 1),
                        "current_cursor": state.get("cursor"),
                    })
                    if state.get("finished"):
                        completed.add(key)
                        checkpoint["discovery_done"] = sorted(completed)
                    _save_discovery_checkpoint(job, checkpoint, job_count, cities)
                    if raw_cap and job_count >= raw_cap:
                        break
            except Exception:
                if last_item_checkpoint and not page_boundary_seen:
                    state.update(last_item_checkpoint)
                    states[key] = state
                    checkpoint.update({
                        "current_key": key,
                        "current_page": state.get("page", 1),
                        "current_cursor": state.get("cursor"),
                    })
                    _save_discovery_checkpoint(job, checkpoint, job_count, cities)
                raise
            states[key] = state
            if state.get("finished"):
                completed.add(key)
                checkpoint["discovery_done"] = sorted(completed)
                _save_discovery_checkpoint(job, checkpoint, job_count, cities)
    return created_this_round


def _process_details(job, cities):
    """Fetch detail chunks concurrently while persisting every link status."""
    job.job_sellers.filter(detail_status="processing").update(detail_status="retry")
    links_qs = job.job_sellers.select_related("seller").filter(
        detail_status__in=("pending", "retry", "blocked"),
    )
    local = threading.local()
    batch_size = max(1, settings.COLLECT_DETAIL_CHUNK)
    concurrency = max(1, settings.COLLECT_CONCURRENCY)
    blocked_exc = None

    def fetch(link):
        if not hasattr(local, "adapter"):
            local.adapter = get_adapter(job.marketplace)
        fallback_ref = link.external_seller_id
        seller_ref = (getattr(link, "seller", None) and getattr(link.seller, "seller_url", "")) or fallback_ref
        try:
            return local.adapter.fetch_seller(seller_ref)
        except Exception:
            if seller_ref != fallback_ref:
                return local.adapter.fetch_seller(fallback_ref)
            raise

    links = list(links_qs.iterator(chunk_size=max(50, batch_size)))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for offset in range(0, len(links), batch_size):
            chunk = links[offset:offset + batch_size]
            ids = [link.id for link in chunk]
            CollectionJobSeller.objects.filter(id__in=ids).update(detail_status="processing", detail_error="")
            futures = {link.id: pool.submit(fetch, link) for link in chunk}
            for link in chunk:
                try:
                    detail = futures[link.id].result()
                    if isinstance(detail, SellerData):
                        link.seller = upsert_seller(detail)
                        link.detail_status = "done"
                        link.detail_error = ""
                        enrich_with_dadata(link.seller)
                        link.seller.refresh_from_db(fields=["city_id", "legal_address", "inn", "ogrn"])
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

            job.checkpoint = {**(job.checkpoint or {}), "detail_processed": True}
            _refresh_progress(job, cities)
            if blocked_exc:
                raise blocked_exc
            # Stop detailing more chunks once the city/any goal is reached.
            if _target(job) and _goal_count(job, cities) >= _target(job):
                break

    _refresh_progress(job, cities)


def run_collection_job(job_id: int):
    job = CollectionJob.objects.get(pk=job_id)
    job.status = CollectionJob.Status.RUNNING
    job.source_status = "running"
    job.started_at = job.started_at or timezone.now()
    job.finished_at = None
    job.error_message = ""
    job.last_error = ""
    job.retry_after = None
    job.save(update_fields=[
        "status", "source_status", "started_at", "finished_at",
        "error_message", "last_error", "retry_after",
    ])
    adapter = get_adapter(job.marketplace)
    try:
        categories = list(job.categories.all())
        cities = list(job.cities.all())
        if not categories:
            job.fail("Нет выбранных категорий", "empty")
            return
        target = _target(job)
        if target < 1:
            job.fail("Не задан лимит сбора", "empty")
            return

        safety_cap = _raw_safety_cap(job, cities)
        while True:
            matched = _goal_count(job, cities)
            _refresh_progress(job, cities)
            if matched >= target:
                break

            before = job.job_sellers.count()
            if safety_cap and before >= safety_cap:
                log.info(
                    "Job %s hit discovery safety cap %s with only %s city matches",
                    job_id, safety_cap, matched,
                )
                break
            if _discovery_exhausted(job, categories, cities) and not job.job_sellers.filter(
                detail_status__in=("pending", "retry", "blocked", "processing"),
            ).exists():
                break

            need = target - matched
            # Oversample when filtering by city: many sellers will be elsewhere.
            if cities:
                batch = max(need * 3, need)
            else:
                batch = need
            raw_cap = before + batch
            if safety_cap:
                raw_cap = min(raw_cap, safety_cap)

            created = _discover(job, adapter, categories, cities, raw_cap=raw_cap)
            _process_details(job, cities)
            if _goal_count(job, cities) >= target:
                break

            after = job.job_sellers.count()
            if created == 0 and after == before and _discovery_exhausted(job, categories, cities):
                break
            if after == before and not job.job_sellers.filter(
                detail_status__in=("pending", "retry", "blocked"),
            ).exists() and _discovery_exhausted(job, categories, cities):
                break
    except Exception as exc:
        status = _classify_error(exc)
        log.exception("Job %s stopped with %s", job_id, status)
        job.checkpoint = {**(job.checkpoint or {}), "current_page": _adapter_page(adapter)}
        _pause(job, exc, status)
        return

    cities = list(job.cities.all())
    matched = _goal_count(job, cities)
    job.status = CollectionJob.Status.COMPLETED
    if matched == 0 and not job.job_sellers.exists():
        job.source_status = "empty"
        job.error_message = "Источник успешно ответил, но продавцы не найдены."
    elif matched < _target(job):
        job.source_status = "partial"
        if cities:
            job.error_message = (
                f"Найдено {matched} из {_target(job)} продавцов в выбранных городах. "
                f"Источник исчерпан или достигнут лимит поиска."
            )
        else:
            job.error_message = (
                f"Найдено {matched} из {_target(job)} продавцов. "
                f"Источник исчерпан или достигнут лимит поиска."
            )
    else:
        job.source_status = "success"
        job.error_message = ""
    job.total = _target(job)
    job.found = matched
    job.processed = job.job_sellers.filter(detail_status__in=("done", "partial", "failed")).count()
    job.finished_at = timezone.now()
    job.save(update_fields=[
        "status", "source_status", "total", "found", "processed", "finished_at", "error_message",
    ])
