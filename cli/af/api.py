"""HTTP transport for the Aftermath V2 API.

Three properties this layer is responsible for, all of which exist because of
specific documented behaviours of this API:

**POST-first.** Nearly every route in this API is POST, *including reads*. A
route that 404s on GET is almost always a POST route, so :func:`post` is the
primary verb and GET is the exception.

**Retry with backoff.** Transient 5xx/429 and connection resets are retried;
4xx client errors are not, because retrying a malformed request just burns
time and rate limit.

**Previews are tagged unions.** A preview endpoint can return HTTP 200 with an
error body (``{"error": …}`` plus an ``X-Error-Message`` header). Code that
only checks the status code treats a rejection as a success. :func:`preview`
normalises that into an explicit result and fails closed.

The host always comes from :func:`cli.af.config.AF_API_BASE_URL` -- this module
never contains a hostname.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

import requests

from cli.af.config import AF_API_BASE_URL

log = logging.getLogger("af.api")

DEFAULT_TIMEOUT_S = 20.0
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class AfApiError(RuntimeError):
    """A call to the Aftermath API failed."""

    def __init__(self, message: str, *, status: Optional[int] = None, path: str = "", body: Any = None):
        super().__init__(message)
        self.status = status
        self.path = path
        self.body = body


class AfNotAvailable(AfApiError):
    """A route exists in the spec but is not served (e.g. the /api/wallet/* family).

    Distinct from a generic failure so callers can degrade gracefully rather
    than treating an unimplemented route as an outage.
    """


def _url(path: str) -> str:
    return f"{AF_API_BASE_URL()}{path if path.startswith('/') else '/' + path}"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, AfApiError) and exc.status in _RETRYABLE_STATUS:
        return True
    return False


def request(
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = 4,
    session: Optional[requests.Session] = None,
) -> Any:
    """Issue one API call, retrying transient failures with jittered backoff."""
    url = _url(path)
    sender = session or requests
    last: Optional[BaseException] = None

    for attempt in range(max_retries + 1):
        try:
            resp = sender.request(method, url, json=json_body, timeout=timeout)

            if resp.status_code == 404:
                raise AfNotAvailable(
                    f"{method} {path} -> 404 (route not served by this deployment)",
                    status=404,
                    path=path,
                )
            if resp.status_code >= 400:
                raise AfApiError(
                    f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:400]}",
                    status=resp.status_code,
                    path=path,
                    body=resp.text,
                )

            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError as exc:
                raise AfApiError(
                    f"{method} {path} returned a non-JSON body: {resp.text[:200]}",
                    status=resp.status_code,
                    path=path,
                ) from exc

        except BaseException as exc:  # noqa: BLE001 - re-raised below
            last = exc
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            delay = min(8.0, 0.25 * (2**attempt)) * (0.5 + random.random())
            log.debug("retrying %s %s after %.2fs (%s)", method, path, delay, exc)
            time.sleep(delay)

    raise AfApiError(f"{method} {path} exhausted retries") from last  # pragma: no cover


def post(path: str, body: Optional[Dict[str, Any]] = None, **kw: Any) -> Any:
    """POST -- the primary verb for this API, reads included."""
    return request("POST", path, json_body=body or {}, **kw)


def get(path: str, **kw: Any) -> Any:
    """GET -- rare. If this 404s, the route is very likely POST."""
    return request("GET", path, **kw)


# ── Preview results ──────────────────────────────────────────────


@dataclass(frozen=True)
class PreviewOk:
    value: Any
    ok: bool = True


@dataclass(frozen=True)
class PreviewErr:
    error: str
    ok: bool = False


PreviewResult = Union[PreviewOk, PreviewErr]


def is_preview_error_body(body: Any) -> bool:
    """A 200 response that is actually an error carries an ``error`` string."""
    return isinstance(body, dict) and isinstance(body.get("error"), str)


def preview(path: str, body: Dict[str, Any], **kw: Any) -> PreviewResult:
    """Call a preview route and normalise its tagged-union result.

    Fails closed: anything not clearly a success is an error, including a
    transport failure. A preview that cannot be evaluated must never be read as
    permission to transact.
    """
    try:
        res = post(path, body, **kw)
    except AfApiError as exc:
        return PreviewErr(str(exc))
    if is_preview_error_body(res):
        return PreviewErr(str(res["error"]))
    if res is None:
        return PreviewErr(f"{path} returned an empty preview body")
    return PreviewOk(res)
