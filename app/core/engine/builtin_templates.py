"""
Built-in scraping templates (spec section 20).

The "SEO Agency Leads" group below mirrors the user's own Ideal Client
Profile (ICP): high-ticket home-service and professional niches where a
weak existing website is a strong buying signal. Selecting one of these
in Quick Start prefills EVERYTHING needed to run a real scrape with zero
manual selector work:

  - the Field Builder's output columns
  - the "Repeat over" container selector
  - auto-qualification (app/core/engine/qualifier.py), which flags each
    scraped lead as "no website" / "weak site" / "strong site"
  - ready-to-run target URLs, sized to however many leads the user asks
    for in Quick Start's "How many leads (approx.)" control (see
    generate_niche_urls() / generate_niche_urls_yelp() below)

Two independent sources are wired up, each captured live from a real
browser session reading the rendered DOM (document.querySelectorAll),
never guessed from static/cached markup:

  yellowpages.com - one flat "repeat over" pass per search-results page
  gets everything (name/phone/address/city) in one fetch.

  yelp.com - the search-results page only exposes name + a link to the
  business's own Yelp page; the phone number is NOT on the results page.
  So Yelp leads need a second fetch per business (the linked page) to
  fill in the phone - see _enrich_with_detail_page() in
  app/core/job_manager.py, driven by each template's "detail_config".
  Yelp's CSS classes are also build-hashed (e.g.
  "businessName__09f24__kZrLX") and WILL change on Yelp's next
  deployment, unlike yellowpages' plain semantic classes - selectors
  below use `[class*="..."]` substring matching on the stable
  human-readable part of the name specifically to survive that (the hash
  suffix rotates, the "businessName"/"hoverable" prefix does not, per
  Yelp's own CSS-module naming convention), but they may still need
  re-capturing sooner than the yellowpages ones.

Captured live on 2026-08-19. Confirmed against real listings, e.g.
"Athena Pools LLC" / (512) 914-0554 / athenapools.com (yellowpages, pool
builders, Austin TX) and "Diaz Foundation Repair" / (yelp.com, foundation
repair, Dallas TX, phone pulled from its own business page). Two sources
were investigated and rejected: LinkedIn (explicitly out of scope - see
app/core/engine/ai_extractor.py's module docstring for the technical +
legal/privacy reasoning, which still applies) and Google Search result
pages (no reliable automated access without a real logged-in browser
session - a live test from an authenticated Chrome session worked, but
that isn't representative of what an unauthenticated automated fetch
gets, which is typically CAPTCHA'd; Google's results also aren't
structured business data even when reachable). yellowpages.ca was also
tried for Canada coverage and is fully blocked (Cloudflare 403) even
from a real browser.

Site markup can still change after the capture date above; if a niche
template stops matching, re-capture selectors the same way (Inspect
Element / a live DOM read on a real listing) and update the constants
below.
"""
from urllib.parse import quote

from app.core.models import ExtractionField, ExtractionType

# ---------------------------------------------------------------------
# yellowpages.com - captured live from search-results markup. One
# <div class="result"> per business in the results list.
# ---------------------------------------------------------------------
_YP_CONTAINER = {"selector": ".result", "type": "css"}

_YP_LEAD_FIELDS = [
    ExtractionField(name="business_name", selector=".business-name"),
    ExtractionField(name="phone", selector=".phones.phone.primary"),
    ExtractionField(
        name="website", selector="a.track-visit-website",
        extraction_type=ExtractionType.ATTRIBUTE, attribute="href",
    ),
    ExtractionField(name="address", selector=".adr .street-address"),
    ExtractionField(name="city", selector=".adr .locality"),
]

# ---------------------------------------------------------------------
# yelp.com - captured live. Container is every card matching
# [class*="hoverable"] on a search-results page (confirmed: 10 real
# business cards + 1 non-business widget per page, the widget simply has
# no [class*="businessName"] child so extract_records()'s near-empty-
# record filter already drops it - see app/core/engine/extractor.py).
# The results page does NOT expose a phone number at all; that requires
# fetching the business's own Yelp page (yelp_profile_url below), which
# _enrich_with_detail_page() in job_manager.py does automatically as a
# second fetch per lead, mirroring how auto-qualify already fetches each
# lead's own website.
# ---------------------------------------------------------------------
_YELP_CONTAINER = {"selector": '[class*="hoverable"]', "type": "css"}

