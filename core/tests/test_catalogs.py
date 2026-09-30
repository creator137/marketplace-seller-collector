from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Category, City, Marketplace, MarketplaceSession


class CatalogsViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="pass12345")
        self.client.login(username="u", password="pass12345")

    def test_catalogs_page(self):
        r = self.client.get("/catalogs/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Города")
        self.assertContains(r, "Категории")
        self.assertContains(r, "Сессии")

    def test_city_search(self):
        City.objects.create(name="Казань")
        City.objects.create(name="Самара")
        response = self.client.get("/catalogs/?section=cities&q=каз")
        self.assertContains(response, "Казань")
        self.assertNotContains(response, "Самара")

    def test_category_search(self):
        Category.objects.create(marketplace=Marketplace.WB, title="Наушники", external_id="headphones")
        Category.objects.create(marketplace=Marketplace.WB, title="Обувь", external_id="shoes")
        response = self.client.get("/catalogs/?section=categories&marketplace=wildberries&q=науш")
        self.assertContains(response, "Наушники")
        self.assertNotContains(response, "Обувь")

    def test_collection_selects_have_search_fields(self):
        response = self.client.get("/selects/?marketplace=wildberries")
        self.assertContains(response, 'id="city-filter"')
        self.assertContains(response, 'id="category-filter"')

    def test_instruction_is_ui_only(self):
        response = self.client.get("/how-it-works/")
        self.assertContains(response, "Открыть Chromium")
        self.assertNotContains(response, "python manage.py")

    def test_sessions_section(self):
        r = self.client.get("/catalogs/?section=sessions")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Сессии маркетплейсов")
        self.assertContains(r, "Ozon")

    def test_add_city(self):
        r = self.client.post("/catalogs/", {
            "action": "add_city",
            "name": "Казань",
            "dest_code": "",
        })
        self.assertEqual(r.status_code, 302)
        city = City.objects.get(name="Казань")
        self.assertTrue(city.is_active)
        self.assertEqual(city.normalized, "казань")

    def test_add_duplicate_city_rejected(self):
        City.objects.create(name="Казань")
        before = City.objects.count()
        r = self.client.post("/catalogs/", {
            "action": "add_city",
            "name": "казань",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(City.objects.count(), before)

    def test_toggle_city(self):
        city = City.objects.create(name="Самара")
        r = self.client.post("/catalogs/", {
            "action": "toggle_city",
            "city_id": city.id,
        })
        self.assertEqual(r.status_code, 302)
        city.refresh_from_db()
        self.assertFalse(city.is_active)

    def test_add_category(self):
        r = self.client.post("/catalogs/", {
            "action": "add_category",
            "marketplace": Marketplace.OZON,
            "title": "Планшеты",
            "external_id": "планшеты",
        })
        self.assertEqual(r.status_code, 302)
        cat = Category.objects.get(marketplace=Marketplace.OZON, external_id="планшеты")
        self.assertEqual(cat.title, "Планшеты")
        self.assertTrue(cat.is_active)

    def test_add_duplicate_category_rejected(self):
        Category.objects.create(
            marketplace=Marketplace.OZON, title="X", external_id="планшеты",
        )
        before = Category.objects.count()
        r = self.client.post("/catalogs/", {
            "action": "add_category",
            "marketplace": Marketplace.OZON,
            "title": "Планшеты 2",
            "external_id": "планшеты",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Category.objects.count(), before)

    def test_toggle_category(self):
        cat = Category.objects.create(
            marketplace=Marketplace.WB, title="Носки", external_id="noski",
        )
        r = self.client.post("/catalogs/", {
            "action": "toggle_category",
            "category_id": cat.id,
            "marketplace": Marketplace.WB,
        })
        self.assertEqual(r.status_code, 302)
        cat.refresh_from_db()
        self.assertFalse(cat.is_active)

    def test_delete_city(self):
        city = City.objects.create(name="Тула")
        r = self.client.post("/catalogs/", {
            "action": "delete_city",
            "city_id": city.id,
        })
        self.assertEqual(r.status_code, 302)
        self.assertFalse(City.objects.filter(pk=city.id).exists())

    def test_delete_category(self):
        cat = Category.objects.create(
            marketplace=Marketplace.OZON, title="Удалить", external_id="del-me",
        )
        r = self.client.post("/catalogs/", {
            "action": "delete_category",
            "category_id": cat.id,
            "marketplace": Marketplace.OZON,
        })
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Category.objects.filter(pk=cat.id).exists())

    def test_save_and_delete_session(self):
        r = self.client.post("/catalogs/", {
            "action": "save_session",
            "marketplace": Marketplace.OZON,
            "cookies": "a=1; b=2",
            "note": "test",
        })
        self.assertEqual(r.status_code, 302)
        session = MarketplaceSession.objects.get(marketplace=Marketplace.OZON)
        self.assertEqual(session.source, "ui")
        self.assertEqual(session.parsed_cookies(), {"a": "1", "b": "2"})

        r = self.client.post("/catalogs/", {
            "action": "delete_session",
            "session_id": session.id,
        })
        self.assertEqual(r.status_code, 302)
        self.assertFalse(MarketplaceSession.objects.filter(marketplace=Marketplace.OZON).exists())

    def test_browser_save_action(self):
        from unittest.mock import patch

        with patch("core.browser_session.save_marketplace_cookies", return_value=7) as save:
            response = self.client.post("/catalogs/?section=sessions", {
                "action": "browser_save",
                "marketplace": Marketplace.OZON,
            })
        self.assertEqual(response.status_code, 302)
        save.assert_called_once_with(Marketplace.OZON)

    def test_browser_open_redirects_to_protected_novnc(self):
        from unittest.mock import patch

        with patch("core.browser_session.open_marketplace", return_value="https://www.ozon.ru/"), patch(
            "core.browser_session.vnc_password", return_value="secret123",
        ):
            response = self.client.post("/catalogs/?section=sessions", {
                "action": "browser_open",
                "marketplace": Marketplace.OZON,
            })
        self.assertEqual(response.status_code, 302)
        self.assertIn(":6080/vnc.html", response.url)
        self.assertIn("password=secret123", response.url)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/catalogs/").status_code, 302)
