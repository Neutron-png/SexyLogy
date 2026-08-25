from app.core.engine.qualifier import qualify_html

STRONG_SITE = """
<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acme Pool Builders - Custom Pools in Austin, TX</title>
<meta name="description" content="Austin's top-rated custom pool builder. Get a free quote today.">
<script type="application/ld+json">{"@type": "LocalBusiness", "name": "Acme Pool Builders"}</script>
<script async src="https://www.googletagmanager.com/gtag/js"></script>
</head><body><h1>Custom Pools</h1></body></html>
"""

WEAK_SITE = """
<html><head><title></title></head>
<body><table><tr><td>Welcome to our site</td></tr></table></body></html>
"""

NO_TITLE_BUT_HAS_MOST = """
<html><head>
<meta name="viewport" content="width=device-width">
<meta name="description" content="We build things">
<title>Home</title>
</head><body><h1>Home</h1></body></html>
"""


def test_no_website_is_highest_priority():
    result = qualify_html(None)
    assert result.has_website is False
    assert result.score == 100
    assert "No website" in result.label


def test_strong_modern_site_scores_low():
    result = qualify_html(STRONG_SITE)
    assert result.has_website is True
    assert result.score <= 15  # only missing an explicit https:// literal in this fixture's markup
    assert "Strong site" in result.label or "Some gaps" in result.label


def test_weak_legacy_site_scores_high():
    result = qualify_html(WEAK_SITE)
    assert result.has_website is True
    assert result.score >= 60
    assert "Not mobile-friendly (no responsive viewport tag)" in result.signals
    assert "No meta description (weak SEO basics)" in result.signals


def test_short_generic_title_flagged():
    result = qualify_html(NO_TITLE_BUT_HAS_MOST)
    assert any("too short" in s for s in result.signals)


def test_empty_string_treated_as_no_website():
    result = qualify_html("   ")
    assert result.has_website is False
    assert result.score == 100
