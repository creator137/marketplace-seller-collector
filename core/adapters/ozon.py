"""Ozon adapter: internal JSON API (entrypoint-api.bx) + widgetStates parsing.

Live-verified path (2026-09, requires OZON_COOKIES):
  search/category page -> tileGrid* items (product links)
  -> product page -> webCurrentSeller widget (sellerId, name, rating)
  -> seller page -> sellerTransparency widget (full title)

Ozon no longer publishes INN/legal address in seller widgets (checked live);
those fields stay empty unless another source provides them.
"""
import json
import logging
import re
import time
from urllib.parse import quote

from django.conf import settings

from core.adapters.base import CollectionBlocked, CollectionParseError, CollectionTemporaryError, MarketplaceAdapter, SellerData
from core.httpclient import HttpClient, HttpError, ozon_request_id_headers

log = logging.getLogger("core.adapters.ozon")

BASE = "https://www.ozon.ru"
API = f"{BASE}/api/entrypoint-api.bx/page/json/v2"
COMPOSER_API = "https://api.ozon.ru/composer-api.bx/page/json/v2"

API_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "x-o3-app-name": "mweb_client",
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
}
COMPOSER_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "x-o3-app-name": "ozonapp_android",
    "user-agent": "okhttp/4.12.0",
}

SELLER_URL_RE = re.compile(r"/seller/(\d+)")
SELLER_ID_RE = re.compile(r"seller[\"/]+(\d+)")
DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")


def widget_state(data: dict, prefix: str) -> dict | None:
    """Find and parse a widgetStates entry whose key starts with prefix."""
    for key, raw in (data.get("widgetStates") or {}).items():
        if key.startswith(prefix):
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                continue
    return None


def _tile_products(data: dict) -> list[str]:
    """Product page paths from tileGrid* widgets of a search/category page."""
    links = []
    for key, raw in (data.get("widgetStates") or {}).items():
        if not key.startswith("tileGrid"):
            continue
        try:
            items = json.loads(raw).get("items") or []
        except (ValueError, TypeError):
            continue
        for item in items:
            link = (item.get("action") or {}).get("link") or ""
            if link.startswith("/product/"):
                links.append(link.split("?")[0].rstrip("/"))
    return links


def _seller_from_product_state(state: dict) -> tuple[str, str, str]:
    """(seller_id, name, rating) from a webCurrentSeller widget state."""
    cell = state.get("sellerCell") or {}
    name = ((cell.get("centerBlock") or {}).get("title") or {}).get("text") or ""
    rating = ((state.get("rating") or {}).get("title") or {}).get("text") or ""
    # seller id: from the cell action link (ozon://seller/123 or /seller/123)
    dump = json.dumps(state, ensure_ascii=False)
    m = SELLER_ID_RE.search(dump)
    sid = m.group(1) if m else ""
    return sid, name, rating


