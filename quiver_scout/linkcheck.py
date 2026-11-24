"""Checking whether projects' repositories and pages are still there: deleted, blocked (DMCA), disabled, renamed,
archived, or fine.

GitHub repositories are checked through its GraphQL API, 50 to a request, with a token (Settings); without one,
through their web pages, a few at a time, stopping if GitHub says to slow down. GitLab and Forgejo/Gitea
repositories through their APIs. Anything else by asking for the page (HEAD).

What a check finds:
  ok          there
  archived    there, read-only
  moved       renamed or moved; `moved_to` is where (and the project is matched against Quiver there too)
  gone        not there any more (404/410), or private
  blocked     taken down: GitHub's "Repository access blocked" (DMCA), HTTP 451
  disabled    GitHub has disabled it
  unreachable the site didn't answer (twice, a day or more apart, before it counts)
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from urllib.parse import quote

from . import net

TAKEN_DOWN = {"gone", "blocked", "disabled", "unreachable"}
SHARED = TAKEN_DOWN | {"moved"}  # what goes in the shared file
LABELS = {"ok": "ok", "archived": "archived", "moved": "moved", "gone": "gone", "blocked": "blocked (DMCA)",
          "disabled": "disabled", "unreachable": "unreachable", "suspect": ""}

# Days before a result is checked again.
RECHECK = {"ok": 14, "archived": 30, "moved": 30, "gone": 60, "blocked": 60, "disabled": 60, "unreachable": 1,
           "suspect": 1}

GITHUB_TOKEN_PAGE = "https://github.com/settings/tokens/new?description=Quiver%20Scout"
GITLAB_TOKEN_PAGE = "https://gitlab.com/-/user_settings/personal_access_tokens?name=Quiver%20Scout&scopes=read_api"


class RateLimited(Exception):
    pass


# Failures that mean the site didn't answer. Anything else (a file this computer couldn't open, a proxy refusing)
# says nothing about the site, so it isn't counted against it.
NETWORK_FAILURES = (socket.timeout, TimeoutError, ConnectionError, socket.gaierror, ssl.SSLError)
# More than this share of sites failing at once means this computer is offline, not that they all went down.
OFFLINE_SHARE = 0.5


def network_failure(error: BaseException | None) -> bool:
    """Whether an error (or what caused it) is the site not answering."""
    seen = 0
    while error is not None and seen < 5:
        if isinstance(error, NETWORK_FAILURES):
            return True
        if isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError):
            reason = error.reason
            if isinstance(reason, BaseException):
                error = reason
                seen += 1
                continue
            return False
        error = error.__cause__ or error.__context__
        seen += 1
    return False


def due(entry: dict | None, today: date) -> bool:
    if not entry or not entry.get("checked"):
        return True
    days = RECHECK.get(entry.get("status", ""), 14)
    return entry["checked"] < (today - timedelta(days=days)).isoformat()


def _result(status: str, detail: str = "", moved_to: str = "") -> dict:
    out = {"status": status}
    if detail:
        out["detail"] = detail
    if moved_to:
        out["moved_to"] = moved_to
    return out


# ---- GitHub --------------------------------------------------------------------------------------------------------


def github_graphql(repos: list[tuple[str, str, str]], token: str) -> dict[str, dict]:
    """repos: (key, owner, name). One request for all of them (keep it to 50)."""
    parts = []
    for n, (_, owner, name) in enumerate(repos):
        parts.append(f'r{n}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
                     "{ nameWithOwner url isArchived isDisabled isLocked }")
    body = json.dumps({"query": "query { " + " ".join(parts) + " }"}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body, method="POST", headers={
        "Authorization": f"bearer {token}", "Content-Type": "application/json", "User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise RateLimited(f"GitHub: HTTP {e.code}") from e
        if e.code == 401:
            raise net.FetchError("GitHub: the token was refused (401). Check it in Settings.") from e
        raise
    errors = {}
    for err in data.get("errors") or []:
        path = (err.get("path") or [None])[0]
        if path:
            errors[path] = err
        elif err.get("type") == "RATE_LIMITED":
            raise RateLimited("GitHub: rate limited")
    out = {}
    for n, (key, owner, name) in enumerate(repos):
        node = (data.get("data") or {}).get(f"r{n}")
        err = errors.get(f"r{n}")
        if node:
            if node.get("isDisabled"):
                out[key] = _result("disabled")
            elif node.get("isLocked"):
                out[key] = _result("blocked", "locked")
            elif node["nameWithOwner"].lower() != f"{owner}/{name}".lower():
                out[key] = _result("moved", moved_to=node["url"])
            elif node.get("isArchived"):
                out[key] = _result("archived")
            else:
                out[key] = _result("ok")
        elif err:
            msg = (err.get("message") or "").lower()
            if "blocked" in msg or "dmca" in msg or "legal" in msg:
                out[key] = _result("blocked", err.get("message", ""))
            elif err.get("type") == "NOT_FOUND":
                out[key] = _result("gone")
            # other errors: not known, left unchecked
    return out


def _head(url: str, timeout: float = 20) -> tuple[int, str]:
    """(status, final URL after redirects). HEAD, or GET when the site refuses HEAD."""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": net.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.geturl()
        except urllib.error.HTTPError as e:
            if e.code in (405, 501) and method == "HEAD":
                continue
            return e.code, url
    return 0, url


def github_web(key: str, url: str) -> dict | None:
    status, final = _head(url)
    if status == 429:
        raise RateLimited("GitHub: too many requests")
    if status in (404, 410):
        return _result("gone")
    if status == 451:
        return _result("blocked", "HTTP 451")
    if 200 <= status < 400:
        from .urls import canonical
        c = canonical(final)
        if c and c[0] != key:
            return _result("moved", moved_to=c[1])
        return _result("ok")
    return None


# ---- GitLab, Forgejo/Gitea, anything else --------------------------------------------------------------------------


def gitlab(key: str, token: str = "") -> dict | None:
    host, path = key.split("/", 1)
    headers = {"PRIVATE-TOKEN": token} if token and host == "gitlab.com" else {}
    try:
        r = net.request(f"https://{host}/api/v4/projects/{quote(path, safe='')}", headers=headers, retries=1)
    except net.HTTPStatusError as e:
        if e.status == 404:
            return _result("gone")
        if e.status == 451:
            return _result("blocked", "HTTP 451")
        return None
    p = json.loads(r.body)
    if p.get("path_with_namespace", "").lower() != path.lower():
        return _result("moved", moved_to=p.get("web_url", ""))
    return _result("archived" if p.get("archived") else "ok")


def gitea(key: str) -> dict | None:
    host, path = key.split("/", 1)
    owner_name = "/".join(path.split("/")[:2])
    try:
        r = net.request(f"https://{host}/api/v1/repos/{owner_name}", retries=1)
    except net.HTTPStatusError as e:
        if e.status == 404:
            return _result("gone")
        if e.status == 451:
            return _result("blocked", "HTTP 451")
        return None
    p = json.loads(r.body)
    if p.get("full_name", "").lower() != owner_name.lower():
        return _result("moved", moved_to=p.get("html_url", ""))
    return _result("archived" if p.get("archived") else "ok")


def page(key: str, url: str) -> dict | None:
    status, _ = _head(url)
    if status in (404, 410):
        return _result("gone")
    if status == 451:
        return _result("blocked", "HTTP 451")
    if status and status < 400:
        return _result("ok")
    return None  # 403/5xx: can't tell


# ---- all of them ---------------------------------------------------------------------------------------------------


def check(links: list[tuple[str, str]], kinds: dict[str, str], tokens: dict[str, str], progress=None,
          workers: int = 6) -> dict[str, dict]:
    """links: (key, url). kinds: host -> "gitea" | "gitlab" for hosts known to be those. Returns key -> result for the
    links it could tell about; ones it couldn't are left out (checked again next time)."""
    results: dict[str, dict] = {}
    say = progress or (lambda *a: None)
    github = [(k, u) for k, u in links if k.startswith("github.com/") and k.count("/") == 2]
    rest = [(k, u) for k, u in links if not (k.startswith("github.com/") and k.count("/") == 2)]
    total, done = len(links), 0

    token = tokens.get("github", "")
    if github and token:
        batches = [github[i:i + 50] for i in range(0, len(github), 50)]
        for batch in batches:
            try:
                results.update(github_graphql([(k, *k.split("/")[1:3]) for k, _ in batch], token))
            except RateLimited as e:
                say(f"Link check: {e}; the rest of GitHub is checked next time", done, total)
                break
            done += len(batch)
            say(f"Link check: GitHub {done}/{len(github)}", done, total)
    elif github:
        say("Link check: no GitHub token, checking GitHub pages one by one (add a token in Settings for speed)", 0, total)
        stop = False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(github_web, k, u): k for k, u in github}
            for f in as_completed(futures):
                done += 1
                try:
                    r = f.result()
                    if r:
                        results[futures[f]] = r
                except RateLimited:
                    if not stop:
                        say("Link check: GitHub asked to slow down; the rest is checked next time", done, total)
                    stop = True
                    for other in futures:
                        other.cancel()
                except Exception:
                    pass
                if done % 25 == 0:
                    say(f"Link check: GitHub pages {done}/{len(github)}", done, total)

    def one(k: str, u: str) -> dict | None:
        host = k.split("/")[0]
        kind = kinds.get(host, "")
        if kind == "gitlab" and k.count("/") >= 2:
            return gitlab(k, tokens.get("gitlab", ""))
        if kind == "gitea" and k.count("/") >= 2:
            return gitea(k)
        return page(k, u)

    unreachable = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, k, u): k for k, u in rest}
        for f in as_completed(futures):
            done += 1
            k = futures[f]
            try:
                r = f.result()
                if r:
                    results[k] = r
            except (net.HTTPStatusError, ValueError):
                pass
            except Exception as e:
                if network_failure(e):
                    unreachable.append(k)
                # anything else is this computer's trouble, not the site's: no result, checked again next time
            if done % 25 == 0:
                say(f"Link check: {done}/{total}", done, total)
    if len(unreachable) >= 10 and len(unreachable) > OFFLINE_SHARE * max(len(rest), 1):
        say(f"Link check: {len(unreachable)} of {len(rest)} sites didn't answer; this computer looks offline, so none "
            "is counted as down", done, total)
        unreachable = []
    for k in unreachable:
        results[k] = _result("suspect", "didn't answer")
    return results


def merge(previous: dict | None, found: dict, today: str) -> dict:
    """One link's new entry. A site that didn't answer counts as unreachable only the second time, a day or more
    after the first."""
    found = {**found, "checked": today}
    if found["status"] == "suspect":
        if previous and previous.get("status") in ("suspect", "unreachable") and previous.get("checked", "") < today:
            return {**found, "status": "unreachable"}
        return found
    return found
