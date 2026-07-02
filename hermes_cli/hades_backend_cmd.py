"""`hades backend` command for Laravel-backed project knowledge."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from hermes_cli.config import load_config, save_config, save_env_value
from hermes_cli.hades_coordination import hades_coordination_profiles
from hermes_cli.hades_backend_client import HadesBackendClient, redact_secret, token_env_key
from hermes_cli.hades_backend_jobs import execute_job
from hermes_cli.hades_backend_runtime import default_agent_id, default_agent_label
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
    sub.add_parser("sync", help="Run a one-shot backend sync")
    parser.set_defaults(func=cmd_backend)


def _detect_default_capabilities() -> list[str]:
    return ["read_files", "project_inspection", "sync_git_tree", "populate_backend_ast"]


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
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = []
        if agent:
            bindings = conn.execute(
                "SELECT * FROM workspace_bindings WHERE agent_id = ? ORDER BY updated_at DESC",
                (agent.agent_id,),
            ).fetchall()
            job_counts = db.count_jobs_by_status(conn)
            proposal_counts = db.count_memory_proposals_by_status(conn)
            inbox_counts = db.count_inbox_events(conn)
            last_summary = db.get_sync_state(conn, "last_sync_summary")
            last_error = db.get_sync_state(conn, "last_sync_error")
        else:
            job_counts = {}
            proposal_counts = {}
            inbox_counts = {"total": 0, "unread": 0}
            last_summary = None
            last_error = None

    payload = _backend_status_payload(
        agent=agent,
        bindings=bindings,
        job_counts=job_counts,
        proposal_counts=proposal_counts,
        inbox_counts=inbox_counts,
        last_summary=last_summary,
        last_error=last_error,
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
        return 0 if agent is not None else 1
    if agent is None:
        print("Hades backend: not configured")
        return 1
    print("Hades backend")
    print(f"  URL:     {agent.base_url}")
    print(f"  Project: {agent.project_id}")
    print(f"  Agent:   {agent.agent_id} ({agent.label})")
    print(f"  Bindings: {len(bindings)}")
    if job_counts:
        print(f"  Jobs:    {job_counts}")
    if proposal_counts:
        print(f"  Memory proposals: {proposal_counts}")
    if inbox_counts.get("total"):
        print(f"  Persephone inbox: {inbox_counts}")
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


def _backend_status_payload(
    *,
    agent,
    bindings,
    job_counts: dict,
    proposal_counts: dict,
    inbox_counts: dict,
    last_summary,
    last_error,
) -> dict:
    refused = int(proposal_counts.get("refused", 0) or 0) + int(proposal_counts.get("conflicted", 0) or 0)
    waiting = int(job_counts.get("waiting_confirmation", 0) or 0)
    actions: list[str] = []
    if waiting:
        actions.append(f"Review {waiting} backend job(s) waiting for confirmation.")
    if refused:
        actions.append(f"Review {refused} refused/conflicted memory proposal(s).")
    if last_error:
        actions.append("Inspect last backend sync error and rerun `hades backend sync`.")
    return {
        "configured": agent is not None,
        "agent": None if agent is None else {
            "agent_id": agent.agent_id,
            "project_id": agent.project_id,
            "base_url": agent.base_url,
            "label": agent.label,
            "capabilities": agent.capabilities,
        },
        "bindings": [
            {
                "workspace_fingerprint": row["workspace_fingerprint"],
                "workspace_binding_id": row["backend_workspace_binding_id"],
                "project_id": row["project_id"],
                "local_project_id": row["local_project_id"],
                "display_path": row["display_path"],
                "status": row["status"],
            }
            for row in bindings
        ],
        "job_counts": job_counts,
        "proposal_counts": proposal_counts,
        "inbox_counts": inbox_counts,
        "sync": {"last_summary": last_summary, "last_error": last_error},
        "degraded": bool(waiting or refused or last_error),
        "actions": actions,
    }


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


def _status_payload(agent: db.BackendAgent, binding: db.WorkspaceBinding, status: str, **extra) -> dict:
    payload = {
        "project_id": binding.project_id,
        "agent_id": agent.agent_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "status": status,
    }
    payload.update(extra)
    return payload


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
    if action == "sync":
        return _cmd_sync(args)
    print("usage: hades backend <setup|bootstrap|status|profiles|worker|sync>", file=sys.stderr)
    return 0
