"""Looking for a taken-down repository somewhere else: developers often upload the same code to another host, under the
same name or the same account, after GitHub takes theirs down.

For a repository host/owner/name it finds, best first:
  1. the same owner and name on GitHub, GitLab or Codeberg (asked directly)
  2. projects already listed by any site, or in Quiver, with the same name, or the same owner on another host
     (no requests: from the data already read)
  3. repositories called the same on GitHub (search API, needs a token), GitLab and Codeberg (both searched without one)

Each is a possible replacement, for a maintainer to look at: same name doesn't mean same code.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import net
from .urls import canonical

# Hosts asked for the same owner/name, and searched by name: host -> kind.
HOSTS = {"github.com": "github", "gitlab.com": "gitlab", "codeberg.org": "gitea"}
SCORES = {"same owner and name": 3, "same owner": 2, "same name": 1}


def parts(key: str) -> tuple[str, str, str]:
    """(host, owner, name) of a repository key; GitLab subgroups are part of neither."""
    bits = key.split("/")
    return bits[0], bits[1] if len(bits) > 1 else "", bits[-1] if len(bits) > 2 else ""


def _json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _exists(host: str, owner: str, name: str, tokens: dict) -> str | None:
    """The web URL of host/owner/name if it's there."""
    try:
        if HOSTS[host] == "github":
            headers = {"Authorization": f"bearer {tokens['github']}"} if tokens.get("github") else {}
            return _json(f"https://api.github.com/repos/{owner}/{name}", headers).get("html_url")
        if HOSTS[host] == "gitlab":
            headers = {"PRIVATE-TOKEN": tokens["gitlab"]} if tokens.get("gitlab") else {}
            path = urllib.parse.quote(f"{owner}/{name}", safe="")
            return _json(f"https://gitlab.com/api/v4/projects/{path}", headers).get("web_url")
        return _json(f"https://{host}/api/v1/repos/{owner}/{name}").get("html_url")
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError):
        return None


def _search(host: str, name: str, tokens: dict) -> list[str]:
    """Web URLs of repositories on `host` called exactly `name`."""
    q = urllib.parse.quote(name)
    try:
        if HOSTS[host] == "github":
            if not tokens.get("github"):
                return []  # 10 searches a minute without a token: not worth it
            data = _json(f"https://api.github.com/search/repositories?q={q}+in:name&per_page=20",
                         {"Authorization": f"bearer {tokens['github']}"})
            found = [(r["name"], r["html_url"]) for r in data.get("items", [])]
        elif HOSTS[host] == "gitlab":
            headers = {"PRIVATE-TOKEN": tokens["gitlab"]} if tokens.get("gitlab") else {}
            found = [(p["path"], p["web_url"]) for p in _json(f"https://gitlab.com/api/v4/projects?search={q}&per_page=20", headers)]
        else:
            found = [(r["name"], r["html_url"]) for r in _json(f"https://{host}/api/v1/repos/search?q={q}&limit=20").get("data", [])]
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError, KeyError, TypeError):
        return []
    return [url for n, url in found if n.lower() == name.lower()]


def find(key: str, known: dict[str, str], down: set[str], tokens: dict, ask: bool = True) -> list[dict]:
    """Possible replacements for `key`, best first: [{url, how, where}]. `known` is key -> where it's listed ("In
    Quiver", "unbaked, PlayPorted") for every project already read; `down` the keys known to be taken down."""
    host, owner, name = parts(key)
    if not name:
        return []
    out: dict[str, dict] = {}

    def add(url: str, how: str, where: str) -> None:
        c = canonical(url)
        if not c or c[0] == key or c[0] in down:
            return
        old = out.get(c[0])
        if not old or SCORES[how] > SCORES[old["how"]]:
            out[c[0]] = {"url": c[1], "how": how, "where": where or (old or {}).get("where", "")}

    for k, where in known.items():
        h, o, n = parts(k)
        if k == key or not n:
            continue
        if n.lower() == name.lower():
            add(f"https://{k}", "same owner and name" if o.lower() == owner.lower() else "same name", where)
        elif o.lower() == owner.lower() and h != host and h in HOSTS:
            add(f"https://{k}", "same owner", where)

    if ask:
        for other in HOSTS:
            if other == host:
                continue
            url = _exists(other, owner, name, tokens)
            if url:
                add(url, "same owner and name", known.get(canonical(url)[0] if canonical(url) else "", "") or other)
        for other in HOSTS:
            for url in _search(other, name, tokens):
                c = canonical(url)
                if c and parts(c[0])[1].lower() == owner.lower():
                    add(url, "same owner and name", known.get(c[0], "") or other)
                else:
                    add(url, "same name", known.get(c[0], "") if c else other)

    return sorted(out.values(), key=lambda r: (-SCORES[r["how"]], r["url"]))[:15]
