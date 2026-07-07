"""`hades backend` command for Laravel-backed project knowledge."""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import time
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
from hermes_cli.hades_backend_status import (
    QUALITY_REPORT_HISTORY_KEY,
    QUALITY_REPORT_HISTORY_LIMIT,
    load_backend_status_payload,
    support_report_payload,
)
from hermes_cli.hades_backend_benchmark import run_hades_backend_benchmark
from hermes_cli.hades_quality_report import build_hades_quality_report
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
    support_report = sub.add_parser("support-report", help="Emit a redacted backend support report")
    support_report.add_argument("--json", action="store_true", help="Emit machine-readable support report JSON")
    quality_report = sub.add_parser("quality-report", help="Emit a Hades awareness quality report")
    quality_report.add_argument("--no-codebase-eval", default=None, help="Path to a no-codebase diagnosis eval fixture JSON")
    quality_report.add_argument("--skip-local-status", action="store_true", help="Do not include local backend support status")
    quality_report.add_argument("--record", action="store_true", help="Store this report as the latest local Hades quality snapshot")
    quality_report.add_argument("--json", action="store_true", help="Emit machine-readable quality report JSON")
    privacy_export = sub.add_parser("privacy-export", help="Export backend diagnosis/evidence data for the current workspace")
    privacy_export.add_argument(
        "--include-content",
        action="store_true",
        help="Include redacted source slices, evidence payloads, and diagnosis text in JSON output",
    )
    privacy_export.add_argument("--json", action="store_true", help="Emit machine-readable export JSON")
    privacy_delete = sub.add_parser("privacy-delete", help="Dry-run or delete backend diagnosis/evidence data for the current workspace")
    privacy_delete.add_argument("--yes", action="store_true", help="Actually delete scoped backend data; default is dry-run")
    privacy_delete.add_argument("--json", action="store_true", help="Emit machine-readable delete JSON")
    retention_cleanup = sub.add_parser(
        "retention-cleanup",
        help="Dry-run or delete scoped backend diagnosis/evidence data older than the retention window",
    )
    retention_cleanup.add_argument("--retention-days", type=int, required=True, help="Delete rows older than this many days")
    retention_cleanup.add_argument("--yes", action="store_true", help="Actually delete expired scoped data; default is dry-run")
    retention_cleanup.add_argument("--json", action="store_true", help="Emit machine-readable cleanup JSON")
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
    ingest_deploy = sub.add_parser("ingest-deploy", help="Upload deployed-version context as Hades bug evidence")
    ingest_deploy.add_argument("--deploy-commit", required=True, help="Commit currently deployed in the affected environment")
    ingest_deploy.add_argument("--workspace-head", default=None, help="Indexed workspace commit; defaults to the linked workspace head")
    ingest_deploy.add_argument("--bug-report-id", default=None, help="Optional Hades bug report id")
    ingest_deploy.add_argument("--environment", default=None, help="Affected environment label")
    ingest_deploy.add_argument("--source", default=None, help="Evidence source label")
    ingest_deploy.add_argument("--json", action="store_true", help="Emit machine-readable ingestion result")
    bug_intake = sub.add_parser("bug-intake", help="Create a structured Hades bug report with optional evidence files")
    bug_intake.add_argument("--title", required=True, help="Short bug title")
    bug_intake.add_argument("--symptom", required=True, help="Observed symptom")
    bug_intake.add_argument("--steps", default=None, help="Reproduction steps")
    bug_intake.add_argument("--expected", default=None, help="Expected behavior")
    bug_intake.add_argument("--actual", default=None, help="Actual behavior")
    bug_intake.add_argument("--severity", default=None, help="Optional severity label")
    bug_intake.add_argument("--environment", default=None, help="Environment or deploy context")
    bug_intake.add_argument("--test-output", action="append", default=None, help="Failing test output file; repeatable")
    bug_intake.add_argument("--log", action="append", default=None, help="Runtime log file; repeatable")
    bug_intake.add_argument("--deploy-commit", default=None, help="Commit currently deployed in the affected environment")
    bug_intake.add_argument("--workspace-head", default=None, help="Indexed workspace commit; defaults to the linked workspace head")
    bug_intake.add_argument("--deploy-source", default=None, help="Source label for deploy-version evidence")
    bug_intake.add_argument("--json", action="store_true", help="Emit machine-readable intake result")
    backfill_note = sub.add_parser("backfill-note", help="Preview note-quality backfill for a raw chunk or note file")
    backfill_note.add_argument("file", help="Path to a raw chunk or note file")
    backfill_note.add_argument(
        "--create-proposals",
        action="store_true",
        help="Create local pending memory proposals for extracted candidate facts",
    )
    backfill_note.add_argument("--json", action="store_true", help="Emit machine-readable backfill preview")
    benchmark = sub.add_parser("benchmark", help="Run local synthetic Hades backend artifact benchmarks")
    benchmark.add_argument("--medium-symbols", type=int, default=750, help="Synthetic medium graph symbol count")
    benchmark.add_argument("--large-symbols", type=int, default=5000, help="Synthetic large graph symbol count")
    benchmark.add_argument("--json", action="store_true", help="Emit machine-readable benchmark JSON")
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
    login_recovery = identity.get("login_recovery") if isinstance(identity.get("login_recovery"), dict) else {}
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
        if login_recovery.get("recommended_next_action"):
            print(f"  Next identity step: {login_recovery['recommended_next_action']}")
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


