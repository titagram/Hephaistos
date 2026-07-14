import argparse
from pathlib import Path

from hermes_cli.gnothi.contract import new_artifact
from hermes_cli.gnothi.store import OrganismRevisionStore
from hermes_cli.hades_gnothi_cmd import build_gnothi_parser, gnothi_command


def _parser():
    parser = argparse.ArgumentParser()
    build_gnothi_parser(parser.add_subparsers(dest="command"), cmd_gnothi=lambda args: 0)
    return parser


def test_parser_accepts_every_local_surface():
    parser = _parser()
    cases = [
        ["gnothi-seauton", "status", "--json"],
        [
            "gnothi-seauton", "rebuild", "--json", "--force",
            "--workspace", "/tmp/demo", "--collector", "source",
            "--collector", "runtime",
        ],
        ["gnothi-seauton", "inspect", "component", "--json"],
        ["gnothi-seauton", "explain", "capability", "--json"],
        ["gnothi-seauton", "diff", "rev-a", "rev-b", "--json"],
        ["gnothi-seauton", "wiki", "--output", "/tmp/wiki.md"],
    ]
    assert [parser.parse_args(case).gnothi_action for case in cases] == [
        "status", "rebuild", "inspect", "explain", "diff", "wiki"
    ]
    assert parser.parse_args(cases[1]).collectors == ["source", "runtime"]


def test_missing_status_is_actionable_and_returns_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    args = _parser().parse_args(["gnothi-seauton", "status", "--json"])
    assert gnothi_command(args) == 1
    output = capsys.readouterr().out
    assert '"status": "missing"' in output
    assert "rebuild" in output


def test_status_reports_drift_and_targeted_actions(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli import hades_gnothi_cmd as command

    artifact = new_artifact(
        revision_id="rev-1", generation_id="git:abc", generation_scope="stable",
        head_commit="abc", collected_at="2026-07-14T12:00:00Z",
    )
    artifact["organism_contract"]["status"] = "current"
    OrganismRevisionStore().publish(artifact)
    monkeypatch.setattr(
        command,
        "drift_status",
        lambda workspace, current: {
            "invalidated_domains": ["source"],
            "domains": {"source": {"status": "stale"}},
            "actions": ["rebuild --collector source"],
        },
    )

    args = _parser().parse_args(["gnothi-seauton", "status", "--json"])
    assert gnothi_command(args) == 0
    output = capsys.readouterr().out
    assert '"invalidated_domains": ["source"]' in output
    assert "rebuild --collector source" in output


def test_rebuild_accepts_partial_and_wiki_writes_stdout_or_file(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from hermes_cli import hades_gnothi_cmd as command

    artifact = new_artifact(
        revision_id="rev-1", generation_id="git:abc", generation_scope="stable",
        head_commit="abc", collected_at="2026-07-14T12:00:00Z",
    )
    artifact["organism_contract"]["status"] = "partial"
    artifact["organism_contract"]["coverage"] = {}
    monkeypatch.setattr(command, "build_organism_revision", lambda *a, **k: artifact)
    rebuild = _parser().parse_args(
        ["gnothi-seauton", "rebuild", "--workspace", str(tmp_path), "--json"]
    )
    assert gnothi_command(rebuild) == 0

    store = OrganismRevisionStore()
    store.publish(artifact, published_at="2026-07-14T12:00:00Z")
    stdout_args = _parser().parse_args(["gnothi-seauton", "wiki"])
    assert gnothi_command(stdout_args) == 0
    assert "# Gnothi Seauton" in capsys.readouterr().out

    output = tmp_path / "wiki.md"
    file_args = _parser().parse_args(["gnothi-seauton", "wiki", "--output", str(output)])
    assert gnothi_command(file_args) == 0
    assert output.read_text().startswith("# Gnothi Seauton")
