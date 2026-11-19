"""Turning the many ways a project link is written into one key, so the same repository matches across sites.

https://GitHub.com/Owner/Repo.git, github.com/owner/repo/releases/tag/v1 and http://www.github.com/owner/repo/
all become the key "github.com/owner/repo".
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Hosts where a repository is exactly owner/name, and anything after it is a page inside the repository.
TWO_PART_HOSTS = {"github.com", "bitbucket.org", "codeberg.org", "sourcehut.org", "git.sr.ht"}

# Path segments that start a page inside a repository on GitLab, Gitea, Forgejo and friends.
INNER_PAGES = {
    "-", "tree", "blob", "src", "raw", "releases", "release", "tags", "tag", "issues", "pulls", "pull",
    "merge_requests", "wiki", "wikis", "actions", "commits", "commit", "branches", "archive", "packages",
    "pipelines", "jobs", "activity", "milestones", "labels", "projects", "discussions", "security", "settings",
}

DEFAULT_HOSTS = {"github": "github.com", "gitlab": "gitlab.com", "bitbucket": "bitbucket.org", "codeberg": "codeberg.org"}


def _split(url: str):
    url = url.strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    segments = [s for s in parts.path.split("/") if s]
    return host, segments


def _repo_segments(host: str, segments: list[str]) -> list[str]:
    if host in TWO_PART_HOSTS or host.endswith(".github.com"):
        segments = segments[:2]
    else:
        # GitLab allows nested groups, so cut at the first segment that is a page inside the repository.
        for i, s in enumerate(segments):
            if s.lower() in INNER_PAGES and i >= 2:
                segments = segments[:i]
                break
    if segments and segments[-1].lower().endswith(".git"):
        segments[-1] = segments[-1][:-4]
    return segments


def canonical(url: str) -> tuple[str, str] | None:
    """(key, clean_url) for a project link, or None when it isn't a usable link."""
    split = _split(url)
    if not split:
        return None
    host, segments = split
    if host == "raw.githubusercontent.com":
        host = "github.com"
    segments = _repo_segments(host, segments)
    path = "/".join(segments)
    key = f"{host}/{path}".lower().rstrip("/")
    clean = f"https://{host}/{path}".rstrip("/")
    return key, clean


def aliases(key: str) -> set[str]:
    """Other keys the same project is known by: owner.github.io/repo is the site of github.com/owner/repo."""
    out = {key}
    m = re.match(r"^([a-z0-9-]+)\.github\.io/([^/]+)", key)
    if m:
        out.add(f"github.com/{m.group(1)}/{m.group(2)}")
    return out


def from_provider(provider: str, repository: str, host: str | None = None) -> str:
    """A web URL from the provider/host/repository triple Quiver and RecompLauncher store."""
    repository = (repository or "").strip()
    if "://" in repository:
        return repository
    host = host or DEFAULT_HOSTS.get(provider, "")
    if not host:
        return repository
    return f"https://{host}/{repository.strip('/')}"


def normalize_title(title: str) -> str:
    """Lowercase letters and digits only, for comparing game names across sites."""
    title = (title or "").lower().replace("&", "and")
    title = re.sub(r"\(.*?\)", "", title)
    return re.sub(r"[^a-z0-9]+", "", title)
