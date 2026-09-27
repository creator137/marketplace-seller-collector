"""Ozon: какие поля реально извлекаются из discovery и detail (без сети)."""
import json
from unittest.mock import patch

from django.test import TestCase

from core.adapters.base import SellerData
from core.adapters.ozon import OzonAdapter, seller_requisites
from core.models import Category, Marketplace
from core.services import upsert_seller

# Discovery via product tiles -> webCurrentSeller
OZON_SEARCH = {
    "widgetStates": {
        "tileGridDesktop-1": """{"items":[
            {"action":{"link":"/product/naushniki-1667800384/"},"sku":1667800384}
        ]}""",
    },
}

OZON_PRODUCT = {
    "/product/naushniki-1667800384": {
        "widgetStates": {
            "webCurrentSeller-1": """{
                "sellerCell":{
                    "centerBlock":{"title":{"text":"AudioShop"}},
                    "common":{"action":{"link":"https://www.ozon.ru/seller/audioshop-777/"}}
                },
                "rating":{"title":{"text":"4.9"}}
            }""",
        }
    }
}

# Detail via sellerTransparencyProfile (legacy layout with requisites)
OZON_SELLER_DETAIL = {
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

# Discovery via sellerList widget
OZON_SELLER_LIST = {
    "nextPage": None,
    "widgetStates": {
        "sellerList-1": """{"items":[
            {"title":"ListSeller","deeplink":"ozon://seller/999?miniapp","rating":"4.2"}
        ]}""",
    },
}


class OzonDiscoveryFieldsTest(TestCase):
    """Поля, которые Ozon отдаёт на этапе поиска/категории."""

    @patch.object(OzonAdapter, "_api_get")
    def test_product_path_returns_id_name_url_rating(self, api_get):
        def fake(path):
            if path.startswith("/product/"):
                return OZON_PRODUCT.get(path.split("?")[0])
            return OZON_SEARCH

        api_get.side_effect = fake
        cat = Category(marketplace=Marketplace.OZON, external_id="наушники", title="Наушники")
        sellers = OzonAdapter().discover_sellers(cat, limit=5)

        self.assertEqual(len(sellers), 1)
        s = sellers[0]
        self.assertIsInstance(s, SellerData)
        self.assertEqual(s.marketplace, "ozon")
        self.assertEqual(s.external_seller_id, "777")
        self.assertEqual(s.name, "AudioShop")
        self.assertEqual(s.rating, "4.9")
        self.assertIn("/seller/", s.seller_url)
        self.assertEqual(s.category_refs, ["наушники"])
        # На discovery Ozon не отдаёт реквизиты/контакты
        self.assertEqual(s.inn, "")
        self.assertEqual(s.ogrn, "")
        self.assertEqual(s.legal_address, "")
        self.assertEqual(s.mobile_phones, [])
        self.assertEqual(s.emails, [])
        self.assertEqual(s.website, "")
        self.assertIsNone(s.registered_at)

    @patch.object(OzonAdapter, "_api_get")
    def test_sellerlist_path_returns_id_name_url(self, api_get):
        api_get.return_value = OZON_SELLER_LIST
        cat = Category(marketplace=Marketplace.OZON, external_id="/category/x/", title="X")
        sellers = OzonAdapter().discover_sellers(cat, limit=5)

        self.assertEqual(len(sellers), 1)
        s = sellers[0]
        self.assertEqual(s.external_seller_id, "999")
        self.assertEqual(s.name, "ListSeller")
        self.assertEqual(s.seller_url, "https://www.ozon.ru/seller/999/")
        self.assertEqual(s.rating, "4.2")


class OzonDetailFieldsTest(TestCase):
    """Поля карточки продавца Ozon (seller page)."""

    @patch.object(OzonAdapter, "_api_get")
    def test_transparency_profile_fills_requisites_and_date(self, api_get):
        api_get.return_value = OZON_SELLER_DETAIL
        detail = OzonAdapter().fetch_seller("777")

        self.assertEqual(detail.marketplace, "ozon")
        self.assertEqual(detail.external_seller_id, "777")
        self.assertEqual(detail.name, "AudioShop")
        self.assertEqual(detail.inn, "7801234567")
        self.assertEqual(detail.ogrn, "1157801234567")
        self.assertEqual(detail.legal_address, "г. Санкт-Петербург, Ленина 1")
        self.assertEqual(detail.registered_at, "2019-03-15")
        self.assertTrue(detail.seller_url.endswith("/seller/777/"))
        # Телефоны/email с Ozon не парсятся — только через DaData
        self.assertEqual(detail.mobile_phones, [])
        self.assertEqual(detail.emails, [])

        seller = upsert_seller(detail)
        self.assertEqual(seller.inn, "7801234567")
        self.assertEqual(seller.ogrn, "1157801234567")
        self.assertEqual(str(seller.registered_at), "2019-03-15")

    @patch.object(OzonAdapter, "_api_get")
    def test_fetch_seller_shop_info_modal_ogrn(self, api_get):
        """Реквизиты из popup «i» на странице продавца (/modal/shop-in-shop-info)."""
        page = {
            "widgetStates": {
                "sellerTransparency-1": json.dumps({
                    "title": {"text": "Orbitronics"},
                    "badges": [{
                        "common": {
                            "action": {
                                "link": "/modal/shop-in-shop-info?seller_id=3868445",
                            }
                        }
                    }],
                }, ensure_ascii=False),
            }
        }
        modal = {
            "widgetStates": {
                "textBlock-1": json.dumps({
                    "body": [{
                        "type": "textAtom",
                        "textAtom": {
                            "text": "ИП Прокофьев Александр Александрович<br>325595800180311",
                        },
                    }],
                }, ensure_ascii=False),
            }
        }

        def fake(path):
            if "shop-in-shop-info" in path:
                return modal
            return page

        api_get.side_effect = fake
        detail = OzonAdapter().fetch_seller("https://www.ozon.ru/seller/orbitronics/")
        self.assertEqual(detail.external_seller_id, "3868445")
        self.assertEqual(detail.name, "Orbitronics")
        self.assertEqual(detail.ogrn, "325595800180311")
        self.assertEqual(detail.inn, "")
        self.assertIn("ИП Прокофьев", detail.raw["shop_info"]["name"])

    def test_parse_shop_info_modal_direct(self):
        from core.adapters.ozon import _parse_shop_info_modal

        parsed = _parse_shop_info_modal({
            "widgetStates": {
                "textBlock-1": json.dumps({
                    "body": [{
                        "textAtom": {
                            "text": "ИП Прокофьев Александр Александрович<br>325595800180311",
                        }
                    }],
                }, ensure_ascii=False),
            }
        })
        self.assertEqual(parsed["ogrn"], "325595800180311")
        self.assertTrue(parsed["name"].startswith("ИП Прокофьев"))

    def test_ogrn_without_label_is_not_treated_as_inn(self):
        html = "<span>ИП Жилич Максим Александрович<br>324344300091317</span>"
        self.assertEqual(seller_requisites(html), {"ogrn": "324344300091317"})
        self.assertNotIn("inn", seller_requisites(html))
        self.assertEqual(seller_requisites("ИНН: 7801234567"), {"inn": "7801234567"})
