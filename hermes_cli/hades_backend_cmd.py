"""`hades backend` command for Laravel-backed project knowledge."""

from __future__ import annotations

import argparse
import platform
import sys
from typing import Callable

from hermes_cli.config import load_config, save_config, save_env_value
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

    sub.add_parser("status", help="Show backend registration status")
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
    if agent is None:
        print("Hades backend: not configured")
        return 1
    print("Hades backend")
    print(f"  URL:     {agent.base_url}")
    print(f"  Project: {agent.project_id}")
    print(f"  Agent:   {agent.agent_id} ({agent.label})")
    print(f"  Bindings: {len(bindings)}")
    return 0


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
    from hermes_cli.hades_backend_runtime import client_from_config

    with db.connect_closing() as conn:
        agent = db.get_default_agent(conn)
        bindings = db.list_workspace_bindings(conn, status="linked") if agent else []

    if agent is None:
        print("Hades backend sync: backend not configured", file=sys.stderr)
        return 1
    if not bindings:
        print("Hades backend sync: no linked workspaces")
        return 0

    try:
        client = client_from_config()
    except Exception as exc:
        print(f"Hades backend sync: {redact_secret(str(exc))}", file=sys.stderr)
        return 1
    pulled = completed = waiting = failed = skipped = 0
    memory_snapshots = proposals_synced = proposal_errors = 0
    sync_errors = 0

    for binding in bindings:
        try:
            snapshots, synced, errors = _sync_memory(client, binding)
            memory_snapshots += snapshots
            proposals_synced += synced
            proposal_errors += errors
            sync_errors += errors
        except Exception as exc:
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            print(
                f"backend sync: failed to sync memory for {binding.display_path}: {redact_secret(str(exc))}",
                file=sys.stderr,
            )

        try:
            response = client.pull_jobs(
                project_id=binding.project_id,
                agent_id=agent.agent_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                capabilities=_detect_default_capabilities(),
            )
        except Exception as exc:
            sync_errors += 1
            _record_sync_error(binding, str(exc))
            print(f"backend sync: failed to pull jobs for {binding.display_path}: {redact_secret(str(exc))}", file=sys.stderr)
            continue

        for job in _response_jobs(response):
            jid = _job_id(job)
            capability = _job_capability(job)
            payload = _job_payload(job)
            if not jid:
                skipped += 1
                continue
            pulled += 1

            with db.connect_closing() as conn:
                existing = db.get_job(conn, jid)
                if existing and existing.status in SKIP_JOB_STATUSES:
                    skipped += 1
                    continue
                db.upsert_job(
                    conn,
                    job_id=jid,
                    project_id=binding.project_id,
                    workspace_binding_id=binding.backend_workspace_binding_id,
                    capability=capability,
                    payload=payload,
                    status="received",
                )

            try:
                client.update_job_status(jid, **_status_payload(agent, binding, "received"))
                if capability not in AUTO_JOB_CAPABILITIES or _requires_confirmation(job):
                    with db.connect_closing() as conn:
                        db.update_job_status(conn, jid, "waiting_confirmation")
                    client.update_job_status(
                        jid,
                        **_status_payload(
                            agent,
                            binding,
                            "waiting_confirmation",
                            reason="local_confirmation_required",
                        ),
                    )
                    waiting += 1
                    continue

                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, "started")
                client.update_job_status(jid, **_status_payload(agent, binding, "started"))

                result = execute_job(
                    {"job_id": jid, "capability": capability, "payload": payload},
                    workspace_root=binding.repo_root,
                )
                final_status = str(result.get("status") or "completed")
                if final_status not in {"completed", "failed"}:
                    final_status = "completed"
                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, final_status, result=result)
                if final_status == "completed":
                    client.submit_job_result(jid, **_status_payload(agent, binding, final_status, result=result))
                    completed += 1
                else:
                    client.update_job_status(
                        jid,
                        **_status_payload(agent, binding, final_status, error=redact_secret(result.get("summary", ""))),
                    )
                    failed += 1
            except Exception as exc:
                result = {"status": "failed", "summary": redact_secret(str(exc))}
                with db.connect_closing() as conn:
                    db.update_job_status(conn, jid, "failed", result=result)
                try:
                    client.update_job_status(jid, **_status_payload(agent, binding, "failed", error=result["summary"]))
                except Exception:
                    pass
                failed += 1

    summary = {
        "pulled": pulled,
        "completed": completed,
        "waiting": waiting,
        "failed": failed,
        "skipped": skipped,
        "memory_snapshots": memory_snapshots,
        "proposals_synced": proposals_synced,
        "proposal_errors": proposal_errors,
    }
    with db.connect_closing() as conn:
        db.record_sync_state(conn, "last_sync_summary", summary)

    print(
        "Hades backend sync: "
        f"pulled {pulled} job(s), completed {completed}, waiting {waiting}, failed {failed}, skipped {skipped}, "
        f"memory {memory_snapshots}, proposals {proposals_synced}"
    )
    return 1 if sync_errors else 0


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
    if action == "status":
        return _cmd_status(args)
    if action == "sync":
        return _cmd_sync(args)
    print("usage: hades backend <setup|status|sync>", file=sys.stderr)
    return 0
