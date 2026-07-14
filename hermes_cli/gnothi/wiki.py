from __future__ import annotations

import re
from typing import Any

from hermes_cli.gnothi.redaction import redact_value

MAX_SECTION_ROWS = 200


def _text(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")[:500]


def _anchor(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")[:120]


def _evidence_links(node: dict[str, Any]) -> str:
    refs = sorted(str(ref) for ref in node.get("evidence_refs", []) if ref)
    return ", ".join(f"[{_text(ref)}](#evidence-{_anchor(ref)})" for ref in refs) or "Unknown"


def _state(node: dict[str, Any]) -> str:
    state = node.get("state") if isinstance(node.get("state"), dict) else {}
    enabled = sorted(key for key, value in state.items() if value is True)
    disabled = sorted(key for key, value in state.items() if value is False)
    parts = []
    if enabled:
        parts.append("+" + ",".join(enabled))
    if disabled:
        parts.append("-" + ",".join(disabled))
    return "; ".join(parts) or "Unknown"


def _node_table(nodes: list[dict[str, Any]]) -> str:
    ordered = sorted(nodes, key=lambda row: (str(row.get("kind")), str(row.get("label")), str(row.get("id"))))
    visible = ordered[:MAX_SECTION_ROWS]
    if not visible:
        return "No entries."
    lines = ["| ID | Kind | Label | State | Evidence |", "|---|---|---|---|---|"]
    for node in visible:
        lines.append(
            f"| {_text(node.get('id'))} | {_text(node.get('kind'))} | "
            f"{_text(node.get('label'))} | {_text(_state(node))} | {_evidence_links(node)} |"
        )
    if len(ordered) > len(visible):
        lines.append(f"\n_Omitted {len(ordered) - len(visible)} additional entries._")
    return "\n".join(lines)


def render_wiki(artifact: dict[str, Any]) -> str:
    safe, _ = redact_value(artifact)
    nodes = [row for row in safe.get("nodes", []) if isinstance(row, dict)]
    contract = safe.get("organism_contract", {})

    anatomy = [node for node in nodes if node.get("kind") not in {"capability", "dependency", "service", "invariant", "runtime", "observation"}]
    capabilities = [node for node in nodes if node.get("kind") == "capability"]
    dependencies = [node for node in nodes if node.get("kind") in {"dependency", "service"}]
    invariants = [node for node in nodes if node.get("kind") == "invariant"]
    runtime = [node for node in nodes if node.get("kind") == "runtime"]
    degraded = [node for node in nodes if node.get("state", {}).get("degraded") is True]

    generation = contract.get("generation", {})
    coverage = contract.get("coverage", {})
    coverage_lines = ["| Domain | Status | Freshness |", "|---|---|---|"]
    unknown = False
    for name, row in sorted(coverage.items()):
        status = str(row.get("status") or "missing")
        display = status.capitalize()
        if status in {"partial", "missing", "stale"}:
            unknown = True
        coverage_lines.append(
            f"| {_text(name)} | {display} | {_text(row.get('verified_at') or 'Unknown')} |"
        )
    if not coverage:
        coverage_lines.append("| all | Unknown | Unknown |")
        unknown = True
    coverage_lines.append(f"\nOverall: **{str(contract.get('status') or 'unknown').capitalize()}**.")
    if unknown:
        coverage_lines.append("Unknown areas require a rebuild or restored evidence.")

    evidence_lines = []
    for item in sorted(safe.get("evidence", []), key=lambda row: str(row.get("id"))):
        evidence_id = str(item.get("id") or "")
        evidence_lines.append(
            f"<a id=\"evidence-{_anchor(evidence_id)}\"></a>"
            f"- **{_text(evidence_id)}** — {_text(item.get('kind'))}; "
            f"source: {_text(item.get('path') or item.get('opaque_id') or 'metadata')}"
        )

    sections = [
        ("Anatomy", _node_table(anatomy)),
        ("Capabilities", _node_table(capabilities)),
        ("Dependencies", _node_table(dependencies)),
        ("Contracts and invariants", _node_table(invariants)),
        ("Runtime state", _node_table(runtime)),
        ("Known degradation", _node_table(degraded)),
        (
            "Generations and rollback history",
            f"- Revision: `{_text(contract.get('revision_id'))}`\n"
            f"- Generation: `{_text(generation.get('id'))}` ({_text(generation.get('scope'))})\n"
            "- Previous healthy revisions remain in the immutable local store.",
        ),
        ("Coverage, freshness, and unknown areas", "\n".join(coverage_lines)),
        ("Evidence index", "\n".join(evidence_lines[:MAX_SECTION_ROWS]) or "No evidence entries."),
    ]
    body = [
        "# Gnothi Seauton",
        "",
        "> This page is generated from an immutable organism artifact. Manual edits are discarded.",
    ]
    for title, content in sections:
        body.extend(["", f"## {title}", "", content])
    return "\n".join(body).rstrip() + "\n"
