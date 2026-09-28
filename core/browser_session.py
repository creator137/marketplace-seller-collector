"""Control the single persistent Chromium used only for session bootstrap."""
import json
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
        local_storage = {}
        browser_fingerprint = {}
        if page is not None:
            if marketplace == "wildberries" and "Chrome/150." not in page.evaluate("navigator.userAgent"):
                raise RuntimeError("Сначала откройте Wildberries кнопкой «Открыть Chromium»")
            try:
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
            except Exception:
                local_storage = {}
    finally:
        playwright.stop()
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


def marketplace_host(marketplace):
    return urlparse(URLS[marketplace]).hostname or ""


def vnc_password():
    try:
        return (Path(settings.BASE_DIR) / "runtime" / "vnc_password").read_text().strip()
    except OSError:
        return ""
