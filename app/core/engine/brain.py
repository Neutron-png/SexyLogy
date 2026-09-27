"""
The Brain: LOGY's cross-run learning memory for identity rotation and
per-domain pacing. This is the module that makes the crawler *learn*:

- IdentityLedger  — per-identity Bayesian reputation (Beta-Bernoulli
  posterior on "this identity gets blocked"), cooldown timers instead of
  discard-on-block, per-domain sticky identities (a human doesn't change
  IP every 3 seconds on one site), and a deterministic per-identity
  persona (UA/locale/timezone) so an IP never ships with a mismatched
  fingerprint.
- DomainPolicy    — per-domain AIMD pacing (TCP-congestion-style speed
  exploration), a WAF-pressure state estimate in log-odds space built
  from cheap signals (latency spikes, 429s, challenges), and a circuit
  breaker that PARKS a hardened domain before the official 403 lands.

All state survives across jobs in logy.db (identity_stats / domain_stats /
sticky_identities / response_cache tables) — run N learns from run N-1.

Everything here is deliberately dependency-free (no Qt, no the fetch engine) and
single-threaded-by-convention: the scrape worker is the only caller, so
no locks are needed beyond sqlite's own serialization (Database already
serializes access with an RLock).
"""
from __future__ import annotations

import hashlib
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------- personas
# Small pool of real, modern, internally-consistent browser profiles. A
# persona is picked DETERMINISTICALLY from the identity key's hash, so the
# same identity always presents the same fingerprint (a new IP with the
# same old fingerprint is a red flag; a stable IP+persona pair reads as
# one traveling human).
_PERSONAS = [
    {"useragent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "locale": "en-US", "timezone_id": "America/New_York"},
    {"useragent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "locale": "en-GB", "timezone_id": "Europe/London"},
    {"useragent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "locale": "en-US", "timezone_id": "America/Chicago"},
    {"useragent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "locale": "de-DE", "timezone_id": "Europe/Berlin"},
    {"useragent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0", "locale": "fr-FR", "timezone_id": "Europe/Paris"},
    {"useragent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", "locale": "en-CA", "timezone_id": "America/Toronto"},
    {"useragent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36", "locale": "es-ES", "timezone_id": "Europe/Madrid"},
    {"useragent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36", "locale": "en-AU", "timezone_id": "Australia/Sydney"},
]


def persona_for(key: str) -> dict:
    """Deterministic persona for an identity key — same key, same persona,
    forever (that's the whole point)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return dict(_PERSONAS[digest[0] % len(_PERSONAS)])


def host_of(url: str) -> str:
    """netloc of a URL — schemeless hosts ('x.com/page') get an implicit
    scheme so urlparse's netloc parsing works."""
    if "://" not in url:
        url = "http://" + url
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return url


# ---------------------------------------------------------------- records
@dataclass
class IdentityStat:
    key: str
    successes: int = 0
    blocks: int = 0
    avg_latency: float = 0.0
    blocked_until: float = 0.0        # cooldown expiry (epoch s); 0 = free
    persona: dict = field(default_factory=dict)
    last_used: float = 0.0

    @property
    def block_prob(self) -> float:
        """Laplace-smoothed Beta posterior mean on P(block | this identity).
        The +1/+2 prior says 'unknown identities start neutral, slightly
        suspect' — a fresh identity with zero history isn't trusted at 100%."""
        return (self.blocks + 1) / (self.successes + self.blocks + 2)

    @property
    def free(self) -> bool:
        return time.time() >= self.blocked_until


@dataclass
class DomainStat:
    host: str
    delay_ms: float = 0.0             # AIMD state — the learned working rate
    consecutive_blocks: int = 0
    log_odds: float = -2.0            # WAF-pressure state; -2 ≈ P(red) ~12%
    disabled_until: float = 0.0       # circuit breaker expiry (epoch s)
    latencies: deque = field(default_factory=lambda: deque(maxlen=12))
    # Per-job selector-health signal (audit §4): consecutive fetched pages
    # that parsed fine but produced zero records. NOT persisted - it's a
    # within-job degradation detector, reset naturally per Brain instance.
    consecutive_zero_pages: int = 0

    @property
    def red_prob(self) -> float:
        return 1.0 / (1.0 + math.exp(-self.log_odds))


# ---------------------------------------------------------------- brain
class Brain:
    """Owns all learned state. Construct one per job from the Database;
    stats persist in logy.db so later jobs inherit everything learned."""

    # --- tuning constants (kept explicit, they ARE the algorithm knobs) ---
    COOLDOWN_S = 300.0                 # block → 5 min on the bench, not discarded
    STICKINESS = 0.7                   # P(reuse the domain's known-good identity)
    AIMD_DOWN = 0.9                    # success → multiply delay (speed up)
    AIMD_UP = 2.0                      # block → multiply delay (slow down)
    AIMD_MIN_MS, AIMD_MAX_MS = 250.0, 30_000.0
    AIMD_START_MS = 1_500.0
    LOGIT_YELLOW = 0.7                 # soft signal (429/challenge/spike)
    LOGIT_RED = 1.2                    # hard signal (403/blocked)
    LOGIT_GREEN = -0.35                # clean success decay
    BREAKER_THRESHOLD = 0.8            # P(red) above this → park the domain
    BREAKER_PARK_S = 1200.0            # 20 min cool-off
    LATENCY_SPIKE_X = 2.5              # latency > 2.5× recent median = yellow

    _TABLES = """
    CREATE TABLE IF NOT EXISTS identity_stats (
        key TEXT PRIMARY KEY, successes INTEGER DEFAULT 0, blocks INTEGER DEFAULT 0,
        avg_latency REAL DEFAULT 0, blocked_until REAL DEFAULT 0,
        persona TEXT DEFAULT '', last_used REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS domain_stats (
        host TEXT PRIMARY KEY, delay_ms REAL DEFAULT 0, consecutive_blocks INTEGER DEFAULT 0,
        log_odds REAL DEFAULT -2, disabled_until REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sticky_identities (
        host TEXT PRIMARY KEY, identity_key TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS response_cache (
        url TEXT PRIMARY KEY, etag TEXT DEFAULT '', last_modified TEXT DEFAULT '',
        body TEXT DEFAULT '', fetched_at REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS identity_domain_stats (
        key TEXT NOT NULL, host TEXT NOT NULL,
        successes INTEGER DEFAULT 0, blocks INTEGER DEFAULT 0,
        PRIMARY KEY (key, host));
    """

    # Blocks are a RELATIONSHIP between an identity and a target, not a
    # property of the identity alone (audit H4): yelp burning a proxy says
    # nothing about that proxy on yellowpages. Pair stats drive per-domain
    # selection weights; the identity-level aggregate stays for cooldowns.
    CACHE_MAX_BODY_BYTES = 256 * 1024   # skip caching huge pages entirely
    CACHE_MAX_ROWS = 20_000
    CACHE_PRUNE_TO = 15_000
    CACHE_PRUNE_CHECK_EVERY = 512

    def __init__(self, db):
        """db: app.core.storage.db.Database (thread-safe via its RLock)."""
        self.db = db
        with db.cursor() as cur:
            cur.executescript(self._TABLES)
        self._identities: dict[str, IdentityStat] = {}
        self._domains: dict[str, DomainStat] = {}
        self._sticky: dict[str, str] = {}
        self._pairs: dict[tuple[str, str], dict[str, int]] = {}
        self._cache_puts = 0
        self._load()

    # ---------------- persistence ----------------
    def _load(self):
        with self.db.cursor() as cur:
            for row in cur.execute("SELECT * FROM identity_stats").fetchall():
                stat = IdentityStat(
                    key=row["key"], successes=row["successes"], blocks=row["blocks"],
                    avg_latency=row["avg_latency"], blocked_until=row["blocked_until"],
                    persona={}, last_used=row["last_used"])
                stat.persona = persona_for(stat.key)
                self._identities[stat.key] = stat
            for row in cur.execute("SELECT * FROM domain_stats").fetchall():
                stat = DomainStat(host=row["host"], delay_ms=row["delay_ms"],
                                  consecutive_blocks=row["consecutive_blocks"],
                                  log_odds=row["log_odds"], disabled_until=row["disabled_until"])
                self._domains[stat.host] = stat
            for row in cur.execute("SELECT * FROM sticky_identities").fetchall():
                self._sticky[row["host"]] = row["identity_key"]
            for row in cur.execute("SELECT * FROM identity_domain_stats").fetchall():
                self._pairs[(row["key"], row["host"])] = {
                    "successes": row["successes"], "blocks": row["blocks"]}
        # Inherited AIMD decay (audit M: a domain that blocked us days ago
        # must not open the new job at its full 30s learned delay). Clamp
        # the persisted delay to a bounded warm start; live AIMD continues
        # from there within this job.
        for dom in self._domains.values():
            dom.delay_ms = min(dom.delay_ms, self.AIMD_START_MS * 4)

    def _save_identity(self, s: IdentityStat):
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO identity_stats(key, successes, blocks, avg_latency, blocked_until, persona, last_used) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET successes=?, blocks=?, avg_latency=?, "
                "blocked_until=?, persona=?, last_used=?",
                (s.key, s.successes, s.blocks, s.avg_latency, s.blocked_until, "1", s.last_used,
                 s.successes, s.blocks, s.avg_latency, s.blocked_until, "1", s.last_used))

    def _save_domain(self, s: DomainStat):
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO domain_stats(host, delay_ms, consecutive_blocks, log_odds, disabled_until) "
                "VALUES(?,?,?,?,?) ON CONFLICT(host) DO UPDATE SET delay_ms=?, consecutive_blocks=?, "
                "log_odds=?, disabled_until=?",
                (s.host, s.delay_ms, s.consecutive_blocks, s.log_odds, s.disabled_until,
                 s.delay_ms, s.consecutive_blocks, s.log_odds, s.disabled_until))

    def _save_sticky(self, host: str, key: str):
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO sticky_identities(host, identity_key) VALUES(?,?) "
                "ON CONFLICT(host) DO UPDATE SET identity_key=?",
                (host, key, key))

    def _pair(self, key: str, host: str) -> dict[str, int]:
        return self._pairs.setdefault((key, host), {"successes": 0, "blocks": 0})

    def pair_block_prob(self, key: str, host: str) -> float:
        """Laplace-smoothed P(block) for THIS identity on THIS domain. Falls
        back to the identity-level posterior when the pair has no evidence
        yet - so a fresh domain doesn't reset a known-good identity's trust."""
        pair = self._pairs.get((key, host))
        if pair and (pair["successes"] + pair["blocks"]) > 0:
            return (pair["blocks"] + 1) / (pair["successes"] + pair["blocks"] + 2)
        return self.identity(key).block_prob

    def _save_pair(self, key: str, host: str, pair: dict[str, int]):
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO identity_domain_stats(key, host, successes, blocks) VALUES(?,?,?,?) "
                "ON CONFLICT(key, host) DO UPDATE SET successes=?, blocks=?",
                (key, host, pair["successes"], pair["blocks"],
                 pair["successes"], pair["blocks"]))

    # ---------------- identity selection ----------------
    def identity(self, key: str) -> IdentityStat:
        stat = self._identities.get(key)
        if stat is None:
            stat = IdentityStat(key=key, persona=persona_for(key))
            self._identities[key] = stat
        return stat

    def domain(self, host: str) -> DomainStat:
        stat = self._domains.get(host)
        if stat is None:
            stat = DomainStat(host=host, delay_ms=self.AIMD_START_MS)
            self._domains[stat.host] = stat
        return stat

    def pick(self, pool: list[str], host: str, mode: str = "sticky") -> str:
        """Weighted, cooldown-aware, sticky-aware identity choice.

        Three selection modes (see ScrapeOptions.identity_selection):
        - "sticky"   (default): with probability STICKINESS reuse the
          domain's known-good identity, otherwise posterior-weighted
          sampling. The human-shaped default.
        - "weighted": pure posterior-weighted sampling, no stickiness.
        - "ucb1":     optimistic selection — UCB1 from the bandit
          literature (Auer et al. 2002). score(i) = mean_i +
          sqrt(2 ln T / n_i): uncertain identities get an exploration
          bonus that decays logarithmically as evidence accumulates.
          Provably optimal up to constants against stationary targets.

        - cooled identities (blocked_until in the future) are excluded in
          every mode; if EVERYTHING is cooled we take the one whose bench
          time ends first rather than pick nothing."""
        if not pool:
            raise ValueError("empty identity pool")
        eligible = [k for k in pool if self.identity(k).free]
        if not eligible:
            # all on the bench: ride with the soonest-to-free identity
            eligible = sorted(pool, key=lambda k: self.identity(k).blocked_until)
            return eligible[0]

        if mode == "ucb1":
            return self._pick_ucb1(eligible)

        if mode != "sticky":
            weights = [max(0.05, 1.0 - self.pair_block_prob(k, host)) for k in eligible]
            return random.choices(eligible, weights=weights, k=1)[0]

        sticky_key = self._sticky.get(host)
        if sticky_key and sticky_key in eligible and random.random() < self.STICKINESS:
            return sticky_key

        weights = [max(0.05, 1.0 - self.pair_block_prob(k, host)) for k in eligible]
        return random.choices(eligible, weights=weights, k=1)[0]

    def _pick_ucb1(self, eligible: list[str]) -> str:
        """UCB1 (Auer et al. 2002). score(i) = mean_i + sqrt(2 ln T / n_i)
        — the empirical mean (exploitation) plus an optimism bonus that
        rewards uncertainty (exploration) and decays as ln(T)/n_i.
        Untried identities (n_i = 0) are played first, per the algorithm's
        initialization phase — an unmeasured arm is the biggest unknown."""
        plays = {k: (self.identity(k).successes + self.identity(k).blocks) for k in eligible}
        untried = [k for k in eligible if plays[k] == 0]
        if untried:
            return random.choice(untried)
        total = sum(plays.values())

        def score(k: str) -> float:
            s = self.identity(k)
            mean = s.successes / (s.successes + s.blocks)
            return mean + math.sqrt(2.0 * math.log(total) / plays[k])

        return max(eligible, key=score)

    # ---------------- outcome recording ----------------
    def record(self, key: str, host: str, blocked: bool, latency_s: float = 0.0):
        """THE learning step. One call updates: the identity's aggregate
        Beta posterior + cooldown, the per-(identity, domain) pair stats,
        the sticky map, and — via _pressure() — the domain's WAF state,
        AIMD delay and circuit breaker."""
        ident = self.identity(key)
        now = time.time()
        ident.last_used = now
        if blocked:
            ident.blocks += 1
            ident.blocked_until = now + self.COOLDOWN_S
        else:
            ident.successes += 1
            if latency_s > 0:
                ident.avg_latency = (ident.avg_latency * (ident.successes - 1) + latency_s) / ident.successes
        self._save_identity(ident)

        pair = self._pair(key, host)
        if blocked:
            pair["blocks"] += 1
        else:
            pair["successes"] += 1
        self._save_pair(key, host, pair)

        if not blocked:
            self._sticky[host] = key
            self._save_sticky(host, key)
        self._pressure(host, blocked, latency_s)

    # ---------------- per-domain pacing & WAF state ----------------
    def _pressure(self, host: str, blocked: bool, latency_s: float):
        dom = self.domain(host)
        if latency_s > 0:
            dom.latencies.append(latency_s)

        if blocked:
            dom.consecutive_blocks += 1
            dom.delay_ms = min(dom.delay_ms * self.AIMD_UP, self.AIMD_MAX_MS)
            dom.log_odds = min(dom.log_odds + self.LOGIT_RED, 8.0)
        else:
            dom.consecutive_blocks = 0
            dom.delay_ms = max(dom.delay_ms * self.AIMD_DOWN, self.AIMD_MIN_MS)
            spike = (len(dom.latencies) >= 4
                     and latency_s > self.LATENCY_SPIKE_X * _median(list(dom.latencies)[:-1]))
            dom.log_odds = max(dom.log_odds + (self.LOGIT_YELLOW if spike else self.LOGIT_GREEN), -4.0)

        if dom.red_prob > self.BREAKER_THRESHOLD:
            dom.disabled_until = time.time() + self.BREAKER_PARK_S
        self._save_domain(dom)

    def gate(self, url_or_host: str) -> bool:
        """Circuit breaker: False while the domain is parked (P(red) got too
        high). The job loop requeues parked URLs instead of hammering."""
        host = host_of(url_or_host) if "/" in url_or_host else url_or_host
        return time.time() >= self.domain(host).disabled_until

    def delay_s(self, host: str) -> float:
        """Current AIMD delay for the domain, in seconds."""
        return self.domain(host).delay_ms / 1000.0

    def record_page_outcome(self, host: str, had_records: bool) -> int:
        """Selector-health tracking (audit §4): distinguishes 'the market is
        empty' (a zero here and there) from 'the extractor is broken'
        (consecutive zero-record pages on one source). Returns the current
        consecutive-zero count for the host."""
        dom = self.domain(host)
        dom.consecutive_zero_pages = 0 if had_records else dom.consecutive_zero_pages + 1
        return dom.consecutive_zero_pages

    def unlock(self, host: str):
        dom = self.domain(host)
        dom.disabled_until = 0.0
        dom.log_odds = -2.0
        self._save_domain(dom)

    # ---------------- response cache (conditional requests) ----------------
    def cache_get(self, url: str, ttl_s: float) -> Optional[tuple[str, dict]]:
        """Returns (html, conditional_headers) when a usable entry exists:
        - fresh within ttl → (html, {}) — no network needed at all
        - stale but has validators → (html, {If-None-Match/If-Modified-Since})
          so the caller can revalidate with a cheap 304 instead of a full GET
        - nothing → None"""
        with self.db.cursor() as cur:
            row = cur.execute("SELECT etag, last_modified, body, fetched_at FROM response_cache WHERE url=?", (url,)).fetchone()
        if not row or not row["body"]:
            return None
        headers = {}
        if row["etag"]:
            headers["If-None-Match"] = row["etag"]
        if row["last_modified"]:
            headers["If-Modified-Since"] = row["last_modified"]
        if time.time() - row["fetched_at"] < ttl_s:
            return row["body"], {}          # fresh: zero-request hit
        return row["body"], headers         # stale: revalidate

    def cache_put(self, url: str, html: str, etag: str = "", last_modified: str = ""):
        # Bounded cache (audit H3): full HTML bodies in SQLite grow the DB
        # by ~150KB/page - unbounded, a 10k-page run writes gigabytes.
        # Skip oversized bodies entirely, and periodically prune oldest
        # rows to keep the table capped.
        if len(html) > self.CACHE_MAX_BODY_BYTES:
            return
        self._cache_puts += 1
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO response_cache(url, etag, last_modified, body, fetched_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET etag=?, last_modified=?, body=?, fetched_at=?",
                (url, etag, last_modified, html, time.time(),
                 etag, last_modified, html, time.time()))
            if self._cache_puts % self.CACHE_PRUNE_CHECK_EVERY == 0:
                count = cur.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0]
                if count > self.CACHE_MAX_ROWS:
                    cur.execute(
                        "DELETE FROM response_cache WHERE url IN ("
                        "SELECT url FROM response_cache ORDER BY fetched_at ASC LIMIT ?)",
                        (count - self.CACHE_PRUNE_TO,),
                    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# ---------------------------------------------------------------- pacer
class BurstPacer:
    """Human-burst request shaping via a Poisson CLUSTER process - the
    classic model for bursty human traffic (page load → rapid follow-ups
    → silence). Uniform random sleeps produce statistically obvious
    inter-arrival gaps; this produces the two-regime shape real sessions
    have:

        quiet stretch: gaps ~ Exp(quiet_rate)   (seconds to tens of seconds)
        burst:         gaps ~ Exp(burst_rate)   (a second or two, back to back)

    with probabilistic transitions between the regimes. Related to the
    Hawkes self-exciting family (bursts = excitation, silence = decayed
    excitation) but as an explicit two-regime process it stays simple,
    inspectable and testable - no hidden clock state to desync.
    """

    def __init__(self, burst_rate: float = 0.8, quiet_rate: float = 0.05,
                 p_enter: float = 0.15, p_exit: float = 0.08,
                 max_delay_s: float = 90.0):
        # burst_rate  : events/sec inside a burst  (mean gap ~1.25s)
        # quiet_rate  : events/sec while browsing slowly (mean gap ~20s)
        self.burst_rate = burst_rate
        self.quiet_rate = quiet_rate
        self.p_enter, self.p_exit = p_enter, p_exit
        self.max_delay_s = max_delay_s
        self._in_burst = False

    def sample(self, floor_s: float = 0.0) -> float:
        """Next inter-arrival gap in seconds (≥ floor_s, ≤ max_delay_s)."""
        if self._in_burst:
            gap = random.expovariate(self.burst_rate)
            if random.random() < self.p_exit:
                self._in_burst = False    # the browsing spurt is over
        else:
            gap = random.expovariate(self.quiet_rate)
            if random.random() < self.p_enter:
                self._in_burst = True     # a new burst of page loads starts
        return max(min(gap, self.max_delay_s), floor_s)
