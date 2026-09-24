from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from core.export import build_xlsx
from core.models import Category, City, CollectionJob, Marketplace, Seller
from core.services import upsert_seller
from core.adapters.base import SellerData


class JobRunnerTest(TestCase):
    def _run_job(self, discover_sellers):
        user = User.objects.create_user("u", password="pass12345")
        cat = Category.objects.create(marketplace=Marketplace.OZON, external_id="q", title="Q")
        city = City.objects.get_or_create(name="Уфа")[0]
        job = CollectionJob.objects.create(user=user, marketplace=Marketplace.OZON)
        job.categories.set([cat])
        job.cities.set([city])
        with patch("core.tasks.get_adapter") as get_adapter:
            adapter = get_adapter.return_value
            adapter.discover_sellers.side_effect = discover_sellers
            from core.tasks import run_collection_job

            run_collection_job(job.id)
        job.refresh_from_db()
        return job

    def test_job_runs_and_completes(self):
        data = SellerData(marketplace=Marketplace.OZON, external_seller_id="10", name="S1")
        job = self._run_job(lambda *a, **kw: [data])
        self.assertEqual(job.status, CollectionJob.Status.COMPLETED)
        self.assertEqual(job.found, 1)
        self.assertEqual(Seller.objects.count(), 1)

    def test_one_seller_failure_does_not_kill_job(self):
        def discover(*a, **kw):
            raise RuntimeError("boom")

        job = self._run_job(discover)
        self.assertEqual(job.status, CollectionJob.Status.FAILED)

    def test_job_without_categories_fails(self):
        job = CollectionJob.objects.create(marketplace=Marketplace.OZON)
        from core.tasks import run_collection_job

        run_collection_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJob.Status.FAILED)


class ExportTest(TestCase):
    def test_xlsx_smoke(self):
        upsert_seller(SellerData(
            marketplace=Marketplace.WB, external_seller_id="5",
            name="Экспорт Тест", mobile_phones=["+79170000000"], emails=["a@b.ru"],
        ))
        data = build_xlsx(Seller.objects.all())
        self.assertGreater(len(data), 1000)
        self.assertTrue(data[:2], b"PK")  # xlsx = zip


class ViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="pass12345")
        self.client.login(username="u", password="pass12345")

    def test_dashboard_and_results(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/results/")
        self.assertEqual(r.status_code, 200)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/results/").status_code, 302)
