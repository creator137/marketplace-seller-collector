# Mass collection status

Real live runs from this machine (host venv, macOS, real network). Every number
below comes from an actual crawl executed on 2026-09-24; nothing is inferred
from fixtures.

## Yandex Market — WORKING, main live result

- endurance command: `python manage.py endurance_marketplaces --marketplace yandex_market --query "наушники" --target 100`
- result: `success` — **100 unique seller refs**, 11 pages, 434 products, 111 requests, 0 blocked, 58s
- `--target 1000` on the same query: `success` (query exhausted at 139 unique sellers / 19 pages — YM caps visible depth)
- multi-query live crawl (existing adapter API, 104 broad queries, ~3,000 requests):
  - batch 1: **4,060 unique refs**, 0×403, 0×429, 0×5xx, 2,372s
  - batch 2: **4,974 unique refs**, 0×403, 0×429, 0×5xx, 2,689s
  - batch 3 (continuation of batch 2 set): **5,000+ reached**, 0 blocked
- session bootstrap (host, headed Chromium): `runtime/sessions/yandex_market.json` created;
  `HttpClient.marketplace_cookies()` loads 27 cookies in a separate worker-like Django process (verified)
- limitation: `/seller/<id>/` detail pages require slug URLs (bare-id URLs 404 even in browser);
  details are `partial` — discovery refs and ratings are unaffected

## Ozon — blocked at browser-fingerprint level

- `.env` cookies captured from a real user browser worked live for several hours in the
  morning (search → tiles → product pages → webCurrentSeller widget, names/ratings collected),
  then expired with HTTP 403 challenge — typical short session TTL
- Playwright Chromium probe: initial navigation 403; JS challenge does not auto-pass
  ("Похоже, нет соединения" dead-end); **in-page `fetch()` of entrypoint-api from the same
  browser with the same cookies also returns 403** → the block is fingerprint-level
  (automation Chromium detected), not a cookie-transfer problem
- consequence: Ozon requires cookies from a real user browser (`.env` OZON_COOKIES) and
  periodic refresh; `bootstrap_sessions` on Playwright Chromium cannot obtain them here

## Wildberries

- search/card endpoints still require a valid session (403/498 without cookies);
  category menu JSON works; no session available from this environment

## Bootstrap notes

- host bootstrap (recommended): `python manage.py bootstrap_sessions --marketplace <code>`
  opens a visible browser on the host and writes `./runtime/sessions/<code>.json`
- Docker: `./runtime/sessions` is bind-mounted into both `web` and `worker`, so a session
  created on the host is visible to the RQ worker without rebuild
- Docker image bootstrap needs `DISPLAY` for headed mode; host bootstrap is the supported path
