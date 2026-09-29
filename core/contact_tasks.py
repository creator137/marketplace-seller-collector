import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.utils import timezone

from core.maps_contacts import MapsBlockedError, TwoGisBrowserLookup, YandexMapsLookup
from core.models import ContactEnrichmentJob, Seller, SellerContact
from core.services import enrich_with_dadata, merge_external_phone

log = logging.getLogger("core.contact_tasks")


def _seller_queryset(job):
    # Maps backfill is evidence-based: start from an INN (which DaData can turn
    # into a legal address) or an address already supplied by the marketplace.
    queryset = Seller.objects.exclude(inn="", legal_address="")
    if job.collection_job_id:
        queryset = queryset.filter(collection_links__job_id=job.collection_job_id)
    elif job.marketplace:
        queryset = queryset.filter(marketplace=job.marketplace)
    return queryset.exclude(contacts__type__in=("phone", "city_phone")).distinct().order_by("id")


def run_contact_enrichment(job_id):
    job = ContactEnrichmentJob.objects.get(pk=job_id)
    job.status = job.Status.RUNNING
    job.started_at = job.started_at or timezone.now()
    job.finished_at = None
    job.message = ""
    queryset = _seller_queryset(job)
    job.total = queryset.count()
    job.save(update_fields=["status", "started_at", "finished_at", "message", "total"])

    yandex_blocked = False
    two_gis_blocked = False
    # Playwright's synchronous CDP client owns an asyncio loop while connected.
    # This RQ job is strictly single-threaded/sequential, so ORM access remains
    # safe; Django only needs the explicit opt-in while that loop is alive.
    previous_async_unsafe = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    try:
        two_gis_context = TwoGisBrowserLookup()
        two_gis = two_gis_context.__enter__()
    except Exception as exc:
        log.warning("2GIS browser unavailable: %s", exc)
        two_gis_context = None
        two_gis = None
        two_gis_blocked = True

    try:
        local = threading.local()

        def yandex_lookup(seller):
            if not hasattr(local, "yandex"):
                local.yandex = YandexMapsLookup()
            return local.yandex.lookup(seller.name, seller.legal_address)

        sellers = list(queryset.select_related("city").iterator(chunk_size=100))
        batch_size = max(10, settings.COLLECT_CONCURRENCY * 5)
        with ThreadPoolExecutor(max_workers=max(1, settings.COLLECT_CONCURRENCY)) as pool:
            for offset in range(0, len(sellers), batch_size):
                chunk = sellers[offset:offset + batch_size]
                for seller in chunk:
                    # TTL prevents unnecessary DaData requests for sellers
                    # that were already enriched by the collection pipeline.
                    enrich_with_dadata(seller)
                    seller.refresh_from_db()
                futures = {
                    seller.id: pool.submit(yandex_lookup, seller)
                    for seller in chunk if not yandex_blocked
                }
                for seller in chunk:
                    contacts = []
                    try:
                        if seller.id in futures:
                            contacts.extend(futures[seller.id].result())
                    except MapsBlockedError as exc:
                        yandex_blocked = True
                        job.message = str(exc)
                    except Exception as exc:
                        job.errors_count += 1
                        log.warning("Yandex maps seller=%s failed: %s", seller.id, exc)
                    # One verified public phone is enough for the backfill.
                    if not contacts and two_gis and not two_gis_blocked:
                        try:
                            contacts.extend(two_gis.lookup(
                                seller.name, seller.legal_address,
                                seller.city.name if seller.city_id else "",
                            ))
                        except MapsBlockedError as exc:
                            two_gis_blocked = True
                            job.message = str(exc)
                        except Exception as exc:
                            job.errors_count += 1
                            log.warning("2GIS seller=%s failed: %s", seller.id, exc)
                    added = 0
                    for contact in contacts:
                        ref = f"match={contact.quality}; {contact.url}"[:255]
                        added += int(merge_external_phone(seller, contact.phone, contact.source, ref))
                    if contacts:
                        job.matched += 1
                    job.contacts_added += added
                    job.processed += 1
                    if job.processed % 10 == 0:
                        job.save(update_fields=["processed", "matched", "contacts_added", "errors_count", "message"])
    except Exception as exc:
        job.status = job.Status.FAILED
        job.message = str(exc)[:2000]
    finally:
        if two_gis_context:
            two_gis_context.__exit__(None, None, None)
        if previous_async_unsafe is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = previous_async_unsafe

    if job.status != job.Status.FAILED:
        job.status = job.Status.PAUSED if two_gis_blocked else job.Status.COMPLETED
        if two_gis_blocked:
            job.message = "Яндекс обработан. Для 2ГИС откройте серверный Chromium, пройдите проверку и запустите дозаполнение снова."
    job.finished_at = timezone.now()
    job.save(update_fields=[
        "status", "processed", "matched", "contacts_added", "errors_count", "message", "finished_at",
    ])