def _cmd_support_report(args: argparse.Namespace) -> int:
    report = support_report_payload()
    if getattr(args, "json", False):
        print(json.dumps(report, sort_keys=True))
        return 0
    print("Hades backend support report")
    print(f"  Configured: {report['configured']}")
    print(f"  Degraded:   {report['degraded']}")
    awareness = report.get("awareness") if isinstance(report.get("awareness"), dict) else {}
    print(f"  Awareness:  {awareness.get('status', 'unknown')}")
    print(f"  Bindings:   {len(report.get('bindings') or [])}")
    for action in report.get("actions") or []:
        print(f"  Action:     {action}")
    return 0


def _cmd_quality_report(args: argparse.Namespace) -> int:
    no_codebase_report = None
    if getattr(args, "no_codebase_eval", None):
        from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture

        fixtures, runs = load_no_codebase_eval_fixture(args.no_codebase_eval)
        no_codebase_report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    report = build_hades_quality_report(
        no_codebase_report=no_codebase_report,
        support_report=None if getattr(args, "skip_local_status", False) else support_report_payload(),
    )
    if getattr(args, "record", False):
        with db.connect_closing() as conn:
            _record_quality_report_snapshot(conn, report)
    if getattr(args, "json", False):
        print(json.dumps(report, sort_keys=True))
    else:
        print("Hades quality report")
        print(f"  Status:   {report['status']}")
        print(f"  Blockers: {report['summary']['blockers']}")
        print(f"  Warnings: {report['summary']['warnings']}")
        for action in report["action_queue"]:
            print(f"  Action:   [{action['severity']}] {action['id']} - {action['message']}")
    return 1 if report["status"] == "failed" else 0


def _record_quality_report_snapshot(conn, report: dict[str, Any]) -> None:
    recorded_at = int(time.time())
    db.record_sync_state(conn, "last_quality_report", report)
    previous = db.get_sync_state(conn, QUALITY_REPORT_HISTORY_KEY) or {}
    previous_entries = previous.get("entries") if isinstance(previous, dict) else []
    entries = [_quality_history_entry(report, recorded_at)]
    if isinstance(previous_entries, list):
        entries.extend(entry for entry in previous_entries if isinstance(entry, dict))
    db.record_sync_state(
        conn,
        QUALITY_REPORT_HISTORY_KEY,
        {
            "schema": "hades.quality_report_history.v1",
            "limit": QUALITY_REPORT_HISTORY_LIMIT,
            "entries": entries[:QUALITY_REPORT_HISTORY_LIMIT],
        },
    )


def _quality_history_entry(report: dict[str, Any], recorded_at: int) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    action_queue = report.get("action_queue") if isinstance(report.get("action_queue"), list) else []
    return {
        "schema": report.get("schema"),
        "generated_at": report.get("generated_at"),
        "recorded_at": recorded_at,
        "status": report.get("status"),
        "summary": {
            "blockers": int(summary.get("blockers") or 0),
            "warnings": int(summary.get("warnings") or 0),
            "actions": int(summary.get("actions") or 0),
        },
        "action_queue": [action for action in action_queue if isinstance(action, dict)][:10],
    }


