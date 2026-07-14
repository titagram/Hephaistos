from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

from hermes_cli import config, plugins
from hermes_cli.gnothi.collectors.base import CollectorContext, CollectorResult
from hermes_cli.gnothi.contract import stable_id
from hermes_cli.gnothi.redaction import safe_exception_class

_NPM_PACKAGE_NAME = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$",
    re.IGNORECASE,
)
_SAFE_PACKAGE_SPECIFIER = re.compile(r"^[A-Za-z0-9.*<>=~^|&! _+()-]+$")


def _safe_package_specifier(value: object) -> str:
    text = str(value or "")
    if len(text) <= 256 and _SAFE_PACKAGE_SPECIFIER.fullmatch(text):
        return text
    return "[OPAQUE_SPECIFIER]"


def _evidence(kind: str, identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": stable_id("evidence", {"kind": kind, **identity}),
        "kind": kind,
        **identity,
    }


def _state(*, installed: bool, active: bool = False) -> dict[str, bool]:
    return {
        "declared": True,
        "installed": installed,
        "available": installed,
        "active": active,
        "verified": True,
        "degraded": False,
        "candidate": False,
    }


def _node(
    context: CollectorContext,
    *,
    label: str,
    owner_class: str,
    state: dict[str, bool],
    evidence_ref: str,
    properties: dict[str, Any],
    kind: str = "dependency",
) -> dict[str, Any]:
    node_id = stable_id(kind, {"label": label})
    return {
        "id": node_id,
        "kind": kind,
        "label": label,
        "owner": {"class": owner_class, "id": "hermes"},
        "generation_scope": context.generation_scope,
        "state": state,
        "evidence_refs": [evidence_ref],
        "properties": {"collector": "dependencies", **properties},
        "verified_at": context.collected_at,
    }


def _requires(source: str, target: str, evidence_ref: str) -> dict[str, Any]:
    return {
        "id": stable_id(
            "edge",
            {"kind": "requires", "source": source, "target": target},
        ),
        "kind": "requires",
        "from": source,
        "to": target,
        "evidence_refs": [evidence_ref],
        "properties": {"collector": "dependencies"},
    }


