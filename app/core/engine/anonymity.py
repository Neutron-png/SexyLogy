"""
Anonymity helpers for LOGY's crawler - the only place that knows HOW to
rotate identities, so the job manager just calls one function per request.

What actually works against websites (and what doesn't):

- IP address: the ONLY network identity a website sees. Rotated by (a)
  cycling through a user-supplied proxy list, or (b) asking a local Tor
  daemon to switch circuits (SIGNAL NEWNYM -> new exit IP). Both live here.
- MAC address: a layer-2 identifier that never leaves the local network -
  the first router strips it. Websites CANNOT see it, so rotating it does
  nothing for web anonymity. Deliberately not implemented; don't fake it.
- Browser fingerprint (UA/TLS/WebRTC/canvas...): handled by Scrapling's
  fetchers themselves (chrome impersonation, stealth Chromium, block_webrtc
  when tunneling through Tor - see scrapling_adapter.py).
"""
from __future__ import annotations

import os
import socket
import threading
from typing import Optional

# Playwright-based browsers (Dynamic/Stealth) only accept http/https/socks4/
# socks5 proxy schemes - "socks5h" raises ValueError in Scrapling's
# construct_proxy_dict(). curl-cffi (FAST_HTTP) on the other hand supports
# socks5h natively, and the trailing "h" matters there: it forces DNS
# resolution through the proxy, so the machine's own DNS resolver never
# sees the target's hostname. Two schemes, per fetcher type, on purpose.
TOR_SOCKS_BROWSER = "socks5://{host}:{port}"    # browser engines (scheme whitelist above)
TOR_SOCKS_HTTP = "socks5h://{host}:{port}"      # curl-cffi (remote DNS)

# Places Tor's control_auth_cookie file is commonly found on Windows.
# Tor Browser install: .../Tor Browser/Browser/TorBrowser/Data/Tor/
# Expert Bundle: Program Files or %APPDATA%\Tor
_COOKIE_CANDIDATES = (
    os.path.join(os.environ.get("APPDATA", ""), "Tor Browser", "Browser", "TorBrowser", "Data", "Tor", "control_auth_cookie"),
    os.path.join(os.environ.get("APPDATA", ""), "tor", "control_auth_cookie"),
    r"C:\Program Files\Tor\control_auth_cookie",
    r"C:\Program Files (x86)\Tor\control_auth_cookie",
    "/usr/local/etc/tor/control_auth_cookie",  # in case LOGY runs on POSIX
    "/var/run/tor/control.authcookie",
)


# Exit nodes are PUBLICLY LISTED - that's how sites "block Tor": they match
# the exit IP against published lists, no fingerprinting involved. The ways
# that actually get through:
#   1. rotate the circuit IMMEDIATELY on a block (new exit, maybe not blocked)
#   2. hybrid mode: mix regular proxies with Tor, so a blocked identity is
#      followed by a non-Tor one
#   3. pin ExitNodes in torrc to countries/ASNs the target doesn't block
# (see rotate_tor_circuit / CyclicProxyRotator / ScrapeJobWorker._prepare_proxy)

# HTTP responses/messages that mean "this identity is blocked/walled off"
# rather than "the URL is broken" - retrying the SAME identity is wasted
# work; rotate first, then retry.
BLOCK_MARKERS = (
    "http 403",
    "http 429",
    "http 503",
    "cloudflare",
    "captcha",
    "access denied",
    "forbidden",
    "banned",
    "blocked",
)


def looks_like_block(reason: str) -> bool:
    """True when a fetch error reads like an anti-bot/identity block, not a
    dead link or network failure. Conservative on purpose - it drives
    identity rotation, and rotating on a genuinely dead URL just burns
    circuit for nothing."""
    if not reason:
        return False
    haystack = reason.lower()
    return any(marker in haystack for marker in BLOCK_MARKERS)


def tor_socks_url(socks_port: int, for_http: bool) -> str:
    """The proxy string to hand Scrapling for tunneling through Tor."""
    template = TOR_SOCKS_HTTP if for_http else TOR_SOCKS_BROWSER
    return template.format(host="127.0.0.1", port=int(socks_port))


def tor_reachable(socks_port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    """Cheap TCP probe: is something listening on Tor's SOCKS port? Does
    NOT verify it's really Tor - the fetch itself will fail loudly if not."""
    try:
        with socket.create_connection((host, int(socks_port)), timeout=timeout):
            return True
    except OSError:
        return False


def _control_cookie() -> Optional[bytes]:
    for path in _COOKIE_CANDIDATES:
        try:
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    return f.read().strip()
        except OSError:
            continue
    return None


def rotate_tor_circuit(control_port: int, password: str = "",
                       host: str = "127.0.0.1", timeout: float = 5.0) -> tuple[bool, str]:
    """Ask Tor to build a fresh circuit: SIGNAL NEWNYM. The next requests
    go out through a different exit node = different public IP.

    Auth order (Tor accepts at most one): CookieAuthentication file first,
    then a supplied control password, then unauthenticated (only works if
    the daemon was started with an empty password AND no cookie file).
    Returns (ok, human-readable reason) - never raises; circuit rotation is
    a best-effort bonus, a failure should never abort a scrape.
    """
    try:
        with socket.create_connection((host, int(control_port)), timeout=timeout) as s:
            s.settimeout(timeout)
            reader = s.makefile("rb")

            def _cmd(cmd: bytes) -> str:
                s.sendall(cmd + b"\r\n")
                return reader.readline().decode("latin-1", errors="replace").strip()

            cookie = _control_cookie()
            if cookie:
                resp = _cmd(b"AUTHENTICATE " + cookie.hex().encode())
                if not resp.startswith("250"):
                    resp = _cmd(b'AUTHENTICATE "%s"' % password.encode()) if password else "5xx no-auth-fallback"
                    if not resp.startswith("250"):
                        return False, "Tor رفض الـ authentication (cookie + password)"
            elif password:
                resp = _cmd(b'AUTHENTICATE "%s"' % password.encode())
                if not resp.startswith("250"):
                    return False, "Tor رفض الـ control password"
            else:
                resp = _cmd(b"AUTHENTICATE")
                if not resp.startswith("250"):
                    return False, "Tor محتاج authentication (cookie/password) والملف مش موجود"

            resp = _cmd(b"SIGNAL NEWNYM")
            if resp.startswith("250"):
                return True, "تم تدوير دائرة Tor (IP جديد للريكويستات الجاية)"
            return False, f"Tor رفض SIGNAL NEWNYM: {resp}"
    except OSError as e:
        return False, f"مش قادر أوصل لمنفذ Tor control ({control_port}): {e}"


class CyclicProxyRotator:
    """Thread-safe round-robin over the user's proxy list. One instance per
    job. Job manager picks `next()` before EVERY request - this is the fix
    for the old behavior where 'Proxy list / Rotating' modes were collected
    from the UI but the adapter always silently used proxies[0] (same IP
    for the whole run, the exact thing the user thinks is 'rotating')."""

    def __init__(self, proxies: list[str]):
        if not proxies:
            raise ValueError("CyclicProxyRotator needs at least one proxy")
        self._proxies = list(proxies)
        self._idx = 0
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            proxy = self._proxies[self._idx % len(self._proxies)]
            self._idx += 1
            return proxy

    def __len__(self) -> int:
        return len(self._proxies)
