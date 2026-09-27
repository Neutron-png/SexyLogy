"""
Free-proxy feed: fetch the community proxy list (proxifly/free-proxy-list,
CDN-served) at the START of every campaign, so "rotating"/"hybrid" runs
never reuse a stale snapshot from a previous session - the list is
regenerated as often as upstream publishes new proxies.

Why curl.exe (not requests/scrapling): the user asked for the CDN url
exactly as proxifly publishes it, curl.exe ships with Windows 10+ and
-git-bash, and a one-shot subprocess can't leak UI-blocking network calls
into the engine thread.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

FEED_URL = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.txt"

# The list lives next to logy.db (the app's working directory), so it
# refreshes with every run and survives restarts.
FEED_FILENAME = "all.txt"

# A rotated pool this big slows the pre-flight probe (one request per
# proxy) without helping anonymity - alive-IPs-per-run is what matters.
MAX_PROXIES = 400

SCHEME_ORDER = ("http", "https", "socks4", "socks5")

STALE_AFTER_S = 6 * 3600  # older than this -> treat cached copy as expired


def _feed_path() -> Path:
    return Path(os.getcwd()) / FEED_FILENAME


def cached_copy_age_s() -> float | None:
    p = _feed_path()
    if not p.exists():
        return None
    return max(0.0, time.time() - p.stat().st_mtime)


def _is_valid_proxy(line: str) -> bool:
    if not line or len(line) > 128:
        return False
    if "://" not in line:
        return False
    scheme, _, rest = line.rpartition("://")
    if scheme not in SCHEME_ORDER:
        return False
    host, _, port = rest.partition(":")
    if not host or not port.isdigit():
        return False
    try:
        o = urlparse(f"{scheme}://{host}")
    except ValueError:
        return False
    return bool(o.hostname)


def refresh_free_proxies(
    timeout_s: int = 45,
    force: bool = False,
    max_proxies: int = MAX_PROXIES,
) -> tuple[bool, int, str]:
    """Download/refresh all.txt and return (ok, usable_count, message).

    - force=False: skips the download when a cached copy is younger than
      STALE_AFTER_S (immediate re-runs reuse it; once stale it refetches).
    - force=True: always re-downloads (used when the user explicitly
      refreshes, or when starting a campaign with an active proxy mode).
    """
    path = _feed_path()
    if not force:
        age = cached_copy_age_s()
        if age is not None and age < STALE_AFTER_S:
            lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="ignore").splitlines()]
            usable = [l for l in lines if _is_valid_proxy(l)]
            if usable:
                return True, min(len(usable), max_proxies), f"{len(usable)} بروكسي من النسخة المحفوظة ({int(age)} ثانية)"

    cmd = ["curl.exe", "-sL", "--fail", "--connect-timeout", "20", "--max-time", str(timeout_s), FEED_URL, "-o", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        # curl.exe missing (old Windows): try plain `curl` from PATH
        try:
            cmd[0] = "curl"
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            return False, 0, "curl.exe مش موجود - نزّل الملف يدوي"
    if proc.returncode != 0:
        return False, 0, f"curl فشل (rc={proc.returncode}) {proc.stderr.strip()[:120]}"

    if not path.exists() or path.stat().st_size < 512:
        return False, 0, "الملف نزل فاضي أو صغير - القائمة مش متاحة الآن"

    lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="ignore").splitlines()]
    usable = [l for l in lines if _is_valid_proxy(l)]
    if not usable:
        return False, 0, "القائمة نزلت بس مافيهاش بروكسي صالحة"

    # Keep the newest copy in place (all.txt); sampling keeps the probe fast.
    if len(usable) > max_proxies:
        step = len(usable) / max_proxies
        usable = [usable[int(i * step)] for i in range(max_proxies)]
    return True, len(usable), f"{len(usable)} بروكسي صالح من أصل {sum(1 for l in lines if l.strip())}"


def load_list(fallback_limit: int = MAX_PROXIES, scheme: str | None = None) -> list[str]:
    """Read the freshly refreshed/cached list for use in rotation pools.
    Empty list -> the caller keeps the user's manual proxies untouched."""
    path = _feed_path()
    if not path.exists():
        return []
    lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="ignore").splitlines()]
    pool = [l for l in lines if _is_valid_proxy(l)]
    if scheme:
        pool = [l for l in pool if l.startswith(scheme + "://")]
    if len(pool) > fallback_limit:
        step = len(pool) / fallback_limit
        pool = [pool[int(i * step)] for i in range(fallback_limit)]
    return pool
