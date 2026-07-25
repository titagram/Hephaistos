"""Repair 1A.1 cleanup — bounded fail-closed and schema hygiene tests.

Covers:
  TASK 1 — single UNIQUE mechanism for opportunity_key
  TASK 2 — SuggestionRepository fail-closed validation
  TASK 3 — ensure_evolution_initialized() no longer accepts global_root
  TASK 4 — TelosStore no self-approval, activate/rollback fail-closed
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from hermes_cli.evolution import ledger as ledger_module
from hermes_cli.evolution.ledger import EvolutionLedger
from hermes_cli.evolution.suggestions import (
    SuggestionRepository,
    SuggestionRepositoryError,
    _connect_existing,
)
from hermes_cli.evolution.telos_store import TelosStore, TelosStoreError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_v3_database(path: Path) -> None:
    """Create a v4 database with all Project A + Project B tables."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in ledger_module._SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_version(singleton, version) VALUES (1, 4)"
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


def _create_v2_database(path: Path) -> None:
    """Create a valid v2 database (Project A only, no v4 tables)."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in ledger_module._SCHEMA_V2_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_version(singleton, version) VALUES (1, 2)"
    )
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


def _create_v3_missing_table(path: Path) -> None:
    """Create a v4 database missing one Project B table."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in ledger_module._SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO schema_version(singleton, version) VALUES (1, 4)"
    )
    # Drop one Project B table
    connection.execute("DROP TABLE opportunity_suggestion_events")
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


# ---------------------------------------------------------------------------
# TASK 1 — single UNIQUE for opportunity_key
# ---------------------------------------------------------------------------

def test_opportunity_key_has_exactly_one_unique_mechanism(tmp_path: Path) -> None:
    """Column is NOT NULL only; the named unique index provides semantic uniqueness."""
    path = tmp_path / "evolution.db"
    _create_v3_database(path)

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        all_indexes = conn.execute(
            "PRAGMA index_list(opportunity_suggestions)"
        ).fetchall()
        unique_for_opportunity_key = []
        for idx_row in all_indexes:
            idx_name = idx_row[1]
            is_unique = bool(idx_row[2])
            if not is_unique:
                continue
            info = conn.execute(
                f"PRAGMA index_info({idx_name})"
            ).fetchall()
            for col_info in info:
                if col_info[2] == "opportunity_key":
                    unique_for_opportunity_key.append(idx_name)
    finally:
        conn.close()

    # Exactly one UNIQUE index on opportunity_key: the named one
    assert unique_for_opportunity_key == [
        "opportunity_suggestions_opportunity_key_idx"
    ], (
        f"Expected only the named unique index on opportunity_key, "
        f"got: {unique_for_opportunity_key}"
    )


