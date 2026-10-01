"""
ICP Intelligence - document parsing, structured profile, query
generation and source qualification.

Pipeline role: ICP document -> structured draft -> USER REVIEW ->
discovery queries -> SearchService -> qualified source URLs -> the
existing crawler. The crawler stays the only thing that fetches pages;
this module only DISCOVERS and QUALIFIES.

Reuse over reinvention:
- understanding (optional AI): one call through the EXISTING
  app/core/engine/ai_extractor layer + saved API keys (never its own
  key/billing), falling back to deterministic keyword drafting when no
  key exists.
- URL hygiene: app/core/search/urls.py (normalization/SSRF).
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Optional

# Stopwords for the deterministic keyword-draft pass (EN+AR common).
_STOPWORDS = {
    "the", "and", "for", "with", "a", "an", "of", "to", "in", "on", "at",
    "is", "are", "we", "our", "their", "they", "that", "this", "from",
    "by", "as", "be", "or", "it", "should", "will", "must", "have",
    "clients", "company", "companies", "business", "businesses", "target",
    "looking", "want", "need", "any", "all", "more", "than", "also",
    "في", "من", "على", "عن", "الى", "إلى", "التي", "الذي", "مع", "هذا",
    "هذه", "ان", "أن", "او", "أو", "الى", "يكون", "المشار", "لها",
}


# ---------------------------------------------------------------------------
# Document parsing (PDF / DOCX / TXT) -> plain text
# ---------------------------------------------------------------------------
def extract_text(path: str | Path) -> str:
    """PDF via pypdf, DOCX via python-docx, txt as-is. Never raises for
    content problems - returns what it can read; empty text is the
    caller's 'unreadable document' signal. Size is capped by the UI
    before calling this."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if suffix in (".docx", ".doc"):
            from docx import Document as _Docx
            doc = _Docx(str(path))
            return "\n".join(p.text for p in doc.paragraphs if p.text)
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ValueError(f"تعذر قراءة الملف ({suffix or 'unknown'}): {e}") from e


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Structured ICP profile
# ---------------------------------------------------------------------------
ICP_FIELDS = (
    "industries", "keywords", "locations", "exclusions",
    "company_size", "roles", "qualifying_signals", "disqualifying_signals",
)


def empty_profile() -> dict:
    return {k: [] for k in ICP_FIELDS}


def _split_csv(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[,\n؛]", text) if s.strip()]


def profile_from_ui(values: dict[str, str]) -> dict:
    """The user-reviewed profile - the ONLY thing that ever drives
    discovery (the parsed draft is a suggestion, never authority)."""
    out = empty_profile()
    for key in ICP_FIELDS:
        out[key] = _split_csv(values.get(key, ""))
    return out


def draft_from_text(text: str, max_items: int = 12) -> dict:
    """Deterministic no-AI draft: most frequent capitalized phrases and
    word frequencies. Honest about being a DRAFT - the user edits it."""
    words = re.findall(r"[\w\u0600-\u06FF][\w\u0600-\u06FF\-&+ .]*", text or "")
    counts: dict[str, int] = {}
    for w in words:
        w = w.strip().lower()
        if len(w) < 3 or w in _STOPWORDS or w.isdigit():
            continue
        counts[w] = counts.get(w, 0) + 1
    keywords = [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:max_items]]
    draft = empty_profile()
    draft["keywords"] = keywords
    return draft


def ai_profile_from_text(text: str, db) -> tuple[dict, list[str]]:
    """One AI call through the existing ai_extractor layer (uses the
    user's own saved key + provider). Returns (profile, warnings);
    on any failure returns the deterministic draft instead - AI is an
    enhancement here, never a dependency."""
    from app.core.engine import ai_extractor
    notes: list[str] = []
    field_names = [
        "industries", "keywords", "locations", "exclusions",
        "company_size", "roles", "qualifying_signals", "disqualifying_signals",
    ]
    provider = _effective_provider(db)
    if not provider:
        notes.append("no AI key saved - used the deterministic draft")
        return draft_from_text(text), notes
    keys = db.get_setting("api_keys", {})
    try:
        raw = ai_extractor.extract(provider, text[:20000], field_names, keys.get(provider))
    except Exception as e:
        notes.append(f"AI understanding failed ({e.__class__.__name__}) - used the deterministic draft")
        return draft_from_text(text), notes
    profile = empty_profile()
    for k in ICP_FIELDS:
        v = raw.get(k)
        if isinstance(v, list):
            profile[k] = [str(x).strip() for x in v if str(x).strip()][:20]
        elif isinstance(v, str) and v.strip():
            profile[k] = [v.strip()]
    return profile, notes


