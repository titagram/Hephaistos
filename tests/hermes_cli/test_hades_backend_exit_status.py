from __future__ import annotations

import sys

import pytest


def _disable_unrelated_startup(monkeypatch: pytest.MonkeyPatch, cli_main) -> None:
    monkeypatch.setattr(cli_main, "_set_process_title", lambda: None)
    monkeypatch.setattr(cli_main, "_cleanup_quarantined_exes", lambda: None)
    monkeypatch.setattr(cli_main, "_recover_from_interrupted_install", lambda: None)
    monkeypatch.setattr(cli_main, "_prepare_agent_startup", lambda _args: None)


@pytest.mark.parametrize("action", ["list", "show", "draft", "verify"])
def test_wiki_action_errors_exit_nonzero_through_real_cli_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    action: str,
) -> None:
    from hermes_cli import main as cli_main

    argv = ["hades", "backend", "wiki", action]
    if action == "list":
        argv.extend(["--limit", "0", "--json"])
    elif action == "show":
        argv.extend(["not-a-ulid", "--json"])
    elif action == "draft":
        payload_path = tmp_path / "invalid-draft.json"
        payload_path.write_text("[]", encoding="utf-8")
        argv.extend(["--from-file", str(payload_path), "--json"])
    else:
        payload_path = tmp_path / "invalid-verify.json"
        payload_path.write_text("{}", encoding="utf-8")
        argv.extend(
            [
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "--expected-revision",
                "rev_1",
                "--evidence-file",
                str(payload_path),
                "--json",
            ]
        )

    monkeypatch.setattr(sys, "argv", argv)
    _disable_unrelated_startup(monkeypatch, cli_main)

    with pytest.raises(SystemExit) as exc_info:
        cli_main.main()

    assert exc_info.value.code == 1


@pytest.mark.parametrize("action", ["draft", "verify"])
def test_backend_422_from_wiki_writes_exits_nonzero_through_real_cli_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
    action: str,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from hermes_cli import hades_backend_db as hdb
    from hermes_cli import hades_wiki_actions
    from hermes_cli import main as cli_main
    from hermes_cli.hades_backend_client import HadesBackendError

    with hdb.connect_closing() as conn:
        hdb.save_agent(
            conn,
            agent_id="agent_1",
            project_id="proj_1",
            base_url="https://backend.example",
            label="dev-box",
            token_env_key="HADES_BACKEND_AGENT_TOKEN_TEST",
            capabilities={"populate_project_wiki": True, "verify_project_wiki": True},
        )
        hdb.upsert_workspace_binding(
            conn,
            project_id="proj_1",
            agent_id="agent_1",
            local_project_id="p_local",
            workspace_fingerprint="wf_1",
            display_path="~/repo",
            repo_root=str(workspace),
            git_remote_display="",
            git_remote_hash="",
            head_commit="",
            backend_workspace_binding_id="wb_1",
        )

    class RejectingClient:
        def create_wiki_draft(self, **_payload):
            raise HadesBackendError(
                "422: wiki draft rejected",
                status_code=422,
                code="wiki_draft_rejected",
            )

        def verify_wiki_page(self, _page_id, **_payload):
            raise HadesBackendError(
                "422: wiki verification rejected",
                status_code=422,
                code="wiki_verification_evidence_mismatch",
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        hades_wiki_actions.runtime,
        "client_for_agent",
        lambda _agent: RejectingClient(),
    )

    payload_path = tmp_path / f"valid-{action}.json"
    argv = ["hades", "backend", "wiki", action]
    if action == "draft":
        payload_path.write_text(
            '{"slug":"technical/overview","title":"Overview",'
            '"page_type":"technical","content_markdown":"# Overview",'
            '"evidence_refs":[]}',
            encoding="utf-8",
        )
        argv.extend(["--from-file", str(payload_path), "--json"])
    else:
        payload_path.write_text(
            '[{"kind":"file_ref","path":"src/app.py","hash":"'
            + ("a" * 64)
            + '","claims":[{"claim":"claim","proof":"proof"}]}]',
            encoding="utf-8",
        )
        argv.extend(
            [
                "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "--expected-revision",
                "rev_1",
                "--evidence-file",
                str(payload_path),
                "--json",
            ]
        )

    monkeypatch.setattr(sys, "argv", argv)
    _disable_unrelated_startup(monkeypatch, cli_main)

    with pytest.raises(SystemExit) as exc_info:
        cli_main.main()

    assert exc_info.value.code == 1
    assert "422" in capsys.readouterr().err
