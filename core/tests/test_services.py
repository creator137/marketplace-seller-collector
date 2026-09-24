from django.test import TestCase

from core.citymatch import find_city_in_address, normalize_city_name
from core.models import Category, City, Marketplace, Seller, SellerContact
from core.phoneutils import is_mobile, normalize_email, normalize_phone, telegram_link
from core.services import merge_seller_data, upsert_seller
from core.adapters.base import SellerData


class PhoneUtilsTest(TestCase):
    def test_normalize_mobile(self):
        self.assertEqual(normalize_phone("+7 (917) 123-45-67"), "79171234567")
        self.assertEqual(normalize_phone("8 917 123 45 67"), "79171234567")
        self.assertEqual(normalize_phone("9171234567"), "79171234567")

    def test_mobile_detect(self):
        self.assertTrue(is_mobile("79171234567"))
        self.assertFalse(is_mobile("73472501234"))

    def test_telegram(self):
        self.assertEqual(telegram_link("79171234567"), "https://t.me/+79171234567")
        self.assertEqual(telegram_link(""), "")

    def test_email(self):
        self.assertEqual(normalize_email("Иван <Ivan@Mail.RU>"), "ivan@mail.ru")
        self.assertEqual(normalize_email("nope"), "")


class CityMatchTest(TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_city_name("г. Екатеринбург"), "екатеринбург")
        self.assertEqual(normalize_city_name("Ёлки"), "елки")

    def test_find(self):
        cities = ["уфа", "челябинск", "екатеринбург"]
        self.assertEqual(find_city_in_address("620014, Свердловская обл., г. Екатеринбург, ул. Ленина, 1", cities), "екатеринбург")
        self.assertEqual(find_city_in_address("450000, Башкортостан, Уфа, Ленина 10", cities), "уфа")
        self.assertIsNone(find_city_in_address("Москва, Тверская 1", cities))
        self.assertIsNone(find_city_in_address("", cities))


class SellerUpsertTest(TestCase):
    def _data(self, **kw):
        base = dict(
            marketplace=Marketplace.OZON,
            external_seller_id="123",
            seller_url="https://www.ozon.ru/seller/123/",
            name="Тест Продавец",
            mobile_phones=["+7 917 111-22-33"],
            emails=["SHOP@Mail.RU"],
        )
        base.update(kw)
        return SellerData(**base)

    def test_upsert_creates_once(self):
        s1 = upsert_seller(self._data())
        s2 = upsert_seller(self._data(name="Тест Продавец 2"))
        self.assertEqual(Seller.objects.count(), 1)
        self.assertEqual(SellerContact.objects.filter(seller=s1).count(), 2)
        s1.refresh_from_db()
        self.assertEqual(s1.mobile_phones, ["79171112233"])

    def test_merge_never_erases(self):
        upsert_seller(self._data())
        empty = upsert_seller(self._data(name="", emails=[]))
        empty.refresh_from_db()
        self.assertEqual(empty.name, "Тест Продавец")
        self.assertEqual(empty.emails, ["shop@mail.ru"])

    def test_url_fallback_dedup(self):
        upsert_seller(self._data())
        # same url, different external id -> same record
        upsert_seller(self._data(external_seller_id="999"))
        self.assertEqual(Seller.objects.count(), 1)

    def test_categories_attached(self):
        cat = Category.objects.create(
            marketplace=Marketplace.OZON, external_id="q1", title="Q",
        )
        upsert_seller(self._data(category_refs=["q1"]))
        seller = Seller.objects.first()
        self.assertIn(cat, seller.categories.all())

    def test_city_detected_from_address(self):
        City.objects.get_or_create(name="Екатеринбург")[0]
        upsert_seller(self._data(legal_address="620014, Екатеринбург, Ленина 1"))
        seller = Seller.objects.first()
        self.assertIsNotNone(seller.city)
        self.assertEqual(seller.city.name, "Екатеринбург")

    def test_contact_type_switch_city_phone(self):
        upsert_seller(self._data(mobile_phones=[], city_phones=["347 250-12-34"]))
        seller = Seller.objects.first()
        self.assertEqual(seller.city_phones, ["73472501234"])


class DaDataMergeTest(TestCase):
    def test_enrich_skips_without_token_or_inn(self):
        from django.conf import settings
        from core.services import enrich_with_dadata

        seller = upsert_seller(SellerData(
            marketplace=Marketplace.WB, external_seller_id="1", name="X",
        ))
        # no INN -> nothing happens even with token
        settings.DADATA_TOKEN = "fake"
        self.assertFalse(enrich_with_dadata(seller))
        settings.DADATA_TOKEN = ""

    def test_party_to_fields(self):
        from core.dadata import party_to_fields

        fields = party_to_fields({
            "value": "ООО Ромашка",
            "data": {
                "inn": "0277001234", "ogrn": "1027700123456",
                "address": {"value": "г. Уфа, ул. Ленина, 1"},
                "phones": [{"value": "+7 (917) 555-66-77"}],
                "emails": [{"value": "info@romashka.ru"}],
            },
        })
        self.assertEqual(fields["name"], "ООО Ромашка")
        self.assertEqual(fields["mobile_phones"], ["+7 (917) 555-66-77"])
        self.assertEqual(fields["emails"], ["info@romashka.ru"])
        self.assertEqual(fields["website"], "")
        self.assertTrue(fields["legal_address"].startswith("г. Уфа"))
