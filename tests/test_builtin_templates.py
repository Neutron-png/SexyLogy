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
    resolve_cities, generate_niche_urls_all_sources, SOURCE_PROFILES, get_all_source_profiles,
    generate_niche_urls_thumbtack,
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


def test_generate_niche_urls_covers_cities_breadth_first_before_paging_deeper():
    # Breadth-first across the city list (see generate_niche_urls()'s own
    # docstring - "عايزة يسيرش التوب 100 مدينة"): every city's page 1
    # comes before ANY city's page 2. Ask for enough to spill one page
    # past the full pool, so urls[:len(CITY_POOL)] are all page 1 (across
    # every distinct city) and only the ones after that start paging
    # deeper.
    urls = generate_niche_urls("Foundation Repair", RESULTS_PER_PAGE * (len(CITY_POOL) + 2))
    assert len(urls) == len(CITY_POOL) + 2
    page1_urls = urls[:len(CITY_POOL)]
    assert all("&page=" not in u for u in page1_urls)
    seen_cities = {quote_plus_city(city, state) for city, state in CITY_POOL}
    assert all(any(c in u for c in seen_cities) for u in page1_urls)
    # page 2 only starts after every city's page 1 has been queued
    assert "&page=2" in urls[len(CITY_POOL)]
    assert "&page=2" in urls[len(CITY_POOL) + 1]


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
    # Breadth-first across cities (same ordering as generate_niche_urls()
    # above) - every city's &start=0 (no param) page comes before any
    # city's &start=10 page, so spill one page past the full pool to see
    # a real &start=10/&start=20.
    urls = generate_niche_urls_yelp("Foundation Repair", YELP_RESULTS_PER_PAGE * (len(CITY_POOL) + 2))
    assert len(urls) == len(CITY_POOL) + 2
    assert all("&start=" not in u for u in urls[:len(CITY_POOL)])
    assert "&start=10" in urls[len(CITY_POOL)]
    assert "&start=10" in urls[len(CITY_POOL) + 1]
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


# --- city picker ("خليني اقدر احدد المدن اللي محتاجها") ---

def test_resolve_cities_empty_input_means_use_full_pool():
    assert resolve_cities("") == []
    assert resolve_cities("   ") == []


def test_resolve_cities_parses_city_state_pairs():
    assert resolve_cities("Austin, TX") == [("Austin", "TX")]
    assert resolve_cities("Austin, TX; Toronto, ON") == [("Austin", "TX"), ("Toronto", "ON")]
    assert resolve_cities("Austin, TX\nToronto, ON\n") == [("Austin", "TX"), ("Toronto", "ON")]


def test_resolve_cities_bare_city_with_no_state():
    assert resolve_cities("Cairo") == [("Cairo", "")]


def test_generate_niche_urls_restricts_to_given_cities():
    name = next(iter(NICHE_SEARCH_TERMS))
    cities = [("Austin", "TX"), ("Denver", "CO")]
    urls = generate_niche_urls(name, target_results=RESULTS_PER_PAGE * 20, cities=cities)
    assert urls  # at least page 1 for each city
    for u in urls:
        assert ("Austin" in u) or ("Denver" in u)
    assert not any("Chicago" in u for u in urls)  # a CITY_POOL city NOT in the restriction list


def test_generate_niche_urls_yelp_restricts_to_given_cities():
    name = next(iter(NICHE_SEARCH_TERMS))
    cities = [("Miami", "FL")]
    urls = generate_niche_urls_yelp(name, target_results=YELP_RESULTS_PER_PAGE * 10, cities=cities)
    assert urls
    assert all("Miami" in u for u in urls)


def test_generate_niche_urls_all_sources_restricts_to_given_cities():
    name = next(iter(NICHE_SEARCH_TERMS))
    cities = [("Austin", "TX")]
    urls = generate_niche_urls_all_sources(name, target_results=200, cities=cities)
    assert urls
    # case-insensitive: yellowpages/yelp keep "Austin" as typed (query-string
    # encoded), thumbtack.com's URL shape lowercases every path segment
    # (.../tx/austin/...) - see _thumbtack_slug().
    assert all("austin" in u.lower() for u in urls)


