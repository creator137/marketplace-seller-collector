# Mass collection status

## Server deployment — 2026-09-29

Server IP `2.56.241.55`, headless Chromium bootstrap and HTTP endurance were
executed from the deployed Docker environment (not fixtures):

- Ozon: bootstrap HTTP 403; entrypoint and composer discovery HTTP 403,
  0 sellers, 3 requests, 31.14s. Existing supplied cookies are no longer valid.
- Wildberries: working with the persistent Chromium bootstrap plus HTTP crawl;
  see the measured runs below.
- Yandex Market: bootstrap HTTP 403; HTTP discovery returns a protection page,
  0 sellers. No valid Yandex session is configured.
- WB geo endpoint returned HTTP 200 for Уфа, Челябинск and Екатеринбург;
  the corresponding real delivery `dest` values were resolved for deployment.

The server correctly pauses blocked Ozon/Yandex runs; it does not report a false
successful empty result.

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

## Wildberries — WORKING on deployed server

tested_at: 2026-09-29
query/category: `носки`, `платье`, synced UI category `Платья`
cookies/session: persistent server Chromium; automatic refresh on 403/498
target: 100
discovered: 100 unique sellers
details: 100/100
INN: 96/100
address: not measured by endurance (DaData pipeline separately verified live)
pages: 2
requests: 102
403: 0
429: 0
duration: 1.79s collector time (7.43s command wall time)
result: success
limitation: browser bootstrap is required when the short-lived x-pow session expires

target: 1000
discovered: 1000 unique sellers (`платье`)
details: 1000/1000
INN: 973/1000
pages: 28
requests: 1028
403: 0
429: 0
duration: 31.22s collector time
result: success

target: 5000
discovered: 1800 unique sellers; query exhausted after the WB 60-page visible limit
details: 1800/1800
INN: 1751/1800
pages: 60
requests: 1861
403: 0
429: 0
duration: 65.44s collector time
result: partial by target, successful source exhaustion

Pipeline check: a blocked DB job was resumed after automatic session refresh and
completed; WB detail → Russian INN → DaData produced legal address/city for 8/10
sample sellers. A subsequent interrupted 100-seller job resumed and finished all
details; foreign tax identifiers are retained in raw data instead of overflowing
the 12-character Russian INN field.

## Bootstrap notes

- host bootstrap: `python manage.py bootstrap_sessions --marketplace <code>`
  opens a visible browser on the host and writes `./runtime/sessions/<code>.json`
- Docker: `./runtime/sessions` is bind-mounted into both `web` and `worker`, so a session
  created on the host is visible to the RQ worker without rebuild
- deployed WB jobs automatically refresh the persistent server Chromium session
  after 403/498 and continue the same HTTP crawl; the browser is not used for page crawling