_YELP_LEAD_FIELDS = [
    ExtractionField(name="business_name", selector='[class*="businessName"] a[href*="/biz/"]'),
    ExtractionField(
        name="yelp_profile_url", selector='[class*="businessName"] a[href*="/biz/"]',
        extraction_type=ExtractionType.ATTRIBUTE, attribute="href",
    ),
]

# Drives _enrich_with_detail_page() in job_manager.py: after extracting a
# record from the search-results page, if it has a non-empty
# `link_field`, fetch that URL and merge in whatever `regex_fields`
# match against the fetched page's visible text (see html_to_text() in
# scrapling_adapter.py). Regex, not a CSS selector, on purpose: Yelp's
# business-page phone number sits in a bare hashed class
# (`<p class="y-css-1baza3a">`) with no stable semantic hook at all
# (unlike "businessName", there's no "phone"-ish substring to key off
# of) - a phone-shaped regex is far more resilient to Yelp's next deploy
# than pinning to that exact hash.
_YELP_DETAIL_CONFIG = {
    "link_field": "yelp_profile_url",
    "fields": [],
    "regex_fields": {"phone": r"\(\d{3}\)\s?\d{3}-\d{4}"},
}

# Public aliases (no leading underscore) - app/ui/screens/new_scrape.py's
# "Load Real Directory Search Links" fallback button imports these
# directly to build a Yelp version of that button, since it doesn't go
# through a template's config dict the way Quick Start does.
YELP_CONTAINER = _YELP_CONTAINER
YELP_LEAD_FIELDS = _YELP_LEAD_FIELDS
YELP_DETAIL_CONFIG = _YELP_DETAIL_CONFIG

# name -> (ideal monthly fee range, search term). The search term is
# reused for both yellowpages (search_terms=) and yelp (find_desc=) -
# both accept the same plain-English phrase.
ICP_NICHES: list[tuple[str, str, str]] = [
    ("Pool Builders", "$3,000-$7,000/mo", "pool builders"),
    ("Solar Installers", "$3,000-$7,000/mo", "solar installers"),
    ("Foundation Repair", "$3,000-$6,000/mo", "foundation repair"),
    ("Kitchen Remodeling", "$3,000-$6,000/mo", "kitchen remodeling"),
    ("Restoration (Fire/Water)", "$3,000-$6,000/mo", "water damage restoration"),
    ("Waterproofing Contractors", "$2,500-$6,000/mo", "waterproofing contractors"),
    ("Commercial Painters", "$2,500-$6,000/mo", "commercial painters"),
    ("Excavation Contractors", "$2,500-$6,000/mo", "excavation contractors"),
    ("Bathroom Remodeling", "$2,500-$5,000/mo", "bathroom remodeling"),
    ("Commercial Cleaning", "$2,000-$6,000/mo", "commercial cleaning services"),
    ("Personal Injury / Criminal Defense Attorneys", "high-value", "personal injury attorney"),
    ("Dental Implants / Cosmetic Dentistry", "high-value", "dental implants"),
    ("Private Medical / Specialty Clinics", "high-value", "medical clinic"),
    ("Luxury Home Services", "high-value", "luxury home builders"),
    ("Multi-Location Automotive / Dealerships", "high-value", "car dealership"),
]

NICHE_SEARCH_TERMS: dict[str, str] = {niche: term for niche, _fee, term in ICP_NICHES}