def test_no_autoindex_on_opportunity_key(tmp_path: Path) -> None:
    """sqlite_autoindex for PRIMARY KEY (suggestion_id) is allowed,
    but no autoindex UNIQUE should exist for opportunity_key."""
    path = tmp_path / "evolution.db"
    _create_v3_database(path)

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "PRAGMA index_list(opportunity_suggestions)"
        ).fetchall()
        for row in rows:
            idx_name = row[1]
            if not idx_name.startswith("sqlite_autoindex"):
                continue
            info = conn.execute(
                f"PRAGMA index_info({idx_name})"
            ).fetchall()
            for col_info in info:
                col_name = col_info[2]
                assert col_name != "opportunity_key", (
                    f"Autoindex {idx_name} covers opportunity_key — double UNIQUE exists"
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TASK 2 — SuggestionRepository fail-closed
# ---------------------------------------------------------------------------

def test_repository_missing_database_raises_without_creating(tmp_path: Path) -> None:
    """A. Database inesistente → SuggestionRepositoryError, nessun file creato."""
    missing = tmp_path / "missing.db"
    assert not missing.exists()

    with pytest.raises(SuggestionRepositoryError):
        SuggestionRepository(missing)

    assert not missing.exists(), "Repository must not create the database file"


def test_repository_rejects_symlink(tmp_path: Path) -> None:
    """B. Symlink → SuggestionRepositoryError, target non aperto."""
    target = tmp_path / "actual.db"
    _create_v3_database(target)
    symlink = tmp_path / "link.db"
    symlink.symlink_to(target)

    with pytest.raises(SuggestionRepositoryError):
        SuggestionRepository(symlink)


def test_repository_rejects_non_regular_file(tmp_path: Path) -> None:
    """C. Directory passed as path → SuggestionRepositoryError."""
    with pytest.raises(SuggestionRepositoryError):
        SuggestionRepository(tmp_path)


def test_repository_rejects_non_sqlite_file(tmp_path: Path) -> None:
    """C. File normale non-SQLite → SuggestionRepositoryError, nessuna modifica."""
    bad = tmp_path / "not_a_db.db"
    bad.write_bytes(b"this is not a sqlite database")
    original_bytes = bad.read_bytes()

    with pytest.raises(SuggestionRepositoryError):
        SuggestionRepository(bad)

    assert bad.read_bytes() == original_bytes, "Bytes must not be modified"


def test_repository_rejects_v2_database_without_migrating(tmp_path: Path) -> None:
    """D. v2 database → SuggestionRepositoryError, non migrato, schema_version resta 2."""
    path = tmp_path / "evolution.db"
    _create_v2_database(path)

    with pytest.raises(SuggestionRepositoryError) as exc_info:
        SuggestionRepository(path)

    assert "observer_schema_unsupported" in str(exc_info.value)

    # Verify schema_version is still 2
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        version = conn.execute(
            "SELECT version FROM schema_version WHERE singleton = 1"
        ).fetchone()[0]
        assert version == 2, f"Schema must remain v2, got v{version}"
        # No Project B tables created
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "observation_envelopes" not in tables
        assert "opportunity_suggestions" not in tables
        assert "opportunity_suggestion_events" not in tables
    finally:
        conn.close()


def test_repository_accepts_valid_v3_database(tmp_path: Path) -> None:
    """E. v3 valido → accettato, letture e scritture funzionano."""
    path = tmp_path / "evolution.db"
    _create_v3_database(path)

    repo = SuggestionRepository(path)
    assert repo.db_path == path

    # Verify we can list suggestions (should be empty)
    suggestions = repo.list_suggestions()
    assert suggestions == []

    # Verify we can list envelopes (should be empty)
    envelopes = repo.list_all_envelopes()
    assert envelopes == []


def test_repository_rejects_v3_missing_table(tmp_path: Path) -> None:
    """F. v3 privo di una tabella Project B → SuggestionRepositoryError."""
    path = tmp_path / "evolution.db"
    _create_v3_missing_table(path)

    with pytest.raises(SuggestionRepositoryError) as exc_info:
        SuggestionRepository(path)

    assert "observer_tables_missing" in str(exc_info.value)


def test_repository_race_after_construction(tmp_path: Path) -> None:
    """G. Race: DB eliminato dopo costruzione → fail, non ricreato."""
    path = tmp_path / "evolution.db"
    _create_v3_database(path)

    repo = SuggestionRepository(path)

    # Delete the database after construction
    path.unlink()
    assert not path.exists()

    # Any method that calls _get_connection() must fail
    with pytest.raises(SuggestionRepositoryError):
        repo.list_suggestions()

    # Database must not be recreated
    assert not path.exists(), "Database must not be recreated after deletion"


def test_repository_error_messages_never_include_path(tmp_path: Path) -> None:
    """H. Nessun messaggio d'errore contiene il path temporaneo."""
    missing_path = tmp_path / "missing.db"

    with pytest.raises(SuggestionRepositoryError) as exc_info:
        SuggestionRepository(missing_path)

    message = str(exc_info.value)
    assert str(missing_path) not in message
    assert "missing.db" not in message


def test_repository_constructor_does_not_create_directories(tmp_path: Path) -> None:
    """Il costruttore non crea directory."""
    deep = tmp_path / "a" / "b" / "missing.db"
    assert not deep.parent.exists()

    with pytest.raises(SuggestionRepositoryError):
        SuggestionRepository(deep)

    assert not deep.parent.exists(), "Constructor must not create parent directories"


def test_repository_constructor_does_not_create_database(tmp_path: Path) -> None:
    """Il costruttore non crea il file database."""
    path = tmp_path / "nonexistent.db"
    assert not path.exists()

    with pytest.raises(SuggestionRepositoryError):
        SuggestionRepository(path)

    assert not path.exists()


# ---------------------------------------------------------------------------
# TASK 3 — bootstrap.py no longer accepts global_root
# ---------------------------------------------------------------------------

def test_ensure_evolution_initialized_has_no_global_root_parameter() -> None:
    """ensure_evolution_initialized() must not accept global_root."""
    import inspect
    from hermes_cli.evolution.bootstrap import ensure_evolution_initialized

    sig = inspect.signature(ensure_evolution_initialized)
    params = list(sig.parameters.keys())
    assert "global_root" not in params, (
        f"ensure_evolution_initialized must not accept global_root, "
        f"got parameters: {params}"
    )


# ---------------------------------------------------------------------------
# TASK 4 — TelosStore no self-approval
# ---------------------------------------------------------------------------

def test_telos_store_has_no_issue_approval_receipt(tmp_path: Path) -> None:
    """A. issue_approval_receipt non esiste."""
    store = TelosStore(tmp_path / "organism")
    assert not hasattr(store, "issue_approval_receipt"), (
        "issue_approval_receipt must not exist on TelosStore"
    )


def test_telos_store_has_no_approval_receipt_class() -> None:
    """ApprovalReceipt non esiste nel modulo."""
    from hermes_cli.evolution import telos_store

    assert not hasattr(telos_store, "ApprovalReceipt"), (
        "ApprovalReceipt must not exist in telos_store module"
    )


def test_activate_revision_raises_host_approval_not_implemented(tmp_path: Path) -> None:
    """B. activate_revision → TelosStoreError, nessun file creato."""
    organism = tmp_path / "organism"
    store = TelosStore(organism)

    with pytest.raises(TelosStoreError) as exc_info:
        store.activate_revision("a" * 64, "receipt-1")

    assert "host_approval_not_implemented" in str(exc_info.value)

    # Nessun file active.json creato
    assert not store.active_pointer.exists()
    # Nessun file last-known-good.json creato
    assert not store.lkg_pointer.exists()
    # Nessun database creato
    db_path = organism / "evolution" / "evolution.db"
    assert not db_path.exists()


def test_rollback_raises_host_approval_not_implemented(tmp_path: Path) -> None:
    """C. rollback → TelosStoreError, nessun file modificato."""
    organism = tmp_path / "organism"
    store = TelosStore(organism)

    with pytest.raises(TelosStoreError) as exc_info:
        store.rollback("a" * 64, "receipt-1")

    assert "host_approval_not_implemented" in str(exc_info.value)

    # Nessun file creato o modificato
    assert not store.active_pointer.exists()
    assert not store.lkg_pointer.exists()
    db_path = organism / "evolution" / "evolution.db"
    assert not db_path.exists()


def test_activate_revision_with_existing_database_still_fail_closed(tmp_path: Path) -> None:
    """B. Anche se evolution.db esiste, activate_revision fallisce senza mutazioni."""
    organism = tmp_path / "organism"
    organism.mkdir(parents=True)
    evo_dir = organism / "evolution"
    evo_dir.mkdir()
    db_path = evo_dir / "evolution.db"
    _create_v3_database(db_path)

    store = TelosStore(organism)
    # Pre-create active.json to verify it's not modified
    store.telos_dir.mkdir(parents=True, exist_ok=True)
    store.active_pointer.write_text('{"test": "before"}')

    with pytest.raises(TelosStoreError):
        store.activate_revision("a" * 64, "receipt-1")

    # active.json unchanged
    assert store.active_pointer.read_text() == '{"test": "before"}'
    # no last-known-good.json created
    assert not store.lkg_pointer.exists()


def test_rollback_with_existing_database_still_fail_closed(tmp_path: Path) -> None:
    """C. Anche se evolution.db esiste, rollback fallisce senza mutazioni."""
    organism = tmp_path / "organism"
    organism.mkdir(parents=True)
    evo_dir = organism / "evolution"
    evo_dir.mkdir()
    db_path = evo_dir / "evolution.db"
    _create_v3_database(db_path)

    store = TelosStore(organism)
    store.telos_dir.mkdir(parents=True, exist_ok=True)
    store.active_pointer.write_text('{"test": "before"}')

    with pytest.raises(TelosStoreError):
        store.rollback("a" * 64, "receipt-1")

    assert store.active_pointer.read_text() == '{"test": "before"}'
    assert not store.lkg_pointer.exists()


def test_save_and_get_revision_still_work(tmp_path: Path) -> None:
    """save_revision e get_revision restano disponibili come storage inerte."""
    from hermes_cli.evolution.telos_contract import (
        CapabilityDirection,
        DesiredTrait,
        Priority,
        ProactivityPolicy,
        Prohibition,
        SuccessIndicator,
        TelosRevision,
    )

    organism = tmp_path / "organism"
    store = TelosStore(organism)

    rev = TelosRevision(
        schema_version=1,
        organism_id="00000000-0000-0000-0000-000000000000",
        parent_digest=None,
        purpose="Test revision for repair 1a cleanup verification.",
        desired_traits=(
            DesiredTrait("reliable", "High accuracy.", ("trait.reliability",), 5),
        ),
        capability_directions=(
            CapabilityDirection("test", "Test capability.", ("capability.test",), 4),
        ),
        priorities=(
            Priority("safety", "Always prioritize safety.", ("priority.safety",), 5),
        ),
        tradeoffs=(),
        prohibitions=(
            Prohibition("no_net", "No unauthorized network.", ("prohibition.net",), 5),
        ),
        proactivity_policy=ProactivityPolicy("bounded", "Passive suggestions.", ("proactivity.passive",), 3),
        success_indicators=(
            SuccessIndicator("completion", "Task completion > 95%.", ("indicator.comp",), 4),
        ),
    )
    path = store.save_revision(rev)
    assert path.exists()

    loaded = store.get_revision(rev.canonical_digest)
    assert loaded.canonical_digest == rev.canonical_digest


def test_connect_existing_raises_on_deleted_db(tmp_path: Path) -> None:
    """_connect_existing fallisce se il database è stato eliminato."""
    path = tmp_path / "evolution.db"
    _create_v3_database(path)

    # Should work when DB exists
    conn = _connect_existing(path)
    conn.close()

    # Delete and retry — must fail
    path.unlink()
    with pytest.raises(SuggestionRepositoryError) as exc_info:
        _connect_existing(path)

    assert "observer_database_missing" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Repair 1A.2 — Task 2: path security inside _connect_existing
# ---------------------------------------------------------------------------

def test_repository_rejects_symlink_replacement_after_construction(
    tmp_path: Path,
) -> None:
    """A symlink swapped in after construction must be rejected on next open."""
    db1 = tmp_path / "db1.db"
    db2 = tmp_path / "db2.db"
    _create_v3_database(db1)
    _create_v3_database(db2)

    repo = SuggestionRepository(db1)

    # Rename db1 away, replace with a symlink to db2
    db1_renamed = tmp_path / "db1_renamed.db"
    db1.rename(db1_renamed)
    db1.symlink_to(db2)

    try:
        with pytest.raises(SuggestionRepositoryError) as exc_info:
            repo.list_suggestions()
        assert "observer_database_unsafe" in str(exc_info.value)

        # db2 must not have been modified
        conn = sqlite3.connect(f"{db2.absolute().as_uri()}?mode=ro", uri=True)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_version"
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()
    finally:
        # Cleanup: restore db1
        if db1.exists() and db1.is_symlink():
            db1.unlink()
        if db1_renamed.exists():
            db1_renamed.rename(db1)


def test_connect_existing_rejects_non_regular_path_directly(tmp_path: Path) -> None:
    """_connect_existing must reject directories, symlinks, and non-regular files."""
    # Directory
    with pytest.raises(SuggestionRepositoryError) as exc_info:
        _connect_existing(tmp_path)
    assert "observer_database_unsafe" in str(exc_info.value)

    # Symlink
    target = tmp_path / "target.db"
    _create_v3_database(target)
    link = tmp_path / "link.db"
    link.symlink_to(target)
    with pytest.raises(SuggestionRepositoryError) as exc_info:
        _connect_existing(link)
    assert "observer_database_unsafe" in str(exc_info.value)

    # FIFO (if platform supports it)
    import stat as stat_module
    fifo = tmp_path / "fifo"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError):
        pass
    else:
        with pytest.raises(SuggestionRepositoryError) as exc_info:
            _connect_existing(fifo)
        assert "observer_database_unsafe" in str(exc_info.value)


