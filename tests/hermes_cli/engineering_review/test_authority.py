"""Real-process tests for the long-lived review authority topology."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.engineering_review import authority as authority_module
from agent.review_evidence import write_reviewer_transcript
from hermes_cli.engineering_review.authority import (
    ReviewAuthority,
    ReviewAuthorityClient,
    ReviewAuthorityUnavailable,
)
from hermes_cli.engineering_review.evidence import encode_verified_findings
from hermes_cli.engineering_review.bridge import (
    EngineEvidenceError,
    EngineeringReviewBridge,
)
from hermes_cli.engineering_review.protocol import EngineRequest
from hermes_cli.engineering_review.runs import ReviewRunError
from hermes_cli.engineering_review import runs as runs_module


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "review@example.invalid"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Review Test"], cwd=workspace, check=True
    )
    source = workspace / "x.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "x.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=workspace, check=True)
    source.write_text("value = dangerous()\n", encoding="utf-8")
    return workspace


def _proxy_script(
    action: str, session_id: str, request_path: Path | None = None
) -> dict:
    script = """
import json
import sys
from pathlib import Path
from hermes_cli.engineering_review.authority import ReviewAuthorityClient
from hermes_cli.engineering_review.protocol import EngineRequest

client = ReviewAuthorityClient(sys.argv[1])
if sys.argv[2] == "start":
    value = client.start()
else:
    wire = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    request = EngineRequest.from_wire(wire)
    response = client.invoke(request, timeout=20)
    value = response.to_wire()