# US + Canadian metros, population 100k+ (matching the ICP's own
# targeting rule), ordered biggest/most business-dense first. This is
# NOT literally "every city" in either country (there are several
# thousand incorporated places total, most under 100k - hardcoding all
# of them would mostly add noise, not real leads) - it's the ~100-city
# set where a meaningful volume of the target niches actually operate.
# Used by generate_niche_urls()/generate_niche_urls_yelp() to scale a
# Quick Start pick up to however many leads the user asks for.
CITY_POOL: list[tuple[str, str]] = [
    # --- United States (~80 cities) ---
    ("New York", "NY"), ("Los Angeles", "CA"), ("Chicago", "IL"), ("Houston", "TX"),
    ("Phoenix", "AZ"), ("Philadelphia", "PA"), ("San Antonio", "TX"), ("San Diego", "CA"),
    ("Dallas", "TX"), ("Austin", "TX"), ("Jacksonville", "FL"), ("Fort Worth", "TX"),
    ("San Jose", "CA"), ("Columbus", "OH"), ("Charlotte", "NC"), ("Indianapolis", "IN"),
    ("San Francisco", "CA"), ("Seattle", "WA"), ("Denver", "CO"), ("Oklahoma City", "OK"),
    ("Nashville", "TN"), ("El Paso", "TX"), ("Washington", "DC"), ("Boston", "MA"),
    ("Las Vegas", "NV"), ("Portland", "OR"), ("Detroit", "MI"), ("Louisville", "KY"),
    ("Memphis", "TN"), ("Baltimore", "MD"), ("Milwaukee", "WI"), ("Albuquerque", "NM"),
    ("Tucson", "AZ"), ("Fresno", "CA"), ("Sacramento", "CA"), ("Mesa", "AZ"),
    ("Kansas City", "MO"), ("Atlanta", "GA"), ("Omaha", "NE"), ("Colorado Springs", "CO"),
    ("Raleigh", "NC"), ("Long Beach", "CA"), ("Virginia Beach", "VA"), ("Miami", "FL"),
    ("Oakland", "CA"), ("Minneapolis", "MN"), ("Tulsa", "OK"), ("Bakersfield", "CA"),
    ("Wichita", "KS"), ("Arlington", "TX"), ("Aurora", "CO"), ("Tampa", "FL"),
    ("New Orleans", "LA"), ("Cleveland", "OH"), ("Honolulu", "HI"), ("Anaheim", "CA"),
    ("Lexington", "KY"), ("Stockton", "CA"), ("Corpus Christi", "TX"), ("Riverside", "CA"),
    ("Santa Ana", "CA"), ("Orlando", "FL"), ("Irvine", "CA"), ("Cincinnati", "OH"),
    ("Newark", "NJ"), ("Pittsburgh", "PA"), ("St. Louis", "MO"), ("Greensboro", "NC"),
    ("Jersey City", "NJ"), ("Lincoln", "NE"), ("Plano", "TX"), ("Anchorage", "AK"),
    ("Durham", "NC"), ("Chandler", "AZ"), ("Toledo", "OH"), ("Chula Vista", "CA"),
    ("Buffalo", "NY"), ("Fort Wayne", "IN"), ("Reno", "NV"), ("St. Petersburg", "FL"),
    # --- Canada (~20 cities) ---
    ("Toronto", "ON"), ("Montreal", "QC"), ("Calgary", "AB"), ("Ottawa", "ON"),
    ("Edmonton", "AB"), ("Mississauga", "ON"), ("Winnipeg", "MB"), ("Vancouver", "BC"),
    ("Brampton", "ON"), ("Hamilton", "ON"), ("Quebec City", "QC"), ("Surrey", "BC"),
    ("Laval", "QC"), ("Halifax", "NS"), ("London", "ON"), ("Markham", "ON"),
    ("Vaughan", "ON"), ("Gatineau", "QC"), ("Saskatoon", "SK"), ("Kitchener", "ON"),
]

RESULTS_PER_PAGE = 30       # yellowpages.com's own page size, confirmed live
YELP_RESULTS_PER_PAGE = 10  # yelp.com's own page size, confirmed live

# Requesting more than this many pages for one city started returning
# stale/repeated results in a live check on yellowpages (page 7 of a
# 165-result, ~6-page search still returned 26 rows instead of running
# out) - past this point LOGY spreads across more cities instead of
# paging one city further, to avoid burning fetches on likely-duplicate
# rows. Applied to both sources for consistency.
MAX_PAGES_PER_CITY = 5

# "عايزة يسيرش التوب 100 مدينة" - full CITY_POOL coverage at
# MAX_PAGES_PER_CITY depth each is the real ceiling of what LOGY can
# generate for one niche+source; generate_niche_urls()/_yelp() default to
# this instead of an arbitrary smaller cap (the old default of 200
# silently truncated well before 100 cities x 5 pages), so asking for
# enough leads (via the "How many leads" control, or by requesting this
# many URLs directly) can actually reach every city in CITY_POOL rather
# than looking like it's still holding results back.
MAX_URLS_ALL_CITIES = len(CITY_POOL) * MAX_PAGES_PER_CITY


def _yp_search_url(term: str, city: str, state: str, page: int = 1) -> str:
    geo = f"{city}, {state}"
    url = f"https://www.yellowpages.com/search?search_terms={quote(term)}&geo_location_terms={quote(geo)}"
    if page > 1:
        url += f"&page={page}"
    return url


def _yelp_search_url(term: str, city: str, state: str, start: int = 0) -> str:
    loc = f"{city}, {state}"
    url = f"https://www.yelp.com/search?find_desc={quote(term)}&find_loc={quote(loc)}"
    if start > 0:
        url += f"&start={start}"
    return url


def _niche_start_urls(term: str, cities: list[tuple[str, str]]) -> list[str]:
    return [_yp_search_url(term, city, state) for city, state in cities]


