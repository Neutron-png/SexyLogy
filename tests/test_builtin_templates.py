"""Tests for app/core/engine/builtin_templates.py - specifically the
upgrade path (a previously-seeded ICP template with old empty selectors
must get refreshed to the real yellowpages.com selectors/URLs, not stay
stuck on the first-ever version forever) and that every ICP template ships
a non-empty container + field selectors + start_urls, since Quick Start
depends on all three being present to skip manual selector entry."""
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app.core.storage.db import Database
from app.core.engine.builtin_templates import (
    BUILTIN_TEMPLATES, seed_builtin_templates, generate_niche_urls, generate_niche_urls_yelp,
    NICHE_SEARCH_TERMS, CITY_POOL, RESULTS_PER_PAGE, YELP_RESULTS_PER_PAGE, MAX_PAGES_PER_CITY,
)


@contextmanager
def temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(Path(tmp) / "test.db")
        try:
            yield database
        finally:
            database.close()


def _icp_templates():
    return [t for t in BUILTIN_TEMPLATES if t.get("icp")]


def _yp_icp_templates():
    return [t for t in _icp_templates() if t.get("source") == "yellowpages"]


def _yelp_icp_templates():
    return [t for t in _icp_templates() if t.get("source") == "yelp"]


def test_every_icp_template_has_container_fields_and_start_urls():
    # 15 niches x 2 sources (yellowpages.com, Yelp) - see builtin_templates.py
    icp_templates = _icp_templates()
    assert len(icp_templates) == 30
    assert len(_yp_icp_templates()) == 15
    assert len(_yelp_icp_templates()) == 15

    for tpl in _yp_icp_templates():
        container = tpl.get("container")
        assert container and container.get("selector"), tpl["name"]
        assert container.get("type") == "css"
        assert tpl["start_urls"], tpl["name"]
        assert all(u.startswith("https://www.yellowpages.com/") for u in tpl["start_urls"])
        assert tpl.get("detail_config") is None
        selectors = {f.name: f.selector for f in tpl["fields"]}
        for field_name in ("business_name", "phone", "website", "address", "city"):
            assert selectors.get(field_name), f"{tpl['name']}.{field_name}"

    for tpl in _yelp_icp_templates():
        container = tpl.get("container")
        assert container and container.get("selector"), tpl["name"]
        assert container.get("type") == "css"
        assert tpl["start_urls"], tpl["name"]
        assert all(u.startswith("https://www.yelp.com/") for u in tpl["start_urls"])
        selectors = {f.name: f.selector for f in tpl["fields"]}
        assert selectors.get("business_name"), tpl["name"]
        assert selectors.get("yelp_profile_url"), tpl["name"]
        # Yelp's search-results page has no phone number at all - it must
        # come from detail_config's second-fetch enrichment instead (see
        # job_manager.py's _enrich_with_detail_page()), not a field here.
        assert "phone" not in selectors
        detail_config = tpl.get("detail_config")
        assert detail_config is not None, tpl["name"]
        assert detail_config["link_field"] == "yelp_profile_url"
        assert detail_config["regex_fields"].get("phone")


def test_seed_inserts_all_builtin_templates_once():
    with temp_db() as db:
        seed_builtin_templates(db)
        names = {t["name"] for t in db.list_templates()}
        assert names == {t["name"] for t in BUILTIN_TEMPLATES}


def test_seed_upgrades_a_previously_seeded_icp_template_in_place():
    with temp_db() as db:
        # Simulate an app that already ran once with the old empty-selector
        # version of an ICP template (this is exactly what shipped before
        # yellowpages.com selectors were captured).
        stale_config = {
            "fields": [{"name": "business_name", "selector": "", "selector_type": "css",
                        "extraction_type": "text", "attribute": None, "multiple": False, "parent": None}],
            "icp": True,
            "fee_hint": "$3,000-$7,000/mo",
            "container": None,
            "start_urls": [],
        }
        db.create_template("SEO Leads - Pool Builders", stale_config, builtin=True)

        seed_builtin_templates(db)

        rows = [t for t in db.list_templates() if t["name"] == "SEO Leads - Pool Builders"]
        assert len(rows) == 1  # upgraded in place, not duplicated
        config = json.loads(rows[0]["config_json"])
        assert config["container"]["selector"] == ".result"
        assert config["start_urls"]
        selectors = {f["name"]: f["selector"] for f in config["fields"]}
        assert selectors["business_name"] == ".business-name"