def _cmd_privacy_export(args: argparse.Namespace) -> int:
    try:
        _agent, binding = _current_workspace_binding()
        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            response = client.privacy_export(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                include_content=bool(getattr(args, "include_content", False)),
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend privacy-export: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(response, sort_keys=True))
        return 0

    print("Hades backend privacy export")
    print(f"  Project:   {binding.project_id}")
    print(f"  Workspace: {binding.backend_workspace_binding_id}")
    print(f"  Content:   {'included' if response.get('include_content') else 'metadata only'}")
    for key, value in _privacy_counts(response, "counts").items():
        print(f"  {key}: {value}")
    return 0


def _cmd_privacy_delete(args: argparse.Namespace) -> int:
    dry_run = not bool(getattr(args, "yes", False))
    try:
        _agent, binding = _current_workspace_binding()
        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            response = client.privacy_delete(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                dry_run=dry_run,
                confirm=not dry_run,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend privacy-delete: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(response, sort_keys=True))
        return 0

    print("Hades backend privacy delete")
    print(f"  Project:   {binding.project_id}")
    print(f"  Workspace: {binding.backend_workspace_binding_id}")
    print(f"  Mode:      {'dry-run' if dry_run else 'confirmed delete'}")
    key = "would_delete" if dry_run else "deleted"
    for table, value in _privacy_counts(response, key).items():
        print(f"  {table}: {value}")
    return 0


def _cmd_retention_cleanup(args: argparse.Namespace) -> int:
    dry_run = not bool(getattr(args, "yes", False))
    try:
        _agent, binding = _current_workspace_binding()
        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            response = client.privacy_retention_cleanup(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
                retention_days=int(getattr(args, "retention_days")),
                dry_run=dry_run,
                confirm=not dry_run,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend retention-cleanup: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(response, sort_keys=True))
        return 0

    print("Hades backend retention cleanup")
    print(f"  Project:        {binding.project_id}")
    print(f"  Workspace:      {binding.backend_workspace_binding_id}")
    print(f"  Retention days: {response.get('retention_days', getattr(args, 'retention_days'))}")
    print(f"  Mode:           {'dry-run' if dry_run else 'confirmed delete'}")
    key = "would_delete" if dry_run else "deleted"
    for table, value in _privacy_counts(response, key).items():
        print(f"  {table}: {value}")
    return 0


def _privacy_counts(response: dict[str, Any], key: str) -> dict[str, Any]:
    counts = response.get(key)
    return counts if isinstance(counts, dict) else {}


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
            if status == "pending":
                status = "submitted"
                reason = reason or "backend_pending_review"
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
        f"artifacts {summary.get('artifacts_uploaded', 0) + summary.get('artifacts_skipped', 0)}, "
        f"inbox {summary.get('inbox_events', 0)}"
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


def _clean_commit(value: str | None) -> str:
    return str(value or "").strip()


def _short_commit(value: str | None) -> str:
    clean = _clean_commit(value)
    return clean[:12] if clean else "unknown"


def _commit_mismatch(deploy_commit: str, workspace_head_commit: str) -> bool | None:
    deploy = _clean_commit(deploy_commit)
    workspace = _clean_commit(workspace_head_commit)
    if not deploy or not workspace:
        return None
    return deploy.lower() != workspace.lower()


def _deploy_version_summary(
    *,
    deploy_commit: str,
    workspace_head_commit: str,
    environment: str | None,
) -> str:
    env = f" ({environment})" if environment else ""
    mismatch = _commit_mismatch(deploy_commit, workspace_head_commit)
    if mismatch is True:
        return (
            "Deploy commit mismatch"
            f"{env}: deploy {_short_commit(deploy_commit)} != indexed workspace {_short_commit(workspace_head_commit)}"
        )
    if mismatch is False:
        return f"Deploy commit matches indexed workspace{env}: {_short_commit(deploy_commit)}"
    if _clean_commit(workspace_head_commit):
        return f"Deploy commit unknown{env}; indexed workspace is {_short_commit(workspace_head_commit)}"
    return f"Deploy commit {_short_commit(deploy_commit)}{env}; indexed workspace commit unknown"


