from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hermes_cli import hades_backend_db as db
from hermes_cli import hades_backend_runtime as runtime
from hermes_cli.hades_backend_client import redact_secret
from hermes_cli.hades_backend_cmd import _bug_report_id, _evidence_payload, _first_interesting_line


EVAL_ROOT = Path(__file__).resolve().parent
OLD_ROOT = Path("/Users/gabriele/Dev/Hephaistos/.hades-dev/rocket-club-no-codebase-eval")
PROJECT_ROOT = Path("/Users/gabriele/Dev/rocket-club/progetto-biliardo-codex")
REPORTS = EVAL_ROOT / "reports"
EVIDENCE = EVAL_ROOT / "evidence"


def _load_local_env() -> None:
    home = Path(os.environ["HERMES_HOME"]).expanduser()
    for raw in (home / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


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


def _evidence_text(name: str) -> str:
    return (EVIDENCE / name).read_text(encoding="utf-8")


def _create_bug_report(client: Any, binding: db.WorkspaceBinding, agent: db.BackendAgent, case: dict[str, Any]) -> str | None:
    response = client.create_bug_report(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        title=case["title"],
        symptom=case["symptom"],
        payload={
            "schema": "hades.rocket_club_awareness_hardening_bug.v1",
            "case_id": case["id"],
            "steps": case.get("steps"),
            "expected": case.get("expected"),
            "actual": case.get("actual"),
            "severity": case.get("severity", "medium"),
            "environment": "rocket-club-awareness-hardening-eval",
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
    payload["schema"] = f"{payload.get('schema', 'hades.evidence.v1')}.rocket_club_awareness_hardening_eval"
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


def _source_slice_by_symbol(client: Any, binding: db.WorkspaceBinding, symbol: str) -> str:
    response = client.source_slices(
        project_id=binding.project_id,
        workspace_binding_id=binding.backend_workspace_binding_id,
        symbol=symbol,
        limit=5,
    )
    items = response.get("items") if isinstance(response.get("items"), list) else []
    for item in items:
        if isinstance(item, dict) and str(item.get("symbol") or "") == symbol:
            return str(item.get("id") or "")
    return ""


def _cases(slice_ids: dict[str, str]) -> list[dict[str, Any]]:
    return [
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
            "source_slice_ids": [slice_ids["BookingController@validateBooking"]],
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
            "source_slice_ids": [slice_ids["RecordManualPayment@buildFifoAllocations"]],
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
            "source_slice_ids": [slice_ids["AdminPanelProvider@panel"]],
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
            "source_slice_ids": [],
            "graph_refs": [
                {"type": "route", "ref": "route:console.tournament-registrations.offer"},
                {"type": "edge", "ref": "edge:local_rocket_eval_populate_backend_ast:edge:75", "kind": "route_handler"},
                {"type": "symbol", "ref": "TournamentController@offerWaitlistSpot"},
            ],
        },
    ]


def _write_fixture(created_cases: dict[str, Any]) -> None:
    original = json.loads((OLD_ROOT / "fixtures" / "rocket_club_no_codebase_eval.json").read_text(encoding="utf-8"))
    for fixture in original.get("fixtures", []):
        case_id = str(fixture.get("case_id") or str(fixture["id"]).split("__", 1)[-1])
        created = created_cases[case_id]
        refs = [created["evidence_ref"], f"evidence_pack:{created['evidence_pack_id']}"]
        refs.extend(f"graph:{item['ref']}" for item in created["graph_refs"])
        refs.extend(created["source_slice_refs"])
        fixture["required_evidence_refs"] = [ref for ref in refs if ref]
    original["metadata"]["fresh_backend_project"] = True
    original["metadata"]["fresh_eval_root"] = str(EVAL_ROOT)
    original["trajectory_dirs"] = [
        "trajectories/gpt",
        "trajectories/deepseek-v4-flash",
    ]
    fixture_path = EVAL_ROOT / "fixtures" / "rocket_club_no_codebase_eval_fresh.json"
    fixture_path.write_text(json.dumps(original, indent=2, sort_keys=True), encoding="utf-8")
    suite = {
        "schema": "hades.no_codebase_quality_suite.v1",
        "suites": [
            {
                "id": "rocket_club_awareness_hardening_fresh",
                "path": str(fixture_path),
                "required_status": "passed",
            }
        ],
    }
    (EVAL_ROOT / "fixtures" / "rocket_club_quality_suite_fresh.json").write_text(
        json.dumps(suite, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main() -> int:
    _load_local_env()
    REPORTS.mkdir(parents=True, exist_ok=True)
    agent, binding = _current_binding()
    client = runtime.client_from_config(timeout=30.0)
    slice_ids = {
        "BookingController@validateBooking": _source_slice_by_symbol(client, binding, "BookingController@validateBooking"),
        "RecordManualPayment@buildFifoAllocations": _source_slice_by_symbol(client, binding, "RecordManualPayment@buildFifoAllocations"),
        "AdminPanelProvider@panel": _source_slice_by_symbol(client, binding, "AdminPanelProvider@panel"),
    }
    missing = [symbol for symbol, slice_id in slice_ids.items() if not slice_id]
    if missing:
        raise RuntimeError(f"missing approved source slices: {missing}")

    created_cases: dict[str, Any] = {}
    for case in _cases(slice_ids):
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
        evidence_refs = [{"type": "bug_evidence", "id": evidence_id}] if evidence_id else []
        pack_response = client.create_evidence_pack(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            bug_report_id=bug_report_id,
            title=f"{case['id']} evidence pack",
            summary=(
                f"Evidence bundle for {case['symptom']} "
                + ("Includes candidate-approved bounded source slice(s)." if case["source_slice_ids"] else "Intentionally lacks a handler source slice.")
            ),
            evidence_refs=evidence_refs,
            graph_refs=case["graph_refs"],
            source_slice_ids=case["source_slice_ids"],
            payload={
                "schema": "hades.rocket_club_awareness_hardening_evidence_pack.v1",
                "case_id": case["id"],
                "evidence_file": case["evidence_file"],
                "source_slice_ids": case["source_slice_ids"],
                "missing_evidence": [] if case["source_slice_ids"] else ["source_slice"],
            },
            head_commit=binding.head_commit,
            redactions=0,
        )
        pack = pack_response.get("evidence_pack") if isinstance(pack_response.get("evidence_pack"), dict) else pack_response
        created_cases[case["id"]] = {
            "bug_report_id": bug_report_id,
            "evidence_id": evidence_id,
            "evidence_ref": f"bug_evidence:{evidence_id}" if evidence_id else "",
            "evidence_pack_id": str(pack.get("id") or pack.get("evidence_pack_id") or ""),
            "source_slice_ids": case["source_slice_ids"],
            "source_slice_refs": [f"source_slice:{slice_id}" for slice_id in case["source_slice_ids"]],
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
        "head_commit": binding.head_commit,
        "source_slices": slice_ids,
        "cases": created_cases,
        "awareness": awareness,
    }
    (REPORTS / "fresh-source-evidence-ingest.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    _write_fixture(created_cases)
    print(json.dumps({"status": "ok", "cases": len(created_cases), "diagnosable": awareness.get("diagnosable_without_source")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
