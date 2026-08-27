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

Three independent sources are wired up, each captured live from a real
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
  re-capturing sooner than the yellowpages ones. Re-confirmed live on
  2026-08-27 - still matching today, no re-capture needed yet.

  thumbtack.com - name + rating + a link back to its own profile page
  only. This site never publicly renders a phone number or website at
  all (it's a lead-request marketplace, not a directory - contact only
  happens through its own gated "Message"/"Request a call" flow,
  confirmed by opening a real pro's profile page live), so those two
  fields are intentionally absent rather than guessed - see the
  _THUMBTACK_CONTAINER docstring below for the full detail.

Captured live on 2026-08-19 (yellowpages/Yelp) and 2026-08-27
(thumbtack, plus a re-confirmation pass on yellowpages/Yelp). Confirmed
against real listings, e.g. "Athena Pools LLC" / (512) 914-0554 /
athenapools.com (yellowpages, pool builders, Austin TX), "Diaz
Foundation Repair" (yelp.com, foundation repair, Dallas TX, phone pulled
from its own business page), and "Dreemer Pool & Remodeling" / "Gold
Standard Pool and Tile" (thumbtack.com, pool installation, Austin TX).
Two other sources were investigated and rejected: LinkedIn (explicitly out of scope - see
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
import json
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

# ---------------------------------------------------------------------
# thumbtack.com - captured live via a real Chrome browser session
# (claude-in-chrome, not a raw HTTP fetch) on 2026-08-27, since
# thumbtack.com's bot defenses block every direct WebFetch/curl-style
# attempt from this environment (even its robots.txt request comes back
# as a bot challenge, not the file) - a real rendered browser tab was
# able to get past that where a raw fetch could not, letting the actual
# DOM finally be read.
#
#   URL PATTERN: confirmed live for both categories checked
#   (thumbtack.com/tx/austin/pool-installation,
#   thumbtack.com/tx/austin/kitchen-remodeling - both real, working
#   pages) - all follow the /<state-abbr>/<city-slug>/<niche-slug> shape
#   built by _thumbtack_url()/generate_niche_urls_thumbtack() below, the
#   same way _yp_search_url()/_yelp_search_url() do for their sites.
#   IMPORTANT caveat found live: thumbtack's own category slugs don't
#   always match yellowpages/Yelp's search terms exactly - e.g. "pool
#   builders" 404s and thumbtack silently redirects to its real category
#   "pool-installation" instead (still real content, just a slightly
#   different category than the literal term), while a niche it has NO
#   category for at all (tried: "car-dealership") returns a genuine 404
#   page with zero matching containers, so extract_records() naturally
#   returns zero records for it rather than fabricating anything - a
#   handful of the 15 ICP niches (the medical/legal/dealership ones
#   especially - Thumbtack is a home/personal-services marketplace, not
#   a directory for those) may simply have no real Thumbtack category
#   and will legitimately yield 0 thumbtack.com leads while yellowpages/
#   Yelp still cover them normally. No confirmed pagination parameter
#   exists for this page type (search results are titled "The 10 Best X
#   in <City>" - a single top-10 list, not an obviously paginated one) -
#   same first-page-only-per-city honesty disclosure as the Houzz note
#   further down, not hidden as if it were equivalent to yellowpages'
#   MAX_PAGES_PER_CITY-deep coverage.
#
#   SELECTORS: now genuinely captured live (verified against 10 real pro
#   cards across the two category pages above):
#     - container: `div.bb.b-gray-300.pv3.m_pv4` - one per listed pro.
#       These are Tachyons-style utility classes (border-bottom + border
#       color + padding), not a semantic "pro-card"-style name, so
#       there's a real (if currently unobserved) chance of an unrelated
#       page section reusing the exact same 4-class combo elsewhere -
#       re-check this first if thumbtack results start coming back empty
#       or wrong.
#     - business_name: `.pro-title div.dib` - Thumbtack renders EVERY
#       pro name TWICE inside `.pro-title` (a desktop-visible copy and a
#       mobile-visible copy, both present in the raw HTML, CSS just
#       toggles which one shows) - selecting `.pro-title` alone
#       concatenates both copies into one doubled string
#       ("NameNameHere"). `div.dib` (Tachyons "display:inline-block",
#       present ONLY on the desktop copy) picks out just one of the two.
#     - rating: `.pro-ratings` - e.g. "Exceptional 5.0(21)" (rating word
#       + score + review count together as one string, comma-free so it
#       won't collide with CSV export - there's no separate stable hook
#       to split the score from the review count, so this ships as one
#       combined field rather than a fragile guess at splitting it).
#       Not a "lead" field on its own but useful qualification context.
#     - thumbtack_profile_url: `a` (the single link wrapping the whole
#       card), extraction_type=ATTRIBUTE/href - a RELATIVE path into
#       thumbtack.com itself (e.g.
#       "/tx/austin/swimming-pool-maintenance/some-pro/service/12345"),
#       not an external site - Thumbtack is a lead-marketplace, not a
#       directory of external listings.
#     - phone / website: DELIBERATELY NOT INCLUDED, and this is not a
#       "selectors still missing" gap the way it was before - a real
#       Thumbtack pro profile page was opened live (Dreemer Pool &
#       Remodeling's) and neither a phone number nor an external website
#       link appears ANYWHERE on it, public or otherwise: contact only
#       happens through Thumbtack's own gated "Message" / "Request a
#       call" flow. No CSS selector, however well captured, can extract
#       data that was never rendered on the page - shipping empty phone/
#       website fields "waiting to be filled in" here would misrepresent
#       a structural fact about the site as an unfinished selector.
#   "verified": True on its SOURCE_PROFILES entry below reflects this -
#   the selectors themselves are now real and confirmed, same standing
#   as yellowpages/Yelp, just for a narrower field set than either.
# ---------------------------------------------------------------------
_THUMBTACK_CONTAINER = {"selector": "div.bb.b-gray-300.pv3.m_pv4", "type": "css"}
_THUMBTACK_LEAD_FIELDS = [
    ExtractionField(name="business_name", selector=".pro-title div.dib"),
    ExtractionField(name="rating", selector=".pro-ratings"),
    ExtractionField(
        name="thumbtack_profile_url", selector="a",
        extraction_type=ExtractionType.ATTRIBUTE, attribute="href",
    ),
]
THUMBTACK_CONTAINER = _THUMBTACK_CONTAINER
THUMBTACK_LEAD_FIELDS = _THUMBTACK_LEAD_FIELDS

THUMBTACK_RESULTS_PER_PAGE = 10  # "The 10 Best X in <City>" - thumbtack's own page title pattern


def _thumbtack_slug(text: str) -> str:
    """'Pool Builders' -> 'pool-builders', matching the real slug shape
    seen in live thumbtack.com URLs (thumbtack.com/tx/austin/pool-builders,
    thumbtack.com/tx/san-antonio/pool-builders - confirmed via search,
    not fetched directly, see the module note above)."""
    slug = text.strip().lower().replace("/", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in " -")
    return "-".join(slug.split())


def _thumbtack_url(term: str, city: str, state: str) -> str | None:
    """Builds a thumbtack.com city+niche page URL, or None if `state` is
    blank - thumbtack's URL shape requires a state abbreviation
    (/<state>/<city>/<niche>/), unlike yellowpages/yelp's query-string
    search which still works with just a city name."""
    if not state:
        return None
    return f"https://www.thumbtack.com/{_thumbtack_slug(state)}/{_thumbtack_slug(city)}/{_thumbtack_slug(term)}/"


def generate_niche_urls_thumbtack(
    niche_name: str, target_results: int, max_urls: int | None = None,
    cities: list[tuple[str, str]] | None = None,
) -> list[str]:
    """One thumbtack.com city+niche page per city (~THUMBTACK_RESULTS_PER_PAGE
    leads each - no confirmed pagination for this page type, see the
    module note above), breadth across the city list the same way
    generate_niche_urls()/_yelp() do. Cities with no state abbreviation
    are skipped (see _thumbtack_url()) rather than producing a broken
    URL. `cities`/`target_results`/`max_urls` behave the same as the
    other two generators. max_urls defaults to MAX_URLS_ALL_CITIES,
    resolved lazily here (not as the parameter default) since that
    constant is defined further down this module, after this function."""
    if max_urls is None:
        max_urls = MAX_URLS_ALL_CITIES
    term = NICHE_SEARCH_TERMS.get(niche_name)
    if not term:
        return []
    pool = cities if cities else CITY_POOL
    urls_needed = min(max(1, -(-target_results // THUMBTACK_RESULTS_PER_PAGE)), max_urls)
    urls: list[str] = []
    for city, state in pool:
        url = _thumbtack_url(term, city, state)
        if url is None:
            continue
        urls.append(url)
        if len(urls) >= urls_needed:
            return urls
    return urls


def resolve_cities(text: str) -> list[tuple[str, str]]:
    """Parses a user-typed city list (one per line, or separated by ';')
    into (city, state) pairs for generate_niche_urls()/_yelp()/
    _all_sources() below - the "خليني اقدر احدد المدن اللي محتاجها" city
    picker in New Scrape's Quick Start card. Each entry's LAST comma
    splits city from state/province - "New York, NY" -> ("New York",
    "NY"); an entry with no comma is kept as (entry, "") so a bare city
    name still builds a working search URL (yellowpages/yelp's own geo
    search just degrades to a wider match without a state, it doesn't
    error). Not limited to CITY_POOL - any city text the user types is
    used as-is, so this also covers cities/regions LOGY doesn't ship in
    its own top-100 list.

    Blank/whitespace-only input returns [] - the caller's cue to fall
    back to the full CITY_POOL ("leave empty for all top cities")."""
    if not text or not text.strip():
        return []
    entries: list[tuple[str, str]] = []
    for line in text.replace(";", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        if "," in line:
            city, state = line.rsplit(",", 1)
            entries.append((city.strip(), state.strip()))
        else:
            entries.append((line, ""))
    return entries

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

# "عايزة يطلع لكل مدينة على الاقل 3000 ليد او على الاقل يبقى عندها
# المقدرة لكدا" - the user wants the CAPABILITY to page a single city
# this deep, not to be capped at a handful of pages before LOGY moves on
# to the next city. ceil(3000 / RESULTS_PER_PAGE) = 100 pages covers that
# for yellowpages.com (30/page); ceil(3000 / YELP_RESULTS_PER_PAGE) = 300
# covers the same target for Yelp (10/page) - 300 is the shared value so
# neither source falls short of it.
#
# This was previously capped at 5: a live check found that requesting
# more pages than a search actually had for one city+niche
# (yellowpages: page 7 of a 165-result, ~6-page search still returned 26
# rows instead of running out) returned STALE/REPEATED results past the
# real end of that list. That risk hasn't gone away - most real
# city+niche combinations on any of these 3 free directories have nowhere
# near 3000 distinct businesses, so a run this deep WILL spend most of
# its fetches re-reading the same tail-end rows once the real listings
# run out. What changed is that this project's lead-history de-dup layer
# (see app/core/storage/db.py's lead_history table +
# ScrapeOptions.skip_duplicate_leads, wired into job_manager.py) now
# exists specifically to make that safe: a re-fetched duplicate lead is
# recognized and skipped rather than saved twice, so paging this deep no
# longer risks a result file full of repeats - the cost is only extra
# fetch time, not bad data. 3000 leads for ONE city+niche pair is still
# not a promise LOGY can make (it depends entirely on how many real
# businesses that directory actually lists there), only a depth ceiling
# LOGY will now page all the way down to if that many real, distinct
# results exist. Applied to both sources for consistency.
MAX_PAGES_PER_CITY = 300

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


def generate_niche_urls(
    niche_name: str, target_results: int, max_urls: int = MAX_URLS_ALL_CITIES,
    cities: list[tuple[str, str]] | None = None,
) -> list[str]:
    """
    Build enough yellowpages.com search-result-page URLs to cover
    approximately `target_results` leads for the given niche (bare name,
    e.g. "Foundation Repair" - matches the keys in NICHE_SEARCH_TERMS).

    Iterates BREADTH-FIRST across the city list: every city's page 1
    comes before ANY city's page 2, page 2 across every city before page
    3, and so on, up to MAX_PAGES_PER_CITY. This is a fix for "عايزة
    يسيرش التوب 100 مدينة" (search across the top 100 cities) - the
    previous DEPTH-first order (page through all MAX_PAGES_PER_CITY
    pages of city 1, then city 2, ...) meant a moderate lead-count
    target got fully absorbed by the first 2-3 cities and never reached
    most of it; asking for the same number of leads now spreads across
    far more cities first, and only pages deeper into cities already
    covered once every city has at least one page queued.

    `cities`: optional (city, state) pairs to restrict the search to -
    "خليني اقدر احدد المدن اللي محتاجها". None/omitted uses the full
    CITY_POOL (the previous, only, behavior); see resolve_cities() above
    for turning a user-typed city list into this shape. Passing a
    smaller list means fewer real businesses exist to find, so the
    result naturally tops out well below `target_results` - that's
    expected, not a bug (see the note below).

    "target_results" is a target to page toward, not a guarantee - the
    real ceiling is however many businesses actually exist in
    yellowpages.com's index for that niche/city combination.
    """
    term = NICHE_SEARCH_TERMS.get(niche_name)
    if not term:
        return []
    pool = cities if cities else CITY_POOL
    urls_needed = min(max(1, -(-target_results // RESULTS_PER_PAGE)), max_urls)  # ceil division
    urls: list[str] = []
    for page in range(1, MAX_PAGES_PER_CITY + 1):
        for city, state in pool:
            urls.append(_yp_search_url(term, city, state, page))
            if len(urls) >= urls_needed:
                return urls
    return urls  # exhausted the whole city pool before reaching urls_needed


def generate_niche_urls_yelp(
    niche_name: str, target_results: int, max_urls: int = MAX_URLS_ALL_CITIES,
    cities: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Same idea as generate_niche_urls() but for yelp.com (10 results
    per page via &start=10,20,...  instead of yellowpages' &page=2,3...),
    and the same breadth-first-across-city-list ordering (see that
    function's docstring) - every city's first page before any city's
    second page. `cities` works the same way as generate_niche_urls()'s
    (None = full CITY_POOL). Each URL here costs LOGY a SECOND fetch per
    lead found on it (see _YELP_DETAIL_CONFIG) to get the phone number,
    so a Yelp-sourced run is slower than the equivalent yellowpages one
    for the same lead count - that trade-off is what the user asked for
    (full phone numbers over raw speed)."""
    term = NICHE_SEARCH_TERMS.get(niche_name)
    if not term:
        return []
    pool = cities if cities else CITY_POOL
    urls_needed = min(max(1, -(-target_results // YELP_RESULTS_PER_PAGE)), max_urls)
    urls: list[str] = []
    for page_i in range(MAX_PAGES_PER_CITY):
        for city, state in pool:
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
     "fields": _YP_LEAD_FIELDS, "detail_config": None, "verified": True},
    {"name": "yelp", "domain": "yelp.com", "container": _YELP_CONTAINER,
     "fields": _YELP_LEAD_FIELDS, "detail_config": _YELP_DETAIL_CONFIG, "verified": True},
    {"name": "thumbtack", "domain": "thumbtack.com", "container": _THUMBTACK_CONTAINER,
     "fields": _THUMBTACK_LEAD_FIELDS, "detail_config": None, "verified": True},
]


def get_all_source_profiles(db) -> list[dict]:
    """SOURCE_PROFILES (built-in: yellowpages, yelp, thumbtack) PLUS any
    source the user added from inside the app - "خليني اقدر من جوا اضيف
    مصادر جديدة": New Scrape's Sources card -> "+ Add Source", persisted
    via Database.create_custom_source()/list_custom_sources(). Together
    this is the single list "Load All Sources (combined)" and Quick
    Start's "All Sources" niche option should iterate, so a user-added
    source participates in a combined run exactly like the built-in ones
    do - see job_manager.ScrapeJobWorker._resolve_source(), which picks
    a fetched URL's selectors by matching its domain against this same
    list. Custom rows are converted from their stored JSON shape into
    SOURCE_PROFILES' own dict shape, with "fields" turned back into
    ExtractionField objects (not raw dicts) since that's what the
    extractor consumes - see job_manager.py.

    Custom sources are listed BEFORE the built-ins, and _resolve_source()
    in job_manager.py returns on the FIRST domain match - so a
    user-added source for the same domain as a built-in (e.g. filling in
    real selectors for "thumbtack.com", which ships with empty ones -
    see _THUMBTACK_CONTAINER above) correctly WINS over the empty
    built-in stub instead of that stub always matching first."""
    custom_profiles = []
    for row in db.list_custom_sources():
        detail_json = row.get("detail_config_json")
        custom_profiles.append({
            "name": row["name"],
            "domain": row["domain"],
            "container": json.loads(row["container_json"]),
            "fields": [ExtractionField.from_dict(f) for f in json.loads(row["fields_json"])],
            "detail_config": json.loads(detail_json) if detail_json else None,
            "verified": False,  # user-added - not captured/confirmed the way built-ins are
        })
    custom_domains = {p["domain"] for p in custom_profiles}
    builtins = [p for p in SOURCE_PROFILES if p["domain"] not in custom_domains]
    return custom_profiles + builtins


def generate_niche_urls_all_sources(
    niche_name: str, target_results: int, max_urls: int = MAX_URLS_ALL_CITIES * 2,
    cities: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Combined yellowpages.com + Yelp + thumbtack.com URL list for one
    niche, sized so the three sources split target_results roughly
    evenly between them (each generator already accounts for its own
    page size - RESULTS_PER_PAGE / YELP_RESULTS_PER_PAGE /
    THUMBTACK_RESULTS_PER_PAGE - so this just calls all three with a
    third of the target and concatenates). The result mixes all three
    domains in one flat list - that's the point (see SOURCE_PROFILES
    above): a single job whose start_urls list is exactly this can
    extract every URL correctly in one run instead of needing three
    separate runs merged by hand afterward. This is what Quick Start's
    per-niche picker calls automatically now (no more separate
    "yellowpages-only" / "Yelp-only" / "All Sources" choices to make per
    niche - see new_scrape.py's niche_combo). `cities` (see
    resolve_cities()) is forwarded unchanged to every sub-call so a city
    restriction applies to all three sources at once. thumbtack's own
    selectors are still unverified (see the module note above on
    _THUMBTACK_CONTAINER) - its URLs are included here for when they're
    filled in, but until then its pages will fetch fine and simply
    extract 0 records, same as any source with an empty container.
    A source added via the Sources card (other than the three built-ins)
    doesn't have a per-niche URL generator of its own - it only ever
    comes into a combined run via its own start_urls the user pastes in,
    since URL PATTERNS differ per site in a way generate_niche_urls()
    can't guess for an arbitrary new domain."""
    third = max(1, target_results // 3)
    yp_max = max(1, max_urls // 3)
    yp_urls = generate_niche_urls(niche_name, third, max_urls=yp_max, cities=cities)
    remaining = max(1, max_urls - len(yp_urls))
    yelp_max = max(1, remaining // 2)
    yelp_urls = generate_niche_urls_yelp(niche_name, third, max_urls=yelp_max, cities=cities)
    thumbtack_urls = generate_niche_urls_thumbtack(
        niche_name, third, max_urls=max(1, max_urls - len(yp_urls) - len(yelp_urls)), cities=cities,
    )
    return yp_urls + yelp_urls + thumbtack_urls


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
