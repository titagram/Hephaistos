from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from hermes_cli.gnothi.builder import (
    COLLECTOR_ORDER,
    build_organism_revision,
    drift_status,
)
from hermes_cli.gnothi.query import OrganismQuery
from hermes_cli.gnothi.store import OrganismRevisionStore
from hermes_cli.gnothi.wiki import render_wiki
from utils import atomic_replace


def _emit(value: Any, as_json: bool = False) -> None:
    if as_json or isinstance(value, (dict, list)):
        print(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=None if as_json else 2))
    else:
        print(value)


def _write_output(path: Path, content: str) -> None:
    if path.exists() and path.is_dir():
        raise ValueError("wiki output is a directory")
    if not path.parent.is_dir():
        raise ValueError("wiki output parent does not exist")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def gnothi_command(args) -> int:
    store = OrganismRevisionStore()
    query = OrganismQuery(store)
    action = args.gnothi_action
    try:
        if action == "status":
            result = query.status()
            current = store.current()
            if current:
                drift = drift_status(Path(args.workspace or Path.cwd()), current)
                result["drift"] = drift["domains"]
                result["invalidated_domains"] = drift["invalidated_domains"]
                result["actions"] = sorted(
                    set(result.get("actions", [])) | set(drift.get("actions", []))
                )
            _emit(result, args.json)
            return 1 if result.get("status") == "missing" else 0
        if action == "rebuild":
            result = build_organism_revision(
                Path(args.workspace or Path.cwd()),
                store=store,
                force=args.force,
                collector_names=args.collectors,
            )
            _emit(result if args.json else {
                "revision_id": result["organism_contract"]["revision_id"],
                "status": result["organism_contract"]["status"],
                "build_result": result.get("build_result"),
            }, args.json)
            return 0
        if action == "inspect":
            _emit(query.inspect(args.component), args.json)
            return 0
        if action == "explain":
            _emit(query.explain(args.capability), args.json)
            return 0
        if action == "diff":
            _emit(query.diff(args.revision_a, args.revision_b), args.json)
            return 0
        if action == "wiki":
            artifact = store.current()
            if not artifact:
                _emit({"status": "missing", "actions": ["rebuild"]}, True)
                return 1
            rendered = render_wiki(artifact)
            if args.output:
                _write_output(Path(args.output).expanduser(), rendered)
            else:
                print(rendered, end="")
            return 0
    except (OSError, ValueError) as exc:
        _emit({"status": "error", "error_class": type(exc).__name__, "action": action}, True)
        return 1
    return 1


def build_gnothi_parser(subparsers, cmd_gnothi):
    parser = subparsers.add_parser("gnothi-seauton", help="Inspect the installed Hades organism")
    actions = parser.add_subparsers(dest="gnothi_action", required=True)
    status = actions.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.add_argument("--workspace")
    rebuild = actions.add_parser("rebuild")
    rebuild.add_argument("--json", action="store_true")
    rebuild.add_argument("--force", action="store_true")
    rebuild.add_argument("--workspace")
    rebuild.add_argument(
        "--collector",
        dest="collectors",
        action="append",
        choices=COLLECTOR_ORDER,
    )
    inspect_parser = actions.add_parser("inspect")
    inspect_parser.add_argument("component")
    inspect_parser.add_argument("--json", action="store_true")
    explain = actions.add_parser("explain")
    explain.add_argument("capability")
    explain.add_argument("--json", action="store_true")
    diff = actions.add_parser("diff")
    diff.add_argument("revision_a")
    diff.add_argument("revision_b")
    diff.add_argument("--json", action="store_true")
    wiki = actions.add_parser("wiki")
    wiki.add_argument("--output")
    for child in (status, rebuild, inspect_parser, explain, diff, wiki):
        child.set_defaults(func=cmd_gnothi)
    return parser
