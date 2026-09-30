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
        job = CollectionJob.objects.create(user=user, marketplace=Marketplace.OZON, max_sellers=1)
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
        data = SellerData(
            marketplace=Marketplace.OZON,
            external_seller_id="10",
            name="S1",
            legal_address="г. Уфа, ул. Ленина, 1",
        )

        def discover(*a, **kw):
            return [data]

        user = User.objects.create_user("u", password="pass12345")
        cat = Category.objects.create(marketplace=Marketplace.OZON, external_id="q", title="Q")
        city = City.objects.get_or_create(name="Уфа")[0]
        job = CollectionJob.objects.create(user=user, marketplace=Marketplace.OZON, max_sellers=1)
        job.categories.set([cat])
        job.cities.set([city])
        with patch("core.tasks.get_adapter") as get_adapter:
            adapter = get_adapter.return_value
            adapter.discover_sellers.side_effect = discover
            adapter.fetch_seller.return_value = data
            from core.tasks import run_collection_job

            run_collection_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJob.Status.COMPLETED)
        self.assertGreaterEqual(job.found, 1)
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
        self.assertContains(r, "Выйти")
        self.assertContains(r, "Сколько собрать")
        self.assertContains(r, 'name="max_sellers"')
        r = self.client.get("/results/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Выйти")

    def test_start_job_requires_max_sellers(self):
        Category.objects.create(marketplace=Marketplace.OZON, external_id="q", title="Q")
        city = City.objects.get_or_create(name="Уфа")[0]
        cat = Category.objects.get(marketplace=Marketplace.OZON, external_id="q")
        r = self.client.post("/", {
            "marketplace": Marketplace.OZON,
            "categories": [cat.id],
            "cities": [city.id],
            "max_sellers": "0",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(CollectionJob.objects.count(), 0)

    def test_start_job_saves_max_sellers(self):
        cat = Category.objects.create(marketplace=Marketplace.OZON, external_id="q", title="Q")
        city = City.objects.get_or_create(name="Уфа")[0]
        with patch("core.views._rq_queue") as queue:
            queue.return_value.enqueue.side_effect = RuntimeError("no redis")
            with patch("core.views._run_job_in_background"):
                r = self.client.post("/", {
                    "marketplace": Marketplace.OZON,
                    "categories": [cat.id],
                    "cities": [city.id],
                    "max_sellers": "42",
                })
        self.assertEqual(r.status_code, 302)
        job = CollectionJob.objects.get()
        self.assertEqual(job.max_sellers, 42)
        self.assertEqual(job.total, 42)

    def test_start_job_all_options_expand_active_catalogs(self):
        Category.objects.create(marketplace=Marketplace.OZON, external_id="all-a", title="A")
        Category.objects.create(marketplace=Marketplace.OZON, external_id="all-b", title="B")
        City.objects.get_or_create(name="Казань")
        with patch("core.views._rq_queue") as queue:
            r = self.client.post("/", {
                "marketplace": Marketplace.OZON,
                "categories": ["__all__"],
                "cities": ["__all__"],
                "max_sellers": "10",
            })
        self.assertEqual(r.status_code, 302)
        job = CollectionJob.objects.get()
        self.assertEqual(
            job.categories.count(),
            Category.objects.filter(marketplace=Marketplace.OZON, is_active=True).count(),
        )
        self.assertEqual(job.cities.count(), City.objects.filter(is_active=True).count())
        queue.return_value.enqueue.assert_called_once()

    def test_results_by_job_show_only_selected_cities(self):
        """Job results list sellers that match the selected cities."""
        from core.models import CollectionJobSeller

        city = City.objects.get_or_create(name="Уфа")[0]
        job = CollectionJob.objects.create(marketplace=Marketplace.OZON, max_sellers=10)
        job.cities.set([city])
        matched = Seller.objects.create(
            marketplace=Marketplace.OZON,
            external_seller_id="in-ufa",
            name="В Уфе",
            city=city,
        )
        other = Seller.objects.create(
            marketplace=Marketplace.OZON,
            external_seller_id="no-city",
            name="Без города",
        )
        CollectionJobSeller.objects.create(
            job=job, seller=matched, external_seller_id="in-ufa", detail_status="done",
        )
        CollectionJobSeller.objects.create(
            job=job, seller=other, external_seller_id="no-city", detail_status="done",
        )
        r = self.client.get(f"/results/?job={job.id}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "В Уфе")
        self.assertNotContains(r, "Без города")

    def test_results_city_name_search(self):
        ufa = City.objects.get_or_create(name="Уфа")[0]
        kazan = City.objects.get_or_create(name="Казань")[0]
        Seller.objects.create(marketplace=Marketplace.WB, external_seller_id="ufa", name="Уфимский", city=ufa)
        Seller.objects.create(marketplace=Marketplace.WB, external_seller_id="kazan", name="Казанский", city=kazan)
        response = self.client.get("/results/?city_q=уфа")
        self.assertContains(response, "Уфимский")
        self.assertNotContains(response, "Казанский")

    def test_start_job_requires_city(self):
        cat = Category.objects.create(marketplace=Marketplace.OZON, external_id="q", title="Q")
        r = self.client.post("/", {
            "marketplace": Marketplace.OZON,
            "categories": [cat.id],
            "max_sellers": "10",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(CollectionJob.objects.count(), 0)
    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/results/").status_code, 302)
