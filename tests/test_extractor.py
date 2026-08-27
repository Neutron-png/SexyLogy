from app.core.engine.extractor import extract_fields, extract_records, ExtractionError
from app.core.models import ExtractionField, ExtractionType
from tests.fake_selector import FakeSelector

SAMPLE_HTML = """
<html><body>
<div class="product-card">
  <h2 class="name">Wireless Mouse</h2>
  <span class="price">$19.99</span>
  <a class="link" href="/products/mouse">View</a>
</div>
<div class="product-card">
  <h2 class="name">Mechanical Keyboard</h2>
  <span class="price">$89.00</span>
  <a class="link" href="/products/keyboard">View</a>
</div>
</body></html>
"""


def test_extract_fields_single_page():
    page = FakeSelector(SAMPLE_HTML)
    fields = [
        ExtractionField(name="name", selector=".product-card .name"),
        ExtractionField(name="price", selector=".product-card .price"),
    ]
    record = extract_fields(page, fields)
    assert record["name"] == "Wireless Mouse"
    assert record["price"] == "$19.99"


def test_extract_fields_attribute():
    page = FakeSelector(SAMPLE_HTML)
    fields = [ExtractionField(name="url", selector=".link", extraction_type=ExtractionType.ATTRIBUTE, attribute="href")]
    record = extract_fields(page, fields)
    assert record["url"] == "/products/mouse"


def test_extract_fields_multiple():
    page = FakeSelector(SAMPLE_HTML)
    fields = [ExtractionField(name="names", selector=".name", multiple=True)]
    record = extract_fields(page, fields)
    assert record["names"] == ["Wireless Mouse", "Mechanical Keyboard"]


def test_extract_records_per_container():
    page = FakeSelector(SAMPLE_HTML)
    fields = [
        ExtractionField(name="name", selector=".name"),
        ExtractionField(name="price", selector=".price"),
    ]
    records = extract_records(page, ".product-card", "css", fields)
    assert len(records) == 2
    assert records[0]["name"] == "Wireless Mouse"
    assert records[1]["name"] == "Mechanical Keyboard"
    assert records[0]["price"] == "$19.99"
    assert records[1]["price"] == "$89.00"


def test_extract_fields_missing_selector_returns_none_not_crash():
    page = FakeSelector(SAMPLE_HTML)
    fields = [ExtractionField(name="missing", selector=".does-not-exist")]
    record = extract_fields(page, fields)
    assert record["missing"] is None


def test_bad_css_selector_raises_extraction_error():
    page = FakeSelector(SAMPLE_HTML)
    fields = [ExtractionField(name="bad", selector=":::not-valid:::")]
    try:
        extract_fields(page, fields)
        assert False, "expected ExtractionError"
    except ExtractionError as e:
        assert e.field_name == "bad"


# Real markup shape from yellowpages.com search results (see
# app/core/engine/builtin_templates.py) - the business name text sits one
# level deeper than the matched element (inside a <span>), and the phone
# number's own div carries three classes at once. This is a regression
# test for a real, shipped bug: TEXT-type fields used to return the
# matched element's raw outer HTML (e.g.
# '<a class="business-name"...><span>Holy Drilling</span></a>') instead
# of the clean visible text - visible directly in an exported CSV a user
# sent back. Scrapling's Selector API follows scrapy/parsel conventions
# where .get() on an element match returns outer HTML, not text; text
# needs an explicit '::text' query, which is what extract_fields() now
# does under the hood (see extractor._element_text()).
DIRECTORY_LISTING_HTML = """
<html><body>
<div class="result">
  <a class="business-name" href="/dallas-tx/mip/holy-drilling-505053797">
    <span>Holy Drilling</span>
  </a>
  <div class="phones phone primary">(469) 983-5146</div>
  <div class="adr">
    <div class="street-address">381 Casa Linda Plz</div>
    <div class="locality">Dallas, TX 75218</div>
  </div>
</div>
</body></html>
"""


