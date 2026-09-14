"""SensitiveDataSanitizer — the one recursive scrubbing policy shared by audit rows,
Run Event Ledger payloads, and any future log sink (docs/architecture/adr/ADR-001-run-event-ledger.md §2).

Contract:
- secret-shaped keys are redacted at ANY nesting depth;
- credential-bearing headers are redacted by name, whatever dict they ride in;
- URL query credentials are stripped inside string values;
- body-ish keys are redacted wholesale.
Truncation/preview shaping stays with callers; this module only decides what must
not persist. Deterministic: same input → same output.
"""

from packages.sanitize import (
    redact_url_credentials,
    sanitize_payload,
    sanitize_value,
)


def test_secret_keys_redacted_at_any_depth():
    payload = {
        "bot_token": "xoxb-1",
        "nested": {"api_key": "k", "deeper": [{"access_token": "t"}]},
    }
    out = sanitize_payload(payload)
    assert out["bot_token"] == "[redacted]"
    assert out["nested"]["api_key"] == "[redacted]"
    assert out["nested"]["deeper"][0]["access_token"] == "[redacted]"


def test_sensitive_headers_redacted_by_name_wherever_they_sit():
    event = {
        "request": {"headers": {"Authorization": "Bearer abc", "Accept": "text/html"}},
        "response_cookies": {"Set-Cookie": "sid=1"},
    }
    out = sanitize_value(event)
    assert out["request"]["headers"]["Authorization"] == "[redacted]"
    assert out["request"]["headers"]["Accept"] == "text/html"
    assert out["response_cookies"]["Set-Cookie"] == "[redacted]"


def test_url_query_credentials_stripped_in_strings():
    url = "https://example.com/callback?code=abc&access_token=secret&state=xyz"
    assert redact_url_credentials(url) == (
        "https://example.com/callback?code=abc&access_token=[redacted]&state=xyz"
    )
    # Non-http(s) or non-URL strings pass through untouched.
    assert redact_url_credentials("ftp://h/?token=x") == "ftp://h/?token=x"
    assert redact_url_credentials("plain text") == "plain text"


def test_body_keys_redacted_wholesale_but_recursion_continues_elsewhere():
    out = sanitize_payload(
        {
            "body": "<html>anything</html>",
            "result_body": "also hidden",
            "note": "kept",
            "items": ["kept too", {"content": "hidden"}],
        }
    )
    assert out["body"] == "[redacted body]"
    assert out["result_body"] == "[redacted body]"
    assert out["note"] == "kept"
    assert out["items"][0] == "kept too"
    assert out["items"][1]["content"] == "[redacted body]"