def _fingerprint(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> str:
    stable_nodes = [
        {key: value for key, value in node.items() if key != "verified_at"}
        for node in nodes
    ]
    encoded = json.dumps(
        {"nodes": stable_nodes, "edges": edges, "evidence": evidence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class DependencyCollector:
    name = "dependencies"

    def collect(self, context: CollectorContext) -> CollectorResult:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        root_evidence = _evidence("dependency_inventory", {"component": "hades-runtime"})
        evidence.append(root_evidence)
        root = _node(
            context,
            label="capability:hades-runtime",
            owner_class="core",
            state=_state(installed=True, active=True),
            evidence_ref=root_evidence["id"],
            properties={},
            kind="capability",
        )
        nodes.append(root)

        try:
            self._python_and_binaries(context, root["id"], nodes, edges, evidence)
            self._node_packages(context, root["id"], nodes, edges, evidence)
            self._external_services(context, root["id"], nodes, edges, evidence)
        except Exception as exc:
            return CollectorResult(
                name=self.name,
                status="partial",
                nodes=nodes,
                edges=edges,
                evidence=evidence,
                fingerprint=_fingerprint(nodes, edges, evidence),
                verified_at=None,
                error_code=safe_exception_class(exc),
            )

        return CollectorResult(
            name=self.name,
            status="current",
            nodes=nodes,
            edges=edges,
            evidence=evidence,
            fingerprint=_fingerprint(nodes, edges, evidence),
            verified_at=context.collected_at,
        )

    @staticmethod
    def _python_and_binaries(
        context: CollectorContext,
        root_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> None:
        path = context.workspace_root / "pyproject.toml"
        if not path.is_file():
            return
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        project = document.get("project") if isinstance(document.get("project"), dict) else {}
        declarations = list(project.get("dependencies") or [])
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in sorted(optional):
                values = optional[group]
                if isinstance(values, list):
                    declarations.extend(values)

        seen: set[str] = set()
        for raw in declarations:
            try:
                requirement = Requirement(str(raw))
            except InvalidRequirement:
                continue
            name = requirement.name
            if name in seen:
                continue
            seen.add(name)
            try:
                installed_version = metadata.version(name)
            except metadata.PackageNotFoundError:
                installed_version = None
            evidence_row = _evidence(
                "python_requirement",
                {"name": name, "specifier": str(requirement.specifier)},
            )
            evidence.append(evidence_row)
            node = _node(
                context,
                label=f"python:{name}",
                owner_class="third-party",
                state=_state(installed=installed_version is not None),
                evidence_ref=evidence_row["id"],
                properties={
                    "declared_specifier": str(requirement.specifier),
                    "installed_version": installed_version,
                },
            )
            nodes.append(node)
            edges.append(_requires(root_id, node["id"], evidence_row["id"]))

        scripts = project.get("scripts")
        if isinstance(scripts, dict):
            for name in sorted(scripts):
                available = shutil.which(str(name)) is not None
                evidence_row = _evidence("binary_declaration", {"name": str(name)})
                evidence.append(evidence_row)
                node = _node(
                    context,
                    label=f"binary:{name}",
                    owner_class="core",
                    state=_state(installed=available),
                    evidence_ref=evidence_row["id"],
                    properties={"available": available},
                )
                nodes.append(node)
                edges.append(_requires(root_id, node["id"], evidence_row["id"]))

    @staticmethod
    def _node_packages(
        context: CollectorContext,
        root_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> None:
        path = context.workspace_root / "package.json"
        if not path.is_file():
            return
        document = json.loads(path.read_text(encoding="utf-8"))
        declarations: dict[str, Any] = {}
        for field in ("dependencies", "optionalDependencies", "devDependencies"):
            values = document.get(field)
            if isinstance(values, dict):
                declarations.update(values)
        for name in sorted(declarations):
            if len(name) > 214 or ".." in name or not _NPM_PACKAGE_NAME.fullmatch(name):
                continue
            declared = _safe_package_specifier(declarations[name])
            installed = (context.workspace_root / "node_modules" / name).exists()
            evidence_row = _evidence(
                "node_requirement",
                {"name": name, "specifier": declared},
            )
            evidence.append(evidence_row)
            node = _node(
                context,
                label=f"node:{name}",
                owner_class="third-party",
                state=_state(installed=installed),
                evidence_ref=evidence_row["id"],
                properties={"declared_specifier": declared},
            )
            nodes.append(node)
            edges.append(_requires(root_id, node["id"], evidence_row["id"]))

    @staticmethod
    def _external_services(
        context: CollectorContext,
        root_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> None:
        loaded_config = config.load_config()
        servers = loaded_config.get("mcp_servers")
        if isinstance(servers, dict):
            for name, raw in sorted(servers.items()):
                entry = raw if isinstance(raw, dict) else {}
                if entry.get("enabled") is False:
                    continue
                label = f"service:mcp:{name}"
                evidence_row = _evidence("external_service", {"opaque_id": label})
                evidence.append(evidence_row)
                node = _node(
                    context,
                    label=label,
                    owner_class="external-service",
                    state=_state(installed=True, active=True),
                    evidence_ref=evidence_row["id"],
                    properties={"opaque_id": label},
                    kind="service",
                )
                nodes.append(node)
                edges.append(_requires(root_id, node["id"], evidence_row["id"]))

        plugins.discover_plugins()
        for row in plugins.get_plugin_manager().list_plugins():
            if row.get("kind") != "service" or not row.get("enabled"):
                continue
            name = str(row.get("name") or row.get("key") or "")
            if not name:
                continue
            label = f"service:plugin:{name}"
            evidence_row = _evidence("external_service", {"opaque_id": label})
            evidence.append(evidence_row)
            node = _node(
                context,
                label=label,
                owner_class="external-service",
                state=_state(installed=True, active=True),
                evidence_ref=evidence_row["id"],
                properties={"opaque_id": label},
                kind="service",
            )
            nodes.append(node)
            edges.append(_requires(root_id, node["id"], evidence_row["id"]))