def test_update_template_config_never_touches_a_user_created_non_builtin_template():
    with temp_db() as db:
        db.create_template("SEO Leads - Pool Builders", {"fields": [], "user_made": True}, builtin=False)
        db.update_template_config("SEO Leads - Pool Builders", {"fields": [], "hacked": True})
        rows = [t for t in db.list_templates() if t["name"] == "SEO Leads - Pool Builders"]
        assert len(rows) == 1
        config = json.loads(rows[0]["config_json"])
        assert config.get("user_made") is True
        assert "hacked" not in config


# --- generate_niche_urls(): the "How many leads (approx.)" Quick Start
# control (app/ui/screens/new_scrape.py) - regression coverage for "ليه
# دايما 30 بس، انا عايز الف/الفين" (why always only ~30, I want a
# thousand or two): the old code hard-coded 2 cities x 1 page per niche
# (~50-60 leads) with no way to ask for more.

def test_generate_niche_urls_scales_with_target_count():
    small = generate_niche_urls("Foundation Repair", 30)
    big = generate_niche_urls("Foundation Repair", 900)
    assert len(small) == 1
    assert len(big) == 30  # ceil(900/30)
    assert len(big) > len(small)


def test_generate_niche_urls_pages_within_a_city_before_moving_on():
    urls = generate_niche_urls("Foundation Repair", RESULTS_PER_PAGE * 3)
    # first MAX_PAGES_PER_CITY urls should all be the same (first) city,
    # with an incrementing &page= for everything after the first
    first_city, first_state = CITY_POOL[0]
    for i, url in enumerate(urls[:3]):
        assert quote_plus_city(first_city, first_state) in url
        if i == 0:
            assert "&page=" not in url
        else:
            assert f"&page={i + 1}" in url


def quote_plus_city(city, state):
    from urllib.parse import quote
    return quote(f"{city}, {state}")


def test_generate_niche_urls_unknown_niche_returns_empty():
    assert generate_niche_urls("Not A Real Niche", 500) == []


def test_generate_niche_urls_never_exceeds_max_urls_cap():
    urls = generate_niche_urls("Foundation Repair", 100_000, max_urls=50)
    assert len(urls) <= 50


def test_generate_niche_urls_covers_all_15_icp_niches():
    assert len(NICHE_SEARCH_TERMS) == 15
    for name in NICHE_SEARCH_TERMS:
        assert generate_niche_urls(name, 60)  # every niche can produce at least a couple pages


# --- generate_niche_urls_yelp(): same idea as generate_niche_urls() but
# for the Yelp source (10 results/page via &start=, not 30/page via
# &page=) - see _apply_niche_template() in app/ui/screens/new_scrape.py,
# which picks whichever generator matches the selected template's source.

def test_generate_niche_urls_yelp_scales_with_target_count():
    small = generate_niche_urls_yelp("Foundation Repair", 10)
    big = generate_niche_urls_yelp("Foundation Repair", 300)
    assert len(small) == 1
    assert len(big) == 30  # ceil(300/10)
    assert len(big) > len(small)


def test_generate_niche_urls_yelp_uses_start_param_not_page():
    urls = generate_niche_urls_yelp("Foundation Repair", YELP_RESULTS_PER_PAGE * 3)
    assert "&start=" not in urls[0]
    assert "&start=10" in urls[1]
    assert "&start=20" in urls[2]
    assert all("yelp.com/search" in u for u in urls)


def test_generate_niche_urls_yelp_unknown_niche_returns_empty():
    assert generate_niche_urls_yelp("Not A Real Niche", 500) == []


def test_generate_niche_urls_yelp_covers_all_15_icp_niches():
    for name in NICHE_SEARCH_TERMS:
        assert generate_niche_urls_yelp(name, 30)


def test_yelp_detail_config_phone_regex_matches_real_yelp_markup():
    """Sanity check for _YELP_DETAIL_CONFIG's regex against the exact
    phone format seen on a live yelp.com business page (see
    app/core/job_manager.py's _enrich_with_detail_page(), which searches
    a fetched detail page's visible text with this pattern since Yelp's
    phone number has no stable CSS hook to select by)."""
    import re
    from app.core.engine.builtin_templates import _YELP_DETAIL_CONFIG

    pattern = _YELP_DETAIL_CONFIG["regex_fields"]["phone"]
    page_text = "Some business blurb\nGet Directions\n(972) 251-0018\nMon-Fri 8:00 am - 5:00 pm"
    m = re.search(pattern, page_text)
    assert m and m.group(0) == "(972) 251-0018"
