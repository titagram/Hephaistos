"""`hades backend` command for Laravel-backed project knowledge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from hermes_cli.config import load_config, save_config, save_env_value
from hermes_cli.hades_coordination import hades_coordination_profiles
from hermes_cli.hades_backend_client import (
    HadesBackendClient,
    HadesBackendError,
    plugin_device_secret_env_key,
    plugin_token_env_key,
    redact_secret,
    token_env_key,
)
from hermes_cli.hades_backend_actions import (
    acknowledge_memory_proposal,
    approve_backend_job,
    approve_backend_jobs,
    list_backend_jobs,
    list_memory_proposals,
    promote_diagnosis_report,
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
from hermes_cli.hades_quality_report import (
    build_agent_work_quality_report,
    build_hades_quality_report,
    build_note_backfill_quality_report,
)
from hermes_cli import hades_backend_db as db


AUTO_JOB_CAPABILITIES = {
    "read_files",
    "project_inspection",
    "sync_git_tree",
    "populate_backend_ast",
    "populate_project_wiki",
}
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
    setup_token = setup.add_mutually_exclusive_group(required=True)
    setup_token.add_argument("--project-token", help="Project-scoped bootstrap token")
    setup_token.add_argument(
        "--project-token-stdin",
        action="store_true",
        help="Read the project-scoped bootstrap token from standard input",
    )
    setup.add_argument("--project-id", required=True, help="Backend project id")
    setup.add_argument("--label", default=None, help="Local agent label")
    setup.add_argument("--non-interactive", action="store_true")

    bootstrap = sub.add_parser("bootstrap", help="Tokenized backend setup, project link, and initial sync")
    bootstrap.add_argument("--url", required=True, help="Backend base URL")
    bootstrap_token = bootstrap.add_mutually_exclusive_group(required=True)
    bootstrap_token.add_argument("--project-token", help="Project-scoped bootstrap token")
    bootstrap_token.add_argument(
        "--project-token-stdin",
        action="store_true",
        help="Read the project-scoped bootstrap token from standard input",
    )
    bootstrap.add_argument("--project-id", required=True, help="Backend project id")
    bootstrap.add_argument("--workspace", default=None, help="Workspace path to link (default: current directory)")
    bootstrap.add_argument("--project-name", default=None, help="Local Hades project name to create when needed")
    bootstrap.add_argument("--non-interactive", action="store_true")
    bootstrap_awareness = sub.add_parser(
        "bootstrap-awareness",
        aliases=("awareness-bootstrap",),
        help="Populate project artifacts, source slices, and wiki awareness for the current workspace",
    )
    bootstrap_awareness.add_argument(
        "--yes",
        action="store_true",
        help="Approve generated read_source_slice jobs for the current workspace during the bootstrap",
    )
    bootstrap_awareness.add_argument(
        "--skip-wiki",
        action="store_true",
        help="Only refresh artifacts/source slices; do not enqueue or process populate_project_wiki",
    )
    bootstrap_awareness.add_argument(
        "--record-quality-report",
        action="store_true",
        help="Record a quality-report snapshot after the awareness bootstrap",
    )
    bootstrap_awareness.add_argument("--json", action="store_true", help="Emit machine-readable bootstrap summary")

    status = sub.add_parser("status", help="Show backend registration status")
    status.add_argument("--json", action="store_true", help="Emit machine-readable status JSON")
    support_report = sub.add_parser("support-report", help="Emit a redacted backend support report")
    support_report.add_argument("--json", action="store_true", help="Emit machine-readable support report JSON")
    quality_report = sub.add_parser("quality-report", help="Emit a Hades awareness quality report")
    quality_report.add_argument("--no-codebase-eval", default=None, help="Path to a no-codebase diagnosis eval fixture JSON")
    quality_report.add_argument("--suite", default=None, help="Path to a Hades no-codebase quality suite JSON")
    quality_report.add_argument("--skip-local-status", action="store_true", help="Do not include local backend support status")
    quality_report.add_argument("--record", action="store_true", help="Store this report as the latest local Hades quality snapshot")
    quality_report.add_argument("--json", action="store_true", help="Emit machine-readable quality report JSON")
    schedule_quality = sub.add_parser(
        "schedule-quality",
        help="Create or update a recurring Hades quality-report cron job",
    )
    schedule_quality.add_argument("--schedule", default="0 8 * * *", help="Cron schedule or interval; default daily at 08:00")
    schedule_quality.add_argument("--name", default="Hades backend quality report", help="Cron job name")
    schedule_quality.add_argument("--deliver", default="local", help="Cron delivery target")
    schedule_quality.add_argument("--no-codebase-eval", default=None, help="Optional no-codebase diagnosis eval fixture JSON")
    schedule_quality.add_argument("--suite", default=None, help="Optional Hades no-codebase quality suite JSON")
    schedule_quality.add_argument("--json", action="store_true", help="Emit machine-readable schedule result")
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
    worker.add_argument("--loop", action="store_true", help="Poll for work until stopped or a loop limit is reached")
    worker.add_argument("--interval", type=float, default=5.0, help="Seconds to wait between idle loop cycles")
    worker.add_argument("--max-cycles", type=int, default=0, help="Maximum loop cycles; 0 means unlimited")
    worker.add_argument("--idle-exit-after", type=int, default=0, help="Exit after this many consecutive idle cycles; 0 disables")
    worker.add_argument("--max-errors", type=int, default=3, help="Stop loop after this many consecutive failed cycles; 0 disables")
    worker.add_argument("--project-id", default=None, help="Backend project id (default: configured project)")
    worker.add_argument("--local-workspace-id", default=None, help="Plugin local workspace id used to claim work")
    worker.add_argument("--agent-key", default="local_agent", help="Backend agent key to poll")
    worker.add_argument("--limit", type=int, default=1, help="Maximum work items to process")
    worker.add_argument("--json", action="store_true", help="Emit machine-readable worker summary")
    worker_setup = sub.add_parser("worker-setup", help="Register this checkout for local backend task work")
    worker_setup.add_argument("--workspace", default=None, help="Workspace path to register (default: current directory)")
    worker_setup.add_argument("--repository-id", default=None, help="Backend repository id when it cannot be inferred")
    worker_setup.add_argument("--json", action="store_true", help="Emit machine-readable setup result")
    tasks = sub.add_parser("tasks", help="List or process backend task work items")
    tasks_sub = tasks.add_subparsers(dest="tasks_action")
    tasks_list = tasks_sub.add_parser("list", help="List backend work items available to the local agent")
    tasks_list.add_argument("--project-id", default=None, help="Backend project id (default: configured project)")
    tasks_list.add_argument("--repository-id", default=None, help="Backend repository id filter")
    tasks_list.add_argument("--agent-key", default="local_agent", help="Backend agent key to poll")
    tasks_list.add_argument("--status", default="queued", help="Work item status filter")
    tasks_list.add_argument("--limit", type=int, default=20, help="Maximum work items to show")
    tasks_list.add_argument("--json", action="store_true", help="Emit machine-readable task list")
    tasks_work = tasks_sub.add_parser("work", help="Process one batch of backend task work items")
    tasks_work.add_argument("--once", action="store_true", help="Explicit one-shot mode; currently the default")
    tasks_work.add_argument("--loop", action="store_true", help="Poll for work until stopped or a loop limit is reached")
    tasks_work.add_argument("--interval", type=float, default=5.0, help="Seconds to wait between idle loop cycles")
    tasks_work.add_argument("--max-cycles", type=int, default=0, help="Maximum loop cycles; 0 means unlimited")
    tasks_work.add_argument("--idle-exit-after", type=int, default=0, help="Exit after this many consecutive idle cycles; 0 disables")
    tasks_work.add_argument("--max-errors", type=int, default=3, help="Stop loop after this many consecutive failed cycles; 0 disables")
    tasks_work.add_argument("--project-id", default=None, help="Backend project id (default: configured project)")
    tasks_work.add_argument("--local-workspace-id", default=None, help="Plugin local workspace id used to claim work")
    tasks_work.add_argument("--agent-key", default="local_agent", help="Backend agent key to poll")
    tasks_work.add_argument("--limit", type=int, default=1, help="Maximum work items to process")
    tasks_work.add_argument("--json", action="store_true", help="Emit machine-readable worker summary")
    tasks_status = tasks_sub.add_parser("status", help="Summarize cached backend task work state")
    tasks_status.add_argument("--project-id", default=None, help="Backend project id (default: configured project)")
    tasks_status.add_argument("--json", action="store_true", help="Emit machine-readable task status")
    tasks_explain = tasks_sub.add_parser("explain", help="Show cached details for one backend task work item")
    tasks_explain.add_argument("work_item_id", help="Backend work item id")
    tasks_explain.add_argument("--json", action="store_true", help="Emit machine-readable task detail")
    wiki = sub.add_parser("wiki", help="Review, draft, and verify backend project wiki pages")
    wiki_sub = wiki.add_subparsers(dest="wiki_action")
    wiki_list = wiki_sub.add_parser("list", help="List bounded wiki pages for the current workspace")
    wiki_list.add_argument("--status", default=None, help="Filter by current revision source status")
    wiki_list.add_argument("--limit", type=int, default=20, help="Maximum pages to return (1-50)")
    wiki_list.add_argument("--cursor", default=None, help="Opaque pagination cursor from the previous page")
    wiki_list.add_argument("--json", action="store_true", help="Emit the backend wiki response as JSON")
    wiki_show = wiki_sub.add_parser("show", help="Show one bounded current wiki revision")
    wiki_show.add_argument("wiki_page_id", help="Backend wiki page id")
    wiki_show.add_argument("--json", action="store_true", help="Emit the backend wiki response as JSON")
    wiki_draft = wiki_sub.add_parser("draft", help="Create an auditable wiki draft from bounded JSON")
    wiki_draft.add_argument("--from-file", required=True, help="Path to a bounded wiki draft JSON object")
    wiki_draft.add_argument("--json", action="store_true", help="Emit the backend wiki response as JSON")
    wiki_verify = wiki_sub.add_parser("verify", help="Verify a wiki page against bounded evidence JSON")
    wiki_verify.add_argument("wiki_page_id", help="Backend wiki page id")
    wiki_verify.add_argument("--expected-revision", required=True, help="Expected current revision id")
    wiki_verify.add_argument("--evidence-file", required=True, help="Path to a bounded evidence-ref JSON list")
    wiki_verify.add_argument("--note", default=None, help="Optional verification note")
    wiki_verify.add_argument("--json", action="store_true", help="Emit the backend wiki response as JSON")
    logbook = sub.add_parser("logbook", help="Read or append durable project logbook entries")
    logbook_sub = logbook.add_subparsers(dest="logbook_action")
    logbook_list = logbook_sub.add_parser("list", help="List project logbook entries")
    logbook_list.add_argument("--type", dest="event_type", default=None, help="Filter by event type")
    logbook_list.add_argument("--actor", default=None, help="Filter by actor identity")
    logbook_list.add_argument("--severity", choices=("info", "warning", "error"), default=None)
    logbook_list.add_argument("--cursor", default=None, help="Opaque pagination cursor")
    logbook_list.add_argument("--limit", type=int, default=20, help="Entries to show (1-50)")
    logbook_list.add_argument("--json", action="store_true", help="Emit the backend response as JSON")
    logbook_show = logbook_sub.add_parser("show", help="Show one project logbook entry")
    logbook_show.add_argument("entry_id", help="Backend logbook entry id")
    logbook_show.add_argument("--json", action="store_true", help="Emit the backend response as JSON")
    logbook_write = logbook_sub.add_parser("write", help="Persist then append one factual project logbook entry")
    logbook_write.add_argument("--type", dest="event_type", required=True, help="Event type")
    logbook_write.add_argument("--summary", required=True, help="Plain-language summary (max 240 characters)")
    logbook_write.add_argument("--idempotency-key", required=True, help="Stable deduplication key")
    logbook_write.add_argument("--narrative-file", default=None, help="Regular UTF-8 narrative file (max 8,000 code points)")
    logbook_write.add_argument("--reference", action="append", default=None, help="Project-local reference KIND:ID; repeatable")
    logbook_write.add_argument("--correlation-id", default=None, help="Optional stable operation id")
    logbook_write.add_argument("--severity", choices=("info", "warning", "error"), default="info")
    logbook_write.add_argument("--json", action="store_true", help="Emit machine-readable write state")
    jobs = sub.add_parser("jobs", help="List local backend jobs needing review")
    jobs.add_argument("--status", action="append", default=None, help="Filter by job status; repeatable")
    jobs.add_argument("--all", action="store_true", help="Show all local backend jobs")
    jobs.add_argument("--json", action="store_true", help="Emit machine-readable job JSON")
    approve_job = sub.add_parser("approve-job", help="Approve and execute a waiting backend job")
    approve_job.add_argument("job_id", help="Local/backend job id")
    approve_jobs = sub.add_parser("approve-jobs", aliases=("approve-all",), help="Approve and execute waiting backend jobs in batch")
    approve_jobs.add_argument("--capability", action="append", default=None, help="Only approve jobs with this capability; repeatable")
    approve_jobs.add_argument("--project-id", default=None, help="Backend project id to approve (default: configured project)")
    approve_jobs.add_argument("--workspace-binding-id", default=None, help="Only approve jobs for this workspace binding")
    approve_jobs.add_argument("--all-projects", action="store_true", help="Approve matching jobs across every linked backend project")
    approve_jobs.add_argument("--limit", type=int, default=0, help="Maximum jobs to approve; 0 means all matching jobs")
    approve_jobs.add_argument("--dry-run", action="store_true", help="Show matching jobs without approving them")
    approve_jobs.add_argument("--json", action="store_true", help="Emit machine-readable batch result")
    refuse_job = sub.add_parser("refuse-job", help="Refuse a waiting backend job")
    refuse_job.add_argument("job_id", help="Local/backend job id")
    refuse_job.add_argument("--reason", default="local_refused", help="Reason sent to the backend")
    proposals = sub.add_parser("proposals", help="List local memory proposals needing review")
    proposals.add_argument("--status", action="append", default=None, help="Filter by proposal status; repeatable")
    proposals.add_argument("--all", action="store_true", help="Show all local memory proposals")
    proposals.add_argument("--json", action="store_true", help="Emit machine-readable proposal JSON")
    ack_proposal = sub.add_parser("ack-proposal", help="Acknowledge a refused or conflicted memory proposal locally")
    ack_proposal.add_argument("proposal_id", help="Local memory proposal id")
    promote_diagnosis = sub.add_parser("promote-diagnosis", help="Promote a verified diagnosis report to resolved bug memory")
    promote_diagnosis.add_argument("diagnosis_report_id", help="Backend diagnosis report id")
    promote_diagnosis.add_argument(
        "--verification-status",
        required=True,
        choices=("user_confirmed", "test_passed", "manual_review"),
        help="How the resolved bug was verified",
    )
    promote_diagnosis.add_argument("--fix-commit", default=None, help="Optional fix commit")
    promote_diagnosis.add_argument("--fix-pr-url", default=None, help="Optional PR or review URL")
    promote_diagnosis.add_argument("--affected-symbol", action="append", default=None, help="Affected symbol; repeatable")
    promote_diagnosis.add_argument("--regression-test", action="append", default=None, help="Regression test; repeatable")
    promote_diagnosis.add_argument("--notes", default=None, help="Optional bounded promotion notes")
    promote_diagnosis.add_argument("--json", action="store_true", help="Emit machine-readable promotion result")
    causal_pack = sub.add_parser("causal-pack", help="Create, inspect, or replay Hades causal evidence packs")
    causal_pack_sub = causal_pack.add_subparsers(dest="causal_pack_action")
    causal_pack_create = causal_pack_sub.add_parser("create", help="Create a causal evidence pack from JSON")
    causal_pack_create.add_argument("--from-file", default=None, help="Path to a causal pack payload JSON")
    causal_pack_create.add_argument("--from-diagnosis", default=None, help="Path to a diagnosis-derived causal pack JSON")
    causal_pack_create.add_argument("--json", action="store_true", help="Emit machine-readable causal pack result")
    causal_pack_list = causal_pack_sub.add_parser("list", help="Search causal evidence packs")
    causal_pack_list.add_argument("--query", default=None, help="Search query")
    causal_pack_list.add_argument("--bug-report-id", default=None, help="Filter by bug report id")
    causal_pack_list.add_argument("--root-cause-id", default=None, help="Filter by root cause id")
    causal_pack_list.add_argument("--limit", type=int, default=10, help="Maximum causal packs to return")
    causal_pack_list.add_argument("--json", action="store_true", help="Emit machine-readable causal pack results")
    causal_pack_show = causal_pack_sub.add_parser("show", help="Show one causal evidence pack")
    causal_pack_show.add_argument("causal_pack_id", help="Backend causal pack id")
    causal_pack_show.add_argument("--json", action="store_true", help="Emit machine-readable causal pack result")
    causal_pack_replay = causal_pack_sub.add_parser("replay", help="Replay-check one causal evidence pack")
    causal_pack_replay.add_argument("causal_pack_id", help="Backend causal pack id")
    causal_pack_replay.add_argument("--json", action="store_true", help="Emit machine-readable replay result")
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
    ingest_http = sub.add_parser("ingest-http", help="Upload HTTP request/response context as Hades bug evidence")
    ingest_http.add_argument("--url", required=True, help="Affected request URL")
    ingest_http.add_argument("--method", default="GET", help="HTTP method")
    ingest_http.add_argument("--status", type=int, default=None, help="Optional response status code")
    ingest_http.add_argument("--request-file", default=None, help="Optional raw request excerpt file")
    ingest_http.add_argument("--response-file", default=None, help="Optional raw response excerpt file")
    ingest_http.add_argument("--bug-report-id", default=None, help="Optional Hades bug report id")
    ingest_http.add_argument("--environment", default=None, help="Affected environment label")
    ingest_http.add_argument("--source", default=None, help="Evidence source label")
    ingest_http.add_argument("--json", action="store_true", help="Emit machine-readable ingestion result")
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
    bug_intake.add_argument("--request-url", default=None, help="Affected request URL")
    bug_intake.add_argument("--request-method", default="GET", help="Affected request method")
    bug_intake.add_argument("--response-status", type=int, default=None, help="Observed HTTP response status")
    bug_intake.add_argument("--request-file", default=None, help="Optional raw request excerpt file")
    bug_intake.add_argument("--response-file", default=None, help="Optional raw response excerpt file")
    bug_intake.add_argument("--json", action="store_true", help="Emit machine-readable intake result")
    backfill_note = sub.add_parser("backfill-note", help="Preview note-quality backfill for a raw chunk or note file")
    backfill_note.add_argument("file", help="Path to a raw chunk or note file")
    backfill_note.add_argument(
        "--create-proposals",
        action="store_true",
        help="Create local pending memory proposals for extracted candidate facts",
    )
    backfill_note.add_argument("--json", action="store_true", help="Emit machine-readable backfill preview")
    benchmark = sub.add_parser("benchmark", help="Run local Hades backend artifact benchmarks")
    benchmark.add_argument("--medium-symbols", type=int, default=750, help="Synthetic medium graph symbol count")
    benchmark.add_argument(
        "--large-symbols",
        type=int,
        default=5_501,
        help="Synthetic large graph symbol count (default: 5501)",
    )
    benchmark.add_argument("--workspace", help="Also benchmark real read-only artifacts from this workspace")
    benchmark.add_argument("--json", action="store_true", help="Emit machine-readable benchmark JSON")
    sub.add_parser("sync", help="Run a one-shot backend sync")
    parser.set_defaults(func=cmd_backend)


def _detect_default_capabilities() -> list[str]:
    return [
        "read_files",
        "read_source_slice",
        "project_inspection",
        "sync_git_tree",
        "populate_backend_ast",
        "populate_project_wiki",
        "verify_project_wiki",
        "write_project_logbook",
    ]


def _project_token_from_args(args: argparse.Namespace) -> str:
    token = str(getattr(args, "project_token", None) or "").strip()
    if bool(getattr(args, "project_token_stdin", False)):
        token = sys.stdin.readline().strip()
    if not token:
        raise ValueError("project bootstrap token is empty")
    return token


def _cmd_setup(args: argparse.Namespace) -> int:
    label = args.label or default_agent_label()
    agent_id = default_agent_id(args.project_id, label)
    try:
        project_token = _project_token_from_args(args)
    except ValueError as exc:
        print(f"backend setup: {exc}", file=sys.stderr)
        return 2
    bootstrap = HadesBackendClient(args.url, project_token)
    bootstrap.verify_token(project_id=args.project_id)
    registered = bootstrap.register_agent(
        project_id=args.project_id,
        agent_id=agent_id,
        label=label,
        platform=platform.system().lower(),
        version=_version(),
        capabilities=_detect_default_capabilities(),
        plugin_device=_plugin_device_payload(agent_id, label),
    )
    derived = str(registered.get("agent_token") or "").strip()
    if not derived:
        print("backend: registration response did not include agent_token", file=sys.stderr)
        return 1
    final_agent_id = str(registered.get("agent_id") or agent_id)
    env_key = token_env_key(args.url, args.project_id, final_agent_id)
    save_env_value(env_key, derived)

    plugin_credentials = registered.get("plugin_credentials")
    plugin_token = ""
    plugin_device_id = ""
    plugin_device_secret = ""
    if isinstance(plugin_credentials, dict):
        plugin_token = str(plugin_credentials.get("token") or "").strip()
        plugin_device_id = str(plugin_credentials.get("device_id") or "").strip()
        plugin_device_secret = str(plugin_credentials.get("device_secret") or "").strip()
        if not all((plugin_token, plugin_device_id, plugin_device_secret)):
            print("backend: registration response included incomplete plugin_credentials", file=sys.stderr)
            return 1

    config = load_config()
    backend = config.setdefault("backend", {})
    backend["enabled"] = True
    backend["base_url"] = args.url.rstrip("/")
    backend["default_project_id"] = args.project_id
    backend["agent_id"] = final_agent_id
    if plugin_token:
        plugin_env_key = plugin_token_env_key(args.url, args.project_id, final_agent_id)
        plugin_secret_env_key = plugin_device_secret_env_key(args.url, args.project_id, final_agent_id)
        save_env_value(plugin_env_key, plugin_token)
        save_env_value(plugin_secret_env_key, plugin_device_secret)
        backend["plugin_token_env_key"] = plugin_env_key
        backend["plugin_device_secret_env_key"] = plugin_secret_env_key
        backend["plugin_device_id"] = plugin_device_id
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
    if not plugin_token:
        print("  Plugin:  unavailable (upgrade backend to provision task credentials)")
    return 0


def _plugin_device_payload(agent_id: str, label: str) -> dict[str, str]:
    profile = os.environ.get("HERMES_PROFILE", "default")
    material = f"{agent_id}|{socket.gethostname()}|{profile}|{platform.system()}|{platform.machine()}"
    return {
        "fingerprint_hash": "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest(),
        "name": label,
        "platform_os": platform.system().lower() or "unknown",
        "platform_arch": platform.machine() or "unknown",
        "plugin_version": _version(),
    }


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
    task_work = payload.get("task_work") if isinstance(payload.get("task_work"), dict) else {}
    if task_work.get("total"):
        print(
            "  Task work: "
            f"{task_work.get('total', 0)} cached "
            f"(queued {task_work.get('queued', 0)}, failed {task_work.get('failed', 0)}, "
            f"memory {task_work.get('shared_memory_context', 0)}/{task_work.get('shared_memory_required', 0)})"
        )
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
    fixtures = None
    no_codebase_report = None
    if getattr(args, "no_codebase_eval", None):
        from hermes_cli.hades_no_codebase_eval import evaluate_no_codebase_diagnoses, load_no_codebase_eval_fixture

        fixtures, runs = load_no_codebase_eval_fixture(args.no_codebase_eval)
        no_codebase_report = evaluate_no_codebase_diagnoses(fixtures, runs).to_dict()
    suite_report = None
    if getattr(args, "suite", None):
        from hermes_cli.hades_quality_suite import load_quality_suite, run_quality_suite

        suite_report = run_quality_suite(load_quality_suite(args.suite))
    with db.connect_closing() as conn:
        note_backfill_report = build_note_backfill_quality_report(db.list_memory_proposals(conn))
        plugin_work_items = db.list_plugin_work_items(conn)
        agent_work_report = build_agent_work_quality_report(plugin_work_items)
    agent_work_no_codebase_report = None
    if fixtures is not None:
        from hermes_cli.hades_agent_work_eval import build_agent_work_no_codebase_report

        agent_work_no_codebase_report = build_agent_work_no_codebase_report(fixtures, plugin_work_items)
    report = build_hades_quality_report(
        no_codebase_report=no_codebase_report,
        agent_work_no_codebase_report=agent_work_no_codebase_report,
        suite_report=suite_report,
        support_report=None if getattr(args, "skip_local_status", False) else support_report_payload(),
        note_backfill_report=note_backfill_report,
        agent_work_report=agent_work_report,
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


QUALITY_REPORT_CRON_SCRIPT_NAME = "hades_backend_quality_report.py"
QUALITY_REPORT_CRON_PROMPT = "Record the Hades backend quality report."


def _quality_report_existing_path(value: str | None, *, label: str) -> str | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    path = Path(clean).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return str(path.resolve())


def _write_quality_report_cron_script(*, no_codebase_eval: str | None, suite: str | None) -> Path:
    from hermes_constants import get_hermes_home

    scripts_dir = get_hermes_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_path = scripts_dir / QUALITY_REPORT_CRON_SCRIPT_NAME
    eval_path = _quality_report_existing_path(no_codebase_eval, label="no-codebase eval fixture")
    suite_path = _quality_report_existing_path(suite, label="Hades quality suite")
    script = f"""from types import SimpleNamespace

