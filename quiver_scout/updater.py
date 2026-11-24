"""Updating Quiver Scout from its GitHub releases.

The release workflow (.github/workflows/release.yml) builds QuiverScout.exe on every change to main and publishes it
as a release tagged build-N, N being the workflow's run number (one higher every run), and stamps N into
quiver_scout/_build.py inside the exe. At start the app asks GitHub for the latest release; when its build number is
higher, the exe can download it, check it against the SHA-256 GitHub gives, put it in its own place and restart.
Running from source (build 0), it only says a new build is out.

Replacing a running exe: Windows won't overwrite a running program's file, but it will rename it. So the running
QuiverScout.exe becomes QuiverScout.exe.old, the new one takes its name and is started, and this one quits. The
next start deletes the .old file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "CuddleBear92/quiver-scout"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ASSET = "QuiverScout.exe"


class UpdateError(Exception):
    """Something to tell the user plainly."""


def current_build() -> int:
    """This copy's build number: stamped by the release workflow, 0 when it runs from source."""
    try:
        from ._build import BUILD

        return int(BUILD)
    except (ImportError, ValueError, TypeError):
        return 0


def version_text() -> str:
    """"Build 7", or "running from source"."""
    build = current_build()
    return f"Build {build}" if build else "running from source"


def frozen() -> bool:
    """Running as the built exe (PyInstaller), which can replace itself."""
    return bool(getattr(sys, "frozen", False))


def _request(url: str, token: str = "") -> urllib.request.Request:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "quiver-scout"}
    if token:
        headers["Authorization"] = f"bearer {token}"
    return urllib.request.Request(url, headers=headers)


def latest(token: str = "", timeout: float = 15) -> dict | None:
    """The latest release: {build, title, notes, page, url, size, sha256}; None when there's none with an exe.
    No token is needed (the repository is public); one only raises GitHub's limit of 60 requests an hour."""
    try:
        with urllib.request.urlopen(_request(API, token), timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no release yet
        if e.code in (403, 429):
            raise UpdateError("GitHub says to wait a while before asking again (its limit is 60 requests an hour "
                              "without a token).") from e
        raise UpdateError(f"GitHub answered {e.code}.") from e
    m = re.fullmatch(r"build-(\d+)", data.get("tag_name") or "")
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    if not m or not asset:
        return None
    digest = asset.get("digest") or ""
    return {
        "build": int(m.group(1)),
        "title": data.get("name") or f"Build {m.group(1)}",
        "notes": data.get("body") or "",
        "page": data.get("html_url") or RELEASES_PAGE,
        "url": asset["browser_download_url"],
        "size": asset.get("size") or 0,
        "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
    }


def download(info: dict, token: str, dest: Path, progress=None) -> Path:
    """Download the release's exe to `dest`, checking its size and SHA-256 against GitHub's. Returns `dest`."""
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    # The download URL redirects to GitHub's file storage, which wants no Authorization header: none is sent.
    req = urllib.request.Request(info["url"], headers={"User-Agent": "quiver-scout"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(f"Downloading build {info['build']}: {done / 1e6:.1f} of {total / 1e6:.1f} MB", done, total)
    if info.get("size") and done != info["size"]:
        part.unlink(missing_ok=True)
        raise UpdateError(f"The download is {done} bytes; the release says {info['size']}. Try again.")
    if info.get("sha256") and digest.hexdigest() != info["sha256"]:
        part.unlink(missing_ok=True)
        raise UpdateError("The download doesn't match the release's SHA-256, so it wasn't used. Try again.")
    part.replace(dest)
    return dest


def install(new_exe: Path) -> None:
    """Put `new_exe` where the running exe is and start it. The caller quits right after."""
    if not frozen():
        raise UpdateError("This copy runs from source; it can't replace itself.")
    exe = Path(sys.executable)
    old = exe.with_name(exe.name + ".old")
    try:
        old.unlink(missing_ok=True)
        exe.rename(old)
    except OSError as e:
        raise UpdateError(f"Couldn't move {exe.name} aside ({e}). Is its folder writable?") from e
    try:
        shutil.move(str(new_exe), str(exe))  # a copy when the download is on another drive
    except OSError as e:
        old.rename(exe)  # put the running one back
        raise UpdateError(f"Couldn't put the new build in place: {e}") from e
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(exe)], close_fds=True, creationflags=flags, cwd=str(exe.parent), env=fresh_environment())


def fresh_environment() -> dict:
    """The environment to start the new build in. A PyInstaller exe tells the programs it starts where it unpacked
    itself (_MEIPASS2, _PYI_*), so that its own child processes reuse that folder; a new build given those would
    use this one's folder, which is deleted when this one quits, and fail to load anything after (every download
    then fails with "No such file or directory: ...\\_MEI...\\base_library.zip"). PYINSTALLER_RESET_ENVIRONMENT
    (PyInstaller 6.10 and later) tells it to start afresh, and the variables are dropped besides."""
    env = {k: v for k, v in os.environ.items() if not (k.startswith("_PYI_") or k.startswith("_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def cleanup() -> None:
    """Delete what the last update left behind: the old exe."""
    if not frozen():
        return
    exe = Path(sys.executable)
    try:
        exe.with_name(exe.name + ".old").unlink(missing_ok=True)
    except OSError:
        pass  # the old one may still be closing: next start
