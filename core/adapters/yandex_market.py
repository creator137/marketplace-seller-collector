"""Yandex Market adapter: catalog HTML -> productSnippet zone-data JSON."""
import html as html_lib
import json
import logging
import re

from django.conf import settings

from core.adapters.base import CollectionBlocked, CollectionParseError, MarketplaceAdapter, SellerData
from core.httpclient import HttpClient, HttpError

log = logging.getLogger("core.adapters.ym")

BASE = "https://market.yandex.ru"
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PRODUCT_TAG_RE = re.compile(
    r'<[^>]*\bdata-zone-name=["\']productSnippet["\'][^>]*>', re.I,
)
ZONE_DATA_ATTR_RE = re.compile(r'\bdata-zone-data=(["\'])(.*?)\1', re.I | re.S)

# Specific challenge markers only. The generic word "captcha" also appears in
# the JS boilerplate of healthy pages and must not be treated as a block.
PROTECTION_MARKERS = ("masscaptcha", "smartcaptcha", "robot verification", "доступ ограничен", "проверка, что вы не робот")


def parse_snippets(html_text: str) -> list[dict]:
    """Extract productSnippet JSON regardless of HTML attribute ordering."""
    out = []
    for tag_match in PRODUCT_TAG_RE.finditer(html_text):
        data_match = ZONE_DATA_ATTR_RE.search(tag_match.group(0))
        if not data_match:
            continue
        try:
            out.append(json.loads(html_lib.unescape(data_match.group(2))))
        except (ValueError, TypeError):
            continue
    return out


def _json_string(value: str) -> str:
    """Decode JSON escapes captured from embedded page state."""
    try:
        return json.loads(f'"{value}"')
    except (ValueError, TypeError):
        return html_lib.unescape(value or "")


