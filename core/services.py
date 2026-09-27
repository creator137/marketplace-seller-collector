"""Seller upsert: deduplication, contact merge, city detection, DaData enrichment."""
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.adapters.base import SellerData
from core.citymatch import canonical_city_name, extract_city_from_address, find_city_in_address, normalize_city_name
from core.dadata import fetch_party, party_to_fields
from core.models import Category, City, Seller, SellerContact
from core.phoneutils import is_mobile, normalize_email, normalize_phone, normalize_url


log = logging.getLogger("core.services")


def _update_field(seller, field_name, new_value):
    """Never erase a non-empty stored value with an empty one."""
    if new_value in (None, ""):
        return
    current = getattr(seller, field_name) or ""
    if not current:
        setattr(seller, field_name, new_value)


def _merge_contact(seller, ctype, raw_value, source, source_ref=""):
    if ctype == "email":
        value = normalize_email(raw_value)
    elif ctype in ("phone", "city_phone"):
        value = normalize_phone(raw_value)
        if not value:
            return False
        if ctype == "phone" and not is_mobile(value):
            ctype = "city_phone"
    elif ctype == "site":
        value = normalize_url(raw_value)
    else:
        value = (raw_value or "").strip()
    if not value:
        return False
    contact, created = SellerContact.objects.get_or_create(
        seller=seller, type=ctype, value=value,
        defaults={"source": source, "source_ref": source_ref},
    )
    if not created and source == SellerContact.SOURCE_DADATA:
        # keep provenance of the richer source
        if contact.source != SellerContact.SOURCE_DADATA:
            contact.source = SellerContact.SOURCE_DADATA
            contact.save(update_fields=["source"])
    return created


def merge_seller_data(seller: Seller, data: SellerData) -> bool:
    """Merge SellerData into an existing Seller. Returns True if changed."""
    changed = False
    _update_field(seller, "name", (data.name or "").strip())
    _update_field(seller, "inn", str(data.inn or ""))
    _update_field(seller, "ogrn", str(data.ogrn or ""))
    _update_field(seller, "legal_address", (data.legal_address or "").strip())
    _update_field(seller, "rating", str(data.rating or ""))
    _update_field(seller, "website", normalize_url(data.website or ""))
    if data.seller_url and not seller.seller_url:
        seller.seller_url = data.seller_url
        changed = True
    if data.registered_at and not seller.registered_at:
        from django.utils.dateparse import parse_date

        seller.registered_at = parse_date(str(data.registered_at)) or None
        changed = True
    for phone in data.mobile_phones:
        changed |= _merge_contact(seller, "phone", phone, SellerContact.SOURCE_MARKETPLACE)
    for phone in data.city_phones:
        changed |= _merge_contact(seller, "city_phone", phone, SellerContact.SOURCE_MARKETPLACE)
    for email in data.emails:
        changed |= _merge_contact(seller, "email", email, SellerContact.SOURCE_MARKETPLACE)
    if data.website:
        changed |= _merge_contact(seller, "site", data.website, SellerContact.SOURCE_MARKETPLACE)
    if data.raw:
        seller.raw = {**(seller.raw or {}), **data.raw}
        changed = True
    return changed


def _resolve_city(name: str | None = None, address: str = "") -> City | None:
    """Find or create a City from an explicit name and/or free-form address."""
    preferred = canonical_city_name(name or "")
    known = list(City.objects.filter(is_active=True).values_list("normalized", "name"))
    known_norm = [n for n, _ in known]

    matched_norm = None
    display = preferred
    if preferred:
        matched_norm = normalize_city_name(preferred)
        if matched_norm in known_norm:
            display = next(title for n, title in known if n == matched_norm)
    if not matched_norm:
        matched_norm = find_city_in_address(address, known_norm)
        if matched_norm:
            display = next(title for n, title in known if n == matched_norm)
    if not matched_norm:
        extracted = extract_city_from_address(address)
        if extracted:
            matched_norm = normalize_city_name(extracted)
            display = extracted
    if not matched_norm:
        return None

    existing = City.objects.filter(normalized=matched_norm).first()
    if existing:
        return existing
    return City.objects.create(
        name=display or canonical_city_name(matched_norm),
        normalized=matched_norm,
        is_active=True,
    )


def detect_city(seller: Seller, preferred_name: str | None = None) -> None:
    """Fill seller.city from DaData hint and/or legal address; create City if needed."""
    if seller.city_id:
        return
    city = _resolve_city(preferred_name, seller.legal_address or "")
    if city:
        seller.city = city


def upsert_seller(data: SellerData) -> Seller:
    """Create or update a seller: no duplicates, no data loss."""
    seller = Seller.objects.filter(
        marketplace=data.marketplace, external_seller_id=data.external_seller_id,
    ).first()
    if seller is None and data.seller_url:
        seller = Seller.objects.filter(
            marketplace=data.marketplace,
            seller_url_normalized=normalize_url(data.seller_url).lower(),
        ).first()
        if seller is not None and not seller.external_seller_id:
            seller.external_seller_id = data.external_seller_id
    created = seller is None
    if created:
        seller = Seller(marketplace=data.marketplace, external_seller_id=data.external_seller_id)
        seller.seller_url = data.seller_url
        seller.seller_url_normalized = normalize_url(data.seller_url).lower()
        seller.save()
    elif data.seller_url and not seller.seller_url_normalized:
        seller.seller_url_normalized = normalize_url(data.seller_url).lower()
    merge_seller_data(seller, data)
    if data.category_refs:
        cats = Category.objects.filter(marketplace=data.marketplace, external_id__in=data.category_refs)
        seller.categories.add(*cats)
    detect_city(seller)
    seller.save()
    return seller


def enrich_with_dadata(seller: Seller, force=False) -> bool:
    """Enrich one seller via DaData by INN or OGRN/OGRNIP. Returns True if added."""
    if not settings.DADATA_TOKEN:
        return False
    if not force and seller.last_enriched_at:
        ttl = timedelta(days=settings.DADATA_TTL_DAYS)
        if timezone.now() - seller.last_enriched_at < ttl:
            return False
    query = (seller.inn or seller.ogrn or "").strip()
    if not query:
        return False
    party = fetch_party(query)
    seller.last_enriched_at = timezone.now()
    if not party:
        seller.save(update_fields=["last_enriched_at"])
        return False
    fields = party_to_fields(party)
    changed = False
    _update_field(seller, "name", fields["name"])
    _update_field(seller, "inn", fields["inn"])
    _update_field(seller, "ogrn", fields["ogrn"])
    _update_field(seller, "legal_address", fields["legal_address"])
    _update_field(seller, "website", normalize_url(fields.get("website") or ""))
    if fields["legal_address"] and not seller.legal_address:
        changed = True
    if fields["inn"] and not seller.inn:
        changed = True
    if fields["ogrn"] and not seller.ogrn:
        changed = True
    for phone in fields["mobile_phones"]:
        changed |= _merge_contact(seller, "phone", phone, SellerContact.SOURCE_DADATA, "dadata")
    for email in fields["emails"]:
        changed |= _merge_contact(seller, "email", email, SellerContact.SOURCE_DADATA, "dadata")
    if fields["website"]:
        changed |= _merge_contact(seller, "site", fields["website"], SellerContact.SOURCE_DADATA, "dadata")
    detect_city(seller, preferred_name=fields.get("city") or "")
    seller.save()
    return changed
