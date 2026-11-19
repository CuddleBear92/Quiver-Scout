"""Telling which unknown sites are self-hosted Forgejo or Gitea servers, which Quiver Launcher reads releases from.

Quiver Launcher reads GitHub, GitLab and Forgejo/Gitea. GitHub and GitLab have one address each; Forgejo and Gitea
run on anyone's server (codeberg.org, git.example.dev), so a project on an unknown site is asked the way Quiver's
own launcher asks (providers/registry.ts): Gitea and Forgejo answer /api/v1/version with their version, and
Forgejo also answers /api/forgejo/v1/version.

Each site is asked once; the answer is kept in the database. Sites that turned out not to be forges are asked again
after RECHECK_DAYS, in case they became one; sites that didn't answer at all, after a day.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import net

RECHECK_DAYS = 30

# Sites that are never Forgejo/Gitea, so they aren't asked.
NOT_FORGES = re.compile(
    r"(^|\.)(github\.io|itch\.io|sourceforge\.net|archive\.org|google\.com|youtube\.com|reddit\.com|discord\.gg|"
    r"discord\.com|twitter\.com|x\.com|moddb\.com|gamebanana\.com|nexusmods\.com|blogspot\.com|wordpress\.com|"
    r"workers\.dev|pages\.dev|netlify\.app|vercel\.app|surge\.sh|bitbucket\.org|github\.com|gitlab\.com|"
    r"codeberg\.org|svn\.code\.sf\.net|wikipedia\.org|steamcommunity\.com|patreon\.com|ko-fi\.com)$"
)


def looks_like_repo(key: str) -> bool:
    """host/owner/name: a forge's repository address, not a project's home page."""
    parts = key.split("/")
    return len(parts) >= 3 and not NOT_FORGES.search(parts[0])


def probe(host: str, timeout: float = 8) -> str | None:
    """"forgejo", "gitea", "" when the site answered and isn't one, None when it didn't answer (ask again later)."""
    try:
        r = net.request(f"https://{host}/api/v1/version", timeout=timeout, retries=1)
        version = json.loads(r.body).get("version") if r.status == 200 else None
    except net.HTTPStatusError:
        return ""
    except (ValueError, AttributeError):  # answered with a page, or JSON of another shape
        return ""
    except Exception:
        return None
    if isinstance(version, dict):
        version = None
    if not isinstance(version, str) or not version:
        return ""
    if "forgejo" in version.lower() or "+gitea" in version.lower():
        return "forgejo"
    try:
        r = net.request(f"https://{host}/api/forgejo/v1/version", timeout=timeout, retries=1)
        if r.status == 200 and json.loads(r.body).get("version"):
            return "forgejo"
    except Exception:
        pass
    return "gitea"


def check(hosts: set[str], known: dict[str, dict], today: date | None = None, workers: int = 8,
          progress=None) -> dict[str, dict]:
    """Ask the sites in `hosts` that `known` (host -> {"kind", "checked"}) has no fresh answer for. Returns `known`,
    updated."""
    today = today or date.today()
    stale_before = (today - timedelta(days=RECHECK_DAYS)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()

    def due(h: str) -> bool:
        k = known.get(h)
        if not k:
            return True
        if k.get("kind"):
            return False
        return k.get("checked", "") < (yesterday if k.get("no_answer") else stale_before)

    ask = sorted(h for h in hosts if due(h))
    if not ask:
        return known
    if progress:
        progress(f"Checking {len(ask)} site(s) for Forgejo/Gitea", 0, len(ask))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for host, kind in zip(ask, pool.map(probe, ask)):
            known[host] = {"kind": kind or "", "checked": today.isoformat(), **({"no_answer": True} if kind is None else {})}
    if progress:
        found = [h for h in ask if (known.get(h) or {}).get("kind")]
        progress(f"Forgejo/Gitea: {len(found)} of {len(ask)} site(s)" + (f": {', '.join(found)}" if found else ""), 1, 1)
    return known
