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


def _job_payload_for_display(job: db.BackendJob) -> dict:
    return {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "workspace_binding_id": job.workspace_binding_id,
        "capability": job.capability,
        "status": job.status,
        "payload_keys": sorted(str(key) for key in job.payload.keys()),
        "result": job.result,
    }


def _proposal_payload_for_display(proposal: db.MemoryProposal) -> dict:
    return {
        "proposal_id": proposal.id,
        "project_id": proposal.project_id,
        "workspace_binding_id": proposal.workspace_binding_id,
        "action": proposal.action,
        "intent": proposal.intent,
        "summary": proposal.summary,
        "status": proposal.status,
        "reason": proposal.reason,
    }


def _cmd_jobs(args: argparse.Namespace) -> int:
    statuses = None if getattr(args, "all", False) else (getattr(args, "status", None) or ["waiting_confirmation"])
    with db.connect_closing() as conn:
        jobs = db.list_jobs(conn, statuses=statuses)
    payload = [_job_payload_for_display(job) for job in jobs]
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
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_sync import _upload_job_artifact

    job_id = str(args.job_id or "").strip()
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        job = db.get_job(conn, job_id)
        binding = db.get_binding_for_backend_id(conn, job.workspace_binding_id) if job else None
    if agent is None:
        print("Hades backend approve-job: backend not configured", file=sys.stderr)
        return 1
    if job is None:
        print(f"Hades backend approve-job: job not found: {job_id}", file=sys.stderr)
        return 1
    if binding is None:
        print(f"Hades backend approve-job: workspace binding not found: {job.workspace_binding_id}", file=sys.stderr)
        return 1
    if job.status != "waiting_confirmation":
        print(f"Hades backend approve-job: job {job_id} is {job.status}, not waiting_confirmation", file=sys.stderr)
        return 1

    client = None
    try:
        client = runtime.client_from_config()
        with db.connect_closing() as conn:
            db.update_job_status(conn, job_id, "started")
        client.update_job_status(job_id, **_status_payload(agent, binding, "started"))
        result = execute_job(
            {"job_id": job.job_id, "capability": job.capability, "payload": job.payload},
            workspace_root=binding.repo_root,
        )
        final_status = str(result.get("status") or "completed")
        if final_status not in {"completed", "failed"}:
            final_status = "completed"
        with db.connect_closing() as conn:
            db.update_job_status(conn, job_id, final_status, result=result)
        if final_status == "completed":
            _upload_job_artifact(client, agent, binding, job_id, result)
            client.submit_job_result(job_id, **_status_payload(agent, binding, final_status, result=result))
        else:
            client.update_job_status(
                job_id,
                **_status_payload(agent, binding, final_status, error=redact_secret(result.get("summary", ""))),
            )
    except Exception as exc:
        result = {"status": "failed", "summary": redact_secret(str(exc))}
        with db.connect_closing() as conn:
            db.update_job_status(conn, job_id, "failed", result=result)
        if client is not None:
            try:
                client.update_job_status(job_id, **_status_payload(agent, binding, "failed", error=result["summary"]))
            except Exception:
                pass
        print(f"Hades backend approve-job: {result['summary']}", file=sys.stderr)
        return 1

    print(f"Hades backend job {job_id}: {final_status}")
    return 0 if final_status == "completed" else 1


def _cmd_refuse_job(args: argparse.Namespace) -> int:
    from hermes_cli import hades_backend_runtime as runtime

    job_id = str(args.job_id or "").strip()
    reason = redact_secret(str(getattr(args, "reason", None) or "local_refused"))
    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        job = db.get_job(conn, job_id)
        binding = db.get_binding_for_backend_id(conn, job.workspace_binding_id) if job else None
    if agent is None:
        print("Hades backend refuse-job: backend not configured", file=sys.stderr)
        return 1
    if job is None:
        print(f"Hades backend refuse-job: job not found: {job_id}", file=sys.stderr)
        return 1
    if binding is None:
        print(f"Hades backend refuse-job: workspace binding not found: {job.workspace_binding_id}", file=sys.stderr)
        return 1
    if job.status != "waiting_confirmation":
        print(f"Hades backend refuse-job: job {job_id} is {job.status}, not waiting_confirmation", file=sys.stderr)
        return 1

    try:
        client = runtime.client_from_config()
        client.update_job_status(job_id, **_status_payload(agent, binding, "cancelled", reason=reason))
    except Exception as exc:
        print(f"Hades backend refuse-job: {redact_secret(str(exc))}", file=sys.stderr)
        return 1
    with db.connect_closing() as conn:
        db.update_job_status(conn, job_id, "cancelled", result={"summary": reason})
    print(f"Hades backend job {job_id}: cancelled")
    return 0


def _cmd_proposals(args: argparse.Namespace) -> int:
    statuses = None if getattr(args, "all", False) else (getattr(args, "status", None) or ["refused", "conflicted"])
    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn) if statuses is None else db.list_memory_proposals_by_status(conn, statuses)
    payload = [_proposal_payload_for_display(proposal) for proposal in proposals]
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
    with db.connect_closing() as conn:
        proposals = db.list_memory_proposals(conn, ids=[proposal_id])
        if not proposals:
            print(f"Hades backend ack-proposal: proposal not found: {proposal_id}", file=sys.stderr)
            return 1
        proposal = proposals[0]
        if proposal.status not in {"refused", "conflicted"}:
            print(
                f"Hades backend ack-proposal: proposal {proposal_id} is {proposal.status}, not refused/conflicted",
                file=sys.stderr,
            )
            return 1
        db.mark_memory_proposal_status(conn, proposal_id, "acknowledged", proposal.reason)
    print(f"Hades memory proposal {proposal_id}: acknowledged")
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
    if action == "sync":
        return _cmd_sync(args)
    print(
        "usage: hades backend <setup|bootstrap|status|profiles|worker|jobs|approve-job|refuse-job|proposals|ack-proposal|sync>",
        file=sys.stderr,
    )
    return 0
