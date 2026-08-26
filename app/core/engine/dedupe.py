"""
Lead de-duplication.

LOGY is normally re-run against the same niche/search more than once (that's
the point of periodically re-scraping a directory for fresh leads) - without
this module every re-run would happily re-save leads that an EARLIER job
already produced, since app/core/storage/db.py's `results` table is scoped
per-job and never compares against previous runs. This is what makes
"هيستوري لليدز اللي طلعت مسبقا متتكررش كل ما نجينيريت ليدز" (a history of
already-seen leads so re-generating never repeats them) possible: every
extracted record gets fingerprinted here, app/core/storage/db.py's
lead_history table remembers every fingerprint ever seen (across every job
and project, forever - it is deliberately not scoped to one job), and
app/core/job_manager.py skips saving/emitting a record whose fingerprint was
already recorded by any earlier run.

fingerprint_lead() turns one extracted record dict into a single stable key.
Priority order (most to least reliable identity signal for "is this the same
real-world lead"):
    1. email           - the single most unique field a lead can have
    2. phone / mobile  - normalized to digits only
    3. website         - normalized to a bare domain (strips scheme/www/path)
    4. name + company  - last resort, case/whitespace-normalized

Only the first signal that's actually present is used (rather than hashing
every field together), because two scrapes of the *same* business rarely
agree on formatting/whitespace/casing for every field, but almost always
agree on its email or phone if either was captured both times.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

_EMAIL_KEYS = ("email", "e-mail", "mail", "contact_email", "owner_email")
_PHONE_KEYS = ("phone", "mobile", "phone_number", "telephone", "tel", "owner_phone")
_WEBSITE_KEYS = ("website", "site", "url", "web", "homepage")
_NAME_KEYS = ("name", "contact_name", "full_name", "title", "owner_name")
_COMPANY_KEYS = ("company", "company_name", "business_name", "business")


def _first_present(data: dict, keys: tuple[str, ...]) -> Optional[str]:
    lower = {str(k).lower(): v for k, v in data.items()}
    for key in keys:
        val = lower.get(key)
        if val is None:
            continue
        val = str(val).strip()
        if val:
            return val
    return None


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def _normalize_website(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^www\.", "", value)
    return value.split("/")[0]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def fingerprint_lead(data: dict) -> Optional[str]:
    """Returns a stable hash identifying this lead across scrapes/jobs, or
    None if the record carries none of the identity signals above - in
    that case it's never de-duplicated, since two unrelated leads with
    nothing identifying in common would otherwise collide."""
    if not data:
        return None

    email = _first_present(data, _EMAIL_KEYS)
    if email and "@" in email:
        return _hash(f"email:{_normalize_email(email)}")

    phone = _first_present(data, _PHONE_KEYS)
    if phone:
        digits = _normalize_phone(phone)
        if len(digits) >= 6:  # too short to be a real, unique number
            return _hash(f"phone:{digits}")

    website = _first_present(data, _WEBSITE_KEYS)
    if website:
        domain = _normalize_website(website)
        if domain:
            return _hash(f"site:{domain}")

    name = _first_present(data, _NAME_KEYS)
    company = _first_present(data, _COMPANY_KEYS)
    if name and company:
        return _hash(f"name+co:{_normalize_text(name)}|{_normalize_text(company)}")

    return None
