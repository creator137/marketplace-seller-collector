"""Marketplace adapter interface and common DTO."""
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class SellerData:
    """Normalized seller payload from any marketplace. Missing data stays None/empty."""

    marketplace: str
    external_seller_id: str
    seller_url: str = ""
    name: str = ""
    inn: str = ""
    ogrn: str = ""
    mobile_phones: list[str] = field(default_factory=list)
    city_phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    website: str = ""
    rating: str = ""
    registered_at: Optional[str] = None  # ISO date, if marketplace provides it
    legal_address: str = ""
    category_refs: list[str] = field(default_factory=list)  # external_id values of Category
    raw: dict = field(default_factory=dict)


class CollectionError(Exception):
    """A classified adapter failure; never silently becomes an empty result."""

    status = "temporary_error"


class CollectionBlocked(CollectionError):
    status = "blocked"


class CollectionRateLimited(CollectionError):
    status = "rate_limited"


class CollectionTemporaryError(CollectionError):
    status = "temporary_error"


class CollectionParseError(CollectionError):
    status = "parse_error"


class MarketplaceAdapter:
    """Compact common interface for marketplace collectors."""

    code: str = ""

    def get_categories(self) -> Iterable[dict]:
        """Yield {external_id, title, parent_external_id?} dicts (may be admin-seeded)."""
        raise NotImplementedError

    def discover_sellers(self, category, city=None, limit=0, max_sellers=None) -> Iterable[SellerData]:
        """Find sellers for a category (optionally city-scoped)."""
        raise NotImplementedError

    def fetch_seller(self, ref: str) -> Optional[SellerData]:
        """Fetch detailed seller info by external id. Returns None if unavailable."""
        raise NotImplementedError
