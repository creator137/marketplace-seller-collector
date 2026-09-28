from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from core.management.commands.auto_collect import schedule_once
from core.models import Category, City, CollectionJob, Marketplace


class AutoCollectTest(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            marketplace=Marketplace.OZON, external_id="auto-q", title="Auto Q",
        )
        self.city = City.objects.get_or_create(name="Уфа")[0]

    @patch("core.management.commands.auto_collect._enqueue")
    def test_creates_job_with_all_active_catalogs(self, enqueue):
        events = schedule_once(
            marketplaces=[Marketplace.OZON], target=5000, interval=86400,
        )
        job = CollectionJob.objects.get()
        self.assertEqual(job.max_sellers, 5000)
        self.assertIn(self.category, job.categories.all())
        self.assertEqual(
            job.categories.count(),
            Category.objects.filter(marketplace=Marketplace.OZON, is_active=True).count(),
        )
        self.assertIn(self.city, job.cities.all())
        enqueue.assert_called_once_with(job)
        self.assertIn("queued job", events[0])

    @patch("core.management.commands.auto_collect._enqueue")
    def test_does_not_duplicate_active_job(self, enqueue):
        CollectionJob.objects.create(
            marketplace=Marketplace.OZON, status=CollectionJob.Status.RUNNING,
        )
        schedule_once(marketplaces=[Marketplace.OZON], target=10, interval=1)
        self.assertEqual(CollectionJob.objects.count(), 1)
        enqueue.assert_not_called()

    @patch("core.management.commands.auto_collect._enqueue")
    def test_blocked_job_waits_for_new_session_then_resumes(self, enqueue):
        job = CollectionJob.objects.create(
            marketplace=Marketplace.OZON,
            status=CollectionJob.Status.PAUSED,
            source_status="blocked",
            started_at=timezone.now(),
        )
        path = Path(settings.BASE_DIR) / "runtime" / "sessions" / "ozon.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        try:
            schedule_once(marketplaces=[Marketplace.OZON], target=10, interval=1)
            enqueue.assert_not_called()
            with patch(
                "core.management.commands.auto_collect._session_updated_at",
                return_value=timezone.now() + timedelta(seconds=1),
            ):
                schedule_once(marketplaces=[Marketplace.OZON], target=10, interval=1)
            job.refresh_from_db()
            self.assertEqual(job.status, CollectionJob.Status.QUEUED)
            enqueue.assert_called_once_with(job)
        finally:
            path.unlink(missing_ok=True)

    @patch("core.management.commands.auto_collect._enqueue")
    def test_same_session_is_not_retried_twice(self, enqueue):
        started = timezone.now() - timedelta(seconds=10)
        job = CollectionJob.objects.create(
            marketplace=Marketplace.OZON,
            status=CollectionJob.Status.PAUSED,
            source_status="blocked",
            started_at=started,
        )
        session_time = timezone.now() - timedelta(seconds=1)
        with patch(
            "core.management.commands.auto_collect._session_updated_at",
            return_value=session_time,
        ):
            schedule_once(marketplaces=[Marketplace.OZON], target=10, interval=1)
            job.refresh_from_db()
            job.status = CollectionJob.Status.PAUSED
            job.source_status = "blocked"
            job.save(update_fields=["status", "source_status"])
            schedule_once(marketplaces=[Marketplace.OZON], target=10, interval=1)
        enqueue.assert_called_once_with(job)
