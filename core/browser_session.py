"""Control the single persistent Chromium used only for session bootstrap."""
import json
import fcntl
import socket
from urllib.parse import urlparse
from urllib.request import urlopen
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from core.models import MarketplaceSession


URLS = {
    "ozon": "https://www.ozon.ru/search/?text=наушники",
    "wildberries": "https://www.wildberries.ru/catalog/0/search.aspx?search=наушники",
    "yandex_market": "https://market.yandex.ru/search?text=наушники",
}

WB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


def _configure_wildberries_browser(context, page):
    """Align bootstrap identity with curl_cffi's newest Chrome profile."""
    session = context.new_cdp_session(page)
    session.send("Network.setUserAgentOverride", {
        "userAgent": WB_USER_AGENT,
        "acceptLanguage": "en-US,en;q=0.9",
        "platform": "Win32",
        "userAgentMetadata": {
            "brands": [
                {"brand": "Not A(Brand", "version": "99"},
                {"brand": "Chromium", "version": "150"},
            ],
            "fullVersionList": [
                {"brand": "Not A(Brand", "version": "99.0.0.0"},
                {"brand": "Chromium", "version": "150.0.0.0"},
            ],
            "fullVersion": "150.0.0.0",
            "platform": "Windows",
            "platformVersion": "10.0.0",
            "architecture": "x86",
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
    })


def _connect():
    from playwright.sync_api import sync_playwright

    endpoint = settings.BROWSER_CDP_URL.rstrip("/")
    parsed = urlparse(endpoint)
    address = socket.gethostbyname(parsed.hostname)
    netloc = f"{address}:{parsed.port}" if parsed.port else address
    direct_endpoint = parsed._replace(netloc=netloc).geturl()
    with urlopen(f"{direct_endpoint}/json/version", timeout=10) as response:
        payload = json.load(response)
    websocket = payload["webSocketDebuggerUrl"]
    websocket = websocket.replace("ws://localhost:9222", f"ws://{netloc}")
    websocket = websocket.replace("ws://127.0.0.1:9222", f"ws://{netloc}")
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(websocket, timeout=15000)
    except Exception:
        playwright.stop()
        raise
    return playwright, browser


def open_marketplace(marketplace):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    playwright, browser = _connect()
    try:
        context = browser.contexts[0] if browser.contexts else browser.new_context(locale="ru-RU")
        page = context.pages[0] if context.pages else context.new_page()
        if marketplace == "wildberries":
            _configure_wildberries_browser(context, page)
        try:
            # The user completes verification in noVNC. The HTTP request only
            # needs navigation to start; it must never occupy a web worker for
            # a full marketplace page load.
            page.goto(URLS[marketplace], wait_until="commit", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        page.bring_to_front()
        return page.url
    finally:
        # Browser.close() would terminate persistent Chromium; only detach CDP.
        playwright.stop()


def open_2gis():
    """Open 2GIS in the persistent visible Chromium for manual verification."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    playwright, browser = _connect()
    try:
        context = browser.contexts[0] if browser.contexts else browser.new_context(locale="ru-RU")
        page = context.new_page()
        try:
            page.goto("https://2gis.ru/search/магазин", wait_until="commit", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        page.bring_to_front()
        return page.url
    finally:
        playwright.stop()


def save_marketplace_cookies(marketplace):
    playwright, browser = _connect()
    try:
        context = browser.contexts[0] if browser.contexts else None
        if context is None:
            raise RuntimeError("Chromium context is not available")
        cookies = context.cookies([URLS[marketplace]])
        page = next(
            (item for item in context.pages if marketplace_host(marketplace) in item.url),
            context.pages[0] if context.pages else None,
        )
        local_storage, browser_fingerprint = _page_session_state(marketplace, page)
    finally:
        playwright.stop()
    return _persist_marketplace_session(
        marketplace, cookies, local_storage, browser_fingerprint,
    )


def _page_session_state(marketplace, page):
    local_storage = {}
    browser_fingerprint = {}
    if page is None:
        return local_storage, browser_fingerprint
    if marketplace == "wildberries" and "Chrome/150." not in page.evaluate("navigator.userAgent"):
        raise RuntimeError("Сначала откройте Wildberries кнопкой «Открыть Chromium»")
    browser_fingerprint = page.evaluate("""() => {
        const data = navigator.userAgentData;
        return {
            user_agent: navigator.userAgent || '',
            brands: data ? data.brands : [],
            mobile: data ? data.mobile : false,
            platform: data ? data.platform : navigator.platform,
        };
    }""")
    if marketplace == "wildberries":
        device_id = page.evaluate("localStorage.getItem('wbx__sessionID') || ''")
        if device_id:
            local_storage["wbx__sessionID"] = device_id
    return local_storage, browser_fingerprint


def _persist_marketplace_session(marketplace, cookies, local_storage, browser_fingerprint):
    if not cookies:
        raise RuntimeError("Браузер не получил cookies этой площадки")

    cookie_header = "; ".join(
        f"{item['name']}={item['value']}" for item in cookies if item.get("name")
    )
    MarketplaceSession.objects.update_or_create(
        marketplace=marketplace,
        defaults={
            "cookies": cookie_header,
            "source": "browser-ui",
            "note": "Сохранено из серверного Chromium",
        },
    )
    session_dir = Path(settings.BASE_DIR) / "runtime" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{marketplace}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({
        "marketplace": marketplace,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source": "browser-ui",
        "url": URLS[marketplace],
        "cookies": cookies,
        # WB validates a per-browser device identifier in addition to cookies.
        # Store only the marketplace's own browser state; never the full profile.
        "local_storage": local_storage,
        "browser": browser_fingerprint,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return len(cookies)


def refresh_marketplace_session(marketplace, timeout_ms=60000):
    """Refresh a normal browser session; never use the browser for crawling."""
    lock_path = Path(settings.BASE_DIR) / "runtime" / f"{marketplace}_bootstrap.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        playwright, browser = _connect()
        cookies = []
        local_storage = {}
        browser_fingerprint = {}
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context(locale="ru-RU")
            page = context.pages[0] if context.pages else context.new_page()
            if marketplace == "wildberries":
                _configure_wildberries_browser(context, page)
            response_seen = False

            def response_handler(response):
                nonlocal response_seen
                if marketplace == "wildberries" and "u-search/exactmatch" in response.url:
                    response_seen = response.status == 200

            page.on("response", response_handler)
            page.goto(URLS[marketplace], wait_until="commit", timeout=15000)
            deadline = datetime.now(timezone.utc).timestamp() + timeout_ms / 1000
            while datetime.now(timezone.utc).timestamp() < deadline and not response_seen:
                page.wait_for_timeout(500)
            if marketplace == "wildberries" and not response_seen:
                raise RuntimeError("Wildberries не подтвердил браузерную сессию")
            cookies = context.cookies([URLS[marketplace]])
            local_storage, browser_fingerprint = _page_session_state(marketplace, page)
        finally:
            playwright.stop()
        return _persist_marketplace_session(
            marketplace, cookies, local_storage, browser_fingerprint,
        )


def marketplace_host(marketplace):
    return urlparse(URLS[marketplace]).hostname or ""


def vnc_password():
    try:
        return (Path(settings.BASE_DIR) / "runtime" / "vnc_password").read_text().strip()
    except OSError:
        return ""
