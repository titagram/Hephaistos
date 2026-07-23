"""Installed components exercised together against real Git and test runners."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from agent.review_evidence import write_reviewer_transcript
from hermes_cli.engineering_review.authority import (
    ReviewAuthority,
    ReviewAuthorityClient,
)
from hermes_cli.engineering_review.evidence import encode_verified_findings
from hermes_cli.engineering_review.execution_policy import decide_execution
from hermes_cli.engineering_review.protocol import EngineRequest
from hermes_cli.engineering_review.runs import ReviewRun
from hermes_cli.engineering_review.terminal_execution import (
    SandboxSource,
    SandboxTerminalExecutor,
)
from toolsets import _HERMES_CORE_TOOLS


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "engineering_review"
ENGINE_TEST_ROOT = REPOSITORY_ROOT / "packages" / "hermes-engineering" / "tests"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Engineering Review E2E",
    "GIT_AUTHOR_EMAIL": "review@example.invalid",
    "GIT_COMMITTER_NAME": "Engineering Review E2E",
    "GIT_COMMITTER_EMAIL": "review@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(workspace: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(
        ["git", *args],
        cwd=workspace,
        env=GIT_ENV,
        text=text,
    )


def _overlay(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        relative = path.relative_to(source)
        if relative.name.endswith(".py.fixture"):
            relative = relative.with_name(
                relative.name.removesuffix(".py.fixture") + ".py"
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _fixture_workspace(kind: str, parent: Path) -> tuple[Path, str]:
    workspace = parent / f"{kind}-workspace"
    shutil.copytree(FIXTURES / kind / "base", workspace)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "base fixture"],
        cwd=workspace,
        env=GIT_ENV,
        check=True,
    )
    base_ref = str(_git(workspace, "rev-parse", "HEAD")).strip()
    _overlay(FIXTURES / kind / "head", workspace)
    # Tests are staged while production and the explicit untracked source remain
    # unstaged. The capture therefore exercises all local-diff channels.
    tests = workspace / "tests"
    if tests.exists():
        subprocess.run(["git", "add", "tests"], cwd=workspace, check=True)
    return workspace, base_ref


def _commit_fixture_head(workspace: Path, untracked: str) -> str:
    subprocess.run(["git", "add", "-u"], cwd=workspace, check=True)
    for production in ("feature.py", "feature.ts"):
        if (workspace / production).is_file():
            # shutil preserves template mtimes; same-size fixture edits can
            # otherwise hit Git's racy-clean stat shortcut.
            subprocess.run(
                ["git", "add", "--force", production],
                cwd=workspace,
                check=True,
            )
    # The named source is intentionally left untracked for the separate local
    # capture; all production and test changes form the immutable range used by
    # the efficacy probe.
    assert untracked in str(_git(workspace, "status", "--porcelain=v1"))
    subprocess.run(
        ["git", "commit", "-qm", "review fixture head"],
        cwd=workspace,
        env=GIT_ENV,
        check=True,
    )
    return str(_git(workspace, "rev-parse", "HEAD")).strip()


def _request(
    authority: ReviewAuthority,
    command: str,
    request_id: str,
    input_value: dict[str, object],
) -> EngineRequest:
    return EngineRequest(
        request_id=request_id,
        command=command,  # type: ignore[arg-type]
        workspace=authority.run.workspace,
        artifact_root=authority.run.root,
        input=input_value,
    )


def _invoke(
    authority: ReviewAuthority,
    command: str,
    input_value: dict[str, object],
    *,
    request_id: str | None = None,
):
    return ReviewAuthorityClient(authority.run.session_id).invoke(
        _request(
            authority,
            command,
            request_id or command,
            input_value,
        ),
        timeout=120,
    )


def _reviewer_result(
    *,
    brief: Path,
    diff: Path,
    read_diff: bool,
    findings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    calls = [
        {
            "id": "brief",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({"file_path": str(brief)}),
            },
        }
    ]
    if read_diff:
        calls.append({
            "id": "diff",
            "function": {
                "name": "read_file",
                "arguments": json.dumps({
                    "file_path": str(diff),
                    "offset": 0,
                    "limit": 100_000,
                }),
            },
        })
    messages: list[dict[str, object]] = [
        {"role": "assistant", "tool_calls": calls},
        *[
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": Path(
                    json.loads(call["function"]["arguments"])["file_path"]  # type: ignore[index]
                ).read_text(encoding="utf-8"),
            }
            for call in calls
        ],
    ]
    final = (
        encode_verified_findings(findings)
        if findings is not None
        else "No issues found in the assigned territory."
    )
    return {"messages": messages, "final_response": final}


def _write_reviewer(
    authority: ReviewAuthority,
    prompt: dict[str, object],
    *,
    agent_id: str,
    read_diff: bool,
    findings: list[dict[str, object]] | None = None,
) -> Path:
    prompt_text = str(prompt["text"])
    prompt_dir = authority.run.root / "plan-prompts"
    brief = prompt_dir / f"{quote(str(prompt['key']), safe='')}.brief.md"
    child = SimpleNamespace(
        _delegate_role="reviewer",
        _subagent_goal=prompt_text,
        _subagent_id=agent_id,
    )
    transcript = write_reviewer_transcript(
        authority.run.session_id,
        child,
        _reviewer_result(
            brief=brief,
            diff=authority.run.root / "target.diff",
            read_diff=read_diff,
            findings=findings,
        ),
    )
    assert transcript is not None
    return transcript


@pytest.mark.integration
@pytest.mark.parametrize(
    ("kind", "runner", "effective", "inert", "untracked"),
    [
        (
            "pytest",
            "pytest",
            "tests/test_effective.py",
            "tests/test_inert.py",
            "untracked.py",
        ),
        (
            "vitest",
            "vitest",
            "tests/effective.test.ts",
            "tests/inert.test.ts",
            "untracked.ts",
        ),
    ],
)
def test_real_local_review_classifies_test_efficacy_without_mutating_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    runner: str,
    effective: str,
    inert: str,
    untracked: str,
) -> None:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Vitest's probe worktree must be able to resolve the repository's already
    # installed, offline dependency tree by walking to the monorepo root.
    if kind == "vitest":
        parent = Path(
            tempfile.mkdtemp(prefix=".tmp-engineering-e2e-", dir=ENGINE_TEST_ROOT)
        )
    else:
        parent = tmp_path / "fixtures"
        parent.mkdir()
    try:
        workspace, base_ref = _fixture_workspace(kind, parent)
        head_ref = _commit_fixture_head(workspace, untracked)
        before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
        with ReviewAuthority(
            workspace=workspace,
            target="local",
            effort="medium",
            session_id=f"e2e-{kind}-local",
        ) as local_authority:
            local_capture = _invoke(
                local_authority,
                "capture-target",
                {"kind": "forged-and-ignored"},
            )
            assert local_capture.status == "passed", json.dumps(
                local_capture.to_wire(), indent=2
            )
            local_diff = (local_authority.run.root / "target.diff").read_text(
                encoding="utf-8"
            )
            assert untracked in local_diff

        with ReviewAuthority(
            workspace=workspace,
            target=f"{base_ref}..{head_ref}",
            effort="medium",
            session_id=f"e2e-{kind}-range",
        ) as authority:
            capture = _invoke(authority, "capture-target", {"kind": "local"})
            assert capture.status == "passed", json.dumps(capture.to_wire(), indent=2)
            assert capture.output["baseRef"] == base_ref
            assert capture.output["headRef"] == head_ref
            diff = (authority.run.root / "target.diff").read_text(encoding="utf-8")
            assert effective in diff
            assert inert in diff

            efficacy = _invoke(
                authority,
                "test-efficacy",
                {
                    "planPath": capture.output["planPath"],
                    "baseRef": base_ref,
                    "runner": runner,
                    "timeoutMs": 60_000,
                },
            )
            assert efficacy.status == "failed", json.dumps(efficacy.to_wire(), indent=2)
            assert efficacy.output["gated"] == [effective], efficacy.to_wire()
            assert efficacy.output["inert"] == [inert]
            assert efficacy.output["inconclusive"] == []
            assert efficacy.output["cleanupFailure"] is None
            assert not list(workspace.glob(".hermes-efficacy-*"))
            cleanup = ReviewAuthorityClient(authority.run.session_id).cleanup(
                authority.run.run_id,
                timeout=60,
            )
            assert cleanup.status == "passed"
        assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before
    finally:
        if kind == "vitest":
            shutil.rmtree(parent)


@pytest.mark.integration
def test_mixed_runner_requires_clarification_then_honors_explicit_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    workspace, base_ref = _fixture_workspace("mixed", tmp_path)
    head_ref = _commit_fixture_head(workspace, "untracked.py")
    before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
    with ReviewAuthority(
        workspace=workspace,
        target=f"{base_ref}..{head_ref}",
        effort="medium",
        session_id="e2e-mixed",
    ) as authority:
        capture = _invoke(authority, "capture-target", {"kind": "local"})
        ambiguous = _invoke(
            authority,
            "test-efficacy",
            {
                "planPath": capture.output["planPath"],
                "baseRef": base_ref,
                "runner": "auto",
                "timeoutMs": 60_000,
            },
            request_id="ambiguous",
        )
        assert ambiguous.status == "inconclusive"
        assert ambiguous.diagnostics[0].code == "ambiguous_runner"
        assert set(ambiguous.output["availableRunners"]) == {"pytest", "vitest"}

        selected = _invoke(
            authority,
            "test-efficacy",
            {
                "planPath": capture.output["planPath"],
                "baseRef": base_ref,
                "runner": "pytest",
                "timeoutMs": 60_000,
            },
            request_id="selected",
        )
        assert selected.output["runner"] == "pytest"
        assert selected.output["gated"] == ["tests/test_effective.py"], json.dumps(
            selected.to_wire(), indent=2
        )
    assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before


@pytest.mark.integration
def test_review_artifacts_fail_unread_coverage_and_deduplicate_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    workspace, base_ref = _fixture_workspace("pytest", tmp_path)
    head_ref = _commit_fixture_head(workspace, "untracked.py")
    before = _git(workspace, "status", "--porcelain=v1", "-z", text=False)
    with ReviewAuthority(
        workspace=workspace,
        target=f"{base_ref}..{head_ref}",
        effort="medium",
        session_id="e2e-artifacts",
    ) as authority:
        capture = _invoke(authority, "capture-target", {"kind": "local"})
        prompts = _invoke(
            authority,
            "build-prompts",
            {"planPath": capture.output["planPath"], "effort": "medium"},
        )
        assert prompts.status == "passed"
        roster = list(prompts.output["prompts"])
        assert len(roster) >= 2

        duplicate_a = {
            "id": "duplicate-a",
            "severity": "high",
            "title": "Selected value is not validated",
            "body": "The selected value should be checked before returning.",
            "path": "feature.py",
            "quotedCode": "return selected",
            "sourceReviewerIds": ["reviewer-a"],
            "verification": "confirmed",
        }
        duplicate_b = {
            **duplicate_a,
            "id": "duplicate-b",
            "title": "  selected VALUE is not validated ",
            "sourceReviewerIds": ["reviewer-b"],
        }
        transcript_a = _write_reviewer(
            authority,
            roster[0],
            agent_id="reviewer-a",
            read_diff=False,
        )
        transcript_b = _write_reviewer(
            authority,
            roster[1],
            agent_id="reviewer-b",
            read_diff=True,
        )
        verifier_prompt = roster[-1]
        _write_reviewer(
            authority,
            verifier_prompt,
            agent_id="finding-verifier",
            read_diff=True,
            findings=[duplicate_a, duplicate_b],
        )

        coverage = _invoke(
            authority,
            "check-coverage",
            {"planPath": capture.output["planPath"]},
        )
        assert coverage.status == "failed"
        assert coverage.output["coverage"]["unopenedAgents"], coverage.to_wire()

        resolved = _invoke(
            authority,
            "resolve-anchors",
            {"findings": [duplicate_a, duplicate_b]},
        )
        assert resolved.status == "passed"
        assert resolved.output["stats"]["deduplicated"] == 1
        assert len(resolved.output["findings"]) == 1

        composed = _invoke(
            authority,
            "compose-review",
            {
                "effort": "medium",
                "buildTestStatus": "passed",
                "testEfficacyStatus": "failed",
                "ciStatus": "not_available",
            },
        )
        assert composed.status == "failed"
        assert composed.output["event"] == "REQUEST_CHANGES"
        assert Path(composed.output["reportPath"]).is_file()
        assert Path(composed.output["verdictPath"]).is_file()
        for transcript in (transcript_a, transcript_b):
            roles = [
                json.loads(line)["message"]["role"]
                for line in transcript.read_text(encoding="utf-8").splitlines()
            ]
            assert all(left != right for left, right in zip(roles, roles[1:]))
        # The review capability is a CLI+skill edge feature, not a new schema
        # paid by every conversation.
        assert all("review" not in name for name in _HERMES_CORE_TOOLS)
    assert _git(workspace, "status", "--porcelain=v1", "-z", text=False) == before


@pytest.mark.integration
def test_real_docker_sandbox_acceptance_requires_reachable_preprovisioned_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declare the real sandbox gate without replacing it with a fake backend.

    Operators opt in with an image containing Node >=22 and the fixed
    ``/opt/hermes-review-dependencies`` layer. A missing daemon socket or image
    is an environment limitation and never counts as passing sandbox execution.
    """
    image = os.environ.get("HERMES_REVIEW_SANDBOX_E2E_IMAGE")
    if not image:
        pytest.skip(
            "real sandbox E2E requires HERMES_REVIEW_SANDBOX_E2E_IMAGE; "
            "the image must pre-provision offline review dependencies"
        )
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if probe.returncode != 0:
        pytest.skip(f"Docker daemon is not reachable: {probe.stderr.strip()}")

    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    workspace = tmp_path / "sandbox-source"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    (workspace / "README.md").write_text("sandbox acceptance\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "sandbox fixture"],
        cwd=workspace,
        env=GIT_ENV,
        check=True,
    )
    head = str(_git(workspace, "rev-parse", "HEAD")).strip()
    run = ReviewRun.create(
        workspace,
        target="https://github.com/example/repository/pull/1",
        effort="low",
        session_id="sandbox-e2e",
    )
    plan_path = run.atomic_artifact(
        "plan.json",
        json.dumps(
            {
                "files": [],
                "hermes": {
                    "buildTest": {
                        "packageManager": "npm",
                        "commands": [],
                    }
                },
            },
            separators=(",", ":"),
        ).encode(),
    )
    decision = decide_execution(
        target_kind="pr",
        sandbox="docker",
        allow_local=False,
        environ={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
    )
    executor = SandboxTerminalExecutor(
        run=run,
        decision=decision,
        config_loader=lambda: {
            "env_type": "docker",
            "docker_image": image,
        },
    )
    response = executor.invoke(
        EngineRequest(
            request_id="real-sandbox-build-test",
            command="build-test",
            workspace=workspace.resolve(),
            artifact_root=run.root,
            input={
                "planPath": str(plan_path),
                "timeoutMs": 60_000,
                "execution": decision.to_wire(),
            },
        ),
        timeout=90,
        source=SandboxSource(worktree=workspace, base_ref=head, head_ref=head),
    )
    cleanup_failure = executor.shutdown()
    assert response.status == "passed", [
        (item.code, item.message) for item in response.diagnostics
    ]
    assert response.output["commands"] == []
    assert cleanup_failure is None
