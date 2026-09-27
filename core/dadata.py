"""DaData enrichment: the only allowed external enrichment source (by INN)."""
import logging
import re

from django.conf import settings

from core.httpclient import HttpError, HttpClient

log = logging.getLogger("core.dadata")

URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


def fetch_party(query: str) -> dict | None:
    """Return the first DaData party suggestion for INN/OGRN/OGRNIP, or None."""
    token = settings.DADATA_TOKEN
    query = (query or "").strip()
    if not token or not query:
        return None
    headers = {
        "Authorization": f"Token {token}",
        "accept": "application/json",
        "content-type": "application/json",
    }
    if settings.DADATA_SECRET:
        headers["X-Secret"] = settings.DADATA_SECRET
    client = HttpClient(headers=headers)
    try:
        resp = client.request("POST", URL, json_payload={"query": query, "count": 1})
    except HttpError as exc:
        log.warning("DaData request failed for %s: %s", query, exc)
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    suggestions = data.get("suggestions") or []
    return suggestions[0] if suggestions else None


def party_to_fields(party: dict) -> dict:
    """Extract only fields our model supports; nothing invented."""
    if not party:
        return {}
    data = party.get("data") or {}
    phones = [p.get("value", "") for p in (data.get("phones") or []) if p.get("value")]
    emails = [e.get("value", "") for e in (data.get("emails") or []) if e.get("value")]
    # The party endpoint does not document a website field. Do not invent one
    # from arbitrary payload keys; website can still come from the marketplace.
    website = ""
    doc = data.get("management") or {}
    address = data.get("address") or {}
    addr_data = address.get("data") or {}
    city = (
        addr_data.get("city")
        or addr_data.get("settlement")
        or addr_data.get("city_with_type")
        or addr_data.get("settlement_with_type")
        or ""
    )
    if isinstance(city, str) and city.lower().startswith(("г ", "г.", "город ")):
        city = re.sub(r"^(?:г\.?\s*|город\s+)", "", city, flags=re.I).strip()
    return {
        "name": party.get("value") or party.get("unrestricted_value") or "",
        "legal_address": (address.get("value") or ""),
        "city": city.strip(),
        "inn": str(data.get("inn") or ""),
        "ogrn": str(data.get("ogrn") or ""),
        "mobile_phones": phones,
        "emails": emails,
        "website": website,
        "management_name": doc.get("name") or "",
        "raw": party,
    }
