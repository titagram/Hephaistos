from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hermes_cli import hades_backend_db as db
from hermes_cli import hades_backend_runtime as runtime
from hermes_cli.hades_backend_jobs import execute_job
from hermes_cli.hades_backend_sync import _artifact_payload_hash, _artifact_upload_fields
from hermes_cli.hades_source_slice_policy import SourceSliceCandidate


EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path("/Users/gabriele/Dev/rocket-club/progetto-biliardo-codex")
REPORTS = EVAL_ROOT / "reports"

REQUIRED_SLICE_SYMBOLS = {
    "BookingController@validateBooking",
    "RecordManualPayment@buildFifoAllocations",
    "AdminPanelProvider@panel",
}


def _load_local_env() -> None:
    home = Path(os.environ["HERMES_HOME"]).expanduser()
    env_path = home / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _current_binding() -> tuple[db.BackendAgent, db.WorkspaceBinding]:
    cwd = PROJECT_ROOT.resolve()
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        if agent is None:
            raise RuntimeError("Hades backend is not configured")
        bindings = db.list_workspace_bindings(conn, status="linked")
    matches: list[db.WorkspaceBinding] = []
    for binding in bindings:
        try:
            cwd.relative_to(Path(binding.repo_root).resolve())
        except (OSError, ValueError):
            continue
        matches.append(binding)
    if not matches:
        raise RuntimeError(f"{PROJECT_ROOT} is not linked to a Hades backend workspace")
    matches.sort(key=lambda item: len(str(Path(item.repo_root))), reverse=True)
    return agent, matches[0]


def _upload_artifact(client: Any, agent: db.BackendAgent, binding: db.WorkspaceBinding, artifact: dict[str, Any]) -> dict[str, Any]:
    artifact_payload = dict(artifact)
    head_commit = str(binding.head_commit or "").strip()
    if head_commit:
        artifact_payload.setdefault("head_commit", head_commit)
        artifact_payload.setdefault("indexed_head_commit", head_commit)
        artifact_payload.setdefault("workspace_head_commit", head_commit)

    schema = str(artifact_payload["schema"])
    payload_hash = _artifact_payload_hash(artifact_payload)
    artifact_fields, compression = _artifact_upload_fields(artifact_payload)
    upload_payload = {
        "project_id": binding.project_id,
        "agent_id": agent.agent_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "schema": schema,
        **artifact_fields,
        "sha256": payload_hash,
        "truncated": bool(artifact_payload.get("truncated", False)),
        "redactions": int(artifact_payload.get("redactions", 0) or 0),
    }
    try:
        response = client.upload_artifact(**upload_payload)
    except Exception:
        if not compression.get("compressed"):
            raise
        response = client.upload_artifact(
            project_id=binding.project_id,
            agent_id=agent.agent_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            schema=schema,
            artifact=artifact_payload,
            sha256=payload_hash,
            truncated=bool(artifact_payload.get("truncated", False)),
            redactions=int(artifact_payload.get("redactions", 0) or 0),
        )
    artifact_response = response.get("artifact") if isinstance(response.get("artifact"), dict) else response
    return {
        "schema": schema,
        "sha256": payload_hash,
        "artifact_id": artifact_response.get("id") if isinstance(artifact_response, dict) else None,
        "compressed": bool(compression.get("compressed")),
        "candidate_count": len(artifact_payload.get("source_slice_candidates") or []),
        "summary": artifact_payload.get("summary"),
    }


def _filter_required_candidates(artifact: dict[str, Any]) -> None:
    candidates = artifact.get("source_slice_candidates")
    if not isinstance(candidates, list):
        artifact["source_slice_candidates"] = []
        return
    selected = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get("symbol") or "")
        path = str(candidate.get("path") or "")
        if symbol in REQUIRED_SLICE_SYMBOLS or path.endswith("RecordManualPayment.php") or path.endswith("AdminPanelProvider.php"):
            selected.append(candidate)
    artifact["source_slice_candidates"] = selected
    summary = str(artifact.get("summary") or "")
    artifact["summary"] = f"{summary}; filtered_source_slice_candidates:{len(selected)}"


def _ensure_eval_candidate(artifact: dict[str, Any], binding: db.WorkspaceBinding) -> None:
    candidates = artifact.setdefault("source_slice_candidates", [])
    if not isinstance(candidates, list):
        artifact["source_slice_candidates"] = candidates = []
    if any(
        isinstance(candidate, dict) and candidate.get("symbol") == "AdminPanelProvider@panel"
        for candidate in candidates
    ):
        return
    candidates.append(
        SourceSliceCandidate(
            path="app/Providers/Filament/AdminPanelProvider.php",
            start_line=24,
            end_line=57,
            symbol="AdminPanelProvider@panel",
            reason="laravel_panel_provider",
            priority=35,
            head_commit=str(binding.head_commit or ""),
        ).to_dict()
    )


def main() -> int:
    _load_local_env()
    REPORTS.mkdir(parents=True, exist_ok=True)
    agent, binding = _current_binding()
    client = runtime.client_from_config(timeout=30.0)

    jobs = [
        {
            "job_id": "fresh_rocket_eval_sync_git_tree",
            "capability": "sync_git_tree",
            "payload": {"max_files": 5000, "head_commit": binding.head_commit},
        },
        {
            "job_id": "fresh_rocket_eval_populate_backend_ast",
            "capability": "populate_backend_ast",
            "payload": {
                "max_files": 5000,
                "max_symbols": 12000,
                "max_edges": 16000,
                "max_source_slice_candidates": 500,
                "head_commit": binding.head_commit,
            },
        },
    ]
    uploaded = []
    for job in jobs:
        result = execute_job(job, workspace_root=PROJECT_ROOT)
        artifact = result.get("artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError(f"job {job['job_id']} produced no artifact")
        if artifact.get("schema") == "hades.php_graph.v1":
            _filter_required_candidates(artifact)
            _ensure_eval_candidate(artifact, binding)
        uploaded.append(
            {
                "job_id": job["job_id"],
                "status": result.get("status"),
                "upload": _upload_artifact(client, agent, binding, artifact),
            }
        )

    (REPORTS / "fresh-index-upload.json").write_text(json.dumps({"uploaded": uploaded}, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "uploaded": uploaded}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
