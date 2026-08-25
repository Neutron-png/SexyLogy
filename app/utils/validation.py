"""
Pure validation helpers used by the New Scrape wizard before a job is
allowed to start. No Qt / no Scrapling imports -> unit-testable.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse


def is_valid_url(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def dedupe_urls(urls: list[str]) -> list[str]:
    seen = set()
    result = []
    for u in urls:
        u = u.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        result.append(u)
    return result


def parse_url_list(raw_text: str) -> tuple[list[str], list[str]]:
    """Split a textarea blob into (valid_urls, invalid_lines)."""
    valid, invalid = [], []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if is_valid_url(line):
            valid.append(line)
        else:
            invalid.append(line)
    return dedupe_urls(valid), invalid


def validate_field_name(name: str) -> bool:
    name = (name or "").strip()
    return bool(name) and len(name) <= 64


def validate_json_schema(raw_text: str) -> tuple[bool, str, dict]:
    """
    Validate a user-supplied JSON Schema (Step 2 -> JSON Schema mode).
    Expected shape: {"field_name": "string"} - a flat mapping of field
    name -> type hint. Returns (is_valid, error_message, parsed_dict).
    """
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return False, f"JSON غير صالح: {e.msg} (سطر {e.lineno})", {}

    if not isinstance(parsed, dict) or not parsed:
        return False, "لازم يكون الـ schema عبارة عن object فيه حقل واحد على الأقل.", {}

    allowed_types = {"string", "number", "boolean", "array"}
    for key, value in parsed.items():
        if not validate_field_name(key):
            return False, f"اسم الحقل غير صالح: {key!r}", {}
        if isinstance(value, str):
            if value not in allowed_types:
                return False, f"نوع غير مدعوم للحقل {key!r}: {value!r}", {}
        elif not isinstance(value, dict):
            return False, f"قيمة غير صالحة للحقل {key!r}", {}

    return True, "", parsed


def validate_selector(selector: str, selector_type: str) -> tuple[bool, str]:
    selector = (selector or "").strip()
    if not selector:
        return False, "السلكتور فاضي."
    if selector_type == "xpath":
        if not (selector.startswith("/") or selector.startswith(".") or selector.startswith("(")):
            return False, "XPath لازم يبدأ بـ / أو . أو ("
    return True, ""