def test_get_connection_enables_foreign_keys(tmp_path: Path) -> None:
    """_get_connection must enable PRAGMA foreign_keys."""
    path = tmp_path / "evolution.db"
    _create_v3_database(path)
    repo = SuggestionRepository(path)

    conn = repo._get_connection()
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1, f"foreign_keys must be ON, got {fk}"
    finally:
        conn.close()

def test_repository_reserved_uri_characters_do_not_redirect_or_create_database(
    tmp_path: Path,
) -> None:
    """A filename containing ? or # must not be interpreted as a URI query/fragment."""
    path = tmp_path / "evolution.db?alias"
    _create_v3_database(path)

    # Verify the plain name does not exist
    plain = tmp_path / "evolution.db"
    assert not plain.exists(), "plain evolution.db must not exist before test"

    repo = SuggestionRepository(path)
    assert repo.list_suggestions() == []

    # Plain evolution.db must still not exist
    assert not plain.exists(), "f'file:{db_path}?mode=rw' must not redirect to a different file"

    # The original path (with ?alias) must still exist and be a regular file
    assert path.exists()
    assert path.is_file()


# ---------------------------------------------------------------------------
# Repair 1A.2 — Task 3: canonical v3 schema validation via ledger
# ---------------------------------------------------------------------------

def _create_malformed_v3_database(path: Path) -> None:
    """Create a fake v3 with correct table names but wrong column structure."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    # Create a schema_version with correct values
    connection.execute(
        "CREATE TABLE schema_version ("
        "  singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton = 1),"
        "  version INTEGER NOT NULL CHECK(version = 4)"
        ") WITHOUT ROWID"
    )
    connection.execute("INSERT INTO schema_version VALUES (1, 4)")
    # Create Project B tables with arbitrary incomplete columns
    connection.execute("CREATE TABLE observation_envelopes (event_id TEXT)")
    connection.execute("CREATE TABLE opportunity_suggestions (suggestion_id TEXT)")
    connection.execute("CREATE TABLE opportunity_suggestion_events (event_id TEXT)")
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


def _create_noncanonical_index_v3_database(path: Path) -> None:
    """Create a canonical v3 then alter an index to make it non-canonical."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in ledger_module._SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_version VALUES (1, 4)")
    # Drop a Project B index
    connection.execute("DROP INDEX IF EXISTS opportunity_suggestions_opportunity_key_idx")
    connection.commit()
    connection.close()
    os.chmod(path, 0o600)


