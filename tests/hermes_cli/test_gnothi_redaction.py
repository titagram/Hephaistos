from pathlib import Path

from hermes_cli.gnothi.redaction import redact_value, safe_exception_class


def test_redacts_secret_keys_and_workspace_paths(tmp_path: Path):
    value = {
        "api_key": "sk-private",
        "nested": {"cookie": "session=private"},
        "path": str(tmp_path / "agent" / "tool.py"),
        "message": "safe",
    }
    redacted, count = redact_value(value, workspace_root=tmp_path)
    assert redacted == {
        "api_key": "[REDACTED]",
        "nested": {"cookie": "[REDACTED]"},
        "path": "agent/tool.py",
        "message": "safe",
    }
    assert count == 3
    assert "private" not in str(redacted)


def test_bounds_untrusted_strings():
    redacted, count = redact_value({"message": "x" * 5000})
    assert len(redacted["message"]) == 1000
    assert count == 1


def test_redacts_embedded_paths_without_consuming_context_or_line_locations():
    value = (
        'Open "/private/secret/plugin.py:42:7", then '
        '[//server/share/logs/agent.log:9] and retry '
        r"C:\\Users\\secret\\tool.py:12; or \\server\\share\\private\\trace.log:4."
    )

    redacted, count = redact_value(value)

    assert redacted == (
        'Open "[ABSOLUTE_PATH]:42:7", then '
        '[[ABSOLUTE_PATH]:9] and retry '
        '[ABSOLUTE_PATH]:12; or [ABSOLUTE_PATH]:4.'
    )
    assert count == 4


def test_embedded_path_redaction_leaves_urls_and_non_paths_unchanged():
    value = (
        "Read https://example.test/docs/path?next=/private/secret and "
        "https://example.test/#/private/secret; keep capability:terminal and "
        "./relative/file.py."
    )

    redacted, count = redact_value(value)

    assert redacted == value
    assert count == 0


def test_embedded_path_redaction_preserves_markdown_quote_delimiters():
    value = "See `/private/secret/plugin.py:8` and '/private/secret/other.py:9'."

    redacted, count = redact_value(value)

    assert redacted == "See `[ABSOLUTE_PATH]:8` and '[ABSOLUTE_PATH]:9'."
    assert count == 2


def test_embedded_path_redaction_keeps_bracketed_path_components_together():
    value = (
        r"Open /private/alice/[billing-prod]/report.txt and "
        r"C:\Users\alice\[billing-prod]\report.txt."
    )

    redacted, count = redact_value(value)

    assert redacted == "Open [ABSOLUTE_PATH] and [ABSOLUTE_PATH]."
    assert count == 2


def test_exception_exposes_class_only():
    assert safe_exception_class(RuntimeError("/private/path token=secret")) == (
        "RuntimeError"
    )
