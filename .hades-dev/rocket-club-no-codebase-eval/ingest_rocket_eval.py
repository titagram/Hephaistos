from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from hermes_cli import hades_backend_db as db
from hermes_cli import hades_backend_runtime as runtime
from hermes_cli.hades_backend_cmd import _bug_report_id, _evidence_payload, _first_interesting_line
from hermes_cli.hades_backend_client import redact_secret


EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path("/Users/gabriele/Dev/rocket-club/progetto-biliardo-codex")
REPORTS = EVAL_ROOT / "reports"
EVIDENCE = EVAL_ROOT / "evidence"


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


def _source_slice(path: str, start: int, end: int) -> tuple[str, str]:
    lines = (PROJECT_ROOT / path).read_text(encoding="utf-8").splitlines()
    selected = lines[start - 1 : end]
    numbered = "\n".join(f"{line_no}: {line}" for line_no, line in enumerate(selected, start=start))
    return "\n".join(selected), numbered


def _evidence_text(name: str) -> str:
    return (EVIDENCE / name).read_text(encoding="utf-8")


def _create_bug_report(client: Any, binding: db.WorkspaceBinding, agent: db.BackendAgent, case: dict[str, Any]) -> str | None:
    response = client.create_bug_report(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        title=case["title"],
        symptom=case["symptom"],
        payload={
            "schema": "hades.rocket_club_eval_bug.v1",
            "case_id": case["id"],
            "steps": case.get("steps"),
            "expected": case.get("expected"),
            "actual": case.get("actual"),
            "severity": case.get("severity", "medium"),
            "environment": "rocket-club-no-codebase-eval",
            "agent_id": agent.agent_id,
        },
    )
    return _bug_report_id(response)


def _create_evidence(
    client: Any,
    binding: db.WorkspaceBinding,
    *,
    bug_report_id: str | None,
    case_id: str,
    file_name: str,
    kind: str,
    retention_class: str,
) -> str | None:
    text = _evidence_text(file_name)
    redacted = redact_secret(text)
    payload = _evidence_payload(kind, redacted, file_name, False)
    payload["case_id"] = case_id
    payload["schema"] = f"{payload.get('schema', 'hades.evidence.v1')}.rocket_club_eval"
    response = client.create_bug_evidence(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        bug_report_id=bug_report_id,
        kind=kind,
        summary=_first_interesting_line(redacted),
        payload=payload,
        source=file_name,
        redactions=1 if redacted != text else 0,
        retention_class=retention_class,
    )
    evidence = response.get("evidence") if isinstance(response.get("evidence"), dict) else response
    return str(evidence.get("id")) if isinstance(evidence, dict) and evidence.get("id") else None


