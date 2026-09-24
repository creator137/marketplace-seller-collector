"""DaData enrichment: the only allowed external enrichment source (by INN)."""
import logging

from django.conf import settings

from core.httpclient import HttpError, HttpClient

log = logging.getLogger("core.dadata")

URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


def fetch_party(inn: str) -> dict | None:
    """Return the first DaData party suggestion for an INN, or None (normal result)."""
    token = settings.DADATA_TOKEN
    if not token or not inn:
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
        resp = client.request("POST", URL, json_payload={"query": inn, "count": 1})
    except HttpError as exc:
        log.warning("DaData request failed for INN %s: %s", inn, exc)
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
    website = ""
    doc = data.get("management") or {}
    return {
        "name": party.get("value") or party.get("unrestricted_value") or "",
        "legal_address": ((data.get("address") or {}).get("value") or ""),
        "inn": str(data.get("inn") or ""),
        "ogrn": str(data.get("ogrn") or ""),
        "mobile_phones": phones,
        "emails": emails,
        "website": website,
        "management_name": doc.get("name") or "",
        "raw": party,
    }