from hermes_cli.hades_backend_cmd import hades_backend_command


args = SimpleNamespace(
    backend_action="quality-report",
    no_codebase_eval={json.dumps(eval_path)},
    suite={json.dumps(suite_path)},
    skip_local_status=False,
    record=True,
    json=True,
)

raise SystemExit(hades_backend_command(args))
"""
    script_path.write_text(script, encoding="utf-8")
    try:
        script_path.chmod(0o600)
    except OSError:
        pass
    return script_path


def _cmd_schedule_quality(args: argparse.Namespace) -> int:
    try:
        script_path = _write_quality_report_cron_script(
            no_codebase_eval=getattr(args, "no_codebase_eval", None),
            suite=getattr(args, "suite", None),
        )
        from cron.jobs import create_job, list_jobs, update_job

        name = str(getattr(args, "name", None) or "Hades backend quality report").strip()
        schedule = str(getattr(args, "schedule", None) or "0 8 * * *").strip()
        deliver = str(getattr(args, "deliver", None) or "local").strip() or "local"
        existing = next((job for job in list_jobs(include_disabled=True) if job.get("name") == name), None)
        if existing:
            job = update_job(
                existing["id"],
                {
                    "name": name,
                    "prompt": QUALITY_REPORT_CRON_PROMPT,
                    "schedule": schedule,
                    "deliver": deliver,
                    "script": script_path.name,
                    "no_agent": True,
                    "enabled": True,
                    "state": "scheduled",
                    "paused_at": None,
                    "paused_reason": None,
                    "attach_to_session": False,
                },
            )
            action = "updated"
        else:
            job = create_job(
                prompt=QUALITY_REPORT_CRON_PROMPT,
                schedule=schedule,
                name=name,
                deliver=deliver,
                script=script_path.name,
                no_agent=True,
                attach_to_session=False,
            )
            action = "created"
        if job is None:
            raise RuntimeError(f"failed to create or update cron job {name!r}")
    except Exception as exc:
        print(f"Hades backend schedule-quality: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    result = {
        "status": action,
        "job_id": job["id"],
        "name": job["name"],
        "schedule": job.get("schedule_display"),
        "next_run_at": job.get("next_run_at"),
        "script": str(script_path),
        "deliver": job.get("deliver"),
        "no_agent": bool(job.get("no_agent")),
    }
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Hades quality cron {action}: {job['name']} ({job['id']})")
        print(f"  Schedule: {job.get('schedule_display')}")
        print(f"  Next run: {job.get('next_run_at') or 'not scheduled'}")
        print(f"  Script:   {script_path}")
    return 0


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
        from hermes_cli.hades_backend_actions import privacy_export

        _agent, binding = _current_workspace_binding()
        result = privacy_export(include_content=bool(getattr(args, "include_content", False)))
        response = result.payload
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
        from hermes_cli.hades_backend_actions import privacy_delete

        _agent, binding = _current_workspace_binding()
        result = privacy_delete(confirm=not dry_run)
        response = result.payload
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
        from hermes_cli.hades_backend_actions import retention_cleanup

        _agent, binding = _current_workspace_binding()
        result = retention_cleanup(
            retention_days=int(getattr(args, "retention_days")),
            confirm=not dry_run,
        )
        response = result.payload
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
    try:
        project_token = _project_token_from_args(args)
    except ValueError as exc:
        print(f"backend bootstrap: {exc}", file=sys.stderr)
        return 2
    setup_rc = _cmd_setup(
        SimpleNamespace(
            url=args.url,
            project_token=project_token,
            project_token_stdin=False,
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

    print("Hades backend bootstrap complete")
    print(f"  Local project: {project.slug} ({project.name})")
    print(f"  Workspace:     {Path(args.workspace or '.').expanduser().resolve()}")
    print(f"  Binding:       {binding_id}")
    print("  Next:          run `hades backend sync` when ready to upload pending memory")
    return 0


def _current_workspace_scoped_agent_binding() -> tuple[db.BackendAgent, db.WorkspaceBinding]:
    _default_agent, binding = _current_workspace_binding()
    with db.connect_closing() as conn:
        scoped_agent = db.get_agent(conn, binding.agent_id)
    if scoped_agent is None:
        raise RuntimeError(f"Hades backend agent {binding.agent_id} is not known locally")
    return scoped_agent, binding


def _quality_report_for_record() -> dict[str, Any]:
    with db.connect_closing() as conn:
        note_backfill_report = build_note_backfill_quality_report(db.list_memory_proposals(conn))
        plugin_work_items = db.list_plugin_work_items(conn)
        agent_work_report = build_agent_work_quality_report(plugin_work_items)
    return build_hades_quality_report(
        support_report=support_report_payload(),
        note_backfill_report=note_backfill_report,
        agent_work_report=agent_work_report,
    )


def _cmd_bootstrap_awareness(args: argparse.Namespace) -> int:
    from hermes_cli import hades_backend_runtime as runtime
    from hermes_cli.hades_backend_jobs import execute_job
    from hermes_cli.hades_backend_sync import _sync_baseline_artifacts, run_backend_sync

    json_mode = bool(getattr(args, "json", False))
    approve_slices = bool(getattr(args, "yes", False))
    skip_wiki = bool(getattr(args, "skip_wiki", False))
    try:
        agent, binding = _current_workspace_scoped_agent_binding()
    except Exception as exc:
        print(f"Hades backend bootstrap-awareness: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    effective_capabilities = agent.capabilities if isinstance(agent.capabilities, dict) else {}
    wiki_blocked = not skip_wiki and effective_capabilities.get("populate_project_wiki") is not True
    result: dict[str, Any] = {
        "project_id": binding.project_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "workspace": binding.display_path,
        "baseline": {},
        "syncs": [],
        "source_slice_approval": None,
        "wiki_request": None,
        "quality_report": None,
    }
    if wiki_blocked:
        result["status"] = "partial"
        result["wiki_request"] = {
            "status": "missing_agent_capability",
            "code": "missing_agent_capability",
            "capability": "populate_project_wiki",
            "summary": "The registered Hades agent cannot publish project wiki results.",
            "next_step": (
                "Obtain a new project-scoped bootstrap token for this project and re-register this "
                "workspace with `hades backend bootstrap`. Do not paste the token into chat and do not reuse "
                "the existing agent token as `--project-token`. If the capability is still absent, the backend "
                "capability policy must be updated."
            ),
        }

    client = None
    try:
        client = runtime.client_for_agent(agent, timeout=60.0)
        uploaded, failed, skipped, candidates = _sync_baseline_artifacts(
            client,
            agent,
            binding,
            execute_job=execute_job,
        )
        result["baseline"] = {
            "artifacts_uploaded": uploaded,
            "artifacts_failed": failed,
            "artifacts_skipped": skipped,
            "source_slice_candidates": candidates,
        }
        if failed:
            raise RuntimeError(f"baseline artifact upload failed for {failed} artifact(s)")

        sync_scope = {"workspace_binding_ids": [binding.backend_workspace_binding_id]}

        first_sync = run_backend_sync(quiet=True, **sync_scope)
        result["syncs"].append({"phase": "after_baseline", **first_sync.summary, "exit_code": first_sync.exit_code})

        if approve_slices:
            approval = approve_backend_jobs(
                capabilities=["read_source_slice"],
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
            )
            result["source_slice_approval"] = {
                "status": approval.status,
                "summary": approval.summary,
                **approval.payload,
            }
            slice_sync = run_backend_sync(quiet=True, **sync_scope)
            result["syncs"].append({"phase": "after_source_slices", **slice_sync.summary, "exit_code": slice_sync.exit_code})
        else:
            result["source_slice_approval"] = {
                "status": "skipped",
                "summary": "read_source_slice jobs require --yes for automatic approval",
            }

        if not skip_wiki and not wiki_blocked:
            try:
                wiki_response = client.bootstrap_project_awareness(
                    project_id=binding.project_id,
                    agent_id=agent.agent_id,
                    workspace_binding_id=binding.backend_workspace_binding_id,
                    reason="CLI bootstrap-awareness",
                )
                result["wiki_request"] = wiki_response.get("job") or wiki_response.get("refresh_request") or wiki_response
            except HadesBackendError as exc:
                if exc.status_code == 404:
                    result["wiki_request"] = {
                        "status": "unsupported_backend",
                        "summary": "Backend does not expose project-awareness/bootstrap; existing queued wiki jobs can still be processed.",
                    }
                else:
                    raise
            wiki_sync = run_backend_sync(quiet=True, **sync_scope)
            result["syncs"].append({"phase": "after_wiki", **wiki_sync.summary, "exit_code": wiki_sync.exit_code})

        final_sync = run_backend_sync(quiet=True, **sync_scope)
        result["syncs"].append({"phase": "final", **final_sync.summary, "exit_code": final_sync.exit_code})
        try:
            result["awareness"] = client.project_awareness_status(
                project_id=binding.project_id,
                workspace_binding_id=binding.backend_workspace_binding_id,
            )
        except Exception as exc:
            result["awareness"] = {"status": "unavailable", "error": redact_secret(str(exc))}

        if bool(getattr(args, "record_quality_report", False)):
            report = _quality_report_for_record()
            with db.connect_closing() as conn:
                _record_quality_report_snapshot(conn, report)
            result["quality_report"] = {
                "status": report.get("status"),
                "summary": report.get("summary"),
            }
    except Exception as exc:
        result["error"] = redact_secret(str(exc))
        if json_mode:
            print(json.dumps(result, sort_keys=True))
        else:
            print(f"Hades backend bootstrap-awareness: {result['error']}", file=sys.stderr)
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    if json_mode:
        print(json.dumps(result, sort_keys=True))
        return 1 if wiki_blocked else 0

    baseline = result["baseline"]
    if wiki_blocked:
        print("Hades backend bootstrap-awareness blocked after partial local preparation")
    else:
        print("Hades backend bootstrap-awareness complete")
    print(f"  Project:     {binding.project_id}")
    print(f"  Workspace:   {binding.display_path}")
    print(
        "  Baseline:    "
        f"{baseline.get('artifacts_uploaded', 0)} uploaded, "
        f"{baseline.get('artifacts_skipped', 0)} skipped, "
        f"{baseline.get('source_slice_candidates', 0)} source-slice candidate(s)"
    )
    approval = result.get("source_slice_approval") if isinstance(result.get("source_slice_approval"), dict) else {}
    print(f"  Source slice: {approval.get('summary', 'not run')}")
    wiki_request = result.get("wiki_request") if isinstance(result.get("wiki_request"), dict) else {}
    if skip_wiki:
        print("  Wiki:         skipped")
    elif wiki_blocked:
        print("  Wiki:         blocked (missing_agent_capability)")
        print(f"  Next:         {wiki_request.get('next_step')}")
    else:
        print(f"  Wiki:         {wiki_request.get('status') or wiki_request.get('id') or 'requested/processed'}")
    awareness = result.get("awareness") if isinstance(result.get("awareness"), dict) else {}
    print(f"  Awareness:    {awareness.get('overall_status') or awareness.get('status') or 'unknown'}")
    if not approve_slices:
        print("  Next:         rerun with `--yes` to approve source-slice jobs automatically")
    return 1 if wiki_blocked else 0


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
    loop = bool(getattr(args, "loop", False)) and not bool(getattr(args, "once", False))
    worker_kwargs = {
        "project_id": getattr(args, "project_id", None),
        "local_workspace_id": getattr(args, "local_workspace_id", None),
        "agent_key": getattr(args, "agent_key", "local_agent") or "local_agent",
        "limit": max(1, int(getattr(args, "limit", 1) or 1)),
        "quiet": json_mode,
    }
    if loop:
        return _run_worker_loop(args, run_plugin_worker_once, worker_kwargs, json_mode=json_mode)

    result = run_plugin_worker_once(**worker_kwargs)
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


def _run_worker_loop(
    args: argparse.Namespace,
    run_plugin_worker_once: Callable[..., Any],
    worker_kwargs: dict[str, Any],
    *,
    json_mode: bool,
) -> int:
    max_cycles = max(0, int(getattr(args, "max_cycles", 0) or 0))
    idle_exit_after = max(0, int(getattr(args, "idle_exit_after", 0) or 0))
    max_errors = max(0, int(getattr(args, "max_errors", 3) or 0))
    interval = max(0.0, float(getattr(args, "interval", 5.0) or 0.0))
    aggregate = {
        "mode": "loop",
        "cycles": 0,
        "listed": 0,
        "claimed": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "idle_cycles": 0,
        "error_cycles": 0,
        "last": {},
    }
    exit_code = 0

    while max_cycles == 0 or aggregate["cycles"] < max_cycles:
        result = run_plugin_worker_once(**worker_kwargs)
        summary = result.summary
        aggregate["cycles"] += 1
        aggregate["last"] = summary
        if "error" in summary:
            if json_mode:
                print(json.dumps({"error": summary["error"], **aggregate}, sort_keys=True))
            else:
                error = summary["error"] if isinstance(summary.get("error"), dict) else {}
                print(f"Hades backend worker: {error.get('message', 'failed')}", file=sys.stderr)
                if error.get("next_step"):
                    print(f"  Next step: {error['next_step']}", file=sys.stderr)
            return result.exit_code or 1

        for key in ("listed", "claimed", "completed", "failed", "skipped"):
            aggregate[key] += int(summary.get(key) or 0)
        if int(summary.get("claimed") or 0) == 0 and int(summary.get("completed") or 0) == 0:
            aggregate["idle_cycles"] += 1
        else:
            aggregate["idle_cycles"] = 0
        cycle_failed = bool(result.exit_code) or int(summary.get("failed") or 0) > 0
        if cycle_failed:
            exit_code = result.exit_code or 1
            aggregate["error_cycles"] += 1
        else:
            aggregate["error_cycles"] = 0
        if idle_exit_after and aggregate["idle_cycles"] >= idle_exit_after:
            break
        if max_errors and aggregate["error_cycles"] >= max_errors:
            break
        if max_cycles and aggregate["cycles"] >= max_cycles:
            break
        if (aggregate["idle_cycles"] > 0 or aggregate["error_cycles"] > 0) and interval > 0:
            time.sleep(interval)

    if json_mode:
        print(json.dumps(aggregate, sort_keys=True))
    else:
        print(
            "Hades backend worker loop: "
            f"{aggregate['cycles']} cycle(s), listed {aggregate['listed']} item(s), "
            f"claimed {aggregate['claimed']}, completed {aggregate['completed']}, "
            f"failed {aggregate['failed']}, skipped {aggregate['skipped']}, "
            f"idle {aggregate['idle_cycles']}, error cycles {aggregate['error_cycles']}"
        )
    return exit_code


def _cmd_worker_setup(args: argparse.Namespace) -> int:
    from hermes_cli.hades_plugin_tasks import setup_plugin_worker

    result = setup_plugin_worker(
        workspace=getattr(args, "workspace", None),
        repository_id=getattr(args, "repository_id", None),
    )
    payload = result.payload
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
        return result.exit_code
    if "error" in payload:
        error = payload["error"] if isinstance(payload.get("error"), dict) else {}
        print(f"Hades backend worker setup: {error.get('message', 'failed')}", file=sys.stderr)
        if error.get("next_step"):
            print(f"  Next step: {error['next_step']}", file=sys.stderr)
        return result.exit_code
    print("Hades backend worker setup complete")
    print(f"  Project:          {payload.get('project_id')}")
    print(f"  Repository:       {payload.get('repository_name') or payload.get('repository_id')}")
    print(f"  Device:           {payload.get('device_id')}")
    print(f"  Local workspace:  {payload.get('local_workspace_id')}")
    print(f"  Checkout:         {payload.get('display_path')}")
    return result.exit_code


def _cmd_tasks(args: argparse.Namespace) -> int:
    action = getattr(args, "tasks_action", None)
    if action == "list":
        from hermes_cli.hades_plugin_tasks import list_plugin_tasks

        payload = list_plugin_tasks(
            project_id=getattr(args, "project_id", None),
            repository_id=getattr(args, "repository_id", None),
            agent_key=getattr(args, "agent_key", "local_agent") or "local_agent",
            status=getattr(args, "status", "queued") or "queued",
            limit=max(1, int(getattr(args, "limit", 20) or 20)),
        )
        if getattr(args, "json", False):
            print(json.dumps(payload, sort_keys=True))
            return 1 if "error" in payload else 0
        if "error" in payload:
            error = payload["error"] if isinstance(payload.get("error"), dict) else {}
            print(f"Hades backend tasks: {error.get('message', 'failed')}", file=sys.stderr)
            if error.get("next_step"):
                print(f"  Next step: {error['next_step']}", file=sys.stderr)
            return 1
        _print_task_list(payload)
        return 0
    if action == "work":
        return _cmd_worker(args)
    if action == "status":
        from hermes_cli.hades_plugin_tasks import plugin_tasks_status

        payload = plugin_tasks_status(project_id=getattr(args, "project_id", None))
        if getattr(args, "json", False):
            print(json.dumps(payload, sort_keys=True))
            return 1 if "error" in payload else 0
        if "error" in payload:
            error = payload["error"] if isinstance(payload.get("error"), dict) else {}
            print(f"Hades backend tasks status: {error.get('message', 'failed')}", file=sys.stderr)
            if error.get("next_step"):
                print(f"  Next step: {error['next_step']}", file=sys.stderr)
            return 1
        _print_task_status(payload)
        return 0
    if action == "explain":
        from hermes_cli.hades_plugin_tasks import explain_plugin_task

        payload = explain_plugin_task(str(getattr(args, "work_item_id", "") or ""))
        if getattr(args, "json", False):
            print(json.dumps(payload, sort_keys=True))
            return 1 if "error" in payload else 0
        if "error" in payload:
            error = payload["error"] if isinstance(payload.get("error"), dict) else {}
            print(f"Hades backend tasks explain: {error.get('message', 'failed')}", file=sys.stderr)
            if error.get("next_step"):
                print(f"  Next step: {error['next_step']}", file=sys.stderr)
            return 1
        _print_task_detail(payload)
        return 0
    print("usage: hades backend tasks <list|work|status|explain>", file=sys.stderr)
    return 1


def _print_task_list(payload: dict[str, Any]) -> None:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    print("Hades backend tasks")
    print(f"  Project: {payload.get('project_id')}")
    print(f"  Agent:   {payload.get('agent_key')}")
    if not items:
        print("  No matching backend task work items.")
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        work_item_id = item.get("work_item_id") or "unknown"
        status = item.get("status") or "unknown"
        priority = item.get("priority") or "normal"
        title = item.get("title") or item.get("prompt_preview") or "(untitled)"
        task_id = item.get("task_id")
        suffix = f" task={task_id}" if task_id else ""
        print(f"  {work_item_id}: {status} {priority} - {title}{suffix}")


def _print_task_status(payload: dict[str, Any]) -> None:
    print("Hades backend task status")
    print(f"  Project: {payload.get('project_id')}")
    print(f"  Cached:  {payload.get('total', 0)} work item(s)")
    by_status = payload.get("by_status") if isinstance(payload.get("by_status"), dict) else {}
    if by_status:
        print("  By status:")
        for status, count in sorted(by_status.items()):
            print(f"    {status}: {count}")
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    missing = int(quality.get("missing_shared_memory_context_count") or 0)
    print(f"  Shared memory coverage: {quality.get('shared_memory_context_coverage', 1.0)}")
    if missing:
        print(f"  Action: repair shared memory context for {missing} work item(s)")
    next_step = payload.get("next_step")
    if next_step:
        print(f"  Next step: {next_step}")


def _print_task_detail(payload: dict[str, Any]) -> None:
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    print("Hades backend task detail")
    print(f"  Work item:  {item.get('work_item_id')}")
    print(f"  Project:    {item.get('project_id')}")
    print(f"  Repository: {item.get('repository_id') or '-'}")
    print(f"  Agent:      {item.get('agent_key')}")
    print(f"  Status:     {item.get('status')}")
    print(f"  Kind:       {item.get('kind')}")
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    print(f"  Shared memory context: {quality.get('shared_memory_context_count', 0)}/{quality.get('shared_memory_required_count', 0)}")
    payload_data = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    title = payload_data.get("title") or payload_data.get("normalized_problem")
    if title:
        print(f"  Title:      {str(title).splitlines()[0][:160]}")


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


def _cmd_approve_jobs(args: argparse.Namespace) -> int:
    try:
        result = approve_backend_jobs(
            capabilities=getattr(args, "capability", None),
            project_id=getattr(args, "project_id", None),
            workspace_binding_id=getattr(args, "workspace_binding_id", None),
            all_projects=bool(getattr(args, "all_projects", False)),
            limit=int(getattr(args, "limit", 0) or 0),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    except Exception as exc:
        print(f"Hades backend approve-jobs: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    payload = result.payload
    if getattr(args, "json", False):
        print(json.dumps({"status": result.status, "summary": result.summary, **payload}, sort_keys=True))
        return 0 if result.ok else 1

    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    if bool(payload.get("dry_run")):
        print(f"Hades backend approve-jobs dry-run: {len(jobs)} job(s) match")
        for item in jobs:
            print(f"  {item['job_id']}: {item['status']} {item['capability']} ({item['workspace_binding_id']})")
        return 0

    print(f"Hades backend approve-jobs: {result.summary}")
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    for item in results:
        print(f"  {item['job_id']}: {item['status']}")
        if not item.get("ok"):
            print(f"    {item.get('summary')}", file=sys.stderr)
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


def _cmd_promote_diagnosis(args: argparse.Namespace) -> int:
    diagnosis_report_id = str(getattr(args, "diagnosis_report_id", "") or "").strip()
    try:
        result = promote_diagnosis_report(
            diagnosis_report_id,
            verification_status=str(getattr(args, "verification_status", "") or ""),
            fix_commit=getattr(args, "fix_commit", None),
            fix_pr_url=getattr(args, "fix_pr_url", None),
            affected_symbols=getattr(args, "affected_symbol", None) or [],
            regression_tests=getattr(args, "regression_test", None) or [],
            notes=getattr(args, "notes", None),
        )
    except Exception as exc:
        print(f"Hades backend promote-diagnosis: {redact_secret(str(exc))}", file=sys.stderr)
        return 1
    payload = {
        "status": result.status,
        "summary": result.summary,
        **result.payload,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
    else:
        memory_id = payload.get("resolved_bug_memory_id") or "created"
        print(f"Hades diagnosis promoted: {diagnosis_report_id} -> {memory_id}")
    return 0


def _cmd_causal_pack(args: argparse.Namespace) -> int:
    action = str(getattr(args, "causal_pack_action", "") or "").strip()
    try:
        _agent, binding = _current_workspace_binding()
        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            if action == "create":
                payload = _causal_pack_payload_from_args(args, binding)
                response = client.create_causal_pack(**payload)
                pack = response.get("causal_pack") if isinstance(response.get("causal_pack"), dict) else response
                result = {
                    "schema": "hades.causal_pack_cli_result.v1",
                    "status": "ok",
                    "action": "create",
                    "created": True,
                    "causal_pack": pack,
                }
            elif action == "list":
                result = client.causal_packs(
                    project_id=binding.project_id,
                    workspace_binding_id=binding.backend_workspace_binding_id,
                    query=getattr(args, "query", None) or None,
                    bug_report_id=getattr(args, "bug_report_id", None) or None,
                    root_cause_id=getattr(args, "root_cause_id", None) or None,
                    limit=getattr(args, "limit", 10),
                )
            elif action == "show":
                result = client.causal_pack(
                    str(getattr(args, "causal_pack_id", "") or ""),
                    project_id=binding.project_id,
                    workspace_binding_id=binding.backend_workspace_binding_id,
                )
            elif action == "replay":
                result = client.replay_causal_pack(
                    str(getattr(args, "causal_pack_id", "") or ""),
                    project_id=binding.project_id,
                    workspace_binding_id=binding.backend_workspace_binding_id,
                )
            else:
                raise ValueError("causal-pack action is required: create, list, show, or replay")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend causal-pack: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
        return 0

    if action == "create":
        pack = result.get("causal_pack") if isinstance(result, dict) else {}
        pack_id = pack.get("id") if isinstance(pack, dict) else None
        status = pack.get("status") if isinstance(pack, dict) else None
        print(f"Hades causal pack stored: {pack_id or 'created'} ({status or 'unknown'})")
    elif action == "replay":
        replay = result.get("replay") if isinstance(result, dict) else {}
        replayable = replay.get("replayable") if isinstance(replay, dict) else None
        print(f"Hades causal pack replay: {'replayable' if replayable else 'not replayable'}")
    elif action == "show":
        pack = result.get("causal_pack") if isinstance(result, dict) else {}
        print(f"Hades causal pack: {pack.get('id') if isinstance(pack, dict) else 'unknown'}")
    else:
        items = result.get("items") if isinstance(result, dict) else []
        print(f"Hades causal packs: {len(items) if isinstance(items, list) else 0}")
    return 0


def _causal_pack_payload_from_args(args: argparse.Namespace, binding: db.WorkspaceBinding) -> dict[str, Any]:
    source_path = getattr(args, "from_file", None) or getattr(args, "from_diagnosis", None)
    if not source_path:
        raise ValueError("causal-pack create requires --from-file or --from-diagnosis")
    raw = json.loads(Path(source_path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("causal pack input must be a JSON object")

    source = raw.get("causal_pack") if isinstance(raw.get("causal_pack"), dict) else raw
    if not isinstance(source, dict):
        raise ValueError("causal pack payload must be a JSON object")

    if isinstance(source.get("diagnosis"), dict):
        from hermes_cli.hades_causal_pack import build_causal_pack

        source = build_causal_pack(source)

    diagnosis = source.get("diagnosis") if isinstance(source.get("diagnosis"), dict) else {}
    return {
        "project_id": str(source.get("project_id") or binding.project_id),
        "workspace_binding_id": str(source.get("workspace_binding_id") or source.get("binding_id") or binding.backend_workspace_binding_id),
        "bug_report_id": source.get("bug_report_id") or None,
        "bug_id": source.get("bug_id") or None,
        "root_cause_id": str(source.get("root_cause_id") or diagnosis.get("root_cause_id") or ""),
        "bug_class": str(source.get("bug_class") or diagnosis.get("bug_class") or ""),
        "failure_classification": str(
            source.get("failure_classification") or diagnosis.get("failure_classification") or ""
        ),
        "affected_refs": _string_list(source.get("affected_refs") or diagnosis.get("affected_refs")),
        "freshness": source.get("freshness") if isinstance(source.get("freshness"), dict) else {},
        "awareness": source.get("awareness") if isinstance(source.get("awareness"), dict) else {},
        "evidence_refs": _list_value(source.get("evidence_refs")),
        "graph_refs": _list_value(source.get("graph_refs")),
        "source_slice_refs": _list_value(source.get("source_slice_refs")),
    }


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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


def _frame_refs(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for frame in frames:
        path = str(frame.get("path") or frame.get("file") or "").strip()
        line = frame.get("line")
        if not path or not isinstance(line, int):
            continue
        key = (path, line)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "type": "source_frame",
                "path": path,
                "line": line,
                "graph_query": path,
                "source_slice_hint": {"path": path, "line": line},
            }
        )
    return refs[:20]


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


def _redacted_optional_file(path: str | None) -> tuple[str | None, bool, int]:
    if not path:
        return None, False, 0
    text, truncated = _read_evidence_file(path)
    redacted = redact_secret(text)
    return _compact_lines(redacted), truncated, 1 if redacted != text else 0


def _http_request_payload(
    *,
    method: str,
    url: str,
    request_excerpt: str | None,
    request_truncated: bool,
    environment: str | None,
    source: str,
) -> dict[str, Any]:
    return {
        "schema": "hades.http_request.v1",
        "source": source,
        "environment": environment,
        "method": method,
        "url": url,
        "request_excerpt": request_excerpt,
        "request_truncated": request_truncated,
    }


def _http_response_payload(
    *,
    method: str,
    url: str,
    status: int | None,
    response_excerpt: str | None,
    response_truncated: bool,
    environment: str | None,
    source: str,
) -> dict[str, Any]:
    return {
        "schema": "hades.http_response.v1",
        "source": source,
        "environment": environment,
        "method": method,
        "url": url,
        "status": status,
        "response_excerpt": response_excerpt,
        "response_truncated": response_truncated,
    }


def _http_request_summary(*, method: str, url: str, environment: str | None) -> str:
    env = f" ({environment})" if environment else ""
    return f"HTTP request{env}: {method} {url}"[:1000]


def _http_response_summary(*, method: str, url: str, status: int | None, environment: str | None) -> str:
    env = f" ({environment})" if environment else ""
    status_text = str(status) if status is not None else "unknown status"
    return f"HTTP response{env}: {status_text} for {method} {url}"[:1000]


def _evidence_payload(kind: str, text: str, source: str, truncated: bool) -> dict[str, Any]:
    excerpt = _compact_lines(text)
    frames = _stack_frames(text)
    frame_refs = _frame_refs(frames)
    if kind == "failing_test":
        return {
            "schema": "hades.test_output.v1",
            "source": source,
            "excerpt": excerpt,
            "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest() if excerpt else "",
            "truncated": truncated,
            "framework": _detected_test_framework(text, source),
            "frames": frames,
            "frame_refs": frame_refs,
        }
    return {
        "schema": "hades.runtime_log_excerpt.v1",
        "source": source,
        "excerpt": excerpt,
        "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest() if excerpt else "",
        "truncated": truncated,
        "frames": frames,
        "frame_refs": frame_refs,
        "log_refs": [{"type": "runtime_log_frame", "path": ref["path"], "line": ref["line"]} for ref in frame_refs],
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


def _create_http_context_evidence(
    client: object,
    binding: db.WorkspaceBinding,
    *,
    bug_report_id: str | None,
    method: str,
    url: str,
    status: int | None,
    request_file: str | None,
    response_file: str | None,
    environment: str | None,
    source: str,
) -> list[str | None]:
    method = str(method or "GET").strip().upper() or "GET"
    raw_url = str(url or "").strip()
    if not raw_url:
        raise ValueError("request URL is required")
    redacted_url = redact_secret(raw_url)
    url_redactions = 1 if redacted_url != raw_url else 0
    request_excerpt, request_truncated, request_redactions = _redacted_optional_file(request_file)
    response_excerpt, response_truncated, response_redactions = _redacted_optional_file(response_file)

    evidence_ids: list[str | None] = []
    request_response = client.create_bug_evidence(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        bug_report_id=bug_report_id,
        kind="http_request",
        summary=_http_request_summary(method=method, url=redacted_url, environment=environment),
        payload=_http_request_payload(
            method=method,
            url=redacted_url,
            request_excerpt=request_excerpt,
            request_truncated=request_truncated,
            environment=environment,
            source=source,
        ),
        source=source,
        redactions=url_redactions + request_redactions,
        retention_class="http_trace",
    )
    request_evidence = (
        request_response.get("evidence") if isinstance(request_response.get("evidence"), dict) else request_response
    )
    evidence_ids.append(
        str(request_evidence.get("id"))
        if isinstance(request_evidence, dict) and request_evidence.get("id")
        else None
    )

    if status is not None or response_excerpt is not None:
        response_response = client.create_bug_evidence(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            bug_report_id=bug_report_id,
            kind="http_response",
            summary=_http_response_summary(
                method=method,
                url=redacted_url,
                status=status,
                environment=environment,
            ),
            payload=_http_response_payload(
                method=method,
                url=redacted_url,
                status=status,
                response_excerpt=response_excerpt,
                response_truncated=response_truncated,
                environment=environment,
                source=source,
            ),
            source=source,
            redactions=url_redactions + response_redactions,
            retention_class="http_trace",
        )
        response_evidence = (
            response_response.get("evidence")
            if isinstance(response_response.get("evidence"), dict)
            else response_response
        )
        evidence_ids.append(
            str(response_evidence.get("id"))
            if isinstance(response_evidence, dict) and response_evidence.get("id")
            else None
        )

    return evidence_ids


def _cmd_ingest_http(args: argparse.Namespace) -> int:
    try:
        _agent, binding = _current_workspace_binding()
        source = str(getattr(args, "source", None) or "http")

        from hermes_cli import hades_backend_runtime as runtime

        client = runtime.client_from_config()
        try:
            evidence_ids = _create_http_context_evidence(
                client,
                binding,
                bug_report_id=getattr(args, "bug_report_id", None) or None,
                method=str(getattr(args, "method", None) or "GET"),
                url=str(getattr(args, "url", None) or ""),
                status=getattr(args, "status", None),
                request_file=getattr(args, "request_file", None),
                response_file=getattr(args, "response_file", None),
                environment=getattr(args, "environment", None),
                source=source,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        print(f"Hades backend ingest HTTP: {redact_secret(str(exc))}", file=sys.stderr)
        return 1

    result = {
        "status": "ok",
        "kinds": ["http_request"] + (["http_response"] if len(evidence_ids) > 1 else []),
        "project_id": binding.project_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "evidence_ids": evidence_ids,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Hades bug evidence stored: {len(evidence_ids)} HTTP item(s)")
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
    large_symbols = max(
        medium_symbols,
        int(getattr(args, "large_symbols", 5_501) or 5_501),
    )
    try:
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
            ],
            workspace=getattr(args, "workspace", None),
        )
    except ValueError as exc:
        print(f"Hades backend benchmark failed: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(report, sort_keys=True))
        return 1 if report["status"] == "failed" else 0

    print("Hades backend benchmark")
    print(f"  Status: {report['status']}")
    for case in report["cases"]:
        ratio = case["compression_ratio"]
        ratio_text = f"{ratio:.4f}" if isinstance(ratio, float) else "n/a"
        source = case.get("source") or "synthetic"
        total_ms = case.get("total_duration_ms", case["duration_ms"])
        print(
            f"  {case['name']} [{source}]: {case['upload_mode']} "
            f"{case['original_bytes']}B -> {case['compressed_bytes']}B "
            f"ratio={ratio_text} duration={total_ms}ms"
        )
        if case.get("index_duration_ms") is not None:
            print(f"    indexing={case['index_duration_ms']}ms schema={case.get('schema')} truncated={case.get('truncated')}")
    for warning in report["warnings"]:
        print(f"  warning: {warning}")
    return 1 if report["status"] == "failed" else 0


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
            if (
                getattr(args, "request_url", None)
                or getattr(args, "request_file", None)
                or getattr(args, "response_file", None)
                or getattr(args, "response_status", None) is not None
            ):
                evidence_ids.extend(
                    _create_http_context_evidence(
                        client,
                        binding,
                        bug_report_id=bug_report_id,
                        method=str(getattr(args, "request_method", None) or "GET"),
                        url=str(getattr(args, "request_url", None) or ""),
                        status=getattr(args, "response_status", None),
                        request_file=getattr(args, "request_file", None),
                        response_file=getattr(args, "response_file", None),
                        environment=getattr(args, "environment", None),
                        source="http",
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
    if action in {"bootstrap-awareness", "awareness-bootstrap"}:
        return _cmd_bootstrap_awareness(args)
    if action == "status":
        return _cmd_status(args)
    if action == "support-report":
        return _cmd_support_report(args)
    if action == "quality-report":
        return _cmd_quality_report(args)
    if action == "schedule-quality":
        return _cmd_schedule_quality(args)
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
    if action == "worker-setup":
        return _cmd_worker_setup(args)
    if action == "tasks":
        return _cmd_tasks(args)
    if action == "wiki":
        from hermes_cli.hades_wiki_actions import run_wiki_action

        return run_wiki_action(args)
    if action == "logbook":
        from hermes_cli.hades_logbook_actions import run_logbook_action

        return run_logbook_action(args)
    if action == "jobs":
        return _cmd_jobs(args)
    if action == "approve-job":
        return _cmd_approve_job(args)
    if action in {"approve-jobs", "approve-all"}:
        return _cmd_approve_jobs(args)
    if action == "refuse-job":
        return _cmd_refuse_job(args)
    if action == "proposals":
        return _cmd_proposals(args)
    if action == "ack-proposal":
        return _cmd_ack_proposal(args)
    if action == "promote-diagnosis":
        return _cmd_promote_diagnosis(args)
    if action == "causal-pack":
        return _cmd_causal_pack(args)
    if action == "ingest-test":
        return _cmd_ingest_test(args)
    if action == "ingest-log":
        return _cmd_ingest_log(args)
    if action == "ingest-deploy":
        return _cmd_ingest_deploy(args)
    if action == "ingest-http":
        return _cmd_ingest_http(args)
    if action == "bug-intake":
        return _cmd_bug_intake(args)
    if action == "backfill-note":
        return _cmd_backfill_note(args)
    if action == "benchmark":
        return _cmd_benchmark(args)
    if action == "sync":
        return _cmd_sync(args)
    print(
        "usage: hades backend <setup|bootstrap|bootstrap-awareness|status|support-report|quality-report|schedule-quality|privacy-export|privacy-delete|retention-cleanup|profiles|worker|worker-setup|tasks|wiki|logbook|jobs|approve-job|approve-jobs|refuse-job|proposals|ack-proposal|promote-diagnosis|causal-pack|ingest-test|ingest-log|ingest-deploy|ingest-http|bug-intake|backfill-note|benchmark|sync>",
        file=sys.stderr,
    )
    return 0
