"""Wildberries adapter: public JSON endpoints (search.wb.ru, card.wb.ru, sellers.wb.ru).

Live status 2026-09: search/card endpoints return 403/498 without valid session
cookies (x_wbaas_token etc.). Provide WB_COOKIES env for live collection.
The static category menu JSON works without cookies.
Endpoint structure learned from public reference parsers (no licensed code).
"""
import logging
import re

from django.conf import settings

from core.adapters.base import CollectionBlocked, CollectionParseError, MarketplaceAdapter, SellerData
from core.httpclient import HttpClient

log = logging.getLogger("core.adapters.wb")

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v5/search"
CARD_URL = "https://card.wb.ru/cards/v2/detail"
SELLER_CARD_URL = "https://sellers.wb.ru/sellers/v1/supplier/{id}/card-info"
SELLER_STATIC_URL = "https://static-basket-01.wbbasket.ru/vol0/data/supplier-by-id/{id}.json"
MENU_URL = "https://static-basket-01.wbbasket.ru/vol0/data/main-menu-ru-ru-v3.json"

BASE_HEADERS = {
    "accept": "*/*",
    "origin": "https://www.wildberries.ru",
    "referer": "https://www.wildberries.ru/",
}

DEFAULT_DEST = "-1257786"


class WildberriesAdapter(MarketplaceAdapter):
    code = "wildberries"

    def __init__(self):
        self.last_page = 0
        self.client = HttpClient(
            cookies=HttpClient.parse_cookies(settings.WB_COOKIES),
            headers=BASE_HEADERS,
        )

    def _dest(self, city=None) -> str:
        if city is not None and getattr(city, "dest_code", ""):
            return str(city.dest_code)
        return DEFAULT_DEST

    def get_categories(self):
        """Top-level menu tree from static JSON (works without cookies)."""
        try:
            resp = self.client.get(MENU_URL)
            tree = resp.json()
        except Exception as exc:
            log.warning("WB menu fetch failed: %s", exc)
            return []
        out = []
        for top in tree or []:
            for child in top.get("childs") or []:
                query = child.get("query") or ""
                shard = child.get("shard") or ""
                if not query:
                    continue
                external_id = f"{shard}|{query}" if shard else query
                out.append({
                    "external_id": external_id,
                    "title": child.get("name", "")[:255],
                    "parent_external_id": top.get("name", ""),
                })
        return out

    def discover_sellers(self, category, city=None, limit=0, max_sellers=None):
        """Search category -> products -> supplierId/supplier/supplierRating."""
        limit = max_sellers if max_sellers is not None else limit
        shard, _, query = (category.external_id or "").partition("|")
        query = query or category.external_id
        params = {
            "appType": "1", "curr": "rub", "dest": self._dest(city),
            "query": query, "resultset": "catalog", "page": "1",
            "sort": "popular", "spp": "30", "suppressSpellcheck": "false",
        }
        found = {}
        seen_products = set()
        page = 1
        while True:
            params["page"] = str(page)
            self.last_page = page
            try:
                resp = self.client.get(SEARCH_URL, params=params)
                data = resp.json()
            except ValueError as exc:
                raise CollectionParseError(f"WB returned non-JSON on page {page}") from exc
            except Exception as exc:
                if isinstance(exc, (CollectionBlocked, CollectionParseError)):
                    raise
                log.warning("WB search failed on page %s: %s", page, exc)
                raise
            products = (data.get("data") or data).get("products") or []
            if not products:
                break
            page_new = 0
            for p in products:
                product_key = str(p.get("id") or p.get("nmId") or "")
                if product_key and product_key in seen_products:
                    continue
                if product_key:
                    seen_products.add(product_key)
                    page_new += 1
                sid = p.get("supplierId") or p.get("supplier")
                if sid is None:
                    continue
                sid = str(sid)
                if sid not in found:
                    found[sid] = SellerData(
                        marketplace=self.code,
                        external_seller_id=sid,
                        seller_url=f"https://www.wildberries.ru/seller/{sid}",
                        name=p.get("supplier") or p.get("supplierName") or "",
                        rating=str(p.get("supplierRating") or "") if p.get("supplierRating") else "",
                        category_refs=[category.external_id] if category else [],
                        raw={"product": p.get("id"), "brand": p.get("brand")},
                    )
                if limit and len(found) >= limit:
                    return list(found.values())
            if page > 1 and page_new == 0:
                break
            page += 1
            if page > 10000:
                log.warning("WB pagination safety stop at page %s", page)
                break
        return list(found.values())

    def fetch_seller(self, ref: str) -> SellerData | None:
        """Supplier card: name, INN/OGRN, address, site if returned."""
        for url_tpl in (SELLER_CARD_URL, SELLER_STATIC_URL):
            try:
                resp = self.client.get(url_tpl.format(id=ref))
                data = resp.json()
                if isinstance(data, dict) and data:
                    break
            except Exception as exc:
                log.debug("WB supplier info %s via %s: %s", ref, url_tpl, exc)
                data = None
        if not isinstance(data, dict) or not data:
            return None
        payload = data.get("data") or data
        addr = payload.get("address") or ""
        return SellerData(
            marketplace=self.code,
            external_seller_id=str(ref),
            seller_url=f"https://www.wildberries.ru/seller/{ref}",
            name=payload.get("supplierName") or payload.get("name") or "",
            inn=str(payload.get("INN") or payload.get("inn") or ""),
            ogrn=str(payload.get("OGRN") or payload.get("ogrn") or ""),
            legal_address=addr,
            website=(payload.get("site") or "").strip(),
            raw=payload,
        )


INN_RE = re.compile(r"\d{10,12}")
