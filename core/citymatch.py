"""Simple string-based city matching against a legal address. No GIS."""
import re

_REGION_WORDS = {
    "республика", "респ", "край", "область", "обл", "район", "р-н",
    "округ", "ао", "автономный", "федеральный",
}

_CITY_MARKER_RE = re.compile(
    r"(?:^|[\s,;])(?:г\.?\s*|город\s+)([А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]*(?:\s+[А-ЯЁа-яёA-Za-z\-]+)?)",
    re.U,
)
_SETTLEMENT_MARKER_RE = re.compile(
    r"(?:^|[\s,;])(?:пгт\.?\s*|пос\.?\s*|п\.?\s*|с\.?\s*|село\s+|деревня\s+)([А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]*(?:\s+[А-ЯЁа-яёA-Za-z\-]+)?)",
    re.U,
)
_LEADING_NAME_RE = re.compile(
    r"^([А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]*)\s*,",
    re.U,
)


def normalize_city_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\bг\.\s*", "", s)
    s = re.sub(r"\bгород\s+", "", s)
    return s.strip()


def canonical_city_name(name: str) -> str:
    """Human-readable city title from a raw extracted token."""
    s = re.sub(r"\s+", " ", (name or "").strip())
    if not s:
        return ""
    s = s.replace("ё", "е").replace("Ё", "Е")

    def _cap_token(token: str) -> str:
        return "-".join(part[:1].upper() + part[1:].lower() for part in token.split("-") if part)

    return " ".join(_cap_token(word) for word in s.split())



# Extended names to try for known abbreviations in addresses.
_ALIASES = {
    "екб": "екатеринбург",
    "чек": "челябинск",
    "уфа": "уфа",
    "челябинск": "челябинск",
    "екатеринбург": "екатеринбург",
}


def find_city_in_address(address: str, city_norm_names: list[str]) -> str | None:
    """Return the normalized name of the first known city found in the address."""
    if not address:
        return None
    norm_addr = normalize_city_name(address)
    # Longer names first so "новосибирск" wins over shorter accidental hits.
    for city in sorted((c for c in city_norm_names if c), key=len, reverse=True):
        if city in norm_addr:
            return city
    for alias, full in _ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", norm_addr) and full in city_norm_names:
            return full
    return None


def extract_city_from_address(address: str) -> str | None:
    """Pull a city/settlement name from a free-form legal address.

    Works even when the city is not yet in the City dictionary.
    """
    if not address:
        return None
    text = address.strip()
    for pattern in (_CITY_MARKER_RE, _SETTLEMENT_MARKER_RE):
        match = pattern.search(text)
        if match:
            return canonical_city_name(match.group(1))
    match = _LEADING_NAME_RE.match(text)
    if match:
        token = match.group(1)
        if normalize_city_name(token) not in _REGION_WORDS:
            return canonical_city_name(token)
    return None
