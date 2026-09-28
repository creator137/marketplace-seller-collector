"""Schedule recurring collection jobs without duplicating active work."""
import os
import time
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

import django_rq
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from core.models import Category, City, CollectionJob, Marketplace, MarketplaceSession
from core.tasks import run_collection_job


def _session_updated_at(marketplace):
    values = []
    db_value = MarketplaceSession.objects.filter(
        marketplace=marketplace,
    ).values_list("updated_at", flat=True).first()
    if db_value:
        values.append(db_value)
    path = Path(settings.BASE_DIR) / "runtime" / "sessions" / f"{marketplace}.json"
    try:
        values.append(datetime.fromtimestamp(path.stat().st_mtime, tz=datetime_timezone.utc))
    except OSError:
        pass
    return max(values) if values else None


def _enqueue(job):
    django_rq.get_queue("default").enqueue(run_collection_job, job.id)


def schedule_once(*, marketplaces, target, interval, username=""):
    """Resume eligible jobs and create due jobs. Returns human-readable events."""
    now = timezone.now()
    events = []
    user = None
    if username:
        user = get_user_model().objects.filter(username=username).first()

    for marketplace in marketplaces:
        active = CollectionJob.objects.filter(
            marketplace=marketplace,
            status__in=(CollectionJob.Status.QUEUED, CollectionJob.Status.RUNNING),
        ).order_by("-created_at").first()
        if active:
            events.append(f"{marketplace}: active job #{active.id}, skipped")
            continue

        paused = CollectionJob.objects.filter(
            marketplace=marketplace,
            status=CollectionJob.Status.PAUSED,
        ).order_by("-created_at").first()
        if paused:
            can_resume = False
            reason = ""
            if paused.source_status == "blocked":
                refreshed = _session_updated_at(marketplace)
                baseline = paused.started_at or paused.created_at
                can_resume = bool(refreshed and refreshed > baseline)
                reason = "new session" if can_resume else "waiting for refreshed session"
            elif paused.source_status in ("rate_limited", "temporary_error"):
                can_resume = not paused.retry_after or paused.retry_after <= now
                reason = "retry is due" if can_resume else "waiting for retry_after"
            else:
                reason = f"manual action required ({paused.source_status})"
            if can_resume:
                paused.status = CollectionJob.Status.QUEUED
                paused.save(update_fields=["status"])
                _enqueue(paused)
                events.append(f"{marketplace}: resumed job #{paused.id} ({reason})")
            else:
                events.append(f"{marketplace}: paused job #{paused.id}, {reason}")
            continue

        latest = CollectionJob.objects.filter(marketplace=marketplace).order_by("-created_at").first()
        if latest and (now - latest.created_at).total_seconds() < interval:
            events.append(f"{marketplace}: next run is not due")
            continue

        categories = list(Category.objects.filter(marketplace=marketplace, is_active=True))
        cities = list(City.objects.filter(is_active=True))
        if not categories or not cities:
            events.append(
                f"{marketplace}: skipped, active categories={len(categories)}, cities={len(cities)}"
            )
            continue
        job = CollectionJob.objects.create(
            user=user,
            marketplace=marketplace,
            max_sellers=target,
            total=target,
        )
        job.categories.set(categories)
        job.cities.set(cities)
        _enqueue(job)
        events.append(
            f"{marketplace}: queued job #{job.id}, categories={len(categories)}, "
            f"cities={len(cities)}, target={target}"
        )
    return events


class Command(BaseCommand):
    help = "Periodically collect all active categories and cities; safely resume paused jobs"

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep scheduling forever")
        parser.add_argument("--poll", type=int, default=int(os.getenv("AUTO_COLLECT_POLL", "60")))
        parser.add_argument("--interval", type=int, default=int(os.getenv("AUTO_COLLECT_INTERVAL", "86400")))
        parser.add_argument("--target", type=int, default=int(os.getenv("AUTO_COLLECT_TARGET", "5000")))
        parser.add_argument("--username", default=os.getenv("AUTO_COLLECT_USERNAME", "admin"))
        parser.add_argument(
            "--marketplace", action="append", choices=Marketplace.values,
            help="Marketplace to schedule; repeat the option. Default: all.",
        )

    def handle(self, *args, **options):
        if options["target"] < 1:
            raise CommandError("--target must be at least 1")
        if options["interval"] < 1 or options["poll"] < 1:
            raise CommandError("--interval and --poll must be positive")
        marketplaces = options["marketplace"] or list(Marketplace.values)
        while True:
            close_old_connections()
            try:
                events = schedule_once(
                    marketplaces=marketplaces,
                    target=options["target"],
                    interval=options["interval"],
                    username=options["username"],
                )
                for event in events:
                    self.stdout.write(f"{timezone.now():%Y-%m-%d %H:%M:%S} {event}")
            except Exception as exc:
                if not options["loop"]:
                    raise
                self.stderr.write(self.style.ERROR(f"scheduler error: {exc}"))
            if not options["loop"]:
                break
            time.sleep(options["poll"])