class YandexMarketAdapter(MarketplaceAdapter):
    code = "yandex_market"

    def __init__(self):
        self.last_page = 0
        self.metrics = {"pages": 0, "products": 0, "seller_refs": 0, "duplicates": 0}
        self.client = HttpClient(
            cookies=HttpClient.marketplace_cookies("yandex_market", settings.YANDEX_MARKET_COOKIES),
            referer=BASE,
            headers=HEADERS,
        )

    # Categories are admin-managed: external_id = search text or hid value.
    def get_categories(self):
        return []

    def iter_sellers(self, category, city=None, max_sellers=0, start_page=1, start_cursor=None):
        page = max(1, int(start_cursor or start_page or 1))
        category_ref = str(category.external_id or "").strip()
        # /search is frequently redirected to SmartCaptcha on datacenter IPs.
        # This regular catalog route renders the same productSnippet payload.
        if category_ref.isdigit():
            discovery_url = f"{BASE}/catalog--x/{category_ref}/list"
            params = {"hid": category_ref, "page": str(page)}
        else:
            discovery_url = f"{BASE}/catalog--x/0/list"
            params = {"text": category_ref, "page": str(page)}
        found = set()
        seen_items = set()
        total_found = 0
        while True:
            self.last_page = page
            params["page"] = str(page)
            try:
                resp = self.client.get(discovery_url, params=params)
                if resp.status_code != 200:
                    raise CollectionParseError(f"Yandex Market unexpected search HTTP {resp.status_code}")
                snippets = parse_snippets(resp.text)
            except HttpError as exc:
                if exc.blocked:
                    raise CollectionBlocked(str(exc)) from exc
                raise
            except Exception as exc:
                log.warning("YM search failed: %s", exc)
                raise
            if not snippets and any(marker in resp.text.lower() for marker in PROTECTION_MARKERS):
                raise CollectionBlocked("Yandex Market returned a protection page")
            if not snippets:
                break
            self.metrics["pages"] += 1
            self.metrics["products"] += len(snippets)
            page_new = 0
            page_sellers = {}
            for sn in snippets:
                item_key = str(sn.get("marketSku") or sn.get("sku") or sn.get("title") or "")
                if item_key and item_key in seen_items:
                    self.metrics["duplicates"] += 1
                    continue
                if item_key:
                    seen_items.add(item_key)
                    page_new += 1
                # businessId opens the current seller profile route.  The old
                # bare /seller/<supplierId> route now returns 404.
                business_id = sn.get("businessId")
                sid = business_id or sn.get("supplierId") or sn.get("shopId")
                if not sid:
                    continue
                sid = str(sid)
                if sid not in found and sid not in page_sellers:
                    rating = ""
                    if isinstance(sn.get("rating"), dict):
                        rating = str(sn["rating"].get("rating", "") or "")
                    page_sellers[sid] = SellerData(
                        marketplace=self.code,
                        external_seller_id=sid,
                        seller_url=(
                            f"{BASE}/business--x/{sid}"
                            if business_id else f"{BASE}/seller/{sid}/"
                        ),
                        name=str(sn.get("supplierName") or sn.get("shopName") or ""),
                        rating=rating,
                        category_refs=[category.external_id] if category else [],
                        raw={
                            "marketSku": sn.get("marketSku"),
                            "title": sn.get("title"),
                            "businessId": business_id,
                            "supplierId": sn.get("supplierId"),
                            "shopId": sn.get("shopId"),
                        },
                    )
                elif sid in page_sellers:
                    self.metrics["duplicates"] += 1
            if page > 1 and page_new == 0:
                break
            checkpoint = {"page": page + 1, "cursor": page + 1, "next_cursor": page + 1, "finished": False}
            for sid, seller in page_sellers.items():
                if max_sellers and total_found >= max_sellers:
                    break
                found.add(sid)
                total_found += 1
                self.metrics["seller_refs"] += 1
                yield seller, checkpoint
            if max_sellers and total_found >= max_sellers:
                break
            yield None, checkpoint
            page += 1
            if page > 10000:
                log.warning("Yandex Market pagination safety stop at page %s", page)
                break
        yield None, {"page": page, "cursor": None, "next_cursor": None, "finished": True}

    def fetch_seller(self, ref: str) -> SellerData | None:
        ref = str(ref or "").strip()
        url = ref if ref.startswith(("http://", "https://")) else f"{BASE}/business--x/{ref}"
        try:
            resp = self.client.get(url)
        except HttpError as exc:
            if exc.blocked:
                raise CollectionBlocked(str(exc)) from exc
            log.warning("YM seller %s failed: %s", ref, exc)
            return None
        if resp.status_code != 200:
            log.info("YM seller page %s HTTP %s", ref, resp.status_code)
            return None
        t = resp.text
        if any(marker in t.lower() for marker in PROTECTION_MARKERS):
            raise CollectionBlocked("Yandex Market returned a seller protection page")
        response_url = str(getattr(resp, "url", "") or url)
        id_m = re.search(r'/business--[^/"?]+/(\d+)', response_url)
        if not id_m:
            id_m = re.search(r'/(?:business|seller)/(\d+)', response_url)
        external_id = id_m.group(1) if id_m else re.sub(r"\D", "", ref)
        name_m = re.search(r'"(?:sellerName|shopName|businessName)"\s*:\s*"([^"]{2,200})"', t)
        inn_m = re.search(r'"(?:inn|INN)"\s*:\s*"?(\d{10,12})"?', t)
        ogrn_m = re.search(r'"ogrn"\s*:\s*"?(\d{13,15})"?', t)
        addr_m = re.search(r'"(?:legalAddress|address)"\s*:\s*"([^"]{5,300})"', t)
        rating_m = re.search(r'"shopRating"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?', t)
        canonical_m = re.search(r'"(?:navigationUrl|url)"\s*:\s*"(/business--[^"?]+/\d+)"', t)
        return SellerData(
            marketplace=self.code,
            external_seller_id=external_id or ref,
            seller_url=f"{BASE}{canonical_m.group(1)}" if canonical_m else url,
            name=_json_string(name_m.group(1)) if name_m else "",
            inn=inn_m.group(1) if inn_m else "",
            ogrn=ogrn_m.group(1) if ogrn_m else "",
            legal_address=_json_string(addr_m.group(1)) if addr_m else "",
            rating=rating_m.group(1) if rating_m else "",
            raw={"page_bytes": len(t), "profile": "business"},
        )
