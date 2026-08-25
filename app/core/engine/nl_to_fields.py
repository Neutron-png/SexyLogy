"""
"Smart Extraction" (spec section 7, mode 1): turn a natural-language
description like "company name, website, email, phone and address" into
a starter list of ExtractionField stubs.

This is a small rule-based keyword matcher, NOT a call to an LLM - the
spec requires the AI layer (if any) to be isolated from the scraping
engine and NOT required for basic scraping to work. Shipping a fake
"AI" that's actually just string matching would violate spec section 36
("never fake functionality"), so this is documented plainly as a keyword
matcher. A real LLM-backed version can later implement the same
`generate_fields(description) -> list[ExtractionField]` interface and be
swapped in without touching the rest of the app (see AI_PROVIDER below).

Generated fields have empty selectors - the user fills those in via the
Selector Assistant / Page Preview (spec section 9) by clicking the
element on the page, or edits them directly in the Field Builder.
"""
from __future__ import annotations

from app.core.models import ExtractionField, ExtractionType

# keyword -> (field_name, extraction_type, attribute)
_KEYWORD_MAP: dict[str, tuple[str, ExtractionType, str | None]] = {
    "email": ("email", ExtractionType.TEXT, None),
    "e-mail": ("email", ExtractionType.TEXT, None),
    "phone": ("phone", ExtractionType.TEXT, None),
    "telephone": ("phone", ExtractionType.TEXT, None),
    "mobile": ("phone", ExtractionType.TEXT, None),
    "company name": ("company_name", ExtractionType.TEXT, None),
    "business name": ("company_name", ExtractionType.TEXT, None),
    "name": ("name", ExtractionType.TEXT, None),
    "website": ("website", ExtractionType.ATTRIBUTE, "href"),
    "url": ("url", ExtractionType.ATTRIBUTE, "href"),
    "address": ("address", ExtractionType.TEXT, None),
    "location": ("address", ExtractionType.TEXT, None),
    "price": ("price", ExtractionType.TEXT, None),
    "title": ("title", ExtractionType.TEXT, None),
    "description": ("description", ExtractionType.TEXT, None),
    "image": ("image", ExtractionType.ATTRIBUTE, "src"),
    "rating": ("rating", ExtractionType.TEXT, None),
    "category": ("category", ExtractionType.TEXT, None),
}

AI_PROVIDER = "rule_based_keyword_matcher"  # swap for an LLM provider id later


def generate_fields(description: str) -> list[ExtractionField]:
    text = (description or "").lower()
    found: list[ExtractionField] = []
    seen_names = set()

    for keyword, (field_name, extraction_type, attribute) in _KEYWORD_MAP.items():
        if keyword in text and field_name not in seen_names:
            seen_names.add(field_name)
            found.append(ExtractionField(
                name=field_name,
                selector="",  # filled in later via Selector Assistant
                extraction_type=extraction_type,
                attribute=attribute,
            ))

    if not found:
        # Fall back to splitting on commas/"and" so the user still gets a
        # usable starting point instead of an empty builder.
        import re
        parts = [p.strip() for p in re.split(r",|\band\b", text) if p.strip()]
        for part in parts[:10]:
            slug = re.sub(r"[^a-z0-9]+", "_", part).strip("_") or "field"
            if slug not in seen_names:
                seen_names.add(slug)
                found.append(ExtractionField(name=slug, selector="", extraction_type=ExtractionType.TEXT))

    return found
