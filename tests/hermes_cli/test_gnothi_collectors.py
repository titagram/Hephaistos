from pathlib import Path

from hermes_cli.gnothi.collectors.base import CollectorContext, CollectorResult
from hermes_cli.gnothi.collectors.source import SourceCollector


def _context(workspace: Path) -> CollectorContext:
    return CollectorContext(
        workspace_root=workspace,
        generation_id="git:abc123",
        generation_scope="stable",
        head_commit="abc123",
        collected_at="2026-07-14T12:00:00Z",
    )


def test_collector_result_exposes_the_complete_boundary_contract():
    result = CollectorResult(
        name="fixture",
        status="current",
        nodes=[],
        edges=[],
        evidence=[],
        fingerprint="sha256:abc",
        verified_at="2026-07-14T12:00:00Z",
        error_code=None,
    )

    assert result.name == "fixture"
    assert result.status == "current"
    assert result.nodes == []
    assert result.edges == []
    assert result.evidence == []
    assert result.fingerprint == "sha256:abc"
    assert result.verified_at == "2026-07-14T12:00:00Z"
    assert result.error_code is None


def test_source_collector_emits_source_anatomy_without_absolute_paths(tmp_path: Path):
    workspace = tmp_path / "demo"
    source_dir = workspace / "src"
    source_dir.mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '1.0.0'\n",
        encoding="utf-8",
    )
    (source_dir / "app.py").write_text(
        "class Greeter:\n    def greet(self):\n        return 'hello'\n",
        encoding="utf-8",
    )

    result = SourceCollector().collect(_context(workspace))

    assert result.status == "current"
    assert {node["kind"] for node in result.nodes} >= {
        "workspace",
        "source_file",
        "symbol",
    }
    assert any(edge["kind"] == "contains" for edge in result.edges)
    assert result.evidence
    assert all(node["evidence_refs"] for node in result.nodes)
    assert str(tmp_path) not in str(result.nodes)
    assert str(tmp_path) not in str(result.edges)
    assert str(tmp_path) not in str(result.evidence)

    repeated = SourceCollector().collect(_context(workspace))
    assert repeated.fingerprint == result.fingerprint


def test_source_collector_degrades_without_exposing_parser_error(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "demo"
    workspace.mkdir()
    (workspace / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    from hermes_cli.gnothi.collectors import source

    real_execute_job = source.execute_job

    def failing_parser(job, *, workspace_root):
        if job["capability"] == "populate_backend_ast":
            raise RuntimeError(f"parser failed at {workspace_root}; token=secret")
        return real_execute_job(job, workspace_root=workspace_root)

    monkeypatch.setattr(source, "execute_job", failing_parser)

    result = SourceCollector().collect(_context(workspace))

    assert result.status == "partial"
    assert result.error_code == "RuntimeError"
    assert "parser failed" not in str(result)
    assert "token=secret" not in str(result)
    assert any(node["kind"] == "source_file" for node in result.nodes)
