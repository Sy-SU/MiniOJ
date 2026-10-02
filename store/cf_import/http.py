from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .storage import read_json, write_json


class HTTPError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(code, "HTTP redirect refused; configure the final URL")


class HTTPClient:
    def __init__(self, timeout: float = 30, max_bytes: int = 64 * 1024 * 1024):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(
        self,
        method: str,
        url: str,
        *,
        payload=None,
        token: str = "",
        params: dict | None = None,
        text: bool = False,
    ):
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
        ):
            raise ValueError(
                "HTTP endpoint must be an http(s) URL without embedded credentials"
            )
        if params:
            url += ("&" if parsed.query else "?") + urllib.parse.urlencode(params)
        headers = {
            "User-Agent": "MiniOJ-local-cf-import/1.0",
            "Accept": "text/html" if text else "application/json",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise RuntimeError("HTTP response exceeded configured size limit")
                return (
                    raw.decode("utf-8") if text else (json.loads(raw) if raw else None)
                )
        except urllib.error.HTTPError as exc:
            # Do not print body, URL/query string, headers, source or tokens.
            raise HTTPError(
                exc.code, f"HTTP {exc.code} from {parsed.hostname}"
            ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"HTTP transport failed for {parsed.hostname} ({type(exc).__name__})"
            ) from None


class RequestPacer:
    """Persist request timestamps, including across restarts and failed requests."""

    def __init__(self, path: Path):
        self.path = path

    def wait(self, interval: float) -> None:
        last = read_json(self.path).get("time", 0) if self.path.exists() else 0
        delay = min(interval, max(0, interval - (time.time() - last)))
        if delay:
            time.sleep(delay)
        write_json(self.path, {"time": time.time()})
