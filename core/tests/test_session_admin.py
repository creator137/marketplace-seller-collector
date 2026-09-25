from django.contrib.auth.models import User
from django.test import TestCase

from core.models import MarketplaceSession
from core.httpclient import HttpClient


class MarketplaceSessionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("a", password="pass12345")

    def test_save_writes_session_file_and_worker_reads_it(self):
        session = MarketplaceSession.objects.create(
            marketplace="ozon",
            cookies="__Secure-abc=123; xcid=xyz",
        )
        cookies = HttpClient.marketplace_cookies("ozon", "")
        self.assertEqual(cookies.get("__Secure-abc"), "123")
        self.assertEqual(cookies.get("xcid"), "xyz")
        status = MarketplaceSession.file_status("ozon")
        self.assertIn("2 cookies", status)

    def test_header_string_and_json_both_parse(self):
        s1 = MarketplaceSession(marketplace="ozon", cookies='{"a": "1"}')
        self.assertEqual(s1.parsed_cookies(), {"a": "1"})
        s2 = MarketplaceSession(marketplace="wildberries", cookies="a=1; b=2")
        self.assertEqual(s2.parsed_cookies(), {"a": "1", "b": "2"})

    def test_admin_changelist_renders(self):
        self.client.login(username="a", password="pass12345")
        MarketplaceSession.objects.create(marketplace="yandex_market", cookies="a=1")
        r = self.client.get("/admin/core/marketplacesession/")
        self.assertEqual(r.status_code, 200)
