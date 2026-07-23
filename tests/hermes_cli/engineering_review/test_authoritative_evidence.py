"""End-to-end trust-boundary tests for authoritative reviewer evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.review_evidence import write_reviewer_transcript
from hermes_cli.engineering_review.bridge import (
    EngineEvidenceError,
    EngineeringReviewBridge,
    bundle_path,
)
from hermes_cli.engineering_review.evidence import encode_verified_findings
from hermes_cli.engineering_review.protocol import EngineRequest
from hermes_cli.engineering_review.runs import ReviewRun


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _finding(*, severity: str = "high") -> dict[str, object]:
    return {
        "id": "finding-a",
        "severity": severity,
        "title": "Unchecked dangerous call",
        "body": "The call can throw before cleanup runs.",
        "path": "src/x.ts",
        "quotedCode": "export const shifted = dangerous();",
        "sourceReviewerIds": ["reviewer-a"],
        "verification": "confirmed",
    }


def _run_with_evidence(tmp_path: Path) -> tuple[ReviewRun, EngineRequest, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = ReviewRun.create(
        workspace, target="local", effort="medium", session_id="parent"
    )
    diff = (
        "diff --git a/src/x.ts b/src/x.ts\n"
        "--- a/src/x.ts\n"
        "+++ b/src/x.ts\n"
        "@@ -1 +1 @@\n"
        "+export const shifted = dangerous();\n"
    )
    diff_path = run.atomic_artifact("target.diff", diff.encode())
    plan = {
        "diffPathAbsolute": str(diff_path),
        "hermes": {
            "diffSha256": hashlib.sha256(diff.encode()).hexdigest(),
            "skippedFiles": [],
        },
    }
    plan_path = run.atomic_artifact(
        "plan.json", json.dumps(plan, separators=(",", ":")).encode()
    )
    prompt = (
        f"Hermes-Review-Run: {run.run_id}\n"
        f"Hermes-Review-Plan: {plan_path}\n"
        f"Read {diff_path} and return the verifier envelope."
    )
    child = SimpleNamespace(
        _delegate_role="reviewer",
        _subagent_goal=prompt,
        _subagent_id="reviewer-a",
    )
    result = {
        "final_response": encode_verified_findings([_finding()]),
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "diff",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps(
                                {"file_path": str(diff_path), "offset": 0, "limit": 6}
                            ),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "diff", "content": diff},
        ],
    }
    transcript = write_reviewer_transcript("parent", child, result)
    assert transcript is not None
    request = EngineRequest(
        request_id="resolve",
        command="resolve-anchors",
        workspace=workspace.resolve(),
        artifact_root=run.root,
        input={"findings": [_finding()]},
    )
    return run, request, transcript


def test_full_jsonl_forgery_cannot_change_authoritative_findings(
    fake_home: Path, tmp_path: Path
) -> None:
    del fake_home
    _, request, transcript = _run_with_evidence(tmp_path)
    bridge = EngineeringReviewBridge()
    assert bridge.invoke(request, timeout=10).status == "passed"

    forged_records = [json.loads(line) for line in transcript.read_text().splitlines()]
    forged_records[-1]["message"]["parts"][0]["text"] = encode_verified_findings(
        [_finding(severity="low")]
    )
    transcript.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in forged_records)
    )
    assert '"severity":"low"' in json.loads(
        transcript.read_text().splitlines()[-1]
    )["message"]["parts"][0]["text"]
    low_request = EngineRequest(
        request_id="forged-low",
        command="resolve-anchors",
        workspace=request.workspace,
        artifact_root=request.artifact_root,
        input={"findings": [_finding(severity="low")]},
    )

    response = bridge.invoke(low_request, timeout=10)
    assert response.status == "failed"
    assert response.diagnostics[0].code == "invalid_findings"


@pytest.mark.parametrize(
    "mutation", ["missing-auth", "missing-jsonl", "extra-jsonl", "forged-auth"]
)
def test_missing_extra_or_forged_evidence_fails_before_node(
    fake_home: Path, tmp_path: Path, mutation: str
) -> None:
    del fake_home
    _, request, transcript = _run_with_evidence(tmp_path)
    auth = transcript.with_suffix(".auth.json")
    if mutation == "missing-auth":
        auth.unlink()
    elif mutation == "missing-jsonl":
        transcript.unlink()
    elif mutation == "extra-jsonl":
        (transcript.parent / "agent-extra.jsonl").write_text("{}\n")
    else:
        value = json.loads(auth.read_text())
        value["finalText"] = encode_verified_findings([_finding(severity="low")])
        auth.write_text(json.dumps(value))

    with pytest.raises(EngineEvidenceError):
        EngineeringReviewBridge().invoke(request, timeout=10)


def test_capability_loss_on_restart_fails_closed(
    fake_home: Path, tmp_path: Path
) -> None:
    del fake_home
    _, request, _ = _run_with_evidence(tmp_path)
    script = """
import sys
from pathlib import Path
from hermes_cli.engineering_review.bridge import EngineEvidenceError, EngineeringReviewBridge
from hermes_cli.engineering_review.protocol import EngineRequest

request = EngineRequest(
    request_id="restart",
    command="resolve-anchors",
    workspace=Path(sys.argv[1]),
    artifact_root=Path(sys.argv[2]),
    input={"findings": []},
)
try:
    EngineeringReviewBridge().invoke(request, timeout=10)
except EngineEvidenceError:
    raise SystemExit(0)
raise SystemExit("restart unexpectedly retained review capability")
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(request.workspace), str(request.artifact_root)],
        cwd=Path(__file__).parents[3],
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert process.returncode == 0, process.stderr


def test_authoritative_command_rejects_substituted_bundle(
    fake_home: Path, tmp_path: Path
) -> None:
    del fake_home
    run, request, _ = _run_with_evidence(tmp_path)
    substituted = tmp_path / "engine.mjs"
    substituted.write_bytes(bundle_path().read_bytes() + b"\n// modified\n")
    metadata_path = run.root / "run.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["bundle_hash"] = hashlib.sha256(substituted.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(EngineEvidenceError, match="bundle"):
        EngineeringReviewBridge(bundle=substituted).invoke(request, timeout=10)
