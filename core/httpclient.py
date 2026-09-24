"""Small shared HTTP layer: curl_cffi + retries with exponential backoff.

Adapters stay isolated: each marketplace adapter can fail without
affecting the others.
"""
import json
import logging
import time
import uuid

from django.conf import settings
from curl_cffi import requests as cffi_requests

log = logging.getLogger("core.http")

IMPERSONATE = "chrome124"
USER_AGENT_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class HttpError(Exception):
    """Transient/non-recoverable HTTP error, message-safe for logs."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def blocked(self):
        return self.status in (403, 406, 451, 498)

    @property
    def rate_limited(self):
        return self.status == 429


class HttpClient:
    """Sync session wrapper with browser-like headers, cookies and retries."""

    def __init__(self, cookies: dict | None = None, referer: str = "", headers: dict | None = None):
        self.session = cffi_requests.Session(impersonate=IMPERSONATE)
        self.stats = {"requests": 0, "2xx": 0, "403": 0, "429": 0, "5xx": 0, "other": 0}
        if cookies:
            self.session.cookies.update(cookies)
        self.base_headers = {
            "accept-language": "ru-RU,ru;q=0.9",
            "user-agent": USER_AGENT_DESKTOP,
        }
        if referer:
            self.base_headers["referer"] = referer
        if headers:
            self.base_headers.update(headers)

    @staticmethod
    def parse_cookies(raw: str) -> dict:
        """Parse a cookie header string or a JSON dict of cookies."""
        if not raw:
            return {}
        raw = raw.strip()
        if raw.startswith("{"):
            try:
                return {str(k): str(v) for k, v in json.loads(raw).items()}
            except (ValueError, TypeError):
                return {}
        out = {}
        for pair in raw.split(";"):
            if "=" in pair:
                k, _, v = pair.strip().partition("=")
                out[k.strip()] = v.strip()
        return out

    def get(self, url, params=None, headers=None, timeout=None):
        return self.request("GET", url, params=params, headers=headers, timeout=timeout)

    def request(self, method, url, params=None, headers=None, timeout=None, json_payload=None):
        timeout = timeout or settings.HTTP_TIMEOUT
        retries = settings.HTTP_RETRIES
        delay = 1.0
        last_exc = None
        for attempt in range(1, retries + 1):
            merged = dict(self.base_headers)
            if headers:
                merged.update(headers)
            try:
                self.stats["requests"] += 1
                resp = self.session.request(
                    method, url, params=params, headers=merged, timeout=timeout, json=json_payload,
                )
                if 200 <= resp.status_code < 300:
                    self.stats["2xx"] += 1
                elif str(resp.status_code) in self.stats:
                    self.stats[str(resp.status_code)] += 1
                elif resp.status_code >= 500:
                    self.stats["5xx"] += 1
                else:
                    self.stats["other"] += 1
                # A block/session failure is actionable and must not be retried
                # as if it were a transient network problem.  429 and 5xx get
                # bounded exponential backoff below.
                if resp.status_code in (403, 406, 451, 498) or resp.status_code == 429 or resp.status_code >= 500:
                    raise HttpError(f"HTTP {resp.status_code} for {url}", status=resp.status_code)
                return resp
            except HttpError as exc:
                last_exc = exc
                log.warning("HTTP %s (attempt %d/%d): %s", exc.status, attempt, retries, exc)
                if exc.blocked or attempt == retries:
                    raise
            except Exception as exc:  # network-level
                last_exc = exc
                log.warning("HTTP error (attempt %d/%d): %s", attempt, retries, exc)
                if attempt == retries:
                    raise HttpError(f"network error for {url}: {exc}")
            time.sleep(delay)
            delay *= 2
        raise HttpError(f"request failed for {url}: {last_exc}")


def ozon_request_id_headers() -> dict:
    return {
        "x-o3-parent-requestid": uuid.uuid4().hex,
        "x-page-view-id": str(uuid.uuid4()),
    }
