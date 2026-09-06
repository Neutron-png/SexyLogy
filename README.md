# LOGY — Anonymous Lead-Generation Crawler

LOGY is a desktop (PySide6) lead-generation scraper built on [Scrapling](https://github.com/D4Vinci/Scrapling):
point it at business directories (Yelp, Yellowpages, Thumbtack…), pick a niche + city, and it crawls,
extracts, deduplicates and exports leads — now with a full **per-request identity-rotation engine**
so targets can't fingerprint a single IP.

---

## What's new: the Anonymity Engine

### The problem it solves

Two facts define web anonymity, and everything in this engine follows from them:

1. **Websites never see your MAC address.** It's a layer-2 identifier that dies at the first router.
   The *only* network identity a target can observe is your **IP address**. (This is why the engine
   rotates IPs and deliberately fakes nothing else.)
2. **Sites that "block Tor" are not detecting the browser.** Tor exit nodes are *publicly listed*;
   targets match your IP against those lists. Disguising the client as Chromium fixes the
   *fingerprint*, not the *gate*.

The historical bug this engine replaced: the UI collected a proxy list, the mode said "Rotating",
but the adapter read `proxies[0]` every time — **one IP for an entire 500-page run** (the direct
cause of the Yelp 403 wall: 91/91 requests failed).

### The pipeline

```
┌──────────────┐    ┌───────────────────┐    ┌──────────────────┐
│ REQUEST QUEUE│──▶ │ IDENTITY PICKER   │──▶ │ SCHEME NORMALIZER│──▶ TARGET SITE
└──────────────┘    └─────────┬─────────┘    └────────┬─────────┘
                              │ cyclic pool:          │ curl-cffi → socks5h:// (remote DNS)
                              │  proxy-1, proxy-2,    │ chromium  → socks5://  (pw whitelist)
                              │  tor@127.0.0.1:9050
                              ▼
                 ┌─────────────────────────────┐
                 │ BLOCKED: 403 / 429 / CF     │
                 └──────────────┬──────────────┘
                                ▼
                 INSTANT FAILOVER ── SIGNAL NEWNYM → control:9051 → new exit IP
                                └─ skip identity → retry from a different IP
```

### Proxy modes

| Mode | Rotation | Best for |
|---|---|---|
| No proxy | — | Trusted, robots-friendly targets |
| Single proxy | — | One dedicated exit, low-volume runs |
| **Proxy list** | Cyclic per request + skip-on-block | Paid pools, stable identity set |
| **Tor** | NEWNYM every N requests + on block | Full anonymity, tolerant targets |
| **Hybrid** ⭐ | Proxies + Tor in ONE pool; blocked identities skipped instantly | Sites that blanket-block Tor exits |

---

## The algorithms & the math

### 1. Round-Robin cyclic rotation — `anonymity.CyclicProxyRotator`

A pool of `n` identities and a monotonic counter `i`. The request at step `t` takes
entry `(i₀ + t) mod n`; the modulo wrap makes the list infinite.

```
identity(t)      = pool[(i₀ + t) mod n]
P(identity = pool_j) = 1/n          # perfectly uniform after every cycle
λ_identity        = λ_total / n     # per-identity request rate
```

Most WAFs decide on a *threshold*: "after m requests from one IP → block".
Single IP dies at `m`. Under uniform rotation, the capacity is:

```
T_block(single IP) = m
T_block(rotated)   ≈ n · m           # anti-ban capacity grows LINEARLY in pool size
```

### 2. Tor NEWNYM circuit rotation — `anonymity.rotate_tor_circuit()`

Tor routes traffic through a 3-node circuit (Guard → Middle → **Exit**); the target sees only the
Exit. `SIGNAL NEWNYM` on the control port (9051) orders the daemon to build fresh circuits:

```
old:  G₁ → M₁ → E₁   →  site sees IP(E₁)   # listed → blocked
new:  G₁ → M₂ → E₂   →  site sees IP(E₂)   # different exit, different IP
```

With `|E| ≈ 1000–1500` live exits:

```
P(new exit ≠ old exit) = 1 − 1/|E| ≈ 99.9%
```

Notes: the *Guard* stays stable for months (Tor's own policy) — but it's invisible to the target,
so it costs nothing. Auth order: `control_auth_cookie` first, control password as fallback,
unauthenticated last. **Strictly best-effort**: a failed rotation returns `(False, reason)` and
logs a warning — it never aborts a scrape.

### 3. Block classification — `anonymity.looks_like_block()`

A linear classifier over string markers:

```
is_block(reason) = ∃ m ∈ {403, 429, 503, cloudflare, captcha, forbidden, banned, blocked}
                    : m ⊑ lower(reason)          # complexity O(k·|s|)
```

**Conservative on purpose.** Costs are asymmetric: a false negative costs one wasted retry; a
false positive throws away a perfectly good identity and burns a Tor circuit for nothing.
Therefore 404 and "connection refused" are *deliberately not* markers.

### 4. Instant failover — `job_manager._rotate_identity_on_block()`

On a detected block the engine doesn't wait for the scheduled rotation window — it jumps the
counter ahead and pins the landing identity for the imminent retry (which runs *inside*
`_fetch_with_retries`, before the next scheduled rotation):

```
skip         = min(2, n − 1)
new_identity = pool[(i + skip) mod n]
```

**The invariant:** the new identity is never the blocked one —

```
(i + skip) mod n ≠ i   ⟺   skip mod n ≠ 0
```

which is guaranteed because `1 ≤ skip ≤ n−1`. Why `min(2, n−1)` *exactly*: the upper bound `n−1`
is forced by the invariant (jumping `n` would land back on the blocked entry); taking **2**
instead of 1 (when the pool allows) skips over the entry likely blocked in the previous wave too.
A drift guard re-advances if the pinned identity somehow equals the blocked one.

Retry success follows a **geometric distribution** — if a random identity is clean with
probability `q`:

```
P(success within k tries) = 1 − (1 − q)^k

q = 0.8 →  k=1: 80%   k=2: 96%   k=3: 99.2%
```

### 5. Hybrid pooling — the anti-"Tor blocked" mode

User proxies and the Tor endpoint merge into one pool. If a fraction `f` of the pool is blocked
(say, every exit on the target's list), a *double* failure requires two independent draws into
the blocked set:

```
P(single request hits blocked)  = f
P(retry ALSO hits blocked)      = f²

# f = 0.2  →  retry fails too only 4% of the time
#            (vs. 100% pre-failover on a tor-only run)
```

**The thesis:** independence between identities converts a *likely, repeated* failure into a
*rare, quadratic* one. Tor's exit IPs stop being a single point of failure.

### 6. Exponential backoff — `job_manager._interruptible_sleep()`

```
sleep(attempt) = min(2^attempt, 10) seconds    # 2s → 4s → 8s → 8s → 8s
```

Anti-"retry storm" profile. It also matters against **timing-pattern detection**: WAFs treat
deterministic machine-like gaps as a bot signal; the exponential curve plus identity rotation
scatter the timing beyond easy classification.

### Scorecard

| Scenario | Without the engine | With the engine |
|---|---|---|
| Requests before first block (threshold m per IP) | ≈ m | ≈ n·m |
| Max distinct exit IPs | 1 | 1 + (proxies) + ~1000 Tor exits |
| Retry success after a block | ≈ 0% (same IP) | 1 − (1−q)^k → 96% by k=2 |
| Consecutive-failure probability (f=0.2) | 20% | 4% |
| Timing-pattern bot detection | Trivial (uniform gaps) | Scattered by backoff + rotation |

### Honest limits

- **Behavioral fingerprinting is out of scope.** Targets like Yelp/Cloudflare also model mouse
  movement, request ordering and JS execution — rotation says nothing there; that's the stealth
  engine's job (Scrapling's Chromium stealth + `solve_cloudflare`).
- **Worst case:** a target that blocks every Tor exit *and* every datacenter range leaves only
  **residential proxies** — they lead the ops checklist below.
- **MAC rotation is not implemented on purpose** — it cannot help against websites.

### Engineering details worth knowing

- **Two proxy schemes, one Tor:** curl-cffi (FAST_HTTP) gets `socks5h://` — the trailing `h`
  resolves DNS *through* Tor, so the machine's resolver never sees the hostname. Playwright-based
  engines (Dynamic/Stealth) get `socks5://` — their proxy validator rejects `socks5h`.
  `_normalize_tor_scheme()` rewrites the scheme per engine, per request.
- **WebRTC leak blocked on Tor hops:** WebRTC STUN dials *around* SOCKS tunnels and leaks the
  real IP. `block_webrtc=True` is applied automatically — but only on requests actually
  tunneling through Tor (`_is_tor()`), not on plain-proxy hybrid requests.
- **Startup probe:** tor mode checks `127.0.0.1:9050` once and reports clearly if it's dead —
  instead of failing every request with the same confusing error.
- **Per-request pinning:** `_prepare_proxy()` writes the chosen identity into
  `options.proxy.proxies[0]` — the single field every engine and enrichment path reads, so one
  write covers the main crawl plus detail-page/qualifier/owner-lookup fetches.
- **Encrypted at rest:** proxy credentials are never written to logs or exports.

### Ops checklist

1. **Start Tor Browser (or `tor.exe`) first** — the SOCKS port is 9050, control 9051 by default.
2. **Residential > datacenter.** Datacenter ranges get flagged almost as fast as Tor exits.
3. **Keep `block_webrtc` on** for Tor hops (automatic).
4. **Pin your exits** — `torrc`: `ExitNodes {us},{de}` + `StrictNodes 1`, to choose exit
   countries the target doesn't blanket-block.

---

## Architecture

```
UI (PySide6)
 └─ JobManager (QThread)              app/core/job_manager.py
     ├─ anonymity.py                  # NEW: identity rotation, Tor control, block detection
     ├─ scrapling_adapter.py          # the ONLY module importing Scrapling
     ├─ extractor.py / ai_extractor.py
     ├─ dedupe.py                     # cross-job lead history
     └─ storage/db.py (sqlite3, thread-safe)
```

- **UI → Job Manager → Scraping Engine → Scrapling** boundary; the UI never touches Scrapling.
- Scrapling fetcher modes: `FAST_HTTP` (curl-cffi, TLS impersonation), `DYNAMIC` (Chromium),
  `STEALTH` (Chromium stealth, Cloudflare solving).
- Sources are pluggable profiles (`builtin_templates.py`) — per-site containers/fields/detail configs.
- AI Auto-Extract mode: no selectors; an LLM reads page text and fills field names
  (Anthropic/OpenAI). Owner-lookup enrichment reads a lead's own published site only —
  no automated LinkedIn people-search, by design.

## Run

```bat
run.bat          # Windows — points system Python 3.14 at the venv's site-packages
```

or

```bash
pip install scrapling && scrapling install
python main.py
```

Tests:

```bash
python tests/run_tests.py        # 102/103 passing (1 pre-existing qualifier fixture failure)
```

## Layout

```
app/
  core/
    engine/        # anonymity.py, scrapling_adapter.py, extractor.py, ai_extractor.py,
                   # builtin_templates.py, dedupe.py, qualifier.py, nl_to_fields.py
    job_manager.py # QThread worker: queue, retries, failover, enrichment
    exports/       # CSV / JSON / JSONL / XLSX / Odoo exporters
    storage/       # db.py (thread-safe sqlite3), secrets.py (DPAPI-backed)
  ui/              # main_window, sidebar, theme (dark QSS), screens/, widgets/, dialogs/
main.py            # entry point
run.bat            # Windows launcher
design_prototypes/ # HTML design docs incl. the full engineering deep-dive page
```
