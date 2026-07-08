from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from run_agent import AIAgent


EVAL_ROOT = Path(__file__).resolve().parent
FIXTURE_PATH = EVAL_ROOT / "fixtures" / "rocket_club_no_codebase_eval.json"
NO_SOURCE_CWD = EVAL_ROOT / "no-source-cwd"

CANONICAL_ROOT_CAUSE_IDS = [
    "rc.rocket_club.booking_requires_user_or_ghost_alias",
    "rc.rocket_club.payment_exceeds_open_account_balance",
    "rc.rocket_club.filament_admin_requires_filament_auth",
]


CASE_INPUTS: dict[str, dict[str, str]] = {
    "rc_booking_controller_or_model": {
        "symptom": "POST /console/bookings returns HTTP 422 when neither user_id nor ghost_alias is supplied.",
        "query": "rc_booking_controller_or_model booking ghost_alias user_id 422",
        "graph_start": "BookingController@validateBooking",
        "source_symbol": "BookingController@validateBooking",
    },
    "rc_payment_or_subscription_schema": {
        "symptom": "Recording a manual payment larger than the current open account balance throws a domain exception.",
        "query": "rc_payment_or_subscription_schema manual payment exceeds account balance DomainException",
        "graph_start": "RecordManualPayment@buildFifoAllocations",
        "source_symbol": "RecordManualPayment@buildFifoAllocations",
    },
    "rc_filament_or_inertia_policy": {
        "symptom": "GET /admin redirects or blocks a signed-in venue customer who expected to access the backoffice.",
        "query": "rc_filament_or_inertia_policy Filament admin Authenticate middleware customer blocked",
        "graph_start": "AdminPanelProvider@panel",
        "source_symbol": "AdminPanelProvider@panel",
        "confidence_guidance": "Use medium confidence for this policy/auth case unless retrieved evidence includes the concrete affected user's role/permissions in addition to the panel middleware.",
        "graph_ref_guidance": "Include graph:AdminPanelProvider@panel and graph:Filament\\Http\\Middleware\\Authenticate when present.",
    },
    "rc_incomplete_missing_source_slice": {
        "symptom": "Offering a waitlist spot appears to leave the tournament registration unavailable to the player.",
        "query": "rc_incomplete_missing_source_slice tournament waitlist offer TournamentController offerWaitlistSpot",
        "graph_start": "route:console.tournament-registrations.offer",
        "source_symbol": "TournamentController@offerWaitlistSpot",
        "confidence_guidance": "This case is only diagnosable as insufficient unless a relevant source slice for TournamentController@offerWaitlistSpot is retrieved.",
        "graph_ref_guidance": "Include both graph:route:console.tournament-registrations.offer and graph:TournamentController@offerWaitlistSpot when the route/handler graph identifies them.",
    },
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


def _secret_from_env_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def _fixtures_for_model(model_family: str) -> list[dict[str, Any]]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [
        item
        for item in data.get("fixtures", [])
        if str(item.get("model_family") or "") == model_family
    ]


def _case_id(fixture: dict[str, Any]) -> str:
    return str(fixture.get("case_id") or str(fixture["id"]).split("__", 1)[-1])


def _prompt(fixture: dict[str, Any]) -> str:
    case_id = _case_id(fixture)
    case = CASE_INPUTS[case_id]
    canonical_ids = "\n".join(f"- {item}" for item in CANONICAL_ROOT_CAUSE_IDS)
    return f"""
You are running a strict Hades no-codebase diagnosis evaluation.

You are in a neutral directory that must not access the Rocket Club source tree.
Use only Hades backend memory tools. Do not use shell, terminal, file, grep, rg,
read_file, search_files, browser, or web tools.

Fixture id: {fixture["id"]}
Case id: {case_id}
Observed symptom: {case["symptom"]}
Case confidence guidance: {case.get("confidence_guidance", "Use the confidence level supported by retrieved evidence only.")}
Case graph-ref guidance: {case.get("graph_ref_guidance", "Include the most specific route, symbol, middleware, and edge graph refs returned by graph/evidence-pack tools.")}

Required workflow, in this order:
1. Call hades_backend_project_awareness_status.
2. Call hades_backend_bug_evidence_search with query: {case["query"]!r}.
3. Call hades_backend_evidence_pack_search with query: {case_id!r}.
4. Call hades_backend_graph_search with query/start: {case["graph_start"]!r}.
5. Call hades_backend_source_slice_fetch with symbol: {case["source_symbol"]!r}.
6. Call hades_backend_diagnosis_report_create to persist the final diagnosis.

Evidence refs in the diagnosis report and final JSON must include:
- bug evidence as bug_evidence:<id>
- evidence pack as evidence_pack:<id>
- graph refs as graph:<ref> from graph/evidence-pack results. Use exactly the
  returned ref string after graph:. If the returned ref is
  edge:local_rocket_eval_populate_backend_ast:edge:1428, output
  graph:edge:local_rocket_eval_populate_backend_ast:edge:1428, never
  graph:edge:edge:local_rocket_eval_populate_backend_ast:edge:1428. If the
  returned ref is BookingController@validateBooking, output
  graph:BookingController@validateBooking, never graph:node:BookingController@validateBooking.
- source slices as source_slice:<id> when a relevant slice exists

Canonical root_cause_id values for complete cases:
{canonical_ids}

If the source slice fetch returns no relevant handler/body slice for this case,
do not claim a precise root cause. Use confidence "insufficient", root_cause_id
null, root_cause "not_determined", diagnosable_without_source false, and
missing_evidence ["source_slice"].

For precise diagnoses, confidence must be "high" or "medium", freshness.status
must be "current", and awareness.diagnosable_without_source must be true.

Return the final assistant response as a single JSON object with these keys:
fixture_id, case_id, root_cause_id, confidence, freshness, awareness,
diagnosable_without_source, evidence_refs, missing_evidence, mechanism.
""".strip()


def _disable_provider_sync(agent: AIAgent) -> None:
    manager = getattr(agent, "_memory_manager", None)
    providers = getattr(manager, "providers", []) if manager else []
    for provider in providers:
        try:
            provider._last_sync_at = time.time()
            provider.sync_turn = lambda *args, **kwargs: None
        except Exception:
            pass


def _run_fixture(args: argparse.Namespace, fixture: dict[str, Any]) -> dict[str, Any]:
    api_key = args.api_key or ""
    if not api_key and args.provider == "openai-codex" and args.base_url:
        from hermes_cli.auth import resolve_codex_runtime_credentials

        api_key = str(resolve_codex_runtime_credentials().get("api_key") or "")
    if not api_key and args.provider == "opencode-go":
        api_key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
        if not api_key:
            api_key = _secret_from_env_file(Path.home() / ".hermes" / ".env", "OPENCODE_GO_API_KEY")
    agent = AIAgent(
        provider=args.provider,
        model=args.model,
        api_key=api_key or None,
        base_url=args.base_url or None,
        api_mode=args.api_mode,
        enabled_toolsets=["memory"],
        disabled_toolsets=[],
        skip_memory=False,
        max_iterations=args.max_iterations,
        quiet_mode=True,
        session_id=f"rocket-no-codebase-{fixture['id']}",
    )
    _disable_provider_sync(agent)
    prompt = _prompt(fixture)
    result = agent.run_conversation(prompt)
    completed = bool(result.get("completed", True)) and not bool(result.get("failed"))
    trajectory = agent._convert_to_trajectory_format(result.get("messages") or [], prompt, completed)
    return {
        "metadata": {
            "fixture_id": fixture["id"],
            "case_id": _case_id(fixture),
            "model_family": args.model_family,
            "model": args.model,
            "provider": args.provider,
            "no_codebase_cwd": str(NO_SOURCE_CWD),
            "completed": completed,
            "failed": bool(result.get("failed")),
            "error": result.get("error"),
        },
        "conversations": trajectory,
        "final_response": result.get("final_response"),
        "completed": completed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-family", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-mode", default="chat_completions")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--fixture-id", default="")
    args = parser.parse_args()

    _load_local_env()
    NO_SOURCE_CWD.mkdir(parents=True, exist_ok=True)
    os.chdir(NO_SOURCE_CWD)

    fixtures = _fixtures_for_model(args.model_family)
    if args.fixture_id:
        fixtures = [fixture for fixture in fixtures if fixture["id"] == args.fixture_id]
    if not fixtures:
        raise SystemExit(f"No fixtures matched model family {args.model_family!r}")

    out_dir = EVAL_ROOT / "trajectories" / args.model_family
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for fixture in fixtures:
        entry = _run_fixture(args, fixture)
        out_path = out_dir / f"{fixture['id']}.json"
        out_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append(
            {
                "fixture_id": fixture["id"],
                "completed": entry["completed"],
                "path": str(out_path),
                "error": entry["metadata"].get("error"),
            }
        )
        print(json.dumps(summary[-1], sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
