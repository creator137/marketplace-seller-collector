# Mass collection status

Live tests were run from the current environment on 2026-09-24. No result below
is based on fixtures. All three endurance commands correctly exited non-zero on
blocked/protection discovery. The new bootstrap command was also invoked in the
container, but the pre-change image did not contain the newly added Playwright
dependency; no session JSON was created and no success is claimed.

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
- duration: `43.91s`
- result: `blocked`, endurance exit code `1`
- limitation: both entrypoint and composer require a valid session from this environment; bootstrap needs to be run interactively with the rebuilt image

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
- duration: `0.63s`
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
- duration: `0.77s`
- result: `blocked` — HTTP 200 protection page without `productSnippet`, endurance exit code `1`
- limitation: valid browser session/cookies required by current response

Targets 1,000/5,000/10,000 were not attempted because the required 100-seller
stage did not succeed. No successful live crawl is claimed.

Bootstrap attempts:

- `bootstrap_sessions --marketplace ozon --headless`: command is wired and
  exposes the expected options; the available old image returned
  `Playwright is not installed`, so no cookies were saved.
- A rebuilt Docker image could not be completed in this environment because
  Docker Hub DNS timed out while fetching the base image; the Dockerfile now
  installs Debian Chromium and no longer downloads a browser from the
  Playwright CDN.
