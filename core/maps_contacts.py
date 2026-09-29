"""Optional public contact lookup in Yandex Maps and 2GIS.

This is deliberately separate from marketplace collection.  A contact is
accepted only when either the normalized organization name or its address
matches the seller data obtained from the marketplace/DaData.
"""
import fcntl
import json
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import quote

from django.conf import settings

from core.browser_session import _connect
from core.httpclient import HttpClient


class MapsBlockedError(RuntimeError):
    pass


@dataclass
class MapContact:
    phone: str
    source: str
    quality: str
    url: str


LEGAL_FORMS = re.compile(r"\b(?:ооо|оао|пао|ао|ип|зао|нко)\b", re.I)
NON_WORD = re.compile(r"[^0-9a-zа-яё]+", re.I)
CITY_SLUGS = {
    "москва": "moscow", "санкт петербург": "spb", "уфа": "ufa",
    "екатеринбург": "ekaterinburg", "челябинск": "chelyabinsk",
    "нижний новгород": "n_novgorod", "ростов на дону": "rostov_na_donu",
    "новосибирск": "novosibirsk", "казань": "kazan", "омск": "omsk",
    "самара": "samara", "пермь": "perm", "красноярск": "krasnoyarsk",
    "воронеж": "voronezh", "волгоград": "volgograd", "краснодар": "krasnodar",
    "саратов": "saratov", "тюмень": "tyumen", "барнаул": "barnaul",
    "иркутск": "irkutsk", "хабаровск": "khabarovsk", "владивосток": "vladivostok",
    "ставрополь": "stavropol", "калининград": "kaliningrad", "астрахань": "astrakhan",
    "курск": "kursk", "киров": "kirov", "белгород": "belgorod",
}
TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def normalize_text(value):
    value = LEGAL_FORMS.sub(" ", (value or "").lower().replace("ё", "е"))
    return " ".join(NON_WORD.sub(" ", value).split())


def _name_matches(expected, actual):
    left, right = normalize_text(expected), normalize_text(actual)
    if len(left) < 4 or len(right) < 4:
        return False
    return left == right or (len(left) >= 7 and left in right) or (len(right) >= 7 and right in left)


def _address_key(value):
    text = normalize_text(value)
    # Country, region and postal code add noise; street + house are the useful
    # public organization-card identity.
    stop = {"россия", "российская", "федерация", "область", "край", "район", "город", "г"}
    return [token for token in text.split() if token not in stop and not re.fullmatch(r"\d{6}", token)]


def _address_matches(expected, actual):
    left, right = _address_key(expected), _address_key(actual)
    if len(left) < 2 or len(right) < 2:
        return False
    # Require a house number plus at least one textual address token.
    numbers = {token for token in left if any(ch.isdigit() for ch in token)}
    words = {token for token in left if len(token) >= 4 and token.isalpha()}
    right_set = set(right)
    return bool(numbers & right_set) and bool(words & right_set)


def match_quality(expected_name, expected_address, actual_name, actual_address):
    by_name = _name_matches(expected_name, actual_name)
    by_address = _address_matches(expected_address, actual_address)
    if by_name and by_address:
        return "name+address"
    if by_name:
        return "name"
    if by_address:
        return "address"
    return ""


def city_slug(city):
    normalized = normalize_text(city)
    return CITY_SLUGS.get(normalized) or normalized.translate(TRANSLIT).replace(" ", "_") or "moscow"


