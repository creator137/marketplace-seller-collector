"""Adapter parsing tests from saved fixtures (no live network)."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from core.adapters.ozon import OzonAdapter, widget_state
from core.adapters.yandex_market import parse_snippets
from core.models import Category, Marketplace
from core.services import upsert_seller
from core.httpclient import HttpClient

OZON_CATEGORY_RESPONSE = {
    "nextPage": "/category/naushniki-15692/?page=2",
    "widgetStates": {
        "sellerList-123": """{"items":[
            {"title":"AudioShop","deeplink":"ozon://seller/777?miniapp"},
            {"title":"Без id","deeplink":"ozon://bad"},
            {"title":"VideoStore","deeplink":"ozon://seller/888?miniapp"}
        ]}""",
    },
}

OZON_SEARCH_RESPONSE = {
    "widgetStates": {
        "tileGridDesktop-1": """{"items":[
            {"action":{"link":"/product/naushniki-1667800384/?x=1"},"sku":1667800384},
            {"action":{"link":"/product/naushniki-1667800385/"},"sku":1667800385}
        ]}""",
    },
}

OZON_PRODUCT_RESPONSES = {
    "/product/naushniki-1667800384": {
        "widgetStates": {
            "webCurrentSeller-1": """{"sellerCell":{"centerBlock":{"title":{"text":"AudioShop"}},"common":{"action":{"link":"https://www.ozon.ru/seller/audioshop-777/"}}},"rating":{"title":{"text":"4.9"}}}""",
        }
    },
    "/product/naushniki-1667800385": {
        "widgetStates": {
            "webCurrentSeller-2": """{"sellerCell":{"centerBlock":{"title":{"text":"VideoStore"}},"common":{"action":{"link":"ozon://seller/888?miniapp"}}},"rating":{"title":{"text":"4.7"}}}""",
        }
    },
}

YM_SEARCH_HTML = """
<div data-zone-name="productSnippet" data-zone-data="{&quot;marketSku&quot;:&quot;111&quot;,&quot;supplierId&quot;:&quot;216408895&quot;,&quot;shopId&quot;:&quot;216408895&quot;,&quot;title&quot;:&quot;Футболка&quot;,&quot;rating&quot;:{&quot;rating&quot;:&quot;4.9&quot;}}"><a>1</a></div>
<div data-zone-name="productSnippet" data-zone-data="{&quot;marketSku&quot;:&quot;222&quot;,&quot;supplierId&quot;:&quot;6316787&quot;,&quot;title&quot;:&quot;Куртка&quot;}"><a>2</a></div>
<div data-zone-name="productSnippet" data-zone-data="{&quot;broken&quot;:"><a>3</a></div>
"""

WB_SEARCH_RESPONSE = {
    "data": {
        "products": [
            {"id": 1, "supplierId": 555001, "supplier": "ООО Носки", "supplierRating": 4.8, "brand": "X"},
            {"id": 2, "supplierId": 555002, "supplier": "ИП Петров"},
            {"id": 3},
        ]
    }
}


class OzonParsingTest(TestCase):
    def test_information_registration_is_not_inn(self):
        from core.adapters.ozon import seller_requisites

        html = '<span>ИП Жилич Максим Александрович<br>324344300091317</span>'
        self.assertEqual(seller_requisites(html), {'ogrn': '324344300091317'})
        self.assertEqual(seller_requisites({'inn': '324344300091317'}), {})
        self.assertEqual(seller_requisites('ИНН: 7801234567'), {'inn': '7801234567'})
        self.assertEqual(seller_requisites({'id': '123456789012'}), {})

    def test_runtime_browser_session_overrides_env_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "runtime" / "sessions"
            session_dir.mkdir(parents=True)
            (session_dir / "ozon.json").write_text(json.dumps({
                "cookies": [{"name": "session", "value": "browser"}, {"name": "fresh", "value": "1"}],
            }), encoding="utf-8")
            with override_settings(BASE_DIR=Path(tmp)):
                cookies = HttpClient.marketplace_cookies("ozon", "session=env; old=value")
        self.assertEqual(cookies, {"session": "browser", "old": "value", "fresh": "1"})

    def test_widget_state(self):
        state = widget_state(OZON_CATEGORY_RESPONSE, "sellerList")
        self.assertEqual(len(state["items"]), 3)
        self.assertIsNone(widget_state(OZON_CATEGORY_RESPONSE, "nope"))

    @patch.object(OzonAdapter, "_api_get")
    def test_discover_sellers_from_products(self, api_get):
        def fake_api(path):
            if "page=" in path or "search" in path or path.startswith("/category/"):
                return OZON_SEARCH_RESPONSE
            return OZON_PRODUCT_RESPONSES.get(path.split("?")[0])

        api_get.side_effect = fake_api
        adapter = OzonAdapter()
        cat = Category(marketplace=Marketplace.OZON, external_id="наушники", title="Наушники")
        sellers = adapter.discover_sellers(cat, limit=10)
        self.assertEqual([s.external_seller_id for s in sellers], ["777", "888"])
        self.assertEqual(sellers[0].name, "AudioShop")
        self.assertEqual(sellers[0].rating, "4.9")

    @patch.object(OzonAdapter, "_api_get")
    def test_discover_sellers_sellerlist_fallback(self, api_get):
        api_get.return_value = {**OZON_CATEGORY_RESPONSE, "nextPage": None}
        adapter = OzonAdapter()
        cat = Category(marketplace=Marketplace.OZON, external_id="/category/naushniki-15692/", title="Наушники")
        sellers = adapter.discover_sellers(cat, limit=10)
        self.assertEqual([s.external_seller_id for s in sellers], ["777", "888"])
        self.assertEqual(sellers[0].seller_url, "https://www.ozon.ru/seller/777/")

    @patch.object(OzonAdapter, "_api_get")
    def test_fetch_seller_transparency(self, api_get):
        api_get.return_value = {
            "layout": [{
                "component": "sellerTransparencyProfile",
                "placeholders": [{
                    "name": "onAboutShopInfo",
                    "fields": {
                        "title": "AudioShop",
                        "inn": "7801234567",
                        "ogrn": "1157801234567",
                        "legalAddress": "г. Санкт-Петербург, Ленина 1",
                        "registrationDate": "с 15.03.2019",
                    },
                }],
            }]
        }
        detail = OzonAdapter().fetch_seller("777")
        self.assertEqual(detail.name, "AudioShop")
        self.assertEqual(detail.inn, "7801234567")
        self.assertEqual(detail.registered_at, "2019-03-15")
        seller = upsert_seller(detail)
        self.assertEqual(seller.inn, "7801234567")

    @patch.object(OzonAdapter, "_api_get")
    def test_fetch_seller_current_widget(self, api_get):
        api_get.return_value = {
            "widgetStates": {
                "sellerTransparency-1": """{"title":{"text":"Официальный магазин Bloody"}}""",
            }
        }
        detail = OzonAdapter().fetch_seller("1981149")
        self.assertEqual(detail.name, "Официальный магазин Bloody")

    @patch.object(OzonAdapter, "_api_get")
    def test_pagination_streams_multiple_pages(self, api_get):
        page1 = {"nextPage": "/search/?text=x&page=2", "widgetStates": {
            "sellerList-1": '{"items":[{"title":"One","deeplink":"/seller/1/"}]}'
        }}
        page2 = {"widgetStates": {
            "sellerList-2": '{"items":[{"title":"Two","deeplink":"/seller/2/"}]}'
        }}
        api_get.side_effect = [page1, page2]
        cat = Category(marketplace=Marketplace.OZON, external_id="x", title="X")
        adapter = OzonAdapter()
        sellers = adapter.discover_sellers(cat, max_sellers=10)
        self.assertEqual([s.external_seller_id for s in sellers], ["1", "2"])
        self.assertEqual(adapter.last_page, 2)


class YandexMarketParsingTest(TestCase):
    def test_parse_snippets(self):
        snippets = parse_snippets(YM_SEARCH_HTML)
        self.assertEqual(len(snippets), 2)
        self.assertEqual(snippets[0]["supplierId"], "216408895")

    @patch("core.adapters.yandex_market.HttpClient")
    def test_discover(self, client_cls):
        resp = MagicMock(status_code=200)
        resp.text = YM_SEARCH_HTML
        client_cls.return_value.get.return_value = resp
        from core.adapters.yandex_market import YandexMarketAdapter

        cat = Category(marketplace=Marketplace.YM, external_id="футболка", title="Футболки")
        sellers = YandexMarketAdapter().discover_sellers(cat, limit=10)
        self.assertEqual([s.external_seller_id for s in sellers], ["216408895", "6316787"])

    @patch("core.adapters.yandex_market.HttpClient")
    def test_pagination(self, client_cls):
        html1 = YM_SEARCH_HTML.replace("216408895", "1001").replace("6316787", "1002")
        html2 = YM_SEARCH_HTML.replace("111", "333").replace("222", "444").replace("216408895", "1003").replace("6316787", "1004")
        responses = []
        for html in (html1, html2, ""):
            response = MagicMock(status_code=200)
            response.text = html
            responses.append(response)
        client_cls.return_value.get.side_effect = responses
        from core.adapters.yandex_market import YandexMarketAdapter

        cat = Category(marketplace=Marketplace.YM, external_id="футболка", title="Футболки")
        sellers = YandexMarketAdapter().discover_sellers(cat, max_sellers=10)
        self.assertEqual([s.external_seller_id for s in sellers], ["1001", "1002", "1003", "1004"])


class WildberriesParsingTest(TestCase):
    @patch("core.adapters.wildberries.HttpClient")
    def test_discover(self, client_cls):
        resp = MagicMock()
        resp.json.return_value = WB_SEARCH_RESPONSE
        client_cls.return_value.get.return_value = resp
        from core.adapters.wildberries import WildberriesAdapter

        cat = Category(marketplace=Marketplace.WB, external_id="noski|носки", title="Носки")
        sellers = WildberriesAdapter().discover_sellers(cat, limit=10)
        self.assertEqual([s.external_seller_id for s in sellers], ["555001", "555002"])
        self.assertEqual(sellers[0].name, "ООО Носки")

    @patch("core.adapters.wildberries.HttpClient")
    def test_fetch_seller_card(self, client_cls):
        resp = MagicMock()
        resp.json.return_value = {
            "data": {"supplierName": "ООО Носки", "INN": "0277001234", "address": "г. Уфа, ул. Первомайская, 1"}
        }
        client_cls.return_value.get.return_value = resp
        from core.adapters.wildberries import WildberriesAdapter

        detail = WildberriesAdapter().fetch_seller("555001")
        self.assertEqual(detail.inn, "0277001234")
        seller = upsert_seller(detail)
        self.assertEqual(seller.inn, "0277001234")

    @patch("core.adapters.wildberries.HttpClient")
    def test_pagination(self, client_cls):
        first = MagicMock()
        first.json.return_value = WB_SEARCH_RESPONSE
        second = MagicMock()
        second.json.return_value = {"data": {"products": [
            {"id": 4, "supplierId": 555003, "supplier": "ООО Третий"},
        ]}}
        third = MagicMock()
        third.json.return_value = {"data": {"products": []}}
        client_cls.return_value.get.side_effect = [first, second, third]
        from core.adapters.wildberries import WildberriesAdapter

        cat = Category(marketplace=Marketplace.WB, external_id="noski|носки", title="Носки")
        sellers = WildberriesAdapter().discover_sellers(cat, max_sellers=10)
        self.assertEqual([s.external_seller_id for s in sellers], ["555001", "555002", "555003"])
        calls = client_cls.return_value.get.call_args_list
        self.assertIn("/common/v18/search", calls[0].args[0])
        self.assertNotIn("page", calls[0].kwargs["params"])
        self.assertNotIn("x-queryid", calls[0].kwargs["headers"])
        self.assertEqual(calls[1].kwargs["params"]["page"], "2")

    @patch("core.adapters.wildberries.HttpClient")
    def test_browser_device_id_is_sent(self, client_cls):
        client_cls.marketplace_session.return_value = {
            "local_storage": {"wbx__sessionID": "site_test-device"},
            "browser": {
                "user_agent": "Browser Test/154",
                "brands": [{"brand": "Chromium", "version": "154"}],
                "mobile": False,
                "platform": "Linux",
            },
        }
        from core.adapters.wildberries import WildberriesAdapter

        WildberriesAdapter()

        self.assertEqual(client_cls.call_args.kwargs["headers"]["deviceid"], "site_test-device")
        self.assertEqual(client_cls.call_args.kwargs["headers"]["user-agent"], "Browser Test/154")
        self.assertEqual(client_cls.call_args.kwargs["headers"]["sec-ch-ua"], '"Chromium";v="154"')
        self.assertEqual(client_cls.call_args.kwargs["impersonate"], "chrome150")

    @patch("core.adapters.wildberries.HttpClient")
    def test_synced_category_uses_title_not_routing_query(self, client_cls):
        response = MagicMock()
        response.json.return_value = {"data": {"products": []}}
        client_cls.return_value.get.return_value = response
        from core.adapters.wildberries import WildberriesAdapter

        cat = Category(
            marketplace=Marketplace.WB,
            external_id="electronic81|cat=60808",
            title="3D-печать",
        )
        list(WildberriesAdapter().iter_sellers(cat, max_sellers=10))

        params = client_cls.return_value.get.call_args.kwargs["params"]
        self.assertEqual(params["query"], "3D-печать")

    @patch("core.browser_session.refresh_marketplace_session")
    @patch("core.adapters.wildberries.HttpClient")
    def test_blocked_search_refreshes_session_once(self, client_cls, refresh):
        from core.httpclient import HttpError

        blocked = MagicMock()
        success = MagicMock()
        success.json.return_value = {"data": {"products": []}}
        first_client = MagicMock()
        second_client = MagicMock()
        first_client.get.side_effect = HttpError("HTTP 498", status=498)
        second_client.get.return_value = success
        client_cls.side_effect = [first_client, second_client]
        client_cls.marketplace_session.return_value = {}
        client_cls.marketplace_cookies.return_value = {}
        from core.adapters.wildberries import WildberriesAdapter

        cat = Category(marketplace=Marketplace.WB, external_id="носки", title="Носки")
        list(WildberriesAdapter().iter_sellers(cat, max_sellers=10))

        refresh.assert_called_once_with("wildberries")
        second_client.get.assert_called_once()
