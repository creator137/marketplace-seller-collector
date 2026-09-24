# Mass collection status

Live tests were run from the current environment on 2026-09-24. No result below
is based on fixtures. All three endurance commands correctly exited non-zero on
blocked/unavailable discovery.

## Ozon

- tested_at: `2026-09-24`
- query/category: `наушники`
- cookies/session: not configured
- target: `100`
- discovered: `0`
- details: `0/0`
- INN: `0/0`
- address: `0/0`
- pages: `1` attempted, checkpoint not completed
- requests: `2`
- 403: `1` (composer request then network/DNS timeout)
- 429: `0`
- duration: `5.92s`
- result: `blocked`, endurance exit code `1`
- limitation: both entrypoint and composer require a valid session from this environment

## Wildberries

- tested_at: `2026-09-24`
- query/category: `наушники`
- cookies/session: not configured
- target: `100`
- discovered: `0`
- details: `0/0`
- INN: `0/0`
- address: `0/0`
- pages: `0`
- requests: `1`
- 403: `0` in this run; previous smoke returned `403`
- 429: `0`
- duration: `5.01s`
- result: `temporary_error` (network timeout), endurance exit code `1`
- limitation: current endpoint/session is unavailable; previous live response confirmed blocking

## Yandex Market

- tested_at: `2026-09-24`
- query/category: `наушники`
- cookies/session: not configured
- target: `100`
- discovered: `0`
- details: `0/0`
- INN: `0/0`
- address: `0/0`
- pages: `0`
- requests: `1`
- 403: `0`
- 429: `0`
- duration: `0.76s`
- result: `blocked` — HTTP 200 protection page without `productSnippet`, endurance exit code `1`
- limitation: valid browser session/cookies required by current response

Targets 1,000/5,000/10,000 were not attempted because the required 100-seller
stage did not succeed. No successful live crawl is claimed.
