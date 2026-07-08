from __future__ import annotations

import json
import os
from pathlib import Path

from hermes_cli import hades_backend_db as db
from hermes_cli import hades_backend_runtime as runtime


EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path("/Users/gabriele/Dev/rocket-club/progetto-biliardo-codex")


def _load_local_env() -> None:
    home = Path(os.environ["HERMES_HOME"]).expanduser()
    for raw in (home / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _binding() -> db.WorkspaceBinding:
    root = PROJECT_ROOT.resolve()
    with db.connect_closing() as conn:
        bindings = db.list_workspace_bindings(conn, status="linked")
    for binding in bindings:
        try:
            root.relative_to(Path(binding.repo_root).resolve())
        except (OSError, ValueError):
            continue
        return binding
    raise RuntimeError("Rocket Club binding not found")


def main() -> int:
    _load_local_env()
    binding = _binding()
    client = runtime.client_from_config(timeout=30.0)
    queries = [
        "BookingController@validateBooking",
        "RecordManualPayment@buildFifoAllocations",
        "AdminPanelProvider@panel",
        "TournamentController@offerWaitlistSpot",
    ]
    results = {}
    for query in queries:
        results[query] = client.memory_search(
            project_id=binding.project_id,
            workspace_binding_id=binding.backend_workspace_binding_id,
            query=query,
            domains=["artifacts"],
            limit=10,
        )
    out = EVAL_ROOT / "reports" / "fresh-graph-search.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({query: len((value.get("items") or [])) for query, value in results.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