def _deploy_version_payload(
    *,
    deploy_commit: str,
    workspace_head_commit: str,
    environment: str | None,
    source: str,
) -> dict[str, Any]:
    return {
        "schema": "hades.deploy_version.v1",
        "source": source,
        "environment": environment,
        "deploy_commit": _clean_commit(deploy_commit) or None,
        "deploy_commit_short": _short_commit(deploy_commit),
        "workspace_head_commit": _clean_commit(workspace_head_commit) or None,
        "workspace_head_commit_short": _short_commit(workspace_head_commit),
        "mismatch": _commit_mismatch(deploy_commit, workspace_head_commit),
    }


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


def _create_deploy_version_evidence(
    client: object,
    binding: db.WorkspaceBinding,
    *,
    bug_report_id: str | None,
    deploy_commit: str,
    workspace_head_commit: str | None,
    environment: str | None,
    source: str,
) -> str | None:
    deploy_commit = _clean_commit(deploy_commit)
    if not deploy_commit:
        raise ValueError("deploy commit is required")
    workspace_head_commit = _clean_commit(workspace_head_commit or binding.head_commit)
    payload = _deploy_version_payload(
        deploy_commit=deploy_commit,
        workspace_head_commit=workspace_head_commit,
        environment=environment,
        source=source,
    )
    response = client.create_bug_evidence(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        bug_report_id=bug_report_id,
        kind="deploy_version",
        summary=_deploy_version_summary(
            deploy_commit=deploy_commit,
            workspace_head_commit=workspace_head_commit,
            environment=environment,
        ),
        payload=payload,
        source=source,
        redactions=0,
        retention_class="runtime_evidence",
    )
    evidence = response.get("evidence") if isinstance(response.get("evidence"), dict) else response
    return str(evidence.get("id")) if isinstance(evidence, dict) and evidence.get("id") else None


