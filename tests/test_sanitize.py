"""SensitiveDataSanitizer — the one recursive scrubbing policy shared by audit rows,
Run Event Ledger payloads, and any future log sink (docs/run-ledger-adr.md §2).

Contract:
- secret-shaped keys are redacted at ANY nesting depth;
- credential-bearing headers are redacted by name, whatever dict they ride in;
- URL query credentials are stripped inside string values;
- body-ish keys are redacted wholesale.
Truncation/preview shaping stays with callers; this module only decides what must
not persist. Deterministic: same input → same output.
"""

from core.audit import _sanitize_args
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


def test_audit_args_keep_their_tool_specific_rules():
    # browser_type's typed text is sensitive even though the key says "text".
    assert _sanitize_args("browser_type", {"target": "#q", "text": "hunter2"}) == {
        "target": "#q",
        "text": "[redacted input]",
    }
    # Everything else follows the shared policy + preview truncation.
    args = _sanitize_args("send_message", {"target": "slack:C1", "text": "hi"})
    assert args == {"target": "slack:C1", "text": "hi"}
    secret = _sanitize_args("mcp_tool", {"config": {"password": "p", "url": "https://h/?key=abc"}})
    assert secret["config"]["password"] == "[redacted]"
    assert "[redacted]" in secret["config"]["url"]


def test_ledger_scrubs_on_append_so_callers_cannot_leak(tmp_path):
    from core.ledger import RunEventLedger

    led = RunEventLedger(tmp_path / "events.db")
    row = led.append(
        "r1",
        "tool.proposed",
        payload={"arguments": {"password": "hunter2"}, "url": "https://h/?token=t"},
    )
    stored = led.events("r1")[0]
    assert stored["payload"]["arguments"]["password"] == "[redacted]"
    assert "token=[redacted]" in stored["payload"]["url"]
    # The chain is computed over exactly what persists.
    assert led.verify("r1") and row["hash"]
