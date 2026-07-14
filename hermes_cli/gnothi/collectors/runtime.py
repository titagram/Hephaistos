from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from hermes_cli import __version__, config, hades_backend_status as backend_status, profiles
from hermes_cli.gnothi.collectors.base import (
    CollectorContext,
    CollectorResult,
    fingerprint_payload,
)
from hermes_cli.gnothi.contract import stable_id
from hermes_cli.gnothi.redaction import (
    SECRET_KEY_PATTERN,
    redact_value,
    safe_exception_class,
)


def _git_generation(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        head = result.stdout.strip()
        if head:
            return f"git:{head}"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"release:{__version__}"


def _evidence(kind: str, identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": stable_id("evidence", {"kind": kind, **identity}),
        "kind": kind,
        **identity,
    }


def _runtime_node(
    context: CollectorContext,
    *,
    label: str,
    properties: dict[str, Any],
    evidence_ref: str,
) -> dict[str, Any]:
    node_id = stable_id("runtime", {"label": label})
    return {
        "id": node_id,
        "kind": "runtime",
        "label": label,
        "owner": {"class": "core", "id": "hermes"},
        "generation_scope": context.generation_scope,
        "state": {
            "declared": True,
            "installed": True,
            "available": True,
            "active": True,
            "verified": True,
            "degraded": False,
            "candidate": False,
        },
        "evidence_refs": [evidence_ref],
        "properties": {"collector": "runtime", **properties},
        "verified_at": context.collected_at,
    }


def _flatten_config(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten_config(value[key], path)
        return
    if isinstance(value, (str, int, float, bool)) or value is None:
        yield prefix, value


def _fingerprint(nodes: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    stable_nodes = [
        {key: value for key, value in node.items() if key != "verified_at"}
        for node in nodes
    ]
    encoded = json.dumps(
        {"nodes": stable_nodes, "evidence": evidence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class RuntimeCollector:
    name = "runtime"

    def probe_fingerprint(self, context: CollectorContext) -> str:
        loaded_config = config.load_config()
        config_rows = []
        for key_path, raw_value in _flatten_config(loaded_config):
            if not key_path or key_path.split(".", 1)[0] == "mcp_servers":
                continue
            secret_shaped = any(
                SECRET_KEY_PATTERN.search(part) for part in key_path.split(".")
            )
            if secret_shaped:
                value = {"present": raw_value is not None}
            else:
                value, _ = redact_value(raw_value, workspace_root=context.workspace_root)
            config_rows.append((key_path, value))
        status = backend_status.load_backend_status_payload()
        awareness = status.get("awareness")
        bindings = status.get("bindings")
        backend = {
            "configured": bool(status.get("configured")),
            "degraded": bool(status.get("degraded")),
            "awareness_status": (
                awareness.get("status") if isinstance(awareness, dict) else None
            ),
            "binding_count": len(bindings) if isinstance(bindings, list) else 0,
        }
        return fingerprint_payload(
            {
                "python": platform.python_version(),
                "platform": (sys.platform, platform.machine()),
                "generation": _git_generation(context.workspace_root),
                "release": __version__,
                "profile": profiles.get_active_profile(),
                "executable": Path(sys.executable).name,
                "config": config_rows,
                "backend": backend,
            }
        )

    def collect(self, context: CollectorContext) -> CollectorResult:
        nodes: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        try:
            generation = _git_generation(context.workspace_root)
            facts = [
                ("runtime:python", {"version": platform.python_version()}),
                ("runtime:platform", {"system": sys.platform, "machine": platform.machine()}),
                ("runtime:generation", {"generation_id": generation, "release": __version__}),
                ("runtime:profile", {"name": profiles.get_active_profile()}),
                (
                    "runtime:process",
                    {"executable": Path(sys.executable).name},
                ),
            ]
            for label, properties in facts:
                evidence_row = _evidence("runtime_probe", {"label": label, **properties})
                evidence.append(evidence_row)
                nodes.append(
                    _runtime_node(
                        context,
                        label=label,
                        properties=properties,
                        evidence_ref=evidence_row["id"],
                    )
                )

            loaded_config = config.load_config()
            for key_path, raw_value in _flatten_config(loaded_config):
                if not key_path:
                    continue
                secret_shaped = any(
                    SECRET_KEY_PATTERN.search(part) for part in key_path.split(".")
                )
                if secret_shaped:
                    properties = {"key_path": key_path, "value_present": raw_value is not None}
                else:
                    safe_value, _ = redact_value(
                        raw_value,
                        workspace_root=context.workspace_root,
                    )
                    properties = {"key_path": key_path, "value": safe_value}
                evidence_row = _evidence("effective_config", {"key_path": key_path})
                evidence.append(evidence_row)
                nodes.append(
                    _runtime_node(
                        context,
                        label=f"config:{key_path}",
                        properties=properties,
                        evidence_ref=evidence_row["id"],
                    )
                )

            status = backend_status.load_backend_status_payload()
            awareness = status.get("awareness")
            bindings = status.get("bindings")
            backend_properties = {
                "configured": bool(status.get("configured")),
                "degraded": bool(status.get("degraded")),
                "awareness_status": (
                    awareness.get("status") if isinstance(awareness, dict) else None
                ),
                "binding_count": len(bindings) if isinstance(bindings, list) else 0,
            }
            evidence_row = _evidence("backend_status", backend_properties)
            evidence.append(evidence_row)
            backend_node = _runtime_node(
                context,
                label="runtime:backend",
                properties=backend_properties,
                evidence_ref=evidence_row["id"],
            )
            backend_node["state"]["degraded"] = backend_properties["degraded"]
            nodes.append(backend_node)
        except Exception as exc:
            return CollectorResult(
                name=self.name,
                status="partial",
                nodes=nodes,
                edges=[],
                evidence=evidence,
                fingerprint=_fingerprint(nodes, evidence),
                verified_at=None,
                error_code=safe_exception_class(exc),
            )

        return CollectorResult(
            name=self.name,
            status="current",
            nodes=nodes,
            edges=[],
            evidence=evidence,
            fingerprint=self.probe_fingerprint(context),
            verified_at=context.collected_at,
        )
