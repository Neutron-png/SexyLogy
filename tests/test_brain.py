"""Tests for the intelligence layer: brain.py (identity ledger, AIMD,
WAF breaker, cache, pacer), sitemap.py discovery and pow_solver.py."""

import re
import statistics
import time

from app.core.engine import brain as brain_mod
from app.core.engine import sitemap as sitemap_mod
from app.core.engine import pow_solver

# column order per table, matching Brain's INSERT statements (and SELECT *)
_TABLE_COLS = {
    "identity_stats": ["key", "successes", "blocks", "avg_latency", "blocked_until", "persona", "last_used"],
    "domain_stats": ["host", "delay_ms", "consecutive_blocks", "log_odds", "disabled_until"],
    "sticky_identities": ["host", "identity_key"],
    "response_cache": ["url", "etag", "last_modified", "body", "fetched_at"],
}
_KEY_COL = {"identity_stats": "key", "domain_stats": "host",
            "sticky_identities": "host", "response_cache": "url"}


class _FakeCursor:
    """Minimal sqlite3 cursor stand-in: in-memory upsert/select/update on
    dict rows, driven by the real column names parsed out of Brain's SQL."""

    def __init__(self, store):
        self.store = store

    def executescript(self, sql):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.lower().split())
        if s.startswith("select"):
            m = re.search(r"from\s+(\w+)", s)
            table = m.group(1)
            rows = self.store.get(table, [])
            if "where" in s:
                key_col = _KEY_COL[table]
                rows = [r for r in rows if r[key_col] == params[0]]
            return _FakeResult([dict(r) for r in rows])
        if s.startswith("insert"):
            table = s.split("insert into")[1].split("(")[0].strip()
            cols = sql.split("(", 2)[1].split(")")[0].split(",")
            cols = [c.strip() for c in cols]
            row = dict(zip(cols, params))
            rows = self.store.setdefault(table, [])
            key_col = _KEY_COL[table]
            rows[:] = [r for r in rows if r[key_col] != row[key_col]]
            rows.append(row)
            return _FakeResult([])
        if s.startswith("update"):
            table = s.split("update")[1].split("set")[0].strip()
            m = re.search(r"set\s+(.+?)\s+where\s+(\w+)\s*=\s*\?", s)
            set_col, where_col = m.group(1).split("=")[0].strip(), m.group(2)
            rows = self.store.setdefault(table, [])
            for r in rows:
                if r[where_col] == params[1]:
                    r[set_col] = params[0]
            return _FakeResult([])
        raise AssertionError(f"unsupported SQL in test fake: {sql}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self):
        self.store = {}

    def cursor(self):
        return _FakeCursor(self.store)


def _fresh_brain():
    return brain_mod.Brain(_FakeDB())


# ---------------- identity ledger ----------------

def test_block_prob_laplace_smoothing():
    b = _fresh_brain()
    ident = b.identity("p1")
    assert ident.block_prob == 0.5       # (0+1)/(0+0+2): neutral Laplace prior
    ident.successes = 98
    ident.blocks = 2
    assert ident.block_prob < 0.05


def test_pick_skips_cooled_identities():
    b = _fresh_brain()
    for key in ("p1", "p2", "p3"):
        b.identity(key)
    b.identity("p1").blocked_until = time.time() + 999
    b.identity("p2").blocked_until = time.time() + 999
    # p3 is the only free one → must be picked every time
    assert all(b.pick(["p1", "p2", "p3"], "x.com") == "p3" for _ in range(30))


def test_pick_when_all_cooled_returns_soonest():
    b = _fresh_brain()
    b.identity("p1").blocked_until = time.time() + 100
    b.identity("p2").blocked_until = time.time() + 5
    assert b.pick(["p1", "p2"], "x.com") == "p2"


def test_record_applies_cooldown_not_discard():
    b = _fresh_brain()
    before = time.time()
    b.record("p1", "x.com", blocked=True)
    ident = b.identity("p1")
    assert ident.blocks == 1
    assert ident.blocked_until >= before + brain_mod.Brain.COOLDOWN_S - 1


def test_weighted_pick_favors_healthy_identity():
    b = _fresh_brain()
    b.identity("good").successes = 100
    b.identity("good").blocks = 1
    b.identity("bad").successes = 10
    b.identity("bad").blocks = 40
    picks = [b.pick(["good", "bad"], "x.com") for _ in range(200)]
    good_share = picks.count("good") / 200
    assert good_share > 0.75


def test_sticky_identity_reused_per_domain():
    b = _fresh_brain()
    for i in range(3):
        b.record(f"p{i}", "yelp.com", blocked=False)
    # sticky is set to the LAST successful identity; most picks reuse it
    picks = [b.pick(["p0", "p1", "p2"], "yelp.com") for _ in range(100)]
    most_common = max(set(picks), key=picks.count)
    assert picks.count(most_common) >= brain_mod.Brain.STICKINESS * 100 - 5


def test_persona_deterministic_per_identity():
    p1a = brain_mod.persona_for("http://p1:8080")
    p1b = brain_mod.persona_for("http://p1:8080")
    p2 = brain_mod.persona_for("http://p2:8080")
    assert p1a == p1b
    assert p1a["useragent"] and p1a["locale"] and p1a["timezone_id"]
    assert len(brain_mod._PERSONAS) > 1  # personas actually vary


# ---------------- domain policy: AIMD + WAF breaker ----------------

def test_aimd_speeds_up_on_success_and_slows_on_block():
    b = _fresh_brain()
    start = b.domain("x.com").delay_ms
    b.record("p1", "x.com", blocked=False)
    after_ok = b.domain("x.com").delay_ms
    assert after_ok < start                    # ×0.9
    b.record("p1", "x.com", blocked=True)
    after_block = b.domain("x.com").delay_ms
    assert after_block >= after_ok * 2 - 0.01  # ×2 (exact when not clamped)
    assert after_block <= brain_mod.Brain.AIMD_MAX_MS


def test_aimd_clamped_to_bounds():
    b = _fresh_brain()
    for _ in range(50):
        b.record("p1", "x.com", blocked=True)
    assert b.domain("x.com").delay_ms == brain_mod.Brain.AIMD_MAX_MS
    b2 = _fresh_brain()
    for _ in range(200):
        b2.record("p1", "x.com", blocked=False)
    assert b2.domain("x.com").delay_ms >= brain_mod.Brain.AIMD_MIN_MS - 0.01


def test_waf_state_rises_on_blocks_and_trips_breaker():
    b = _fresh_brain()
    assert b.gate("x.com/page")
    for _ in range(6):
        b.record("p1", "x.com", blocked=True)
    dom = b.domain("x.com")
    assert dom.red_prob > brain_mod.Brain.BREAKER_THRESHOLD
    assert not b.gate("x.com/page")            # parked


def test_success_decays_waf_state():
    b = _fresh_brain()
    for _ in range(3):
        b.record("p1", "x.com", blocked=True)
    high = b.domain("x.com").log_odds
    for _ in range(10):
        b.record("p1", "x.com", blocked=False)
    assert b.domain("x.com").log_odds < high


def test_latency_spike_is_a_yellow_signal():
    b = _fresh_brain()
    for _ in range(6):
        b.record("p1", "x.com", blocked=False, latency_s=0.5)
    calm = b.domain("x.com").log_odds
    b.record("p1", "x.com", blocked=False, latency_s=5.0)  # 10× median → spike
    assert b.domain("x.com").log_odds > calm


def test_brain_survives_reload_from_db():
    db = _FakeDB()
    b1 = brain_mod.Brain(db)
    b1.record("p1", "x.com", blocked=False, latency_s=0.4)
    b1.record("p1", "x.com", blocked=True)
    b2 = brain_mod.Brain(db)                   # "next job" re-opens the brain
    ident = b2.identity("p1")
    assert ident.successes == 1 and ident.blocks == 1   # memory inherited


# ---------------- response cache ----------------

def test_cache_roundtrip_and_fresh_hit():
    b = _fresh_brain()
    b.cache_put("http://x.com/a", "<html>a</html>", etag='"v1"')
    hit = b.cache_get("http://x.com/a", ttl_s=600)
    assert hit is not None
    html, cond = hit
    assert html == "<html>a</html>"
    assert cond == {}                          # fresh → no conditional headers


def test_cache_stale_returns_validators():
    b = _fresh_brain()
    b.cache_put("http://x.com/a", "<html>a</html>", etag='"v1"', last_modified="Tue, 01 Jan 2026 00:00:00 GMT")
    # backdate the entry
    with b.db.cursor() as cur:
        cur.execute("UPDATE response_cache SET fetched_at=? WHERE url=?", (time.time() - 7200, "http://x.com/a"))
    hit = b.cache_get("http://x.com/a", ttl_s=600)
    assert hit is not None
    html, cond = hit
    assert html == "<html>a</html>"
    assert cond.get("If-None-Match") == '"v1"'
    assert cond.get("If-Modified-Since") == "Tue, 01 Jan 2026 00:00:00 GMT"


def test_cache_miss_returns_none():
    assert _fresh_brain().cache_get("http://x.com/none", 600) is None


# ---------------- burst pacer ----------------

def test_pacer_sample_positive_and_bounded():
    p = brain_mod.BurstPacer()
    gaps = [p.sample(floor_s=0.0) for _ in range(50)]
    assert all(0 < g <= p.max_delay_s + 1 for g in gaps)
    assert len(set(gaps)) > 10                 # not a constant


def test_pacer_respects_floor():
    p = brain_mod.BurstPacer()
    assert all(p.sample(floor_s=2.0) >= 2.0 for _ in range(20))


def test_pacer_bursts_shorter_than_quiet():
    # warmup then compare regimes: a Poisson cluster process spends most
    # warm samples inside bursts (~1s gaps) vs quiet-stretch gaps (~20s)
    p = brain_mod.BurstPacer()
    for _ in range(10):
        p.sample()
    gaps = [p.sample() for _ in range(60)]
    bursty = sorted(gaps)[: len(gaps) // 2]
    quiet = sorted(gaps)[len(gaps) // 2 :]
    assert statistics.median(bursty) < statistics.median(quiet) / 3


# ---------------- sitemap discovery ----------------

_SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex><sitemap><loc>http://x.com/sitemap_pages.xml</loc></sitemap></sitemapindex>"""
_SITEMAP_PAGES = """<?xml version="1.0"?>
<urlset>
 <url><loc>http://x.com/biz/alpha</loc></url>
 <url><loc>http://x.com/biz/beta</loc></url>
 <url><loc>http://other.com/biz/gamma</loc></url>
</urlset>"""


def test_parse_sitemap_splits_urls_and_children():
    urls, children = sitemap_mod.parse_sitemap(_SITEMAP_INDEX)
    assert children == ["http://x.com/sitemap_pages.xml"] and urls == []
    urls, children = sitemap_mod.parse_sitemap(_SITEMAP_PAGES)
    assert "http://x.com/biz/alpha" in urls and not children


def test_discovery_filters_foreign_domains_and_patterns():
    bodies = {"http://x.com/sitemap.xml": _SITEMAP_INDEX,
              "http://x.com/sitemap_pages.xml": _SITEMAP_PAGES}
    found = sitemap_mod.discover_sitemap_urls(
        ["http://x.com/"], lambda u: bodies.get(u),
        include_patterns=["*/biz/*"], max_urls=50)
    assert found == ["http://x.com/biz/alpha", "http://x.com/biz/beta"]
    none = sitemap_mod.discover_sitemap_urls(
        ["http://x.com/"], lambda u: bodies.get(u),
        exclude_patterns=["*alpha*"], max_urls=50)
    assert "http://x.com/biz/alpha" not in none


# ---------------- pow solver ----------------

def test_solve_hash_prefix_challenge():
    challenge = "abcdef0123456789" * 4
    difficulty = 12                            # 12 zero bits = 3 hex zeros
    # verify our own solution semantics
    sol = pow_solver.solve(challenge, difficulty, max_attempts=1 << 22)
    assert sol is not None
    import hashlib
    d = hashlib.sha256(f"{challenge}{sol.nonce}".encode()).digest()
    assert d[:difficulty // 8] == b"\x00" * (difficulty // 8)


def test_detection_needs_markers_and_params():
    real = ('<html><body>Making sure you\'re not a bot'
            '<script>var challenge="deadbeefcafebabe"; var difficulty="4";</script></body></html>')
    assert pow_solver.looks_like_pow_wall(real)
    assert not pow_solver.looks_like_pow_wall("<html>ordinary page</html>")
    huge = "x" * 300_000 + "anubis challenge=\"ff\" difficulty=\"4\""
    assert not pow_solver.looks_like_pow_wall(huge)   # real pages aren't tiny


def test_handle_pow_wall_end_to_end():
    challenge = "cafebabe" * 6
    page = (f'<html>Making sure you\'re not a bot'
            f'<script>const challenge="{challenge}", difficulty="8";</script></html>')
    sol = pow_solver.handle_pow_wall(page)
    assert sol is not None and sol.challenge == challenge and sol.difficulty == 8