def test_repository_rejects_structurally_malformed_v3_database(
    tmp_path: Path,
) -> None:
    """A database with v4 table names but wrong columns must be rejected."""
    path = tmp_path / "fake.db"
    _create_malformed_v3_database(path)

    with pytest.raises(SuggestionRepositoryError) as exc_info:
        SuggestionRepository(path)

    assert "observer_schema_invalid" in str(exc_info.value)
    # No extra database files created beyond the original fake.db
    db_files = [p for p in tmp_path.iterdir() if p.suffix == ".db"]
    assert db_files == [path]


def test_repository_rejects_noncanonical_v3_index_layout(
    tmp_path: Path,
) -> None:
    """A v4 database with a missing Project B index must be rejected."""
    path = tmp_path / "evolution.db"
    _create_noncanonical_index_v3_database(path)

    with pytest.raises(SuggestionRepositoryError) as exc_info:
        SuggestionRepository(path)

    assert "observer_schema_invalid" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Repair 1A.2 — Task 4: explicit connection close in constructor
# ---------------------------------------------------------------------------

def test_repository_constructor_closes_validation_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constructor must explicitly close its validation connection."""
    path = tmp_path / "evolution.db"
    _create_v3_database(path)

    # Track connections returned by _connect_existing
    tracked_connections = []

    import hermes_cli.evolution.suggestions as suggestions_module
    _real_connect_existing = suggestions_module._connect_existing

    def _tracking_connect_existing(db_path):
        conn = _real_connect_existing(db_path)
        tracked_connections.append(conn)
        return conn

    monkeypatch.setattr(
        suggestions_module,
        "_connect_existing",
        _tracking_connect_existing,
    )

    repo = SuggestionRepository(path)  # noqa: F841

    # After construction, the tracked connection must be closed
    assert len(tracked_connections) >= 1, "Constructor must call _connect_existing"
    for conn in tracked_connections:
        # Attempt to use the connection — if it was closed, this will raise
        try:
            conn.execute("SELECT 1")
            # If we reach here, the connection is still open — fail
            conn.close()
            pytest.fail("Constructor must close its validation connection")
        except sqlite3.ProgrammingError:
            # Expected: closed connection raises ProgrammingError
            pass


# ---------------------------------------------------------------------------
# Repair 1A.2 — Task 5: bounded errors — no path in tracebacks
# ---------------------------------------------------------------------------

def test_repository_missing_path_is_absent_from_formatted_traceback(
    tmp_path: Path,
) -> None:
    """Traceback from a missing database must not expose the path."""
    import traceback

    missing = tmp_path / "sensitive-name.db"
    tb_text = ""

    try:
        SuggestionRepository(missing)
    except SuggestionRepositoryError:
        tb_text = traceback.format_exc()

    assert str(missing) not in tb_text, (
        f"Traceback must not contain path: {str(missing)}"
    )
    assert "sensitive-name" not in tb_text, (
        "Traceback must not contain filename"
    )


def test_repository_invalid_sqlite_path_is_absent_from_formatted_traceback(
    tmp_path: Path,
) -> None:
    """Traceback from an invalid database must not expose the path."""
    import traceback

    bad = tmp_path / "secret-location.db"
    bad.write_bytes(b"not a database")
    tb_text = ""

    try:
        SuggestionRepository(bad)
    except SuggestionRepositoryError:
        tb_text = traceback.format_exc()

    assert str(bad) not in tb_text, (
        f"Traceback must not contain path: {str(bad)}"
    )
    assert "secret-location" not in tb_text, (
        "Traceback must not contain filename"
    )


# ---------------------------------------------------------------------------
# Repair 1A.2.1 — Task 1: PermissionError bounded and pathless
# ---------------------------------------------------------------------------

def test_connect_existing_permission_error_is_bounded_and_pathless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PermissionError from lstat() must become observer_database_unsafe, not raw."""
    import traceback

    sensitive_path = tmp_path / "private-project-secret.db"
    _real_lstat = Path.lstat

    def _raising_lstat(self):
        if self == sensitive_path:
            raise PermissionError(f"permission denied: {sensitive_path}")
        return _real_lstat(self)

    monkeypatch.setattr(Path, "lstat", _raising_lstat)

    tb_text = ""
    try:
        _connect_existing(sensitive_path)
    except SuggestionRepositoryError as exc:
        tb_text = traceback.format_exc()
        assert "observer_database_unsafe" in str(exc), "Must be observer_database_unsafe"
    else:
        pytest.fail("Must raise SuggestionRepositoryError")

    assert str(sensitive_path) not in tb_text
    assert "private-project-secret" not in tb_text
    assert "permission denied" not in tb_text


