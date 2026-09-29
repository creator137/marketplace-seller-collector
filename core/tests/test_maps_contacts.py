import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.maps_contacts import TwoGisBrowserLookup, YandexMapsLookup, match_quality
from core.models import ContactEnrichmentJob, Marketplace, Seller, SellerContact


class MapMatchingTest(TestCase):
    def test_accepts_name_or_address_match(self):
        self.assertEqual(match_quality('ООО "Позитроника"', "", "Позитроника", "другой адрес"), "name")
        self.assertEqual(
            match_quality("Другое имя", "г. Уфа, ул. Ленина, д. 10", "Магазин", "Уфа, улица Ленина, 10"),
            "address",
        )
        self.assertEqual(match_quality("Альфа", "ул. Ленина, 10", "Бета", "ул. Мира, 2"), "")

    @override_settings(MAPS_RATE_DELAY=0)
    def test_yandex_extracts_phone_and_provenance(self):
        state = {
            "search": {"results": [{
                "title": "Позитроника",
                "address": "Красногорск, бульвар Строителей, 4к1",
                "phones": [{"value": "+78003330333"}],
                "uri": "ymapsbm1://org?oid=1",
            }]},
        }
        response = Mock(
            url="https://yandex.ru/maps/?text=x",
            text=f'<script type="application/json" class="state-view">{json.dumps(state)}</script>',
        )
        lookup = YandexMapsLookup()
        lookup.http.get = Mock(return_value=response)
        result = lookup.lookup("Позитроника", "")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].phone, "+78003330333")
        self.assertEqual(result[0].quality, "name")

    @override_settings(MAPS_RATE_DELAY=0, MAPS_BROWSER_TIMEOUT=10)
    def test_2gis_uses_browser_context_http_and_ssr_phone(self):
        search = Mock(
            url="https://2gis.ru/moscow/search/x",
            text=Mock(return_value='''
                <article>Позитроника, бульвар Строителей, 4к1
                <a href="/moscow/firm/123">Открыть</a></article>
            '''),
        )
        detail = Mock(
            url="https://2gis.ru/moscow/firm/123",
            text=Mock(return_value='''
                <title>Позитроника, магазин — 2ГИС</title>
                <div>бульвар Строителей, 4к1</div>
                <a href="tel:+78003330333">Показать телефон</a>
            '''),
        )
        lookup = TwoGisBrowserLookup.__new__(TwoGisBrowserLookup)
        lookup.context = Mock()
        lookup.context.request.get = Mock(side_effect=[search, detail])
        result = lookup.lookup("Позитроника", "бульвар Строителей, 4к1", "Москва")
        self.assertEqual([(item.phone, item.source) for item in result], [("+78003330333", "2gis")])


class MapEnrichmentViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("maps", password="pass12345")
        self.client.login(username="maps", password="pass12345")

    @patch("core.views._rq_queue")
    def test_button_queues_background_job(self, queue):
        Seller.objects.create(
            marketplace=Marketplace.WB,
            external_seller_id="1",
            name="Позитроника",
            inn="7736607968",
        )
        response = self.client.post("/contacts/enrich/", {"marketplace": Marketplace.WB})
        self.assertEqual(response.status_code, 302)
        job = ContactEnrichmentJob.objects.get()
        self.assertEqual(job.marketplace, Marketplace.WB)
        queue.return_value.enqueue.assert_called_once()

    def test_results_show_maps_contact(self):
        seller = Seller.objects.create(
            marketplace=Marketplace.WB, external_seller_id="2", name="Магазин",
        )
        SellerContact.objects.create(
            seller=seller,
            type=SellerContact.TYPE_CITY_PHONE,
            value="+73470000000",
            source=SellerContact.SOURCE_YANDEX_MAPS,
            source_ref="match=name",
        )
        response = self.client.get("/results/?marketplace=wildberries")
        self.assertContains(response, "+73470000000")
