"""
Shared HTTP client for the scrapers' non-browser paths.

Everything goes through curl_cffi with Chrome impersonation, so requests carry
a real browser's TLS and HTTP/2 fingerprint. Plain python clients get answered
differently by bot screening: Shopify's Cloudflare edge returned 429 to
python-requests on 9 of 10 calls while curl_cffi from the same IP got 200 on
all of them, and Akamai (Pepperfry) 403s plain requests outright. Those
failures were silent: scrapers fell back to the slow browser path.

No User-Agent is set here or by callers: the impersonation sends one that
matches its fingerprint, and overriding it with a different Chrome version is
exactly the mismatch screening looks for. Playwright contexts still set their
own user agent; that is unrelated to this client.
"""

from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import HTTPError  # noqa: F401  (re-exported)

IMPERSONATE = "chrome"

_BASE_HEADERS = {
    "Accept-Language": "en-IN,en;q=0.9",
}

HTML_HEADERS = {
    **_BASE_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

JSON_HEADERS = {
    **_BASE_HEADERS,
    "Accept": "application/json,text/plain,*/*",
}


def get(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 20,
):
    """GET with a Chrome fingerprint, following redirects. Defaults to HTML."""
    return curl_requests.get(
        url,
        headers=HTML_HEADERS if headers is None else headers,
        params=params,
        timeout=timeout,
        allow_redirects=True,
        impersonate=IMPERSONATE,
    )
