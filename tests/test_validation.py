from app.utils.validation import (
    is_valid_url, dedupe_urls, parse_url_list, validate_json_schema, validate_selector,
)


def test_is_valid_url():
    assert is_valid_url("https://example.com")
    assert is_valid_url("http://example.com/path?q=1")
    assert not is_valid_url("not a url")
    assert not is_valid_url("ftp://example.com")
    assert not is_valid_url("")


def test_dedupe_urls():
    assert dedupe_urls(["https://a.com", "https://a.com", "https://b.com"]) == [
        "https://a.com", "https://b.com",
    ]


def test_parse_url_list_splits_valid_and_invalid():
    valid, invalid = parse_url_list("https://example.com\nnot-a-url\nhttps://example.com/products")
    assert valid == ["https://example.com", "https://example.com/products"]
    assert invalid == ["not-a-url"]


def test_validate_json_schema_ok():
    ok, err, parsed = validate_json_schema('{"company_name": "string", "email": "string"}')
    assert ok and err == ""
    assert parsed == {"company_name": "string", "email": "string"}


def test_validate_json_schema_bad_json():
    ok, err, parsed = validate_json_schema("{not valid json")
    assert not ok
    assert "JSON" in err


def test_validate_json_schema_bad_type():
    ok, err, parsed = validate_json_schema('{"field": "not_a_type"}')
    assert not ok


def test_validate_selector():
    ok, _ = validate_selector(".product", "css")
    assert ok
    ok, _ = validate_selector("//div[@class='x']", "xpath")
    assert ok
    ok, err = validate_selector("", "css")
    assert not ok
