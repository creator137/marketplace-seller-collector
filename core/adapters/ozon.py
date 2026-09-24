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

from core.adapters.base import MarketplaceAdapter, SellerData
from core.httpclient import HttpClient, ozon_request_id_headers

log = logging.getLogger("core.adapters.ozon")

BASE = "https://www.ozon.ru"
API = f"{BASE}/api/entrypoint-api.bx/page/json/v2"

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
        self.client = HttpClient(
            cookies=HttpClient.parse_cookies(settings.OZON_COOKIES),
            referer=BASE,
            headers=API_HEADERS,
        )

    def _api_get(self, page_path: str) -> dict | None:
        url = f"{API}?url={quote(page_path, safe='')}"
        try:
            resp = self.client.get(url, headers=ozon_request_id_headers())
            data = resp.json()
            if isinstance(data, dict):
                return data
        except Exception as exc:
            log.warning("Ozon API %s failed: %s", page_path[:80], exc)
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

    def discover_sellers(self, category, city=None, limit=50):
        """Category/search page -> products -> seller per product (deduped)."""
        path = category.external_id
        if path and not path.startswith("/"):
            path = f"/search/?text={path}&from_global=true"
        found: dict[str, SellerData] = {}

        for page_path in self._category_pages(path, max_pages=2):
            data = self._api_get_stable(page_path)
            if not data:
                break

            # Fallback path A: sellerList widget on dedicated sellers pages
            state = widget_state(data, "sellerList")
            for item in (state or {}).get("items") or []:
                m = SELLER_URL_RE.search(item.get("deeplink") or "")
                if not m or m.group(1) in found:
                    continue
                found[m.group(1)] = SellerData(
                    marketplace=self.code,
                    external_seller_id=m.group(1),
                    seller_url=f"{BASE}/seller/{m.group(1)}/",
                    name=item.get("title") or "",
                    rating=str(item.get("rating", "") or ""),
                    category_refs=[category.external_id] if category else [],
                    raw={"deeplink": item.get("deeplink")},
                )
                if len(found) >= limit:
                    return list(found.values())

            # Main path: products -> seller widget on product page
            for product_path in _tile_products(data):
                pdata = self._api_get_stable(product_path)
                if not pdata:
                    continue
                pstate = widget_state(pdata, "webCurrentSeller")
                if not pstate:
                    continue
                sid, name, rating = _seller_from_product_state(pstate)
                if not sid or sid in found:
                    continue
                found[sid] = SellerData(
                    marketplace=self.code,
                    external_seller_id=sid,
                    seller_url=f"{BASE}/seller/{sid}/",
                    name=name,
                    rating=rating,
                    category_refs=[category.external_id] if category else [],
                    raw={"product": product_path},
                )
                if len(found) >= limit:
                    return list(found.values())
                time.sleep(settings.HTTP_RATE_DELAY)

            if not data.get("nextPage"):
                break
        return list(found.values())

    def _category_pages(self, path, max_pages=2):
        yield path
        for i in range(2, max_pages + 1):
            sep = "&" if "?" in path else "?"
            yield f"{path}{sep}page={i}"

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
