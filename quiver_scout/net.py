"""Small HTTP helpers on top of urllib, so the tool needs nothing beyond Python itself."""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .updater import current_build

USER_AGENT = f"quiver-scout/build-{current_build()} (Quiver Launcher maintainer tool)"


class FetchError(RuntimeError):
    pass


class HTTPStatusError(FetchError):
    """The server answered, with an error status (404, 403...)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    status: int  # 200, or 304 when the server says the copy we have is still current
    body: bytes = b""
    headers: dict = field(default_factory=dict)


def request(url: str, headers: dict | None = None, timeout: float = 30, retries: int = 3) -> Response:
    """GET a URL, retrying on network errors, 429 and 5xx with a growing pause."""
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return Response(r.status, body, {k.lower(): v for k, v in r.headers.items()})
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return Response(304, b"", {k.lower(): v for k, v in e.headers.items()})
            last = e
            if e.code != 429 and e.code < 500:
                raise HTTPStatusError(f"{url}: HTTP {e.code}", e.code) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        if attempt + 1 < retries:
            time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{url}: {last}") from last  # the cause tells a network failure from a local one


def get_bytes(url: str, **kw: Any) -> bytes:
    return request(url, **kw).body


def get_text(url: str, **kw: Any) -> str:
    return get_bytes(url, **kw).decode("utf-8", errors="replace")


def get_json(url: str, **kw: Any) -> Any:
    return json.loads(get_bytes(url, **kw))