# ---------------------------------------------------------------------------
# Repair 1A.2.1 — Task 2: sqlite3.Error during connect bounded
# ---------------------------------------------------------------------------

def test_connect_existing_sqlite_connect_error_is_bounded_and_pathless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sqlite3.DatabaseError during connect() must be bounded and pathless."""
    import traceback

    import hermes_cli.evolution.suggestions as suggestions_module

    sensitive_path = tmp_path / "private-project-secret.db"
    # Create a regular file so lstat() passes
    sensitive_path.write_bytes(b"not a db")
    sensitive_path.chmod(0o600)

    _real_connect = sqlite3.connect

    def _raising_connect(*args, **kwargs):
        raise sqlite3.DatabaseError(f"disk failure at {sensitive_path}")

    monkeypatch.setattr(suggestions_module.sqlite3, "connect", _raising_connect)

    tb_text = ""
    try:
        _connect_existing(sensitive_path)
    except SuggestionRepositoryError as exc:
        tb_text = traceback.format_exc()
        assert "observer_database_unsafe" in str(exc), "Must be observer_database_unsafe"
    else:
        pytest.fail("Must raise SuggestionRepositoryError")

    assert str(sensitive_path) not in tb_text
    assert "private-project-secret" not in tb_text
    assert "disk failure" not in tb_text


# ---------------------------------------------------------------------------
# Repair 1A.2.1 — Task 3: sqlite3.Error after open — bounded and closes once
# ---------------------------------------------------------------------------

def test_connect_existing_identity_sqlite_error_is_bounded_and_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sqlite3.DatabaseError during PRAGMA database_list must close once, bounded."""
    import traceback

    import hermes_cli.evolution.suggestions as suggestions_module

    sensitive_path = tmp_path / "private-project-secret.db"
    # Create a regular file so lstat() passes
    sensitive_path.write_bytes(b"x")
    sensitive_path.chmod(0o600)

    class FakeConnection:
        def __init__(self):
            self.close_count = 0
            self._row_factory = None

        @property
        def row_factory(self):
            return self._row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._row_factory = value

        def execute(self, sql, *args):
            if "PRAGMA database_list" in sql:
                raise sqlite3.DatabaseError(
                    f"identity failure at {sensitive_path}"
                )
            return self

        def fetchall(self):
            return []

        def close(self):
            self.close_count += 1

    fake = FakeConnection()

    def _fake_connect(*args, **kwargs):
        return fake

    monkeypatch.setattr(suggestions_module.sqlite3, "connect", _fake_connect)

    tb_text = ""
    try:
        _connect_existing(sensitive_path)
    except SuggestionRepositoryError as exc:
        tb_text = traceback.format_exc()
        assert "observer_database_unsafe" in str(exc), "Must be observer_database_unsafe"
    else:
        pytest.fail("Must raise SuggestionRepositoryError")

    assert fake.close_count == 1, (
        f"Connection must be closed exactly once, got {fake.close_count}"
    )
    assert str(sensitive_path) not in tb_text
    assert "private-project-secret" not in tb_text
    assert "identity failure" not in tb_text