def generate_niche_urls(niche_name: str, target_results: int, max_urls: int = MAX_URLS_ALL_CITIES) -> list[str]:
    """
    Build enough yellowpages.com search-result-page URLs to cover
    approximately `target_results` leads for the given niche (bare name,
    e.g. "Foundation Repair" - matches the keys in NICHE_SEARCH_TERMS).

    Iterates BREADTH-FIRST across CITY_POOL: every city's page 1 comes
    before ANY city's page 2, page 2 across every city before page 3,
    and so on, up to MAX_PAGES_PER_CITY. This is a fix for "عايزة يسيرش
    التوب 100 مدينة" (search across the top 100 cities) - the previous
    DEPTH-first order (page through all MAX_PAGES_PER_CITY pages of city
    1, then city 2, ...) meant a moderate lead-count target got fully
    absorbed by the first 2-3 cities in CITY_POOL and never reached most
    of it; asking for the same number of leads now spreads across far
    more cities first, and only pages deeper into cities already covered
    once every city has at least one page queued.

    "target_results" is a target to page toward, not a guarantee - the
    real ceiling is however many businesses actually exist in
    yellowpages.com's index for that niche/city combination.
    """
    term = NICHE_SEARCH_TERMS.get(niche_name)
    if not term:
        return []
    urls_needed = min(max(1, -(-target_results // RESULTS_PER_PAGE)), max_urls)  # ceil division
    urls: list[str] = []
    for page in range(1, MAX_PAGES_PER_CITY + 1):
        for city, state in CITY_POOL:
            urls.append(_yp_search_url(term, city, state, page))
            if len(urls) >= urls_needed:
                return urls
    return urls  # exhausted the whole city pool before reaching urls_needed


def generate_niche_urls_yelp(niche_name: str, target_results: int, max_urls: int = MAX_URLS_ALL_CITIES) -> list[str]:
    """Same idea as generate_niche_urls() but for yelp.com (10 results
    per page via &start=10,20,...  instead of yellowpages' &page=2,3...),
    and the same breadth-first-across-CITY_POOL ordering (see that
    function's docstring) - every city's first page before any city's
    second page. Each URL here costs LOGY a SECOND fetch per lead found
    on it (see _YELP_DETAIL_CONFIG) to get the phone number, so a
    Yelp-sourced run is slower than the equivalent yellowpages one for
    the same lead count - that trade-off is what the user asked for
    (full phone numbers over raw speed)."""
    term = NICHE_SEARCH_TERMS.get(niche_name)
    if not term:
        return []
    urls_needed = min(max(1, -(-target_results // YELP_RESULTS_PER_PAGE)), max_urls)
    urls: list[str] = []
    for page_i in range(MAX_PAGES_PER_CITY):
        for city, state in CITY_POOL:
            urls.append(_yelp_search_url(term, city, state, start=page_i * YELP_RESULTS_PER_PAGE))
            if len(urls) >= urls_needed:
                return urls
    return urls


# "عايز كل اللينكات الممكنة في وقت واحد مش يمشي عليها واحد واحد" - run
# yellowpages.com and Yelp URLs together in ONE job instead of one source
# at a time. A single job can only apply one container/field selector set
# per fetched page, and yellowpages' ".result" selector matches nothing
# on yelp.com's markup (and vice versa) - so mixing URLs from both sites
# into one list only works if each URL still gets its OWN site's
# selectors. SOURCE_PROFILES is exactly that lookup table:
# job_manager.ScrapeJobWorker._resolve_source() matches each URL's domain
# against this list before extracting that page, so one combined run
# extracts yellowpages pages with yellowpages' selectors and Yelp pages
# with Yelp's selectors, merging everything into the same job's results
# (and therefore the same CSV/XLSX export) automatically. Add a new
# entry here (domain + container + fields + detail_config) whenever a
# new source is wired up, and "Load All Sources" / "All Sources" niche
# picks it up with no other code changes.
SOURCE_PROFILES: list[dict] = [
    {"name": "yellowpages", "domain": "yellowpages.com", "container": _YP_CONTAINER,
     "fields": _YP_LEAD_FIELDS, "detail_config": None},
    {"name": "yelp", "domain": "yelp.com", "container": _YELP_CONTAINER,
     "fields": _YELP_LEAD_FIELDS, "detail_config": _YELP_DETAIL_CONFIG},
]


def generate_niche_urls_all_sources(niche_name: str, target_results: int, max_urls: int = MAX_URLS_ALL_CITIES * 2) -> list[str]:
    """Combined yellowpages.com + Yelp URL list for one niche, sized so
    the two sources split target_results roughly evenly between them (a
    yellowpages page is worth RESULTS_PER_PAGE leads, a Yelp page only
    YELP_RESULTS_PER_PAGE - each generator already accounts for its own
    page size, so this just calls both with half the target and
    concatenates). The result mixes both domains in one flat list -
    that's the point (see SOURCE_PROFILES above): a single job whose
    start_urls list is exactly this can extract every URL correctly in
    one run instead of needing two separate runs merged by hand
    afterward."""
    half = max(1, target_results // 2)
    yp_max = max(1, max_urls // 2)
    yp_urls = generate_niche_urls(niche_name, half, max_urls=yp_max)
    yelp_urls = generate_niche_urls_yelp(niche_name, half, max_urls=max(1, max_urls - len(yp_urls)))
    return yp_urls + yelp_urls


BUILTIN_TEMPLATES: list[dict] = [
    {
        "name": f"SEO Leads - {niche}",
        "fields": _YP_LEAD_FIELDS,
        "icp": True,
        "fee_hint": fee,
        "source": "yellowpages",
        "container": _YP_CONTAINER,
        "start_urls": _niche_start_urls(term, CITY_POOL[:2]),
        "detail_config": None,
    }
    for niche, fee, term in ICP_NICHES
] + [
    {
        "name": f"SEO Leads (Yelp) - {niche}",
        "fields": _YELP_LEAD_FIELDS,
        "icp": True,
        "fee_hint": fee,
        "source": "yelp",
        "container": _YELP_CONTAINER,
        "start_urls": [_yelp_search_url(term, city, state) for city, state in CITY_POOL[:2]],
        "detail_config": _YELP_DETAIL_CONFIG,
    }
    for niche, fee, term in ICP_NICHES
] + [
    {
        "name": "Business Leads (generic)",
        "fields": [
            ExtractionField(name="company_name", selector=""),
            ExtractionField(name="email", selector=""),
            ExtractionField(name="phone", selector=""),
            ExtractionField(name="website", selector="", extraction_type=ExtractionType.ATTRIBUTE, attribute="href"),
            ExtractionField(name="address", selector=""),
        ],
    },
    {
        "name": "Company Websites",
        "fields": [
            ExtractionField(name="company_name", selector=""),
            ExtractionField(name="website", selector="", extraction_type=ExtractionType.ATTRIBUTE, attribute="href"),
            ExtractionField(name="industry", selector=""),
        ],
    },
    {
        "name": "Contact Information",
        "fields": [
            ExtractionField(name="name", selector=""),
            ExtractionField(name="email", selector=""),
            ExtractionField(name="phone", selector=""),
        ],
    },
    {
        "name": "Product Data",
        "fields": [
            ExtractionField(name="title", selector=""),
            ExtractionField(name="price", selector=""),
            ExtractionField(name="image", selector="", extraction_type=ExtractionType.ATTRIBUTE, attribute="src"),
            ExtractionField(name="description", selector=""),
        ],
    },
    {
        "name": "Directory Listings",
        "fields": [
            ExtractionField(name="name", selector=""),
            ExtractionField(name="category", selector=""),
            ExtractionField(name="address", selector=""),
            ExtractionField(name="phone", selector=""),
        ],
    },
    {
        "name": "Custom",
        "fields": [],
    },
]


def seed_builtin_templates(db) -> None:
    """Insert any builtin template that doesn't exist yet, AND refresh the
    config of ones that do. The refresh matters in practice: templates
    ship with new/updated selectors and URLs over time (yellowpages
    started as empty placeholders, then got real selectors; Yelp was
    added later still) - without this upgrade path, an app that already
    ran once would stay stuck on whatever version it first saw, forever,
    since create_template only INSERTs. update_template_config() only
    touches builtin=1 rows, so a user's own custom templates are never
    affected."""
    existing = {t["name"] for t in db.list_templates()}
    for tpl in BUILTIN_TEMPLATES:
        config = {
            "fields": [f.to_dict() for f in tpl["fields"]],
            "icp": tpl.get("icp", False),
            "fee_hint": tpl.get("fee_hint", ""),
            "source": tpl.get("source", ""),
            "container": tpl.get("container"),
            "start_urls": tpl.get("start_urls", []),
            "detail_config": tpl.get("detail_config"),
        }
        if tpl["name"] in existing:
            db.update_template_config(tpl["name"], config)
        else:
            db.create_template(tpl["name"], config, builtin=True)
