"""
EXPERIMENTAL: native JS proof-of-work wall handling (Anubis-style).

Some sites (Anubis is the popular one - used by GNOME, Sourcehut, ...) put
a tiny JS challenge in front of content: the browser must brute-force a
nonce such that sha256(challenge + nonce) starts with N zero bits, then
gets the real page via the resulting cookie. Currently NO mainstream open
source scraper solves these natively - everyone just opens a browser.

What lives here: DETECTION + the hash-puzzle SOLVER itself. Solving the
puzzle in pure Python is ~100x cheaper than launching Chromium. The full
handshake (which cookie the site expects afterwards) varies by deployment
and needs a live target to verify, so `handle_pow_wall()` is deliberately
conservative: detect + solve + report, never guess a cookie blind.

Status: solver logic is unit-tested against synthetic challenges; wiring
into real Anubis deployments is the remaining step.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Anubis markers (and generic PoW-ish markers). Checked case-insensitively.
_POW_MARKERS = (
    "making sure you're not a bot",
    "anubis",
    "proof of work",
    "calculating challenge",
)

_CHALLENGE_RE = re.compile(r"challenge\s*[=:]\s*['\"]([0-9a-f]{8,})['\"]", re.I)
_DIFFICULTY_RE = re.compile(r"difficulty\s*[=:]\s*['\"]?(\d+)['\"]?", re.I)
# Anubis v2-style rules also carry a rules hash some deployments verify.
_RULES_RE = re.compile(r"rules\s*[=:]\s*['\"]([0-9a-f]{8,})['\"]", re.I)


@dataclass
class PowSolution:
    challenge: str
    difficulty: int
    nonce: int
    digest: str
    rules: str = ""


def looks_like_pow_wall(html: str) -> bool:
    """Cheap, conservative detection. False positives only cost a parse
    pass; false negatives fall through to normal (browser) fetching."""
    if not html or len(html) > 200_000:   # PoW pages are tiny; real pages aren't
        return False
    haystack = html.lower()
    if not any(marker in haystack for marker in _POW_MARKERS):
        return False
    return bool(_CHALLENGE_RE.search(html) and _DIFFICULTY_RE.search(html))


def solve(challenge: str, difficulty: int, max_attempts: int = 1 << 24) -> PowSolution | None:
    """Brute-force the nonce: sha256(challenge + str(nonce)) must have
    `difficulty` leading zero BITS (Anubis semantics), i.e. the first
    ceil(difficulty/4) hex digits are zeros plus a partial nibble."""
    if difficulty < 1 or difficulty > 32:
        return None
    zero_bytes = difficulty // 8
    rem_bits = difficulty % 8
    prefix = b"\x00" * zero_bytes
    # partial-nibble threshold: the next byte must be < 2^(8-rem) when rem>0
    partial_cap = (1 << (8 - rem_bits)) - 1 if rem_bits else None

    for nonce in range(max_attempts):
        digest = hashlib.sha256(f"{challenge}{nonce}".encode()).digest()
        if digest[:zero_bytes] != prefix:
            continue
        if rem_bits and digest[zero_bytes] > partial_cap:
            continue
        return PowSolution(challenge=challenge, difficulty=difficulty,
                           nonce=nonce, digest=digest.hex())
    return None


def extract_challenge(html: str) -> tuple[str, int, str] | None:
    m_c, m_d, m_r = _CHALLENGE_RE.search(html), _DIFFICULTY_RE.search(html), _RULES_RE.search(html)
    if not (m_c and m_d):
        return None
    return m_c.group(1), int(m_d.group(1)), (m_r.group(1) if m_r else "")


def handle_pow_wall(html: str) -> PowSolution | None:
    """Detect + solve. Returns the solution (caller decides what to do with
    it) or None when this isn't a solvable PoW page."""
    if not looks_like_pow_wall(html):
        return None
    extracted = extract_challenge(html)
    if not extracted:
        return None
    challenge, difficulty, rules = extracted
    return solve(challenge, difficulty)