def _html_text(value):
    value = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", value, flags=re.I | re.S)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _walk_candidates(value):
    if isinstance(value, dict):
        phones = value.get("phones") or value.get("contactGroups") or []
        name = value.get("name") or value.get("title") or value.get("fullName") or ""
        address = value.get("fullAddress") or value.get("address") or value.get("formattedAddress") or ""
        if name and phones:
            yield value, name, address, phones
        for child in value.values():
            yield from _walk_candidates(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_candidates(child)


def _phone_values(value):
    if isinstance(value, str):
        # Keep an extension out of the stored phone. Some cards expose values
        # like ``+74951234567,123`` alongside the human-readable number.
        match = re.search(r"(?:\+?7|8)(?:[\s()\-]*\d){10}", value)
        if match:
            yield match.group(0)
    elif isinstance(value, dict):
        for key in ("value", "number", "text"):
            if value.get(key):
                yield from _phone_values(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _phone_values(item)


class YandexMapsLookup:
    def __init__(self):
        self.http = HttpClient(referer="https://yandex.ru/maps/")

    def lookup(self, name, address, city=""):
        query = " ".join(part for part in (name, address) if part).strip()
        if not query:
            return []
        url = "https://yandex.ru/maps/?text=" + quote(query)
        response = self.http.get(url)
        text = response.text
        if "showcaptcha" in response.url.lower() or "smartcaptcha" in text.lower():
            raise MapsBlockedError("Яндекс Карты запросили captcha")
        match = re.search(
            r'<script[^>]+class=["\']state-view["\'][^>]*>(.*?)</script>', text, re.S | re.I,
        )
        if not match:
            raise MapsBlockedError("Яндекс Карты не вернули обычную страницу поиска")
        try:
            state = json.loads(unescape(match.group(1)))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Не удалось разобрать состояние Яндекс Карт") from exc
        candidates = []
        for raw, candidate_name, candidate_address, phones in _walk_candidates(state):
            quality = match_quality(name, address, candidate_name, candidate_address)
            if not quality:
                continue
            candidates.append(({"name": 1, "address": 2, "name+address": 3}[quality], raw, phones, quality))
        if not candidates:
            time.sleep(max(0, settings.MAPS_RATE_DELAY))
            return []
        # Search order is relevance-ranked. Use one best card, not every branch
        # sharing a generic brand name across Russia.
        _score, raw, phones, quality = max(candidates, key=lambda item: item[0])
        found = []
        seen = set()
        candidate_url = raw.get("url") or raw.get("uri") or url
        for phone in _phone_values(phones):
            if phone not in seen:
                seen.add(phone)
                found.append(MapContact(phone, "yandex_maps", quality, str(candidate_url)))
        time.sleep(max(0, settings.MAPS_RATE_DELAY))
        return found


class TwoGisBrowserLookup:
    """Use HTTP requests authenticated by one persistent normal browser session."""

    def __enter__(self):
        lock_path = Path(settings.BASE_DIR) / "runtime" / "maps_2gis.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = lock_path.open("w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.playwright, self.browser = _connect()
        self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context(locale="ru-RU")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.playwright.stop()
        self.lock.close()

    def lookup(self, name, address, city=""):
        query = " ".join(part for part in (name, address) if part).strip()
        if not query:
            return []
        search_url = f"https://2gis.ru/{city_slug(city)}/search/" + quote(query)
        response = self.context.request.get(search_url, timeout=settings.MAPS_BROWSER_TIMEOUT * 1000)
        html = response.text()
        if "captcha.2gis" in response.url or "подозрительную активность" in html.lower():
            raise MapsBlockedError("2ГИС запросил проверку в серверном Chromium")
        urls = []
        for match in re.finditer(r'href=["\']([^"\']*/firm/\d+[^"\']*)["\']', html, re.I):
            href = unescape(match.group(1))
            # The SSR search card is adjacent to its link. Pre-filter here so
            # only matching cards need a detail HTTP request.
            card_text = _html_text(html[max(0, match.start() - 1500):match.end() + 2500])
            if match_quality(name, address, card_text, card_text):
                clean_url = (href if href.startswith("http") else "https://2gis.ru" + href).split("?", 1)[0]
                if clean_url not in urls:
                    urls.append(clean_url)

        for firm_url in urls[:2]:
            detail_response = self.context.request.get(
                firm_url, timeout=settings.MAPS_BROWSER_TIMEOUT * 1000,
            )
            detail_html = detail_response.text()
            if "captcha.2gis" in detail_response.url or "подозрительную активность" in detail_html.lower():
                raise MapsBlockedError("2ГИС запросил проверку в серверном Chromium")
            title_match = re.search(r"<title[^>]*>(.*?)</title>", detail_html, re.I | re.S)
            title = _html_text(title_match.group(1)) if title_match else ""
            body = _html_text(detail_html)
            quality = match_quality(name, address, title, body)
            if not quality:
                continue
            phones = [unescape(value) for value in re.findall(r'href=["\']tel:([^"\']+)', detail_html, re.I)]
            if not phones:
                phones = re.findall(r"(?:\+7|8)[\s\-(]*\d{3}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}", body)
            if phones:
                time.sleep(max(0, settings.MAPS_RATE_DELAY))
                return [MapContact(phone, "2gis", quality, firm_url) for phone in dict.fromkeys(phones)]
        time.sleep(max(0, settings.MAPS_RATE_DELAY))
        return []