print(json.dumps(value, separators=(",", ":"), sort_keys=True))
"""
    args = [sys.executable, "-c", script, session_id, action]
    if request_path is not None:
        args.append(str(request_path))
    completed = subprocess.run(
        args,
        cwd=Path(__file__).parents[3],
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _proxy_invoke(tmp_path: Path, session_id: str, request: EngineRequest) -> dict:
    path = tmp_path / f"{request.request_id}.json"
    path.write_text(json.dumps(request.to_wire()), encoding="utf-8")
    return _proxy_script("invoke", session_id, path)


def test_real_start_proxy_writer_and_later_bridge_topology(
    fake_home: Path, tmp_path: Path
) -> None:
    del fake_home
    workspace = _git_workspace(tmp_path)
    with ReviewAuthority(
        workspace=workspace,
        target="local",
        effort="medium",
        session_id="parent",
    ) as authority:
        started = _proxy_script("start", "parent")
        assert started == {
            "planPath": str(authority.run.root / "plan.json"),
            "runId": authority.run.run_id,
        }

        captured = _proxy_invoke(
            tmp_path,
            "parent",
            EngineRequest(
                request_id="capture",
                command="capture-target",
                workspace=workspace.resolve(),
                artifact_root=authority.run.root,
                input={"kind": "local"},
            ),
        )
        assert captured["status"] == "passed"
        plan_path = captured["output"]["planPath"]

        prompts = _proxy_invoke(
            tmp_path,
            "parent",
            EngineRequest(
                request_id="prompts",
                command="build-prompts",
                workspace=workspace.resolve(),
                artifact_root=authority.run.root,
                input={"planPath": plan_path, "effort": "medium"},
            ),
        )
        assert prompts["status"] == "passed"
        prompt = prompts["output"]["prompts"][0]["text"]
        child = SimpleNamespace(
            _delegate_role="reviewer",
            _subagent_goal=prompt,
            _subagent_id="reviewer-a",
        )
        result = {
            "final_response": encode_verified_findings([]),
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "read",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({
                                    "file_path": captured["output"]["diffPath"]
                                }),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "read", "content": "diff"},
            ],
        }
        assert write_reviewer_transcript("parent", child, result) is not None

        resolved = _proxy_invoke(
            tmp_path,
            "parent",
            EngineRequest(
                request_id="resolve",
                command="resolve-anchors",
                workspace=workspace.resolve(),
                artifact_root=authority.run.root,
                input={"findings": []},
            ),
        )
        assert resolved["status"] == "passed"

        wrong_workspace = tmp_path / "wrong"
        wrong_workspace.mkdir()
        with pytest.raises(ReviewAuthorityUnavailable, match="workspace"):
            ReviewAuthorityClient("parent").invoke(
                EngineRequest(
                    request_id="wrong",
                    command="capture-target",
                    workspace=wrong_workspace.resolve(),
                    artifact_root=authority.run.root,
                    input={"kind": "local"},
                ),
                timeout=2,
            )

        socket_path = authority.socket_path

    assert not socket_path.exists()
    with pytest.raises(ReviewAuthorityUnavailable):
        ReviewAuthorityClient("parent").start()
    with pytest.raises(ReviewRunError, match="active run"):
        authority.run.commit_reviewer_evidence("late", b"{}")


def test_authority_executes_captured_bundle_bytes_after_source_path_changes(
    fake_home: Path, tmp_path: Path
) -> None:
    del fake_home
    from hermes_cli.engineering_review.bridge import bundle_path

    workspace = _git_workspace(tmp_path)
    copied_bundle = tmp_path / "engine.mjs"
    copied_bundle.write_bytes(bundle_path().read_bytes())
    with ReviewAuthority(
        workspace=workspace,
        target="local",
        effort="low",
        session_id="parent",
        bundle=copied_bundle,
    ) as authority:
        copied_bundle.write_text("throw new Error('substituted');\n", encoding="utf-8")
        response = ReviewAuthorityClient("parent").invoke(
            EngineRequest(
                request_id="capture",
                command="capture-target",
                workspace=workspace.resolve(),
                artifact_root=authority.run.root,
                input={"kind": "local"},
            ),
            timeout=20,
        )
        assert response.status == "passed"
        assert (authority.run.root / "target.diff").is_file()

        substituted = tmp_path / "substituted.mjs"
        substituted.write_text("throw new Error('attacker');\n", encoding="utf-8")
        with pytest.raises(EngineEvidenceError, match="bundle"):
            EngineeringReviewBridge(bundle=substituted, require_authority=True).invoke(
                EngineRequest(
                    request_id="malicious-prompts",
                    command="build-prompts",
                    workspace=workspace.resolve(),
                    artifact_root=authority.run.root,
                    input={
                        "planPath": str(authority.run.root / "plan.json"),
                        "effort": "low",
                    },
                ),
                timeout=20,
            )
        assert not (authority.run.root / "prompts.json").exists()


def test_authority_rejects_unverified_peer_and_bundle_selection_fields(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_home
    workspace = _git_workspace(tmp_path)
    with ReviewAuthority(
        workspace=workspace,
        target="local",
        effort="low",
        session_id="parent",
    ) as authority:
        with pytest.raises(ReviewAuthorityUnavailable, match="fields"):
            authority._dispatch(  # noqa: SLF001 - protocol negative test
                {"version": 1, "action": "start", "bundle": "/tmp/evil.mjs"}
            )
        monkeypatch.setattr(
            authority_module, "_peer_uid", lambda _connection: os.geteuid() + 1
        )
        with pytest.raises(ReviewAuthorityUnavailable, match="ownership"):
            ReviewAuthorityClient("parent").start()


def test_unsafe_socket_directory_rolls_back_capability(
    fake_home: Path, tmp_path: Path
) -> None:
    del fake_home
    workspace = _git_workspace(tmp_path)
    authority = ReviewAuthority(
        workspace=workspace,
        target="local",
        effort="low",
        session_id="parent",
    )
    unsafe = tmp_path / "unsafe-sockets"
    unsafe.mkdir(mode=0o755)
    authority.socket_path = unsafe / "authority.sock"

    with pytest.raises(ReviewAuthorityUnavailable, match="not private"):
        authority.start_serving()

    assert authority.run.root not in runs_module._CAPABILITIES
    assert authority.run.status == "cleanup_failed"
    with pytest.raises(ReviewRunError, match="active run"):
        authority.run.commit_reviewer_evidence("late", b"{}")


def test_constructor_preflight_failure_cannot_create_capability(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_home
    workspace = _git_workspace(tmp_path)
    before = set(runs_module._CAPABILITIES)
    monkeypatch.setattr(
        authority_module,
        "_authority_socket_path",
        lambda _session_id: (_ for _ in ()).throw(RuntimeError("preflight failed")),
    )

    with pytest.raises(RuntimeError, match="preflight failed"):
        ReviewAuthority(
            workspace=workspace,
            target="local",
            effort="low",
            session_id="parent",
        )

    assert set(runs_module._CAPABILITIES) == before


def test_thread_publication_failure_removes_socket_and_capability(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_home
    workspace = _git_workspace(tmp_path)
    authority = ReviewAuthority(
        workspace=workspace,
        target="local",
        effort="low",
        session_id="parent",
    )
    monkeypatch.setattr(
        authority_module.threading.Thread,
        "start",
        lambda _thread: (_ for _ in ()).throw(RuntimeError("thread failed")),
    )

    with pytest.raises(RuntimeError, match="thread failed"):
        authority.start_serving()

    assert not authority.socket_path.exists()
    assert authority.run.root not in runs_module._CAPABILITIES
    assert authority.run.status == "cleanup_failed"


def test_unsafe_metadata_cannot_prevent_close_from_revoking_capability(
    fake_home: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    del fake_home
    workspace = _git_workspace(tmp_path)
    authority = ReviewAuthority(
        workspace=workspace,
        target="local",
        effort="low",
        session_id="parent",
    )
    authority.start_serving()
    metadata = authority.run.root / "run.json"
    metadata.chmod(0o644)

    authority.close()

    assert not authority.socket_path.exists()
    assert authority.run.root not in runs_module._CAPABILITIES
    assert "could not persist review authority terminal state" in caplog.text
    metadata.chmod(0o600)
    with pytest.raises(ReviewRunError, match="capability is unavailable"):
        authority.run.commit_reviewer_evidence("late", b"{}")