class OzonAdapter(MarketplaceAdapter):
    code = "ozon"

    def __init__(self):
        self.last_page = 0
        self.metrics = {"pages": 0, "products": 0, "seller_refs": 0, "duplicates": 0}
        self.client = HttpClient(
            cookies=HttpClient.marketplace_cookies("ozon", settings.OZON_COOKIES),
            referer=BASE,
            headers=API_HEADERS,
        )

    def _api_get(self, page_path: str) -> dict | None:
        errors = []
        for endpoint in (API, COMPOSER_API):
            url = f"{endpoint}?url={quote(page_path, safe='')}"
            try:
                endpoint_headers = dict(COMPOSER_HEADERS if endpoint == COMPOSER_API else API_HEADERS)
                if endpoint == API:
                    endpoint_headers.update(ozon_request_id_headers())
                resp = self.client.get(url, headers=endpoint_headers)
                data = resp.json()
                if isinstance(data, dict):
                    return data
                errors.append(f"{endpoint}: non-object JSON")
            except HttpError as exc:
                errors.append(str(exc))
                if exc.blocked:
                    continue
            except ValueError as exc:
                body = str(getattr(resp, "text", ""))
                marker = " protection page" if re.search(r"captcha|cloudflare|robot|проверка", body, re.I) else " non-JSON"
                errors.append(f"{endpoint}:{marker}")
        if errors and any("HTTP 403" in error or "HTTP 498" in error or "protection page" in error for error in errors):
            raise CollectionBlocked("Ozon endpoints blocked: " + "; ".join(errors))
        if errors:
            if any("non-JSON" in error for error in errors):
                raise CollectionParseError("Ozon endpoints returned invalid payloads: " + "; ".join(errors)[-1000:])
            raise CollectionTemporaryError("Ozon endpoints unavailable: " + "; ".join(errors)[-1000:])
        return None

    def _api_get_stable(self, page_path: str, attempts: int = 3) -> dict | None:
        """Entrypoint-api intermittently returns an empty payload; retry until
        widgetStates is present. Small delay between attempts."""
        for attempt in range(attempts):
            data = self._api_get(page_path)
            if data and data.get("widgetStates"):
                return data
            if attempt < attempts - 1:
                time.sleep(2)
        return data

    # -- categories: Ozon tree is unstable; admin-managed, external_id = path or query.
    def get_categories(self):
        return []

    def iter_sellers(self, category, city=None, max_sellers=0, start_page=1, start_cursor=None):
        """Stream one completed Ozon page at a time and emit its next cursor."""
        path = category.external_id
        if path and not path.startswith("/"):
            path = f"/search/?text={path}&from_global=true"
        page_path = start_cursor or path
        page_number = max(1, int(start_page or 1))
        seen_paths = set()
        seen_sellers = set()
        total_found = 0

        while page_path:
            if page_path in seen_paths:
                raise CollectionParseError(f"Ozon repeated pagination cursor: {page_path}")
            seen_paths.add(page_path)
            self.last_page = page_number
            self.metrics["pages"] += 1
            data = self._api_get_stable(page_path)
            if not data or not data.get("widgetStates"):
                raise CollectionBlocked("Ozon returned an empty/protection response")
            page_found: dict[str, SellerData] = {}

            state = widget_state(data, "sellerList")
            for item in (state or {}).get("items") or []:
                m = SELLER_URL_RE.search(item.get("deeplink") or "")
                if not m or m.group(1) in seen_sellers:
                    continue
                page_found[m.group(1)] = SellerData(
                    marketplace=self.code,
                    external_seller_id=m.group(1),
                    seller_url=f"{BASE}/seller/{m.group(1)}/",
                    name=item.get("title") or "",
                    rating=str(item.get("rating", "") or ""),
                    category_refs=[category.external_id] if category else [],
                    raw={"deeplink": item.get("deeplink")},
                )

            product_paths = _tile_products(data)
            self.metrics["products"] += len(product_paths)
            for product_path in product_paths:
                pdata = self._api_get_stable(product_path)
                if not pdata:
                    continue
                pstate = widget_state(pdata, "webCurrentSeller")
                if not pstate:
                    continue
                sid, name, rating = _seller_from_product_state(pstate)
                if not sid or sid in seen_sellers or sid in page_found:
                    continue
                page_found[sid] = SellerData(
                    marketplace=self.code,
                    external_seller_id=sid,
                    seller_url=f"{BASE}/seller/{sid}/",
                    name=name,
                    rating=rating,
                    category_refs=[category.external_id] if category else [],
                    raw={"product": product_path},
                )
                if settings.HTTP_RATE_DELAY:
                    time.sleep(settings.HTTP_RATE_DELAY)

            next_page = data.get("nextPage") or data.get("next_page")
            checkpoint = {
                "page": page_number + 1,
                "cursor": next_page or None,
                "next_cursor": next_page or None,
                "finished": not bool(next_page),
            }
            for sid, seller in page_found.items():
                if max_sellers and total_found >= max_sellers:
                    break
                seen_sellers.add(sid)
                total_found += 1
                self.metrics["seller_refs"] += 1
                yield seller, checkpoint
            yield None, checkpoint
            if max_sellers and total_found >= max_sellers:
                break
            if not next_page:
                break
            page_path = next_page
            page_number += 1

    def fetch_seller(self, ref: str) -> SellerData | None:
        """Seller page -> sellerTransparency widget (full title) / legacy profile."""
        data = self._api_get_stable(f"/seller/{ref}/")
        if not data:
            return None
        name = ""
        # current widget
        st = widget_state(data, "sellerTransparency")
        if st:
            name = ((st.get("title") or {}).get("text") or "").strip()
        # legacy widget (older layouts)
        info = {}
        layout = data.get("layout") or []
        profile = next((c for c in layout if c.get("component") == "sellerTransparencyProfile"), None)
        if profile:
            for ph in profile.get("placeholders") or []:
                if ph.get("name") == "onAboutShopInfo":
                    info = ph.get("fields") or {}
                    name = name or (info.get("title") or "")
        registered = None
        m = DATE_RE.search(str(info.get("registrationDate") or info.get("registeredAt") or ""))
        if m:
            d, mo, y = m.group(0).split(".")
            registered = f"{y}-{mo}-{d}"
        return SellerData(
            marketplace=self.code,
            external_seller_id=str(ref),
            seller_url=f"{BASE}/seller/{ref}/",
            name=name,
            inn=str(info.get("inn") or ""),
            ogrn=str(info.get("ogrn") or ""),
            legal_address=info.get("legalAddress") or info.get("address") or "",
            registered_at=registered,
            raw=info,
        )
