"""Adapter parsing tests from saved fixtures (no live network)."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.adapters.ozon import OzonAdapter, widget_state
from core.adapters.yandex_market import parse_snippets
from core.models import Category, Marketplace
from core.services import upsert_seller

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
            "webCurrentSeller-1": """{"sellerCell":{"centerBlock":{"title":{"text":"AudioShop"}},"common":{"action":{"link":"ozon://seller/777?miniapp"}}},"rating":{"title":{"text":"4.9"}}}""",
        }
    },
    "/product/naushniki-1667800385": {
        "widgetStates": {
            "webCurrentSeller-2": """{"sellerCell":{"centerBlock":{"title":{"text":"VideoStore"}},"common":{"action":{"link":"/seller/888/"}}},"rating":{"title":{"text":"4.7"}}}""",
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
        api_get.return_value = OZON_CATEGORY_RESPONSE
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
