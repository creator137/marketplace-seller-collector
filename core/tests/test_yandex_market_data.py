"""Яндекс.Маркет: какие поля реально извлекаются из search snippets и seller page."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.adapters.base import SellerData
from core.adapters.yandex_market import YandexMarketAdapter, parse_snippets
from core.models import Category, Marketplace
from core.services import upsert_seller

YM_SEARCH_HTML = """
<div data-zone-name="productSnippet" data-zone-data="{&quot;marketSku&quot;:&quot;111&quot;,&quot;supplierId&quot;:&quot;216408895&quot;,&quot;shopId&quot;:&quot;216408895&quot;,&quot;supplierName&quot;:&quot;Shop One&quot;,&quot;title&quot;:&quot;Футболка&quot;,&quot;rating&quot;:{&quot;rating&quot;:&quot;4.9&quot;}}"><a>1</a></div>
<div data-zone-name="productSnippet" data-zone-data="{&quot;marketSku&quot;:&quot;222&quot;,&quot;supplierId&quot;:&quot;6316787&quot;,&quot;title&quot;:&quot;Куртка&quot;}"><a>2</a></div>
<div data-zone-name="productSnippet" data-zone-data="{&quot;broken&quot;:"><a>3</a></div>
"""

YM_SELLER_HTML = """
<html><script>
var state = {
  "sellerName": "ООО Маркет Шоп",
  "inn": "7701234567",
  "ogrn": "1027700000000",
  "legalAddress": "г. Москва, Тверская 1"
};
</script></html>
"""


class YandexMarketDiscoveryFieldsTest(TestCase):
    """Поля из HTML-сниппетов поиска/категории."""

    def test_parse_snippets_extracts_supplier_and_rating(self):
        snippets = parse_snippets(YM_SEARCH_HTML)
        self.assertEqual(len(snippets), 2)
        self.assertEqual(snippets[0]["supplierId"], "216408895")
        self.assertEqual(snippets[0]["supplierName"], "Shop One")
        self.assertEqual(snippets[0]["rating"]["rating"], "4.9")

    def test_parse_snippets_accepts_reversed_attribute_order(self):
        page = (
            '<div data-zone-data="{&quot;marketSku&quot;:&quot;1&quot;,'
            '&quot;businessId&quot;:&quot;9001&quot;}" '
            'class="snippet" data-zone-name="productSnippet"></div>'
        )
        self.assertEqual(parse_snippets(page)[0]["businessId"], "9001")

    @patch("core.adapters.yandex_market.HttpClient")
    def test_discover_returns_id_name_url_rating(self, client_cls):
        resp = MagicMock(status_code=200)
        resp.text = YM_SEARCH_HTML
        client_cls.return_value.get.return_value = resp

        cat = Category(marketplace=Marketplace.YM, external_id="футболка", title="Футболки")
        sellers = YandexMarketAdapter().discover_sellers(cat, limit=10)

        self.assertIn("/catalog--x/0/list", client_cls.return_value.get.call_args.args[0])

        self.assertEqual(len(sellers), 2)
        first, second = sellers

        self.assertIsInstance(first, SellerData)
        self.assertEqual(first.marketplace, "yandex_market")
        self.assertEqual(first.external_seller_id, "216408895")
        self.assertEqual(first.name, "Shop One")
        self.assertEqual(first.rating, "4.9")
        self.assertEqual(first.seller_url, "https://market.yandex.ru/seller/216408895/")
        self.assertEqual(first.category_refs, ["футболка"])
        # На discovery YM не отдаёт ИНН/адрес/контакты
        self.assertEqual(first.inn, "")
        self.assertEqual(first.legal_address, "")
        self.assertEqual(first.mobile_phones, [])
        self.assertEqual(first.emails, [])
        self.assertEqual(first.website, "")

        self.assertEqual(second.external_seller_id, "6316787")
        self.assertEqual(second.name, "")  # supplierName отсутствует в сниппете
        self.assertEqual(second.rating, "")

    @patch("core.adapters.yandex_market.HttpClient")
    def test_discovery_prefers_current_business_profile_id(self, client_cls):
        resp = MagicMock(status_code=200)
        resp.text = (
            '<div data-zone-name="productSnippet" data-zone-data="'
            '{&quot;marketSku&quot;:&quot;111&quot;,'
            '&quot;supplierId&quot;:&quot;216408895&quot;,'
            '&quot;businessId&quot;:&quot;125778503&quot;}"></div>'
        )
        client_cls.return_value.get.return_value = resp
        cat = Category(marketplace=Marketplace.YM, external_id="наушники", title="Наушники")

        seller = YandexMarketAdapter().discover_sellers(cat, limit=1)[0]

        self.assertEqual(seller.external_seller_id, "125778503")
        self.assertEqual(
            seller.seller_url,
            "https://market.yandex.ru/business--x/125778503",
        )


class YandexMarketDetailFieldsTest(TestCase):
    """Поля страницы продавца (доступны только при успешном HTTP 200)."""

    @patch("core.adapters.yandex_market.HttpClient")
    def test_seller_page_parses_name_inn_ogrn_address(self, client_cls):
        resp = MagicMock(status_code=200)
        resp.text = YM_SELLER_HTML
        client_cls.return_value.get.return_value = resp

        detail = YandexMarketAdapter().fetch_seller("216408895")

        self.assertEqual(detail.marketplace, "yandex_market")
        self.assertEqual(detail.external_seller_id, "216408895")
        self.assertEqual(detail.name, "ООО Маркет Шоп")
        self.assertEqual(detail.inn, "7701234567")
        self.assertEqual(detail.ogrn, "1027700000000")
        self.assertEqual(detail.legal_address, "г. Москва, Тверская 1")
        self.assertEqual(detail.seller_url, "https://market.yandex.ru/business--x/216408895")
        self.assertEqual(detail.mobile_phones, [])
        self.assertEqual(detail.emails, [])

        seller = upsert_seller(detail)
        self.assertEqual(seller.inn, "7701234567")
        self.assertEqual(seller.name, "ООО Маркет Шоп")

    @patch("core.adapters.yandex_market.HttpClient")
    def test_current_business_profile_parses_name_rating_and_canonical_url(self, client_cls):
        resp = MagicMock(status_code=200)
        resp.url = "https://market.yandex.ru/business--x/125778503"
        resp.text = (
            '<script>{"shopName":"IDDQD","shopRating":4.5,'
            '"navigationUrl":"/business--iddqd/125778503"}</script>'
        )
        client_cls.return_value.get.return_value = resp

        detail = YandexMarketAdapter().fetch_seller(
            "https://market.yandex.ru/business--x/125778503"
        )

        self.assertEqual(detail.external_seller_id, "125778503")
        self.assertEqual(detail.name, "IDDQD")
        self.assertEqual(detail.rating, "4.5")
        self.assertEqual(
            detail.seller_url,
            "https://market.yandex.ru/business--iddqd/125778503",
        )

    @patch("core.adapters.yandex_market.HttpClient")
    def test_seller_page_404_returns_none(self, client_cls):
        resp = MagicMock(status_code=404)
        resp.text = "Not Found"
        client_cls.return_value.get.return_value = resp

        self.assertIsNone(YandexMarketAdapter().fetch_seller("216408895"))