# ---------------------------------------------------------------------------
# Repair 1A.2.1 — Task 4: sqlite3.Error during PRAGMA foreign_keys
# ---------------------------------------------------------------------------

def test_connect_existing_foreign_keys_error_is_bounded_and_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sqlite3.DatabaseError during PRAGMA foreign_keys=ON must close once, bounded."""
    import traceback

    import hermes_cli.evolution.suggestions as suggestions_module

    sensitive_path = tmp_path / "private-project-secret.db"
    # Create a real file that lstat/stat can use
    sensitive_path.write_bytes(b"x")
    sensitive_path.chmod(0o600)

    database_path = str(sensitive_path.resolve())

    class FakeConnection:
        def __init__(self):
            self.close_count = 0
            self._row_factory = None

        @property
        def row_factory(self):
            return self._row_factory

        @row_factory.setter
        def row_factory(self, value):
            self._row_factory = value

        def execute(self, sql, *args):
            sql_str = str(sql)
            if "PRAGMA database_list" in sql_str:
                return FakeResult([(0, "main", database_path)])
            if "PRAGMA foreign_keys" in sql_str:
                raise sqlite3.DatabaseError(
                    f"foreign key failure at {sensitive_path}"
                )
            return FakeResult([])

        def fetchall(self):
            return []

        def close(self):
            self.close_count += 1

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows
        def fetchall(self):
            return self._rows

    fake = FakeConnection()

    def _fake_connect(*args, **kwargs):
        return fake

    monkeypatch.setattr(suggestions_module.sqlite3, "connect", _fake_connect)

    tb_text = ""
    try:
        _connect_existing(sensitive_path)
    except SuggestionRepositoryError as exc:
        tb_text = traceback.format_exc()
        assert "observer_database_unsafe" in str(exc), "Must be observer_database_unsafe"
    else:
        pytest.fail("Must raise SuggestionRepositoryError")

    assert fake.close_count == 1, (
        f"Connection must be closed exactly once, got {fake.close_count}"
    )
    assert str(sensitive_path) not in tb_text
    assert "private-project-secret" not in tb_text
    assert "foreign key failure" not in tb_text
