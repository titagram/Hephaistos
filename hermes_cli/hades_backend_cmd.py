"""`hades backend` command for Laravel-backed project knowledge."""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from hermes_cli.config import load_config, save_config, save_env_value
from hermes_cli.hades_coordination import hades_coordination_profiles
from hermes_cli.hades_backend_client import HadesBackendClient, redact_secret, token_env_key
from hermes_cli.hades_backend_actions import (
    acknowledge_memory_proposal,
    approve_backend_job,
    list_backend_jobs,
    list_memory_proposals,
    refuse_backend_job,
)
from hermes_cli.hades_backend_runtime import default_agent_id, default_agent_label
from hermes_cli.hades_backend_status import load_backend_status_payload
from hermes_cli import hades_backend_db as db


AUTO_JOB_CAPABILITIES = {"read_files", "project_inspection", "sync_git_tree", "populate_backend_ast"}
SKIP_JOB_STATUSES = {"waiting_confirmation", "started", "completed", "failed", "expired", "cancelled", "unlinked"}


def build_backend_parser(subparsers, *, cmd_backend: Callable) -> None:
    parser = subparsers.add_parser(
        "backend",
        help="Configure Hades shared backend",
        description="Set up and inspect the Laravel-backed Hades project integration.",
    )
    sub = parser.add_subparsers(dest="backend_action")

    setup = sub.add_parser("setup", help="Register this local Hades agent with the backend")
    setup.add_argument("--url", required=True, help="Backend base URL")
    setup.add_argument("--project-token", required=True, help="Project-scoped bootstrap token")
    setup.add_argument("--project-id", required=True, help="Backend project id")
    setup.add_argument("--label", default=None, help="Local agent label")
    setup.add_argument("--non-interactive", action="store_true")

    bootstrap = sub.add_parser("bootstrap", help="Tokenized backend setup, project link, and initial sync")
    bootstrap.add_argument("--url", required=True, help="Backend base URL")
    bootstrap.add_argument("--project-token", required=True, help="Project-scoped bootstrap token")
    bootstrap.add_argument("--project-id", required=True, help="Backend project id")
    bootstrap.add_argument("--workspace", default=None, help="Workspace path to link (default: current directory)")
    bootstrap.add_argument("--project-name", default=None, help="Local Hades project name to create when needed")
    bootstrap.add_argument("--non-interactive", action="store_true")

    status = sub.add_parser("status", help="Show backend registration status")
    status.add_argument("--json", action="store_true", help="Emit machine-readable status JSON")
    profiles = sub.add_parser("profiles", help="Show curated local-only Hades coordination profiles")
    profiles.add_argument("--json", action="store_true", help="Emit machine-readable profile JSON")
    worker = sub.add_parser("worker", help="Process one batch of local plugin work items")
    worker.add_argument("--once", action="store_true", help="Explicit one-shot mode; currently the default")
    worker.add_argument("--project-id", default=None, help="Backend project id (default: configured project)")
    worker.add_argument("--local-workspace-id", default=None, help="Plugin local workspace id used to claim work")
    worker.add_argument("--agent-key", default="local_agent", help="Backend agent key to poll")
    worker.add_argument("--limit", type=int, default=1, help="Maximum work items to process")
    worker.add_argument("--json", action="store_true", help="Emit machine-readable worker summary")
    jobs = sub.add_parser("jobs", help="List local backend jobs needing review")
    jobs.add_argument("--status", action="append", default=None, help="Filter by job status; repeatable")
    jobs.add_argument("--all", action="store_true", help="Show all local backend jobs")
    jobs.add_argument("--json", action="store_true", help="Emit machine-readable job JSON")
    approve_job = sub.add_parser("approve-job", help="Approve and execute a waiting backend job")
    approve_job.add_argument("job_id", help="Local/backend job id")
    refuse_job = sub.add_parser("refuse-job", help="Refuse a waiting backend job")
    refuse_job.add_argument("job_id", help="Local/backend job id")
    refuse_job.add_argument("--reason", default="local_refused", help="Reason sent to the backend")
    proposals = sub.add_parser("proposals", help="List local memory proposals needing review")
    proposals.add_argument("--status", action="append", default=None, help="Filter by proposal status; repeatable")
    proposals.add_argument("--all", action="store_true", help="Show all local memory proposals")
    proposals.add_argument("--json", action="store_true", help="Emit machine-readable proposal JSON")
    ack_proposal = sub.add_parser("ack-proposal", help="Acknowledge a refused or conflicted memory proposal locally")
    ack_proposal.add_argument("proposal_id", help="Local memory proposal id")
    ingest_test = sub.add_parser("ingest-test", help="Upload a bounded failing-test output as Hades bug evidence")
    ingest_test.add_argument("file", help="Path to a test output file")
    ingest_test.add_argument("--bug-report-id", default=None, help="Optional Hades bug report id")
    ingest_test.add_argument("--source", default=None, help="Evidence source label")
    ingest_test.add_argument("--json", action="store_true", help="Emit machine-readable ingestion result")
    ingest_log = sub.add_parser("ingest-log", help="Upload a bounded runtime log excerpt as Hades bug evidence")
    ingest_log.add_argument("file", help="Path to a log file")
    ingest_log.add_argument("--bug-report-id", default=None, help="Optional Hades bug report id")
    ingest_log.add_argument("--source", default=None, help="Evidence source label")
    ingest_log.add_argument("--json", action="store_true", help="Emit machine-readable ingestion result")
    backfill_note = sub.add_parser("backfill-note", help="Preview note-quality backfill for a raw chunk or note file")
    backfill_note.add_argument("file", help="Path to a raw chunk or note file")
    backfill_note.add_argument("--json", action="store_true", help="Emit machine-readable backfill preview")
    sub.add_parser("sync", help="Run a one-shot backend sync")
    parser.set_defaults(func=cmd_backend)


