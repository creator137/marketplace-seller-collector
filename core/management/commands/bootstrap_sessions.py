"""Interactive one-time browser session bootstrap for marketplace HTTP crawls."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


URLS = {
    "ozon": "https://www.ozon.ru/search/?text={query}",
    "wildberries": "https://www.wildberries.ru/catalog/0/search.aspx?search={query}",
    "yandex_market": "https://market.yandex.ru/search?text={query}",
}


class Command(BaseCommand):
    help = "Open a visible marketplace browser, let the user pass verification, and save cookies"

    def add_arguments(self, parser):
        parser.add_argument("--marketplace", choices=sorted(URLS), required=True)
        parser.add_argument("--query", default="наушники", help="Search text used for the normal page")
        parser.add_argument("--headless", action="store_true", help="Use headless Chromium (not suitable for manual captcha)")
        parser.add_argument("--timeout", type=int, default=120, help="Page timeout in seconds")

    def handle(self, *args, **options):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CommandError("Playwright is not installed. Install requirements and Chromium first.") from exc

        marketplace = options["marketplace"]
        url = URLS[marketplace].format(query=quote_plus(options["query"]))
        session_dir = Path(settings.BASE_DIR) / "runtime" / "sessions"
        session_path = session_dir / f"{marketplace}.json"
        self.stdout.write(f"Открываю {url}")
        if not options["headless"]:
            self.stdout.write("Пройдите captcha/verification вручную, если она появится.")
        try:
            with sync_playwright() as playwright:
                launch_kwargs = {"headless": options["headless"], "args": ["--no-sandbox"]}
                executable = os.getenv("PLAYWRIGHT_EXECUTABLE_PATH")
                if executable and Path(executable).exists():
                    launch_kwargs["executable_path"] = executable
                browser = playwright.chromium.launch(**launch_kwargs)
                try:
                    context = browser.new_context(locale="ru-RU")
                    page = context.new_page()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=options["timeout"] * 1000)
                    if response and response.status in (403, 406, 429, 451, 498):
                        raise CommandError(f"Marketplace returned HTTP {response.status}; сессия не сохранена.")
                    if not options["headless"]:
                        input("После открытия обычной страницы нажмите Enter здесь: ")
                    else:
                        page.wait_for_timeout(3000)
                    body = page.locator("body").inner_text(timeout=5000).lower()
                    if any(marker in body for marker in ("captcha", "robot verification", "проверка, что вы не робот")):
                        raise CommandError("Открыта protection/captcha page; сессия не сохранена.")
                    cookies = context.cookies()
                    if not cookies:
                        raise CommandError("Браузер не получил cookies; сессия не сохранена.")
                    session_dir.mkdir(parents=True, exist_ok=True)
                    session_path.write_text(
                        json.dumps({
                            "marketplace": marketplace,
                            "saved_at": datetime.now(timezone.utc).isoformat(),
                            "url": url,
                            "cookies": cookies,
                        }, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                finally:
                    browser.close()
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(f"Не удалось получить browser session: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Сессия сохранена: {session_path}"))
