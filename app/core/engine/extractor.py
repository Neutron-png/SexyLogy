"""
Applies a list of ExtractionField definitions to a parsed page and returns
a plain dict (or a list of dicts, when a field is repeated/"multiple").

This module is intentionally decoupled from Scrapling: it only calls
`.css(selector)` / `.xpath(selector)` on whatever `page` object it is
given, and expects elements back that expose `.get()`, `.getall()` and
`.attrib` - exactly the surface Scrapling's `Selector`/`Adaptor` objects
expose (https://github.com/D4Vinci/Scrapling -> parser.Selector).

That decoupling is what makes this file unit-testable without installing
Scrapling or touching the network (see tests/test_extractor.py, which
feeds it a minimal lxml-backed stand-in with the same interface).
"""
from __future__ import annotations

import re
from typing import Any

from app.core.models import ExtractionField, ExtractionType


class ExtractionError(Exception):
    def __init__(self, field_name: str, reason: str):
        self.field_name = field_name
        self.reason = reason
        super().__init__(f"[{field_name}] {reason}")


def _select(page: Any, field: ExtractionField):
    """Run the field's selector (css or xpath) against `page`."""
    if field.selector_type == "xpath":
        return page.xpath(field.selector)
    return page.css(field.selector)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html_or_text: str) -> str:
    """Fallback cleanup for _element_text() below: strip any leftover HTML
    tags and collapse whitespace. Belt-and-suspenders so a raw
    "<div>...</div>" can never leak into an exported result even if the
    ::text query isn't supported by whatever is behind `element`."""
    text = _TAG_RE.sub(" ", html_or_text)
    return re.sub(r"\s+", " ", text).strip()


def _element_text(element: Any) -> Any:
    """
    Get the human-visible text inside a matched element, however deeply
    it's nested - e.g. yellowpages.com renders business names as
    `<a class="business-name"><span>Holy Drilling</span></a>`, text one
    level below the matched element.

    `element.get()` alone does NOT do this: Scrapling's Selector API
    follows the same convention as scrapy/parsel, where css()/xpath()
    return ELEMENT matches, and .get() on an element match returns that
    element's OUTER HTML, not its text (Scrapling's own quickstart docs
    extract text via `page.css('.quote .text::text').getall()` - the
    `::text` is required). Calling plain .get() here was a real, shipped
    bug: every text field (business_name, phone, address, city) was
    coming back as raw "<a class=...>...</a>" / "<div class=...>...
    </div>" markup instead of clean text - visible directly in an
    exported CSV.

    Fix: query `::text` scoped to the already-matched element, which
    (per Scrapling/parsel semantics) returns every descendant text node
    regardless of nesting, and join them. If that query isn't supported
    by whatever selector engine is behind `element` (e.g. the plain
    bs4-backed test double), fall back to stripping tags off the raw
    HTML - so this can never regress to leaking markup again even if a
    selector-engine assumption here turns out wrong for some page.
    """
    if hasattr(element, "css"):
        try:
            texts = element.css("::text").getall()
            joined = " ".join(t.strip() for t in texts if t and t.strip())
            if joined:
                return joined
        except Exception:
            pass
    raw = element.get() if hasattr(element, "get") else str(element)
    if raw is None:
        return None
    cleaned = _strip_tags(raw)
    return cleaned or None


def _extract_value(element: Any, field: ExtractionField):
    if field.extraction_type == ExtractionType.TEXT:
        return _element_text(element)
    if field.extraction_type == ExtractionType.HTML:
        return getattr(element, "html", None) or (element.get() if hasattr(element, "get") else str(element))
    if field.extraction_type == ExtractionType.ATTRIBUTE:
        attrib = getattr(element, "attrib", None) or {}
        return attrib.get(field.attribute) if field.attribute else None
    if field.extraction_type == ExtractionType.URL:
        attrib = getattr(element, "attrib", None) or {}
        return attrib.get("href") or attrib.get("src")
    return _element_text(element)


def extract_fields(page: Any, fields: list[ExtractionField]) -> dict[str, Any]:
    """
    Apply every top-level (non-nested) field to `page` and return a single
    record. Fields with `multiple=True` return a list of values instead of
    a scalar. Nested fields (field.parent set) are resolved relative to
    their parent's matched element(s) - see extract_records() for the
    per-container variant used when a listing page has repeating cards.
    """
    record: dict[str, Any] = {}
    top_level = [f for f in fields if not f.parent]
    nested = {f.parent: f for f in fields if f.parent}

    for f in top_level:
        try:
            matches = _select(page, f)
        except Exception as e:  # selector engine errors (bad css/xpath, etc.)
            raise ExtractionError(f.name, f"selector فشل: {e}") from e

        if f.multiple:
            record[f.name] = [_extract_value(m, f) for m in matches]
        else:
            first = matches[0] if len(matches) else None
            record[f.name] = _extract_value(first, f) if first is not None else None

    return record


def extract_records(page: Any, container_selector: str, container_type: str, fields: list[ExtractionField]) -> list[dict[str, Any]]:
    """
    For listing pages (e.g. search results / directory pages): select a
    repeating container element per record, then run each field relative
    to that container. Used by the Field Builder's "repeat over" option.

    Skips a container that only produced a single non-empty field. Real
    directory sites sometimes render a second, near-empty element that
    happens to match the same "repeat over" class as the real listing
    card - e.g. yellowpages.com's search results were observed emitting
    a bare `<a class="business-name">Name</a>` promo/duplicate ahead of
    the full `.result` card for the same business (name only, no phone/
    address/website at all). That is a real markup quirk of the target
    site, not something LOGY can prevent by selector choice alone, but a
    "record" with just one field carries no lead value and clutters
    exports/qualification with junk rows - a real record from a listing
    page should have at least a name AND one more piece of contact info.
    """
    containers = page.xpath(container_selector) if container_type == "xpath" else page.css(container_selector)
    records = []
    for container in containers:
        record = extract_fields(container, fields)
        non_empty = sum(1 for v in record.values() if v not in (None, "", [], {}))
        if non_empty >= 2:
            records.append(record)
    return records