def _detect_default_capabilities() -> list[str]:
    return ["read_files", "read_source_slice", "project_inspection", "sync_git_tree", "populate_backend_ast"]


def _cmd_setup(args: argparse.Namespace) -> int:
    label = args.label or default_agent_label()
    agent_id = default_agent_id(args.project_id, label)
    bootstrap = HadesBackendClient(args.url, args.project_token)
    bootstrap.verify_token(project_id=args.project_id)
    registered = bootstrap.register_agent(
        project_id=args.project_id,
        agent_id=agent_id,
        label=label,
        platform=platform.system().lower(),
        version=_version(),
        capabilities=_detect_default_capabilities(),
    )
    derived = str(registered.get("agent_token") or "").strip()
    if not derived:
        print("backend: registration response did not include agent_token", file=sys.stderr)
        return 1
    final_agent_id = str(registered.get("agent_id") or agent_id)
    env_key = token_env_key(args.url, args.project_id, final_agent_id)
    save_env_value(env_key, derived)

    config = load_config()
    backend = config.setdefault("backend", {})
    backend["enabled"] = True
    backend["base_url"] = args.url.rstrip("/")
    backend["default_project_id"] = args.project_id
    backend["agent_id"] = final_agent_id
    memory = config.setdefault("memory", {})
    memory["provider"] = "hades_backend"
    memory.setdefault("orphaned_cache_retention_days", 90)
    save_config(config)

    with db.connect_closing() as conn:
        db.save_agent(
            conn,
            agent_id=final_agent_id,
            project_id=args.project_id,
            base_url=args.url.rstrip("/"),
            label=label,
            token_env_key=env_key,
            capabilities=registered.get("capabilities") if isinstance(registered.get("capabilities"), dict) else {},
        )

    print("Backend setup complete")
    print(f"  Project: {args.project_id}")
    print(f"  Agent:   {final_agent_id} ({label})")
    print("  Memory:  hades_backend")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    payload = load_backend_status_payload()
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["configured"] else 1
    agent = payload["agent"]
    if agent is None:
        print("Hades backend: not configured")
        return 1
    print("Hades backend")
    print(f"  URL:     {agent['base_url']}")
    print(f"  Project: {agent['project_id']}")
    print(f"  Agent:   {agent['agent_id']} ({agent['label']})")
    print(f"  Bindings: {len(payload['bindings'])}")
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    personal_memory = identity.get("personal_memory") if isinstance(identity.get("personal_memory"), dict) else {}
    project_memory = identity.get("project_memory") if isinstance(identity.get("project_memory"), dict) else {}
    workspace_binding = identity.get("workspace_binding") if isinstance(identity.get("workspace_binding"), dict) else {}
    if identity:
        print(
            "  Personal memory: "
            f"{personal_memory.get('provider', 'local')} "
            f"({personal_memory.get('scope', 'local_profile')})"
        )
        print(
            "  Project memory:  "
            f"{project_memory.get('project_id') or 'none'} "
            f"({project_memory.get('cached_items', 0)} cached item(s))"
        )
        print(
            "  Workspace scope: "
            f"{workspace_binding.get('linked_bindings', 0)}/{workspace_binding.get('total_bindings', 0)} linked"
        )
    if payload["job_counts"]:
        print(f"  Jobs:    {payload['job_counts']}")
    if payload["proposal_counts"]:
        print(f"  Memory proposals: {payload['proposal_counts']}")
    if payload["inbox_counts"].get("total"):
        print(f"  Persephone inbox: {payload['inbox_counts']}")
    last_error = payload["sync"]["last_error"]
    if last_error:
        print(f"  Last sync error: {last_error.get('message', 'unknown error')}")
    for action in payload["actions"]:
        print(f"  Action:  {action}")
    return 0


