from app.core.engine.dedupe import fingerprint_lead


def test_fingerprint_by_email_is_stable_and_normalized():
    a = fingerprint_lead({"name": "A", "email": "Henry@Elight.com"})
    b = fingerprint_lead({"name": "different name", "email": " henry@elight.com "})
    assert a is not None
    assert a == b


def test_fingerprint_by_phone_ignores_formatting():
    a = fingerprint_lead({"phone": "+1 (650) 356-451"})
    b = fingerprint_lead({"phone": "16503 56451"})
    assert a == b


def test_fingerprint_by_website_ignores_scheme_www_and_path():
    a = fingerprint_lead({"website": "https://www.example.com/contact"})
    b = fingerprint_lead({"website": "example.com"})
    assert a == b


def test_fingerprint_falls_back_to_name_plus_company():
    a = fingerprint_lead({"name": "Henry Jordan", "company_name": "E-light Industry"})
    b = fingerprint_lead({"name": "  henry jordan ", "company_name": "e-light  industry"})
    assert a == b


def test_fingerprint_none_when_no_identity_signal():
    assert fingerprint_lead({"notes": "just some text"}) is None
    assert fingerprint_lead({}) is None


def test_fingerprint_different_leads_differ():
    a = fingerprint_lead({"email": "a@x.com"})
    b = fingerprint_lead({"email": "b@x.com"})
    assert a != b


def test_fingerprint_email_takes_priority_over_phone():
    # Same phone, different email -> must NOT collide (email is more
    # reliable identity than a possibly-shared office phone number).
    a = fingerprint_lead({"email": "a@x.com", "phone": "555-1234"})
    b = fingerprint_lead({"email": "b@x.com", "phone": "555-1234"})
    assert a != b
