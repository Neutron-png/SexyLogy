import json
from unittest.mock import patch, MagicMock

from app.core.engine.ai_extractor import (
    build_prompt, parse_json_response, extract, extract_with_anthropic,
    extract_with_openai, AIExtractionError, DEFAULT_FIELD_NAMES,
)


def test_build_prompt_includes_fields_and_text():
    prompt = build_prompt("Contact John Doe at john@acme.com", ["owner_name", "email"])
    assert "owner_name, email" in prompt
    assert "john@acme.com" in prompt
    assert "JSON object" in prompt


def test_build_prompt_truncates_long_pages():
    prompt = build_prompt("x" * 50000, ["email"])
    assert len(prompt) < 20000


def test_parse_json_response_extracts_object_from_surrounding_text():
    text = 'Sure, here you go:\n{"email": "a@b.com", "phone": null}\nHope that helps!'
    parsed = parse_json_response(text)
    assert parsed == {"email": "a@b.com", "phone": None}


def test_parse_json_response_raises_on_no_json():
    try:
        parse_json_response("no json here at all")
        assert False
    except AIExtractionError:
        pass


def test_extract_requires_api_key():
    try:
        extract("anthropic", "some text", ["email"], api_key="")
        assert False
    except AIExtractionError as e:
        assert "مفيش مفتاح" in str(e)


def test_extract_requires_field_names():
    try:
        extract("anthropic", "some text", [], api_key="sk-fake")
        assert False
    except AIExtractionError:
        pass


def test_extract_rejects_unknown_provider():
    try:
        extract("groq", "some text", ["email"], api_key="sk-fake")
        assert False
    except AIExtractionError as e:
        assert "غير مدعوم" in str(e)


def test_extract_with_anthropic_happy_path():
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "content": [{"text": '{"owner_name": "Jane Smith", "email": "jane@acme.com"}'}]
    }
    with patch("requests.post", return_value=fake_response) as mock_post:
        result = extract_with_anthropic("Jane Smith runs Acme, jane@acme.com", ["owner_name", "email"], "sk-ant-fake")
    assert result == {"owner_name": "Jane Smith", "email": "jane@acme.com"}
    assert mock_post.call_args.kwargs["headers"]["x-api-key"] == "sk-ant-fake"


def test_extract_with_openai_happy_path():
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "choices": [{"message": {"content": '{"owner_name": null, "email": "info@acme.com"}'}}]
    }
    with patch("requests.post", return_value=fake_response):
        result = extract_with_openai("Contact us at info@acme.com", ["owner_name", "email"], "sk-oa-fake")
    assert result == {"owner_name": None, "email": "info@acme.com"}


def test_extract_with_anthropic_bad_response_shape_raises():
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"unexpected": "shape"}
    with patch("requests.post", return_value=fake_response):
        try:
            extract_with_anthropic("text", ["email"], "sk-fake")
            assert False
        except AIExtractionError:
            pass


def test_default_field_names_match_user_request():
    assert "owner_name" in DEFAULT_FIELD_NAMES
    assert "email" in DEFAULT_FIELD_NAMES
    assert "phone" in DEFAULT_FIELD_NAMES
