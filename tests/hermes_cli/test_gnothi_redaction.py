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


def test_exception_exposes_class_only():
    assert safe_exception_class(RuntimeError("/private/path token=secret")) == (
        "RuntimeError"
    )
