"""
Optional LLM-backed extraction: no CSS/XPath needed at all. Given a
page's visible text and a list of desired field names, asks an LLM to
return a JSON object with those fields filled in from whatever is
actually present on the page - it's told explicitly not to invent data,
and to return null for anything it can't find.

This is opt-in and isolated from the core scraping engine (spec: the AI
layer must not be required for basic scraping to work) - it requires an
API key the user adds themselves on the API Keys screen, encrypted at
rest via storage/secrets.py. Nothing here runs unless the user turns on
"AI Auto-Extract" in New Scrape AND has a matching key saved.

Deliberately NOT built: automated LinkedIn profile discovery/scraping.
Two separate reasons, not one:
  1. Technical - LinkedIn's anti-bot measures make reliable automated
     access effectively impossible (see the earlier LinkedIn hang this
     project already ran into), and its Terms of Service explicitly
     prohibit scraping.
  2. This isn't "scrape a business's own published info" anymore - it's
     a people-search operation on an identifiable individual (find THIS
     PERSON's profile), which carries real privacy-law exposure
     (GDPR/CCPA-style rules on processing personal data) on top of the
     ToS problem. That's a materially different risk category from
     scraping a company's public contact page.
If a business's own website happens to publish a link to the owner's
LinkedIn (e.g. on a "Meet the Team" page), this extractor will surface
it like any other link on that page - LOGY just never goes and searches
LinkedIn itself to find one that isn't already there.
"""
from __future__ import annotations

import json
import re

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Fields most people ask for by default (per the user's own "anyone would
# need this" list) - shown as the preset in the AI Auto-Extract tab.
# "owner_linkedin" is included because if a page happens to publish it,
# it's fair game to read - see module docstring for what's deliberately
# NOT built (LinkedIn people-search).
DEFAULT_FIELD_NAMES = ["owner_name", "email", "phone", "owner_linkedin_if_published"]


class AIExtractionError(Exception):
    pass


def build_prompt(page_text: str, field_names: list[str]) -> str:
    fields_list = ", ".join(field_names)
    # Truncate to keep requests small/cheap and inside typical context
    # limits - a homepage/contact page rarely needs more than this.
    snippet = page_text[:12000]
    return (
        "You are extracting structured contact data from the visible text of ONE web page. "
        f"Return ONLY a single JSON object with exactly these keys: {fields_list}. "
        "Rules: use null for any field not explicitly present on this page - never guess, "
        "infer, or invent a value. Do not search your own knowledge for the answer, only use "
        "what's in the text below. Output nothing except the JSON object.\n\n"
        f"PAGE TEXT:\n{snippet}"
    )


def parse_json_response(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AIExtractionError(f"لم يرجّع الموديل JSON صالح: {text[:200]!r}")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise AIExtractionError(f"JSON غير صالح من الموديل: {e}") from e
    if not isinstance(parsed, dict):
        raise AIExtractionError("الموديل رجّع JSON مش عبارة عن object")
    return parsed


def extract_with_anthropic(page_text: str, field_names: list[str], api_key: str,
                            model: str = DEFAULT_ANTHROPIC_MODEL, timeout: int = 30) -> dict:
    import requests

    prompt = build_prompt(page_text, field_names)
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise AIExtractionError(f"فشل الاتصال بـ Anthropic API: {e}") from e

    data = resp.json()
    try:
        text = data["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise AIExtractionError(f"شكل رد غير متوقع من Anthropic API: {data}") from e
    return parse_json_response(text)


def extract_with_openai(page_text: str, field_names: list[str], api_key: str,
                         model: str = DEFAULT_OPENAI_MODEL, timeout: int = 30) -> dict:
    import requests

    prompt = build_prompt(page_text, field_names)
    try:
        resp = requests.post(
            OPENAI_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise AIExtractionError(f"فشل الاتصال بـ OpenAI API: {e}") from e

    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise AIExtractionError(f"شكل رد غير متوقع من OpenAI API: {data}") from e
    return parse_json_response(text)


PROVIDERS = {
    "anthropic": extract_with_anthropic,
    "openai": extract_with_openai,
}


def extract(provider: str, page_text: str, field_names: list[str], api_key: str) -> dict:
    if provider not in PROVIDERS:
        raise AIExtractionError(f"مزوّد AI غير مدعوم: {provider} (المتاح: {', '.join(PROVIDERS)})")
    if not api_key:
        raise AIExtractionError("مفيش مفتاح API متسجل لـ '%s' - ضيفه من شاشة API Keys الأول" % provider)
    if not field_names:
        raise AIExtractionError("مفيش حقول محددة للاستخراج")
    if not page_text or not page_text.strip():
        raise AIExtractionError("الصفحة مفيهاش نص مقروء للاستخراج منه")
    return PROVIDERS[provider](page_text, field_names, api_key)
