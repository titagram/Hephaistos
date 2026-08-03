from __future__ import annotations

import importlib.machinery
import sqlite3
from pathlib import Path

from hermes_cli import hades_backend_db as db
from hermes_cli import kanban_portfolio
from hermes_cli.hades_backend_client import HadesBackendClient
from hermes_cli.hades_backend_status import backend_status_payload


REMOVED_RUNTIME_MODULES = (
    "hermes_cli.hades_persephone_receiver",
    "hermes_cli.hades_persephone_transport",
    "hermes_cli.hades_persephone_messages",
    "hermes_cli.hades_persephone_store",
    "hermes_cli.hades_information_worker",
)


def _status_payload() -> dict:
    return backend_status_payload(
        agent=None,
        bindings=[],
        job_counts={},
        proposal_counts={},
        inbox_counts={},
        last_summary=None,
        last_error=None,
        now=100,
    )


def test_removed_persephone_modules_have_no_import_surface() -> None:
    package_path = Path(__file__).resolve().parents[2] / "hermes_cli"
    for module_name in REMOVED_RUNTIME_MODULES:
        module_basename = module_name.rsplit(".", 1)[-1]
        assert not (package_path / f"{module_basename}.py").exists()
        assert importlib.machinery.PathFinder.find_spec(
            module_basename, [str(package_path)]
        ) is None


def test_fresh_backend_database_has_no_persephone_tables(tmp_path: Path) -> None:
    with db.connect_closing(tmp_path / "fresh.db") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert not {name for name in tables if name.startswith("persephone_")}


def test_connect_leaves_legacy_persephone_tables_untouched(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as legacy:
        legacy.execute(
            "CREATE TABLE persephone_inbox "
            "(legacy_key INTEGER PRIMARY KEY, legacy_payload TEXT NOT NULL)"
        )
        legacy.execute(
            "INSERT INTO persephone_inbox "
            "(legacy_key, legacy_payload) VALUES (1, 'preserve-me')"
        )
        schema_before = legacy.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'persephone_inbox'"
        ).fetchone()[0]

    with db.connect_closing(path) as conn:
        schema_after = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'persephone_inbox'"
        ).fetchone()[0]
        rows_after = conn.execute(
            "SELECT legacy_key, legacy_payload FROM persephone_inbox "
            "ORDER BY legacy_key"
        ).fetchall()

    assert schema_after == schema_before
    assert [tuple(row) for row in rows_after] == [(1, "preserve-me")]


def test_status_and_backend_client_have_no_persephone_surface() -> None:
    assert "persephone" not in _status_payload()
    assert not {
        name
        for name in dir(HadesBackendClient)
        if "persephone" in name.lower()
        or name in {"list_inbox", "create_inbox_message"}
    }


def test_coordination_runtime_and_skill_have_no_persephone_reference() -> None:
    root = Path(__file__).resolve().parents[2]
    surfaces = (
        root / "hermes_cli" / "hades_coordination.py",
        root / "hermes_cli" / "kanban_portfolio.py",
        root
        / "skills"
        / "autonomous-ai-agents"
        / "hades-coordination"
        / "SKILL.md",
    )

    for surface in surfaces:
        assert "persephone" not in surface.read_text(encoding="utf-8").lower()


def test_portfolio_has_no_unverified_mandate_acceptance_entrypoint() -> None:
    assert not hasattr(kanban_portfolio, "accept_remote_mandate_reconciliation")