def _cmd_ingest_deploy(args: argparse.Namespace) -> int:
    try:
        _agent, binding = _current_workspace_binding()
        deploy_commit = _clean_commit(getattr(args, "deploy_commit", None))
        workspace_head_commit = _clean_commit(getattr(args, "workspace_head", None) or binding.head_commit)
        source = str(getattr(args, "source", None) or "deploy")
        environment = getattr(args, "environment", None)

        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            evidence_id = _create_deploy_version_evidence(
                client,
                binding,
                bug_report_id=getattr(args, "bug_report_id", None) or None,
                deploy_commit=deploy_commit,
                workspace_head_commit=workspace_head_commit,
                environment=environment,
                source=source,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend ingest deploy: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    result = {
        "status": "ok",
        "kind": "deploy_version",
        "project_id": binding.project_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "evidence_id": evidence_id,
        "deploy_commit": deploy_commit,
        "workspace_head_commit": workspace_head_commit or None,
        "mismatch": _commit_mismatch(deploy_commit, workspace_head_commit),
    }
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
    else:
        suffix = "unknown"
        if result["mismatch"] is True:
            suffix = f"mismatch deploy {_short_commit(deploy_commit)} != workspace {_short_commit(workspace_head_commit)}"
        elif result["mismatch"] is False:
            suffix = f"matches {_short_commit(deploy_commit)}"
        print(f"Hades bug evidence stored: {evidence_id or 'created'} (deploy_version, {suffix})")
    return 0


def _cmd_backfill_note(args: argparse.Namespace) -> int:
    from hermes_cli.hades_note_quality import analyze_note_quality, read_note_preview

    try:
        text, truncated = read_note_preview(args.file)
        result = analyze_note_quality(text, source=str(args.file), truncated=truncated)
        created_proposals: list[str] = []
        skipped_duplicates: list[str] = []
        if getattr(args, "create_proposals", False):
            _agent, binding = _current_workspace_binding()
            with db.connect_closing() as conn:
                existing_by_fingerprint = _existing_note_backfill_proposals_by_fingerprint(conn)
                for fact in result["candidate_facts"]:
                    fingerprint = str(fact.get("fingerprint") or "").strip()
                    if fingerprint and fingerprint in existing_by_fingerprint:
                        skipped_duplicates.append(existing_by_fingerprint[fingerprint].id)
                        continue
                    proposal = db.create_memory_proposal(
                        conn,
                        project_id=binding.project_id,
                        workspace_binding_id=binding.backend_workspace_binding_id,
                        action="create",
                        intent="note_backfill_candidate",
                        summary=str(fact.get("summary") or ""),
                        provenance={
                            "source": "hades_note_quality",
                            "note_source": str(args.file),
                            "fact_kind": fact.get("kind"),
                            "candidate_fact_fingerprint": fingerprint,
                            "evidence_ref": fact.get("evidence_ref"),
                            "candidate_fact": fact,
                        },
                    )
                    created_proposals.append(proposal.id)
        else:
            created_proposals = []
        result["created_proposal_count"] = len(created_proposals)
        result["created_proposal_ids"] = created_proposals
        result["skipped_duplicate_proposal_count"] = len(skipped_duplicates)
        result["skipped_duplicate_proposal_ids"] = skipped_duplicates
        if created_proposals:
            result["actions"] = [*result["actions"], "Run `hades backend sync` to submit created proposals for review."]
        if skipped_duplicates:
            result["actions"] = [
                *result["actions"],
                "Skipped duplicate candidate facts that already have local review proposals.",
            ]
    except Exception as exc:
        print(f"Hades backend backfill-note: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
        return 0

    print("Hades note backfill preview")
    print(f"  Classification: {result['classification']}")
    print(f"  Raw chunk:      {result['raw_chunk']}")
    print(f"  Quality:        {result['quality_grade']} ({result['quality_score']}/100)")
    print(f"  Promotion:      {result['promotion_state']}")
    print(f"  Candidate facts: {result['candidate_fact_count']}")
    print(f"  Created proposals: {result['created_proposal_count']}")
    print(f"  Skipped duplicates: {result['skipped_duplicate_proposal_count']}")
    for fact in result["candidate_facts"][:5]:
        print(f"  - {fact['summary']}")
    for action in result["actions"]:
        print(f"  Action: {action}")
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    medium_symbols = max(1, int(getattr(args, "medium_symbols", 750) or 750))
    large_symbols = max(medium_symbols, int(getattr(args, "large_symbols", 5000) or 5000))
    report = run_hades_backend_benchmark(
        cases=[
            {
                "name": "medium_code_graph",
                "symbols": medium_symbols,
                "routes": max(1, medium_symbols // 8),
                "edges": max(1, medium_symbols * 2),
            },
            {
                "name": "large_code_graph",
                "symbols": large_symbols,
                "routes": max(1, large_symbols // 10),
                "edges": max(1, large_symbols * 2),
            },
        ]
    )
    if getattr(args, "json", False):
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "passed" else 1

    print("Hades backend benchmark")
    print(f"  Status: {report['status']}")
    for case in report["cases"]:
        ratio = case["compression_ratio"]
        ratio_text = f"{ratio:.4f}" if isinstance(ratio, float) else "n/a"
        print(
            f"  {case['name']}: {case['upload_mode']} "
            f"{case['original_bytes']}B -> {case['compressed_bytes']}B "
            f"ratio={ratio_text} duration={case['duration_ms']}ms"
        )
    for warning in report["warnings"]:
        print(f"  warning: {warning}")
    return 0 if report["status"] == "passed" else 1


def _existing_note_backfill_proposals_by_fingerprint(conn) -> dict[str, db.MemoryProposal]:
    existing: dict[str, db.MemoryProposal] = {}
    for proposal in db.list_memory_proposals(conn):
        provenance = proposal.provenance or {}
        if provenance.get("source") != "hades_note_quality":
            continue
        candidate_fact = provenance.get("candidate_fact")
        legacy_fingerprint = candidate_fact.get("fingerprint") if isinstance(candidate_fact, dict) else ""
        fingerprint = str(provenance.get("candidate_fact_fingerprint") or legacy_fingerprint or "").strip()
        if fingerprint:
            existing[fingerprint] = proposal
    return existing


def _bug_report_id(response: dict[str, Any]) -> str | None:
    source = response.get("bug_report") if isinstance(response.get("bug_report"), dict) else response
    if not isinstance(source, dict):
        return None
    value = source.get("id") or source.get("bug_report_id")
    return str(value) if value else None


def _cmd_bug_intake(args: argparse.Namespace) -> int:
    try:
        agent, binding = _current_workspace_binding()
        payload = {
            "project_id": binding.project_id,
            "workspace_binding_id": binding.backend_workspace_binding_id,
            "title": str(args.title).strip(),
            "symptom": str(args.symptom).strip(),
            "payload": {
                "schema": "hades.bug_intake.v1",
                "steps": getattr(args, "steps", None),
                "expected": getattr(args, "expected", None),
                "actual": getattr(args, "actual", None),
                "severity": getattr(args, "severity", None),
                "environment": getattr(args, "environment", None),
                "agent_id": agent.agent_id,
            },
        }
        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            response = client.create_bug_report(**payload)
            bug_report_id = _bug_report_id(response)
            evidence_ids: list[str | None] = []
            for file_path in getattr(args, "test_output", None) or []:
                evidence_ids.append(
                    _create_intake_evidence(
                        client,
                        binding,
                        bug_report_id=bug_report_id,
                        file_path=file_path,
                        kind="failing_test",
                        retention_class="test_failure",
                    )
                )
            for file_path in getattr(args, "log", None) or []:
                evidence_ids.append(
                    _create_intake_evidence(
                        client,
                        binding,
                        bug_report_id=bug_report_id,
                        file_path=file_path,
                        kind="log_excerpt",
                        retention_class="log_excerpt",
                    )
                )
            if getattr(args, "deploy_commit", None):
                evidence_ids.append(
                    _create_deploy_version_evidence(
                        client,
                        binding,
                        bug_report_id=bug_report_id,
                        deploy_commit=str(args.deploy_commit),
                        workspace_head_commit=getattr(args, "workspace_head", None) or binding.head_commit,
                        environment=getattr(args, "environment", None),
                        source=str(getattr(args, "deploy_source", None) or "deploy"),
                    )
                )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend bug-intake: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    result = {
        "status": "ok",
        "bug_report_id": bug_report_id,
        "project_id": binding.project_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "evidence_ids": evidence_ids,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Hades bug report created: {bug_report_id or 'created'}")
        if evidence_ids:
            print(f"  Evidence: {len(evidence_ids)} item(s)")
    return 0


def _create_intake_evidence(
    client: object,
    binding: db.WorkspaceBinding,
    *,
    bug_report_id: str | None,
    file_path: str,
    kind: str,
    retention_class: str,
) -> str | None:
    text, truncated = _read_evidence_file(file_path)
    redacted = redact_secret(text)
    redactions = 1 if redacted != text else 0
    source = Path(file_path).name
    payload = _evidence_payload(kind, redacted, source, truncated)
    response = client.create_bug_evidence(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        bug_report_id=bug_report_id,
        kind=kind,
        summary=_first_interesting_line(redacted),
        payload=payload,
        source=source,
        redactions=redactions,
        retention_class=retention_class,
    )
    evidence = response.get("evidence") if isinstance(response.get("evidence"), dict) else response
    return str(evidence.get("id")) if isinstance(evidence, dict) and evidence.get("id") else None


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
    if action == "support-report":
        return _cmd_support_report(args)
    if action == "quality-report":
        return _cmd_quality_report(args)
    if action == "privacy-export":
        return _cmd_privacy_export(args)
    if action == "privacy-delete":
        return _cmd_privacy_delete(args)
    if action == "retention-cleanup":
        return _cmd_retention_cleanup(args)
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
    if action == "ingest-deploy":
        return _cmd_ingest_deploy(args)
    if action == "bug-intake":
        return _cmd_bug_intake(args)
    if action == "backfill-note":
        return _cmd_backfill_note(args)
    if action == "benchmark":
        return _cmd_benchmark(args)
    if action == "sync":
        return _cmd_sync(args)
    print(
        "usage: hades backend <setup|bootstrap|status|support-report|quality-report|privacy-export|privacy-delete|retention-cleanup|profiles|worker|jobs|approve-job|refuse-job|proposals|ack-proposal|ingest-test|ingest-log|ingest-deploy|bug-intake|backfill-note|benchmark|sync>",
        file=sys.stderr,
    )
    return 0