def _effective_provider(db) -> Optional[str]:
    """Same provider-resolution policy as New Scrape's AI tab: the saved
    key wins, defaults never hallucinate one."""
    keys = db.get_setting("api_keys", {}) or {}
    for p in ("anthropic", "openai"):
        if keys.get(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Query generation (deterministic - no LLM per query)
# ---------------------------------------------------------------------------
def generate_queries(profile: dict, max_queries: int = 20) -> list[str]:
    """Industries/keywords x locations -> search queries. Cost-capped by
    max_queries (the default cost ceiling). Locations without industries
    still produce generic directory-style queries; missing everything
    yields no queries (the UI asks the user for more ICP detail)."""
    industries = profile.get("industries") or []
    keywords = profile.get("keywords") or []
    locations = profile.get("locations") or []
    terms = industries[:6] or keywords[:6]
    if not terms:
        return []
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str):
        q = " ".join(q.split())
        if q and q.lower() not in seen and len(queries) < max_queries:
            seen.add(q.lower())
            queries.append(q)

    if locations:
        for term in terms:
            for loc in locations[:4]:
                add(f"{term} companies in {loc}")
    else:
        for term in terms:
            add(f"{term} companies directory")
    return queries


# ---------------------------------------------------------------------------
# Source qualification (deterministic scoring; no LLM per URL)
# ---------------------------------------------------------------------------
def qualify_source(url: str, title: str, snippet: str, profile: dict) -> dict:
    """Score 0-100 with the reasons attached (observability: WHY kept /
    rejected). Positive signals: industry/keyword hits in title/snippet/
    domain. Negative: exclusion hits; obvious junk hosts."""
    from app.core.search.urls import domain_of

    hay = f"{title} {snippet} {domain_of(url)}".lower()
    reasons: list[str] = []
    score = 0
    terms = (profile.get("industries") or []) + (profile.get("keywords") or [])
    for t in terms[:12]:
        t_l = t.lower()
        if len(t_l) < 3:
            continue
        if t_l in hay:
            score += 25 if t_l in (profile.get("industries") or []) else 10
            reasons.append(f"match: {t}")
    for x in (profile.get("exclusions") or [])[:12]:
        x_l = x.lower()
        if len(x_l) >= 3 and x_l in hay:
            score -= 40
            reasons.append(f"excluded: {x}")
    junk_hosts = ("facebook.com", "instagram.com", "pinterest.com",
                  "tiktok.com", "twitter.com", "x.com", "linkedin.com")
    if any(h in domain_of(url) for h in junk_hosts):
        score = -100
        reasons.append("social/unsuitable host")
    return {"score": max(0, min(100, score)), "reasons": reasons}


# ---------------------------------------------------------------------------
# Storage (additive table; structured JSON only, never the raw doc)
# ---------------------------------------------------------------------------
def save_profile(db, name: str, source_file: str, source_hash: str, profile: dict) -> int:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO icp_profiles (name, source_file, source_hash, structured_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, source_file, source_hash,
             __import__("json").dumps(profile, ensure_ascii=False), time.time()),
        )
        return cur.lastrowid


def list_profiles(db) -> list[dict]:
    import json
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM icp_profiles ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        try:
            profile = json.loads(r["structured_json"])
        except (TypeError, ValueError):
            profile = empty_profile()
        out.append({"id": r["id"], "name": r["name"], "source_file": r["source_file"],
                    "source_hash": r["source_hash"], "profile": profile})
    return out
