from app.core.engine.scrapling_adapter import html_to_text

SAMPLE = """
<html><head><title>Acme</title><style>.x{color:red}</style></head>
<body>
<script>var x = 1;</script>
<h1>Acme Contracting</h1>
<p>Call us at 555-123-4567 or email owner@acme.com</p>
</body></html>
"""


def test_strips_script_and_style_content():
    text = html_to_text(SAMPLE)
    assert "color:red" not in text
    assert "var x = 1" not in text


def test_keeps_visible_text():
    text = html_to_text(SAMPLE)
    assert "Acme Contracting" in text
    assert "555-123-4567" in text
    assert "owner@acme.com" in text


def test_empty_html_returns_empty_text():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""
