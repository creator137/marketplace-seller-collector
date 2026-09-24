"""Simple string-based city matching against a legal address. No GIS."""
import re


def normalize_city_name(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\bг\.\s*", "", s)
    s = re.sub(r"\bгород\s+", "", s)
    return s.strip()


# Extended names to try for known abbreviations in addresses.
_ALIASES = {
    "екб": "екатеринбург",
    "чек": "челябинск",
    "уфа": "уфа",
    "челябинск": "челябинск",
    "екатеринбург": "екатеринбург",
}


def find_city_in_address(address: str, city_norm_names: list[str]) -> str | None:
    """Return the normalized name of the first city found in the address, else None."""
    if not address:
        return None
    norm_addr = normalize_city_name(address)
    norm_addr = norm_addr.replace("ё", "е")
    for city in city_norm_names:
        if city and city in norm_addr:
            return city
    # try aliases (e.g. "екб")
    for alias, full in _ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", norm_addr) and full in city_norm_names:
            return full
    return None
