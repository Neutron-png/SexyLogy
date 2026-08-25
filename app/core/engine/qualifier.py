"""
Automatic lead qualification against the "weak digital marketing" signal
from the user's Ideal Client Profile: businesses that have money to spend
(high-ticket, already running paid ads/directory listings) but whose own
website looks neglected are the best-fit leads.

This module only ever looks at signals that are cheaply and reliably
readable from a fetched HTML page - it does NOT claim to detect things
that would need real traffic data, ad-spend data, or a domain-age/WHOIS
lookup (those need paid APIs LOGY doesn't have access to). Being explicit
about that boundary matters: a "digital weakness score" that pretended to
know a business's ad spend would be exactly the kind of fake
functionality the project spec forbids.

Pure Python, no Qt/Scrapling import -> unit-testable (see
tests/test_qualifier.py) with a plain HTML string.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)


def _strip_tags(html: str) -> str:
    """Rough visible-text extraction for the thin-content check below -
    drops <script>/<style> blocks entirely (their contents aren't visible
    text) then strips remaining tags. Not meant to be a full HTML-to-text
    pipeline (see scrapling_adapter.html_to_text() for that); just enough
    to get an honest word count for a single fetched page."""
    return _TAG_RE.sub(" ", html)


@dataclass
class QualificationResult:
    has_website: bool
    score: int              # 0-100, higher = MORE qualified (weaker existing site = better lead)
    signals: list[str] = field(default_factory=list)   # human-readable reasons, for the results table

    @property
    def label(self) -> str:
        if not self.has_website:
            return "No website - high priority"
        if self.score >= 70:
            return "Weak site - strong lead"
        if self.score >= 40:
            return "Some gaps - worth a look"
        return "Strong site - low priority"


# Each check: (signal description if MISSING/weak, points added when it's missing)
_CHECKS: list[tuple[str, str, int]] = [
    ("no_https", "No HTTPS (not secure)", 15),
    ("no_viewport", "Not mobile-friendly (no responsive viewport tag)", 20),
    ("no_title", "Missing or empty page <title>", 10),
    ("short_title", "Page title too short/generic (<15 chars)", 5),
    ("no_meta_description", "No meta description (weak SEO basics)", 15),
    ("no_schema_localbusiness", "No LocalBusiness structured data (schema.org)", 10),
    ("no_analytics", "No Google Analytics / GTM / Meta Pixel detected", 10),
    ("no_ssl_or_old_looking_markup", "Legacy markup (tables-for-layout / no modern <meta charset>)", 10),
    ("no_h1", "No <h1> heading found (weak on-page SEO)", 5),
]


def qualify_html(html: str | None) -> QualificationResult:
    """Score a fetched homepage's HTML. Call with html=None when the lead
    has no website at all - that's treated as the strongest signal."""
    if not html or not html.strip():
        return QualificationResult(has_website=False, score=100, signals=["No website found for this business"])

    lower = html.lower()
    score = 0
    signals: list[str] = []

    def flag(key: str, missing: bool, message: str, points: int):
        nonlocal score
        if missing:
            score += points
            signals.append(message)

    flag("no_https", "https://" not in lower[:2000] and "http://" in lower[:2000], "Site not confirmed served over HTTPS", 15)
    flag("no_viewport", 'name="viewport"' not in lower and "name='viewport'" not in lower, "Not mobile-friendly (no responsive viewport tag)", 20)

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title_text = (title_match.group(1).strip() if title_match else "")
    flag("no_title", not title_text, "Missing or empty page <title>", 10)
    if title_text and len(title_text) < 15:
        signals.append("Page title too short/generic (<15 chars)")
        score += 5

    flag("no_meta_description", 'name="description"' not in lower and "name='description'" not in lower, "No meta description (weak SEO basics)", 15)
    flag("no_schema_localbusiness", "localbusiness" not in lower and "schema.org" not in lower, "No LocalBusiness structured data (schema.org)", 10)
    flag(
        "no_analytics",
        "gtag(" not in lower and "googletagmanager" not in lower and "google-analytics" not in lower and "fbevents.js" not in lower,
        "No Google Analytics / GTM / Meta Pixel detected", 10,
    )
    flag("no_h1", "<h1" not in lower, "No <h1> heading found (weak on-page SEO)", 5)

    # --- additional checks: "استهدف الناس اللي مواقعها ضعيفة ك SEO و
    # ماركتينج و ترافيك و نتائج بحثية" - the strongest of these signals a
    # site can actively be BLOCKING itself from search results, not just
    # missing polish. Still HTML-only, same honesty boundary as the
    # checks above: none of this claims to know real traffic/ranking
    # numbers, which would need a paid API LOGY doesn't have.
    if re.search(r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*noindex', lower):
        score += 25
        signals.append("Page has a 'noindex' robots tag - actively excluded from Google's search results")

    if "og:title" not in lower and "og:description" not in lower:
        score += 10
        signals.append("No Open Graph tags (weak social/marketing sharing)")

    if 'rel="canonical"' not in lower and "rel='canonical'" not in lower:
        score += 5
        signals.append("No canonical tag (technical SEO gap)")

    visible_word_count = len(_strip_tags(html).split())
    if visible_word_count < 150:
        score += 15
        signals.append(f"Very thin page content ({visible_word_count} visible words) - weak SEO/marketing signal")

    score = min(score, 100)
    return QualificationResult(has_website=True, score=score, signals=signals)