def test_extract_records_skips_near_empty_ghost_containers():
    """Regression test: a real yellowpages.com results page was observed
    rendering a near-duplicate, mostly-empty '.result' element (name
    only) right before the real listing card for the same business - see
    extract_records()'s docstring. A container that only yields one
    non-empty field should be dropped rather than becoming a junk row."""
    html = """
    <html><body>
    <div class="result"><a class="business-name">Holy Drilling</a></div>
    <div class="result">
      <a class="business-name">Holy Drilling</a>
      <div class="phones phone primary">(469) 983-5146</div>
    </div>
    </body></html>
    """
    page = FakeSelector(html)
    fields = [
        ExtractionField(name="business_name", selector=".business-name"),
        ExtractionField(name="phone", selector=".phones.phone.primary"),
    ]
    records = extract_records(page, ".result", "css", fields)
    assert len(records) == 1
    assert records[0]["business_name"] == "Holy Drilling"
    assert records[0]["phone"] == "(469) 983-5146"


def test_text_field_returns_clean_text_not_raw_html_for_nested_markup():
    page = FakeSelector(DIRECTORY_LISTING_HTML)
    fields = [
        ExtractionField(name="business_name", selector=".business-name"),
        ExtractionField(name="phone", selector=".phones.phone.primary"),
        ExtractionField(name="street", selector=".adr .street-address"),
    ]
    record = extract_fields(page, fields)
    assert record["business_name"] == "Holy Drilling"
    assert "<" not in record["business_name"]
    assert record["phone"] == "(469) 983-5146"
    assert "<" not in record["phone"]
    assert record["street"] == "381 Casa Linda Plz"


# thumbtack.com renders every pro's name TWICE inside one wrapper - a
# desktop-visible copy and a mobile-visible copy, both present in the raw
# HTML with CSS just toggling which one shows (confirmed live via a real
# browser session on 2026-08-27 - see builtin_templates.py's
# _THUMBTACK_CONTAINER docstring). A selector on the shared wrapper alone
# (".pro-title") would concatenate BOTH copies' text into one doubled
# string - this is a regression test for the ".pro-title div.dib" fix
# that picks out only the desktop copy.
THUMBTACK_CARD_HTML = """
<html><body>
<div class="bb b-gray-300 pv3 m_pv4">
  <a href="/tx/austin/swimming-pool-maintenance/dreemer-pool-remodeling/service/554309614018969613">
    <div class="pro-title mr1 black hover-blue">
      <span class="dib m_dn tp-body-2 pre"></span>
      <div class="Type_title7__9t_vN dib s_dn">Dreemer Pool &amp; Remodeling</div>
      <span class="dn s_dib tp-body-1 pre"></span>
      <div class="Type_title6__pMyYO dn s_dib">Dreemer Pool &amp; Remodeling</div>
    </div>
    <div class="pro-ratings flex items-center">
      <p class="Type_text1__634gq">Exceptional 5.0</p>
      <p class="Type_text2__2_pIm flex items-center black-300">(21)</p>
    </div>
  </a>
</div>
</body></html>
"""


def test_thumbtack_business_name_selector_avoids_duplicate_responsive_copy():
    page = FakeSelector(THUMBTACK_CARD_HTML)
    fields = [
        ExtractionField(name="business_name", selector=".pro-title div.dib"),
        ExtractionField(name="rating", selector=".pro-ratings"),
        ExtractionField(
            name="thumbtack_profile_url", selector="a",
            extraction_type=ExtractionType.ATTRIBUTE, attribute="href",
        ),
    ]
    records = extract_records(page, "div.bb.b-gray-300.pv3.m_pv4", "css", fields)
    assert len(records) == 1
    assert records[0]["business_name"] == "Dreemer Pool & Remodeling"
    assert "RemodelingDreemer" not in records[0]["business_name"]  # the doubled-text bug this guards against
    assert records[0]["rating"] == "Exceptional 5.0 (21)"
    assert records[0]["thumbtack_profile_url"].startswith("/tx/austin/")