def test_generate_niche_urls_all_sources_includes_all_three_domains():
    name = next(iter(NICHE_SEARCH_TERMS))
    urls = generate_niche_urls_all_sources(name, target_results=3000)
    domains = {"yellowpages.com": False, "yelp.com": False, "thumbtack.com": False}
    for u in urls:
        for d in domains:
            if d in u:
                domains[d] = True
    assert all(domains.values()), domains


def test_generate_niche_urls_thumbtack_uses_real_url_pattern():
    name = next(iter(NICHE_SEARCH_TERMS))
    urls = generate_niche_urls_thumbtack(name, target_results=50, cities=[("Austin", "TX")])
    assert urls == [f"https://www.thumbtack.com/tx/austin/{NICHE_SEARCH_TERMS[name].replace(' ', '-')}/"]


def test_generate_niche_urls_thumbtack_skips_cities_without_state():
    name = next(iter(NICHE_SEARCH_TERMS))
    urls = generate_niche_urls_thumbtack(name, target_results=50, cities=[("Cairo", "")])
    assert urls == []


def test_generate_niche_urls_no_cities_arg_uses_full_pool_unchanged():
    """Backward compatibility: omitting `cities` must behave exactly like
    before this parameter existed (full CITY_POOL, same output)."""
    name = next(iter(NICHE_SEARCH_TERMS))
    assert generate_niche_urls(name, 500) == generate_niche_urls(name, 500, cities=None)


# --- sources (built-in + user-added, "خليني اقدر من جوا اضيف مصادر جديدة") ---

def test_source_profiles_includes_thumbtack_with_live_captured_selectors():
    # Captured live via a real browser session (claude-in-chrome) on
    # 2026-08-27 - see builtin_templates.py's docstring on
    # _THUMBTACK_CONTAINER for exactly what was verified and why phone/
    # website are deliberately absent (not a selector gap - Thumbtack
    # never publicly renders either).
    thumbtack = next(p for p in SOURCE_PROFILES if p["name"] == "thumbtack")
    assert thumbtack["domain"] == "thumbtack.com"
    assert thumbtack["verified"] is True
    assert thumbtack["container"]["selector"] == "div.bb.b-gray-300.pv3.m_pv4"
    field_names = {f.name for f in thumbtack["fields"]}
    assert field_names == {"business_name", "rating", "thumbtack_profile_url"}
    assert "phone" not in field_names
    assert "website" not in field_names


def test_get_all_source_profiles_includes_builtins_with_no_custom_sources():
    with temp_db() as db:
        names = {p["name"] for p in get_all_source_profiles(db)}
        assert {"yellowpages", "yelp", "thumbtack"} <= names


def test_get_all_source_profiles_includes_user_added_source():
    with temp_db() as db:
        db.create_custom_source(
            "my_directory", "example-directory.com",
            {"selector": ".listing", "type": "css"},
            [{"name": "biz_name", "selector": ".name", "selector_type": "css",
              "extraction_type": "text", "attribute": None, "multiple": False, "parent": None}],
        )
        profiles = get_all_source_profiles(db)
        mine = next(p for p in profiles if p["name"] == "my_directory")
        assert mine["domain"] == "example-directory.com"
        assert mine["verified"] is False
        assert mine["fields"][0].name == "biz_name"  # converted to ExtractionField, not a raw dict


def test_get_all_source_profiles_custom_source_overrides_builtin_same_domain():
    """A user filling in real thumbtack.com selectors must take priority
    over the empty built-in stub - see get_all_source_profiles()'s
    docstring on ordering."""
    with temp_db() as db:
        db.create_custom_source(
            "thumbtack (fixed)", "thumbtack.com",
            {"selector": ".real-card", "type": "css"},
            [{"name": "business_name", "selector": ".real-name", "selector_type": "css",
              "extraction_type": "text", "attribute": None, "multiple": False, "parent": None}],
        )
        profiles = get_all_source_profiles(db)
        thumbtack_matches = [p for p in profiles if p["domain"] == "thumbtack.com"]
        assert len(thumbtack_matches) == 1  # the empty built-in was superseded, not just appended
        assert thumbtack_matches[0]["container"]["selector"] == ".real-card"