def main() -> None:
    _load_local_env()
    REPORTS.mkdir(parents=True, exist_ok=True)
    agent, binding = _current_binding()
    client = runtime.client_from_config(timeout=30.0)
    head_commit = binding.head_commit

    source_specs = {
        "booking_validate": {
            "path": "app/Http/Controllers/Console/BookingController.php",
            "start_line": 220,
            "end_line": 238,
            "language": "php",
            "symbol": "BookingController@validateBooking",
        },
        "payment_fifo": {
            "path": "app/Actions/Accounts/RecordManualPayment.php",
            "start_line": 64,
            "end_line": 85,
            "language": "php",
            "symbol": "RecordManualPayment@buildFifoAllocations",
        },
        "filament_panel": {
            "path": "app/Providers/Filament/AdminPanelProvider.php",
            "start_line": 24,
            "end_line": 57,
            "language": "php",
            "symbol": "AdminPanelProvider@panel",
        },
    }
    source_slices: dict[str, dict[str, Any]] = {}
    for key, spec in source_specs.items():
        raw, numbered = _source_slice(spec["path"], spec["start_line"], spec["end_line"])
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        response = client.create_source_slice(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            path=spec["path"],
            start_line=spec["start_line"],
            end_line=spec["end_line"],
            language=spec["language"],
            symbol=spec["symbol"],
            head_commit=head_commit,
            sha256=sha,
            content_redacted=numbered,
            redactions=0,
            truncated=False,
            retention_class="source_slice",
            policy="rocket_club_no_codebase_eval_bounded_lines",
        )
        item = response.get("source_slice") if isinstance(response.get("source_slice"), dict) else response
        source_slices[key] = {
            "id": str(item.get("id") or item.get("source_slice_id") or ""),
            "path": spec["path"],
            "start_line": spec["start_line"],
            "end_line": spec["end_line"],
            "symbol": spec["symbol"],
            "sha256": sha,
        }

    cases = [
        {
            "id": "rc_booking_controller_or_model",
            "title": "Booking creation returns 422 without registered user or guest alias",
            "symptom": "POST /console/bookings returns HTTP 422 when neither user_id nor ghost_alias is supplied.",
            "steps": "Submit the console booking form without selecting a registered user and without entering a ghost alias.",
            "expected": "The request should be accepted only when an identifiable registered or guest player is present.",
            "actual": "The request aborts with HTTP 422 and the message 'Serve un utente registrato o un alias ghost.'",
            "evidence_file": "rc-booking-stack.log",
            "kind": "log_excerpt",
            "retention_class": "log_excerpt",
            "slice_keys": ["booking_validate"],
            "graph_refs": [
                {"type": "symbol", "ref": "BookingController@validateBooking"},
                {"type": "edge", "ref": "edge:local_rocket_eval_populate_backend_ast:edge:1428", "kind": "http_abort"},
                {"type": "edge", "ref": "edge:local_rocket_eval_populate_backend_ast:edge:1491", "kind": "request_validation"},
                {"type": "route", "ref": "route:console.bookings.store"},
            ],
        },
        {
            "id": "rc_payment_or_subscription_schema",
            "title": "Manual account payment fails when amount exceeds unpaid balance",
            "symptom": "Recording a manual payment larger than the current open account balance throws a domain exception.",
            "steps": "Submit a console account payment amount that is greater than the sum of active unpaid account item balances.",
            "expected": "The payment should be limited to the open account balance or rejected with a clear balance constraint.",
            "actual": "The domain action throws 'Payment exceeds account balance.' during FIFO allocation.",
            "evidence_file": "rc-payment-test.txt",
            "kind": "failing_test",
            "retention_class": "test_failure",
            "slice_keys": ["payment_fifo"],
            "graph_refs": [
                {"type": "symbol", "ref": "RecordManualPayment@buildFifoAllocations"},
                {"type": "edge", "ref": "edge:local_rocket_eval_populate_backend_ast:edge:258", "kind": "instantiates"},
                {"type": "class", "ref": "DomainException"},
            ],
        },
        {
            "id": "rc_filament_or_inertia_policy",
            "title": "Venue customer is blocked from the Filament admin panel",
            "symptom": "GET /admin redirects or blocks a signed-in venue customer who expected to access the backoffice.",
            "steps": "Sign in as a normal venue customer and open /admin.",
            "expected": "Only users satisfying the Filament admin auth policy should reach the admin panel.",
            "actual": "The request is blocked by the Filament panel authentication middleware before resource pages load.",
            "evidence_file": "rc-filament-policy.log",
            "kind": "log_excerpt",
            "retention_class": "log_excerpt",
            "slice_keys": ["filament_panel"],
            "graph_refs": [
                {"type": "symbol", "ref": "AdminPanelProvider@panel"},
                {"type": "class", "ref": "App\\Providers\\Filament\\AdminPanelProvider"},
                {"type": "middleware", "ref": "Filament\\Http\\Middleware\\Authenticate"},
            ],
        },
        {
            "id": "rc_incomplete_missing_source_slice",
            "title": "Tournament waitlist offer appears incomplete",
            "symptom": "Offering a waitlist spot appears to leave the tournament registration unavailable to the player.",
            "steps": "POST /console/tournament-registrations/{registration}/offer for a waitlisted tournament registration.",
            "expected": "The player should be able to act on the waitlist offer if the handler marks it correctly.",
            "actual": "The available graph only identifies the route and handler; the handler body is intentionally not provided.",
            "evidence_file": "rc-incomplete.log",
            "kind": "log_excerpt",
            "retention_class": "log_excerpt",
            "slice_keys": [],
            "graph_refs": [
                {"type": "route", "ref": "route:console.tournament-registrations.offer"},
                {"type": "edge", "ref": "edge:local_rocket_eval_populate_backend_ast:edge:75", "kind": "route_handler"},
                {"type": "symbol", "ref": "TournamentController@offerWaitlistSpot"},
            ],
        },
    ]

    created_cases: dict[str, Any] = {}
    for case in cases:
        bug_report_id = _create_bug_report(client, binding, agent, case)
        evidence_id = _create_evidence(
            client,
            binding,
            bug_report_id=bug_report_id,
            case_id=case["id"],
            file_name=case["evidence_file"],
            kind=case["kind"],
            retention_class=case["retention_class"],
        )
        slice_ids = [source_slices[key]["id"] for key in case["slice_keys"] if source_slices[key]["id"]]
        evidence_refs = [{"type": "bug_evidence", "id": evidence_id}] if evidence_id else []
        pack_response = client.create_evidence_pack(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            bug_report_id=bug_report_id,
            title=f"{case['id']} evidence pack",
            summary=(
                f"Evidence bundle for {case['symptom']} "
                + ("Includes bounded source slice(s)." if slice_ids else "Intentionally lacks a handler source slice.")
            ),
            evidence_refs=evidence_refs,
            graph_refs=case["graph_refs"],
            source_slice_ids=slice_ids,
            payload={
                "schema": "hades.rocket_club_eval_evidence_pack.v1",
                "case_id": case["id"],
                "evidence_file": case["evidence_file"],
                "source_slice_paths": [source_slices[key]["path"] for key in case["slice_keys"]],
                "missing_evidence": [] if slice_ids else ["source_slice"],
            },
            head_commit=head_commit,
            redactions=0,
        )
        pack = pack_response.get("evidence_pack") if isinstance(pack_response.get("evidence_pack"), dict) else pack_response
        created_cases[case["id"]] = {
            "bug_report_id": bug_report_id,
            "evidence_id": evidence_id,
            "evidence_ref": f"bug_evidence:{evidence_id}" if evidence_id else "",
            "evidence_pack_id": str(pack.get("id") or pack.get("evidence_pack_id") or ""),
            "source_slice_ids": slice_ids,
            "source_slice_refs": [f"source_slice:{slice_id}" for slice_id in slice_ids],
            "graph_refs": case["graph_refs"],
        }

    awareness = client.project_awareness_status(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
    )
    result = {
        "project_id": binding.project_id,
        "workspace_binding_id": binding.backend_workspace_binding_id,
        "agent_id": agent.agent_id,
        "head_commit": head_commit,
        "source_slices": source_slices,
        "cases": created_cases,
        "awareness": awareness,
    }
    (REPORTS / "source-evidence-ingest.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    (REPORTS / "source-slices.json").write_text(json.dumps(source_slices, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "ok", "cases": len(created_cases), "source_slices": len(source_slices)}, sort_keys=True))


if __name__ == "__main__":
    main()