def _cmd_profiles(args: argparse.Namespace) -> int:
    profiles = hades_coordination_profiles()
    payload = {
        "local_only": True,
        "backend_visible": False,
        "config_source": "config.yaml",
        "skill": "autonomous-ai-agents/hades-coordination",
        "profiles": profiles,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
        return 0

    print("Hades local coordination profiles")
    print("  Model/provider choices are resolved locally from config.yaml and are not sent to the backend.")
    for profile in profiles:
        routing = profile.get("model_routing") if isinstance(profile.get("model_routing"), dict) else {}
        budget = profile.get("budget") if isinstance(profile.get("budget"), dict) else {}
        toolsets = profile.get("toolsets") if isinstance(profile.get("toolsets"), list) else []
        print(f"  {profile['id']}: {profile['title']}")
        print(f"    Model profile: {routing.get('local_model_profile', 'local config')}")
        print(f"    Selector:      {routing.get('selector', 'local')}")
        print(f"    Budget:        {budget.get('max_turns', '?')} turns / {budget.get('max_runtime_seconds', '?')}s")
        print(f"    Toolsets:      {', '.join(str(item) for item in toolsets)}")
    return 0


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    setup_rc = _cmd_setup(
        SimpleNamespace(
            url=args.url,
            project_token=args.project_token,
            project_id=args.project_id,
            label=None,
            non_interactive=getattr(args, "non_interactive", False),
        )
    )
    if setup_rc != 0:
        return setup_rc

    try:
        project = _ensure_local_project(args)
        binding_id = _link_bootstrap_workspace(project.id, Path(args.workspace or ".").expanduser().resolve(), args.project_id)
    except Exception as exc:
        print(f"backend bootstrap: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    sync_rc = _cmd_sync(SimpleNamespace(backend_action="sync"))
    print("Hades backend bootstrap complete")
    print(f"  Local project: {project.slug} ({project.name})")
    print(f"  Workspace:     {Path(args.workspace or '.').expanduser().resolve()}")
    print(f"  Binding:       {binding_id}")
    print("  Next:          run `hades doctor` or start Hades from this workspace")
    return sync_rc


def _ensure_local_project(args: argparse.Namespace):
    from hermes_cli import projects_db as pdb

    workspace = Path(args.workspace or ".").expanduser().resolve()
    name = str(args.project_name or workspace.name or args.project_id).strip()
    with pdb.connect_closing() as conn:
        project = pdb.project_for_path(conn, str(workspace))
        if project is None:
            project_id = pdb.create_project(
                conn,
                name=name,
                folders=[str(workspace)],
                primary_path=str(workspace),
            )
            project = pdb.get_project(conn, project_id)
    if project is None:
        raise RuntimeError("local project vanished after creation")
    return project


def _link_bootstrap_workspace(local_project_id: str, workspace: Path, backend_project_id: str) -> str:
    from hermes_cli import hades_backend_runtime as runtime

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
    if agent is None:
        raise RuntimeError("Hades backend is not configured after setup")
    client = runtime.client_from_config()
    fingerprint = runtime.workspace_fingerprint(workspace, backend_project_id)
    shown = runtime.display_path(workspace)
    metadata = runtime.git_metadata(workspace)
    response = client.bind_workspace(
        project_id=backend_project_id,
        agent_id=agent.agent_id,
        local_project_id=local_project_id,
        workspace_fingerprint=fingerprint,
        display_path=shown,
        git_remote_display=metadata["git_remote_display"],
        git_remote_hash=metadata["git_remote_hash"],
        head_commit=metadata["head_commit"],
    )
    binding_id = str(response.get("workspace_binding_id") or response.get("id") or fingerprint)
    with db.connect_closing() as conn:
        db.upsert_workspace_binding(
            conn,
            project_id=backend_project_id,
            agent_id=agent.agent_id,
            local_project_id=local_project_id,
            workspace_fingerprint=fingerprint,
            display_path=shown,
            repo_root=str(workspace),
            git_remote_display=metadata["git_remote_display"],
            git_remote_hash=metadata["git_remote_hash"],
            head_commit=metadata["head_commit"],
            backend_workspace_binding_id=binding_id,
        )
    return binding_id


def _response_jobs(response: dict) -> list[dict]:
    value = response.get("jobs", response.get("data", []))
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _job_id(job: dict) -> str:
    return str(job.get("job_id") or job.get("id") or "").strip()


def _job_payload(job: dict) -> dict:
    value = job.get("payload")
    return value if isinstance(value, dict) else {}


def _job_capability(job: dict) -> str:
    payload = _job_payload(job)
    return str(job.get("capability") or payload.get("capability") or "").strip()


def _requires_confirmation(job: dict) -> bool:
    payload = _job_payload(job)
    policy = str(job.get("policy") or job.get("execution_policy") or payload.get("policy") or "").lower()
    return bool(job.get("requires_confirmation")) or policy in {"confirm", "manual", "approval_required"}


def _snapshot_items(response: dict) -> list[dict]:
    value = response.get("items", response.get("memory", []))
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _snapshot_version(response: dict) -> str:
    return str(
        response.get("version")
        or response.get("snapshot_version")
        or response.get("etag")
        or "unknown"
    )


def _proposal_response_status(response: dict) -> tuple[str, str | None]:
    source = response.get("proposal") if isinstance(response.get("proposal"), dict) else response
    status = str(source.get("status") or "pending").strip() or "pending"
    if status == "rejected":
        status = "refused"
    reason = source.get("reason") or source.get("reason_code") or source.get("message")
    return status, str(reason) if reason else None


def _record_sync_error(binding: db.WorkspaceBinding, message: str) -> None:
    with db.connect_closing() as conn:
        db.record_sync_state(
            conn,
            "last_sync_error",
            {
                "workspace_binding_id": binding.backend_workspace_binding_id,
                "project_id": binding.project_id,
                "message": redact_secret(message),
            },
        )


def _sync_memory(client, binding: db.WorkspaceBinding) -> tuple[int, int, int]:
    snapshots = proposals_synced = proposal_errors = 0
    if hasattr(client, "memory_snapshot"):
        response = client.memory_snapshot(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
        )
        items = _snapshot_items(response)
        version = _snapshot_version(response)
        with db.connect_closing() as conn:
            db.replace_memory_cache(
                conn,
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                version=version,
                items=items,
            )
        snapshots += 1

    if not hasattr(client, "create_memory_proposal"):
        return snapshots, proposals_synced, proposal_errors

    with db.connect_closing() as conn:
        pending = [
            proposal
            for proposal in db.list_memory_proposals_by_status(conn, ["pending"])
            if proposal.project_id == binding.project_id
            and proposal.workspace_binding_id == binding.backend_workspace_binding_id
        ]
    for proposal in pending:
        try:
            response = client.create_memory_proposal(
                project_id=proposal.project_id,
                workspace_binding_id=proposal.workspace_binding_id,
                local_proposal_id=proposal.id,
                action=proposal.action,
                intent=proposal.intent,
                summary=proposal.summary,
                provenance=proposal.provenance,
            )
            status, reason = _proposal_response_status(response)
            with db.connect_closing() as conn:
                db.mark_memory_proposal_status(conn, proposal.id, status, reason)
            proposals_synced += 1
        except Exception as exc:
            proposal_errors += 1
            _record_sync_error(binding, str(exc))
    return snapshots, proposals_synced, proposal_errors


def _cmd_sync(args: argparse.Namespace) -> int:
    from hermes_cli.hades_backend_sync import run_backend_sync

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []
    if agent is None:
        print("Hades backend sync: backend not configured", file=sys.stderr)
        return 1
    if not bindings:
        print("Hades backend sync: no linked workspaces")
        return 0
    result = run_backend_sync()
    summary = result.summary

    print(
        "Hades backend sync: "
        f"pulled {summary.get('pulled', 0)} job(s), completed {summary.get('completed', 0)}, "
        f"waiting {summary.get('waiting', 0)}, failed {summary.get('failed', 0)}, "
        f"skipped {summary.get('skipped', 0)}, expired {summary.get('expired', 0)}, "
        f"memory {summary.get('memory_snapshots', 0)}, proposals {summary.get('proposals_synced', 0)}, "
        f"artifacts {summary.get('artifacts_uploaded', 0)}, inbox {summary.get('inbox_events', 0)}"
    )
    return result.exit_code


def _cmd_worker(args: argparse.Namespace) -> int:
    from hermes_cli.hades_plugin_worker import run_plugin_worker_once

    json_mode = bool(getattr(args, "json", False))
    result = run_plugin_worker_once(
        project_id=getattr(args, "project_id", None),
        local_workspace_id=getattr(args, "local_workspace_id", None),
        agent_key=getattr(args, "agent_key", "local_agent") or "local_agent",
        limit=max(1, int(getattr(args, "limit", 1) or 1)),
        quiet=json_mode,
    )
    if json_mode:
        print(json.dumps(result.summary, sort_keys=True))
        return result.exit_code
    summary = result.summary
    if "error" in summary:
        return result.exit_code
    print(
        "Hades backend worker: "
        f"listed {summary.get('listed', 0)} item(s), claimed {summary.get('claimed', 0)}, "
        f"completed {summary.get('completed', 0)}, failed {summary.get('failed', 0)}, "
        f"skipped {summary.get('skipped', 0)}"
    )
    return result.exit_code


def _cmd_jobs(args: argparse.Namespace) -> int:
    statuses = None if getattr(args, "all", False) else (getattr(args, "status", None) or ["waiting_confirmation"])
    payload = list_backend_jobs(statuses=statuses)
    if getattr(args, "json", False):
        print(json.dumps({"jobs": payload}, sort_keys=True))
        return 0
    if not payload:
        print("No Hades backend jobs need review")
        return 0
    print("Hades backend jobs")
    for item in payload:
        print(f"  {item['job_id']}: {item['status']} {item['capability']} ({item['workspace_binding_id']})")
    return 0


def _cmd_approve_job(args: argparse.Namespace) -> int:
    job_id = str(args.job_id or "").strip()
    try:
        result = approve_backend_job(job_id)
    except Exception as exc:
        print(f"Hades backend approve-job: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    print(f"Hades backend job {job_id}: {result.status}")
    if not result.ok:
        print(f"Hades backend approve-job: {result.summary}", file=sys.stderr)
    return 0 if result.ok else 1


def _cmd_refuse_job(args: argparse.Namespace) -> int:
    job_id = str(args.job_id or "").strip()
    try:
        result = refuse_backend_job(job_id, reason=str(getattr(args, "reason", None) or "local_refused"))
    except Exception as exc:
        print(f"Hades backend refuse-job: {redact_secret(str(exc))}", file=sys.stderr)
        return 1
    print(f"Hades backend job {job_id}: {result.status}")
    return 0


def _cmd_proposals(args: argparse.Namespace) -> int:
    statuses = None if getattr(args, "all", False) else (getattr(args, "status", None) or ["refused", "conflicted"])
    payload = list_memory_proposals(statuses=statuses)
    if getattr(args, "json", False):
        print(json.dumps({"proposals": payload}, sort_keys=True))
        return 0
    if not payload:
        print("No Hades memory proposals need review")
        return 0
    print("Hades memory proposals")
    for item in payload:
        reason = f" reason={item['reason']}" if item["reason"] else ""
        print(f"  {item['proposal_id']}: {item['status']} {item['action']} {item['summary']}{reason}")
    return 0


def _cmd_ack_proposal(args: argparse.Namespace) -> int:
    proposal_id = str(args.proposal_id or "").strip()
    try:
        result = acknowledge_memory_proposal(proposal_id)
    except Exception as exc:
        print(f"Hades backend ack-proposal: {redact_secret(str(exc))}", file=sys.stderr)
        return 1
    print(f"Hades memory proposal {proposal_id}: {result.status}")
    return 0


def _current_workspace_binding() -> tuple[db.BackendAgent, db.WorkspaceBinding]:
    cwd = Path.cwd().resolve()
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
        raise RuntimeError("Current directory is not linked to a Hades backend workspace")
    matches.sort(key=lambda item: len(str(Path(item.repo_root))), reverse=True)
    return agent, matches[0]


def _read_evidence_file(path: str, *, max_bytes: int = 64_000) -> tuple[str, bool]:
    candidate = Path(path).expanduser()
    with candidate.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    return raw[:max_bytes].decode("utf-8", errors="replace"), truncated


def _compact_lines(text: str, *, max_chars: int = 4000) -> str:
    compact = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 15].rstrip() + "\n... [truncated]"


def _first_interesting_line(text: str) -> str:
    markers = (
        "failed",
        "failure",
        "error",
        "exception",
        "traceback",
        "sqlstate",
        "fatal",
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(marker in stripped.lower() for marker in markers):
            return stripped[:1000]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:1000]
    return "Hades bug evidence ingested from local file."


def _detected_test_framework(text: str, source: str) -> str:
    lowered = f"{source}\n{text[:4000]}".lower()
    if "phpunit" in lowered or "pest" in lowered:
        return "phpunit"
    if "pytest" in lowered or "traceback (most recent call last)" in lowered:
        return "pytest"
    if "vitest" in lowered:
        return "vitest"
    if "jest" in lowered:
        return "jest"
    return "unknown"


def _stack_frames(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    patterns = [
        re.compile(r"(?P<path>[A-Za-z0-9_./\\-]+\.(?:php|py|js|ts|tsx)):(?P<line>\d+)"),
        re.compile(r"File \"(?P<path>[^\"]+)\", line (?P<line>\d+)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            frames.append(
                {
                    "path": match.group("path").replace("\\\\", "/"),
                    "line": int(match.group("line")),
                }
            )
            if len(frames) >= 20:
                return frames
    return frames


def _evidence_payload(kind: str, text: str, source: str, truncated: bool) -> dict[str, Any]:
    if kind == "failing_test":
        return {
            "schema": "hades.test_output.v1",
            "source": source,
            "excerpt": _compact_lines(text),
            "truncated": truncated,
            "framework": _detected_test_framework(text, source),
            "frames": _stack_frames(text),
        }
    return {
        "schema": "hades.runtime_log_excerpt.v1",
        "source": source,
        "excerpt": _compact_lines(text),
        "truncated": truncated,
        "frames": _stack_frames(text),
    }


def _cmd_ingest_evidence(args: argparse.Namespace, *, kind: str, retention_class: str) -> int:
    try:
        agent, binding = _current_workspace_binding()
        source = str(getattr(args, "source", None) or Path(args.file).name)
        text, truncated = _read_evidence_file(args.file)
        redacted = redact_secret(text)
        redactions = 1 if redacted != text else 0
        payload = _evidence_payload(kind, redacted, source, truncated)
        summary = _first_interesting_line(redacted)

        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            response = client.create_bug_evidence(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                bug_report_id=getattr(args, "bug_report_id", None) or None,
                kind=kind,
                summary=summary,
                payload=payload,
                source=source,
                redactions=redactions,
                retention_class=retention_class,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend ingest evidence: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    evidence = response.get("evidence") if isinstance(response.get("evidence"), dict) else response
    result = {
        "status": "ok",
        "kind": kind,
        "project_id": binding.project_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "evidence_id": evidence.get("id") if isinstance(evidence, dict) else None,
        "redactions": redactions,
        "truncated": truncated,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Hades bug evidence stored: {result['evidence_id'] or 'created'} ({kind})")
    return 0


def _cmd_ingest_test(args: argparse.Namespace) -> int:
    return _cmd_ingest_evidence(args, kind="failing_test", retention_class="test_failure")


def _cmd_ingest_log(args: argparse.Namespace) -> int:
    return _cmd_ingest_evidence(args, kind="log_excerpt", retention_class="log_excerpt")


def _cmd_backfill_note(args: argparse.Namespace) -> int:
    from hermes_cli.hades_note_quality import analyze_note_quality, read_note_preview

    try:
        text, truncated = read_note_preview(args.file)
        result = analyze_note_quality(text, source=str(args.file), truncated=truncated)
    except Exception as exc:
        print(f"Hades backend backfill-note: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
        return 0

    print("Hades note backfill preview")
    print(f"  Classification: {result['classification']}")
    print(f"  Raw chunk:      {result['raw_chunk']}")
    print(f"  Candidate facts: {result['candidate_fact_count']}")
    for fact in result["candidate_facts"][:5]:
        print(f"  - {fact['summary']}")
    for action in result["actions"]:
        print(f"  Action: {action}")
    return 0


def _version() -> str:
    try:
        from hermes_cli import __version__

        return str(__version__)
    except Exception:
        return "0.0.0"


def hades_backend_command(args: argparse.Namespace) -> int:
    action = getattr(args, "backend_action", None)
    if action == "setup":
        return _cmd_setup(args)
    if action == "bootstrap":
        return _cmd_bootstrap(args)
    if action == "status":
        return _cmd_status(args)
    if action == "profiles":
        return _cmd_profiles(args)
    if action == "worker":
        return _cmd_worker(args)
    if action == "jobs":
        return _cmd_jobs(args)
    if action == "approve-job":
        return _cmd_approve_job(args)
    if action == "refuse-job":
        return _cmd_refuse_job(args)
    if action == "proposals":
        return _cmd_proposals(args)
    if action == "ack-proposal":
        return _cmd_ack_proposal(args)
    if action == "ingest-test":
        return _cmd_ingest_test(args)
    if action == "ingest-log":
        return _cmd_ingest_log(args)
    if action == "backfill-note":
        return _cmd_backfill_note(args)
    if action == "sync":
        return _cmd_sync(args)
    print(
        "usage: hades backend <setup|bootstrap|status|profiles|worker|jobs|approve-job|refuse-job|proposals|ack-proposal|ingest-test|ingest-log|backfill-note|sync>",
        file=sys.stderr,
    )
    return 0
