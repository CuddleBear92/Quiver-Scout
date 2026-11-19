"""Small HTTP helpers on top of urllib, so the tool needs nothing beyond Python itself."""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from typing import Any

from . import __version__

USER_AGENT = f"quiver-scout/{__version__} (Quiver Launcher maintainer tool)"


class FetchError(RuntimeError):
    pass


def get_bytes(url: str, timeout: float = 30, retries: int = 3) -> bytes:
    """GET a URL, retrying on network errors, 429 and 5xx with a growing pause."""
    last: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429 and e.code < 500:
                raise FetchError(f"{url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{url}: {last}")


def get_text(url: str, **kw: Any) -> str:
    return get_bytes(url, **kw).decode("utf-8", errors="replace")


def get_json(url: str, **kw: Any) -> Any:
    return json.loads(get_bytes(url, **kw))
