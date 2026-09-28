"""Control the single persistent Chromium used only for session bootstrap."""
import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from core.models import MarketplaceSession


URLS = {
    "ozon": "https://www.ozon.ru/search/?text=наушники",
    "wildberries": "https://www.wildberries.ru/catalog/0/search.aspx?search=наушники",
    "yandex_market": "https://market.yandex.ru/search?text=наушники",
}


def _connect():
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(settings.BROWSER_CDP_URL, timeout=15000)
    except Exception:
        playwright.stop()
        raise
    return playwright, browser


def open_marketplace(marketplace):
    playwright, browser = _connect()
    try:
        context = browser.contexts[0] if browser.contexts else browser.new_context(locale="ru-RU")
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URLS[marketplace], wait_until="domcontentloaded", timeout=60000)
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
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return len(cookies)


def vnc_password():
    try:
        return (Path(settings.BASE_DIR) / "runtime" / "vnc_password").read_text().strip()
    except OSError:
        return ""
