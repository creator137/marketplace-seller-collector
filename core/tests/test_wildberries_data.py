"""Wildberries: какие поля реально извлекаются из search и supplier card (без сети)."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.adapters.base import SellerData
from core.adapters.wildberries import WildberriesAdapter
from core.models import Category, Marketplace
from core.services import upsert_seller

WB_SEARCH = {
    "data": {
        "products": [
            {
                "id": 101,
                "supplierId": 555001,
                "supplier": "ООО Носки",
                "supplierRating": 4.8,
                "brand": "SockBrand",
            },
            {
                "id": 102,
                "supplierId": 555002,
                "supplier": "ИП Петров",
            },
            {"id": 103},  # без seller — пропускается
        ]
    }
}

WB_CARD = {
    "data": {
        "supplierName": "ООО Носки",
        "INN": "0277001234",
        "OGRN": "1020200000000",
        "address": "г. Уфа, ул. Первомайская, 1",
        "site": "https://socks.example",
    }
}


class WildberriesDiscoveryFieldsTest(TestCase):
    """Поля из search.wb.ru каталога/поиска."""

    @patch("core.adapters.wildberries.HttpClient")
    def test_search_returns_id_name_url_rating(self, client_cls):
        resp = MagicMock()
        resp.json.return_value = WB_SEARCH
        client_cls.return_value.get.return_value = resp

        cat = Category(marketplace=Marketplace.WB, external_id="noski|носки", title="Носки")
        sellers = WildberriesAdapter().discover_sellers(cat, limit=10)

        self.assertEqual(len(sellers), 2)
        first, second = sellers

        self.assertIsInstance(first, SellerData)
        self.assertEqual(first.marketplace, "wildberries")
        self.assertEqual(first.external_seller_id, "555001")
        self.assertEqual(first.name, "ООО Носки")
        self.assertEqual(first.rating, "4.8")
        self.assertEqual(first.seller_url, "https://www.wildberries.ru/seller/555001")
        self.assertEqual(first.category_refs, ["noski|носки"])
        self.assertEqual(first.raw.get("brand"), "SockBrand")
        # На discovery WB не отдаёт ИНН/адрес/контакты
        self.assertEqual(first.inn, "")
        self.assertEqual(first.legal_address, "")
        self.assertEqual(first.mobile_phones, [])
        self.assertEqual(first.emails, [])
        self.assertEqual(first.website, "")

        self.assertEqual(second.external_seller_id, "555002")
        self.assertEqual(second.name, "ИП Петров")
        self.assertEqual(second.rating, "")


class WildberriesDetailFieldsTest(TestCase):
    """Поля карточки поставщика WB (sellers.wb.ru / static basket)."""

    @patch("core.adapters.wildberries.HttpClient")
    def test_card_returns_inn_ogrn_address_website(self, client_cls):
        resp = MagicMock()
        resp.json.return_value = WB_CARD
        client_cls.return_value.get.return_value = resp

        detail = WildberriesAdapter().fetch_seller("555001")

        self.assertEqual(detail.marketplace, "wildberries")
        self.assertEqual(detail.external_seller_id, "555001")
        self.assertEqual(detail.name, "ООО Носки")
        self.assertEqual(detail.inn, "0277001234")
        self.assertEqual(detail.ogrn, "1020200000000")
        self.assertEqual(detail.legal_address, "г. Уфа, ул. Первомайская, 1")
        self.assertEqual(detail.website, "https://socks.example")
        self.assertEqual(detail.seller_url, "https://www.wildberries.ru/seller/555001")
        # Телефоны/email с WB card не парсятся — только через DaData
        self.assertEqual(detail.mobile_phones, [])
        self.assertEqual(detail.emails, [])
        self.assertIsNone(detail.registered_at)

        seller = upsert_seller(detail)
        self.assertEqual(seller.inn, "0277001234")
        self.assertEqual(seller.website, "https://socks.example")
        self.assertEqual(seller.legal_address, "г. Уфа, ул. Первомайская, 1")
