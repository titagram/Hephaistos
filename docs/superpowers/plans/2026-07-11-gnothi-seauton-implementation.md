# Gnothi Seauton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a read-only, evidence-backed model of the complete installed Hades organism with immutable revisions, local and backend queries, generated wiki output, structured experience evidence, and explicit freshness and coverage.

**Architecture:** Local read-only collectors emit one canonical hades.organism_graph.v1 artifact. A pipeline validates and redacts collector output, publishes immutable local revisions, optionally uploads the same artifact through the existing Hades backend channel, and exposes status, inspect, explain, diff, wiki, graph-search, and graph-traversal views. The Canonical Graph Foundation remains authoritative; Neo4j and Markdown are rebuildable projections.

**Tech Stack:** Python 3.11+, pytest, argparse, JSON/JSONL, existing Hermes registries and Hades backend client; Laravel/PHP 8.3, PostgreSQL/SQLite tests, Pest, existing Hades artifact/search/awareness APIs.

## Global Constraints

- Implement only gnothi_seauton. Do not implement Evolution Observer, /autopoiesis research, candidate generation, canary, promotion, or rollback.
- Use an isolated worktree at execution time.
- The Hades repository and /home/ubuntu/dev-sandbox backend repository require separate commits.
- The Canonical Graph Foundation plan is a prerequisite: docs/superpowers/plans/2026-07-11-canonical-graph-foundation.md.
- Use schema hades.organism_graph.v1 and organism contract hades.gnothi_seauton.v1 exactly.
- Collectors are read-only and never publish directly.
- Canonical artifacts are authoritative; local indexes, Neo4j, search documents, and wiki Markdown are rebuildable.
- Never serialize secret values, cookies, tokens, private keys, raw prompts, transcripts, arbitrary tool output, unbounded exception text, or unnecessary absolute paths.
- Stable, candidate, and historical generation scopes never mix.
- A verified capability always has a current evidence reference.
- Partial collection preserves prior valid evidence with its original verified_at; it never reports carried evidence as fresh.
- Do not add a new universal core model tool. Extend existing Hades service-gated graph/awareness tools with scope=organism.
- Do not mutate the active conversation system prompt or tool schema.
- All writes use atomic replace semantics.
- Each task follows RED -> GREEN -> focused regression -> commit.
- Run local commands from /Users/gabriele/Dev/Hephaistos.
- Run backend commands from /home/ubuntu/dev-sandbox through Docker Compose.
- Never print backend tokens or raw private artifact content.

## File Map

### Hades repository

Create:

- hermes_cli/gnothi/__init__.py — public package exports.
- hermes_cli/gnothi/contract.py — schema constants, builders, validation, stable IDs.
- hermes_cli/gnothi/redaction.py — recursive secret/path redaction.
- hermes_cli/gnothi/store.py — immutable local revisions and atomic current pointer.
- hermes_cli/gnothi/collectors/base.py — collector protocol and result contract.
- hermes_cli/gnothi/collectors/source.py — canonical source graph adapter.
- hermes_cli/gnothi/collectors/capabilities.py — tools, commands, skills, plugins, MCP.
- hermes_cli/gnothi/collectors/runtime.py — generation, effective config, service state.
- hermes_cli/gnothi/collectors/contracts.py — versioned invariants and tests.
- hermes_cli/gnothi/collectors/dependencies.py — packages, binaries, services.
- hermes_cli/gnothi/collectors/experience.py — structured event aggregation.
- hermes_cli/gnothi/events.py — bounded JSONL experience event writer.
- hermes_cli/gnothi/builder.py — collector orchestration and partial-revision semantics.
- hermes_cli/gnothi/query.py — status, inspect, explain, semantic diff.
- hermes_cli/gnothi/wiki.py — deterministic generated Markdown.
- hermes_cli/hades_gnothi_cmd.py — top-level CLI parser and command.
- agent/gnothi_prompt.py — stable slash-command prompt builder.

Modify:

- hermes_cli/main.py — register top-level gnothi-seauton command.
- hermes_cli/hades_backend_sync.py — accept and upload organism artifacts.
- hermes_cli/hades_backend_client.py — organism-scoped traversal support remains through graph_traverse payload.
- agent/tool_executor.py — emit bounded failed-tool experience events.
- plugins/memory/hades_backend/__init__.py — scope=project|organism on existing graph tools.
- hermes_cli/commands.py — register /gnothi_seauton.
- cli.py, gateway/run.py, tui_gateway/server.py — dispatch the slash command without changing toolsets.
- docs/hades/backend.md — operator documentation.

Create tests:

- tests/hermes_cli/test_gnothi_contract.py
- tests/hermes_cli/test_gnothi_redaction.py
- tests/hermes_cli/test_gnothi_store.py
- tests/hermes_cli/test_gnothi_collectors.py
- tests/hermes_cli/test_gnothi_events.py
- tests/hermes_cli/test_gnothi_builder.py
- tests/hermes_cli/test_gnothi_query.py
- tests/hermes_cli/test_gnothi_wiki.py
- tests/hermes_cli/test_hades_gnothi_cmd.py
- tests/hermes_cli/test_gnothi_e2e.py
- tests/run_agent/test_gnothi_experience.py
- tests/cli/test_gnothi_command.py
- tests/gateway/test_gnothi_command.py
- tests/tui_gateway/test_gnothi_command.py

Modify tests:

- tests/hermes_cli/test_hades_backend_sync_runner.py
- tests/agent/test_hades_backend_memory_provider.py
- tests/hermes_cli/test_commands.py
- tests/test_tui_gateway_server.py

### Backend repository

Modify:

- backend/app/Http/Controllers/Hades/ArtifactController.php
- backend/app/Http/Controllers/Hades/GraphTraversalController.php
- backend/app/Http/Controllers/Hades/CapabilitiesController.php
- backend/app/Services/Hades/HadesProjectAwareness.php
- backend/app/Services/Hades/HadesSearchDocumentIndexer.php
- backend/tests/Feature/Hades/HadesM5MvpCompletionTest.php
- backend/tests/Feature/Hades/HadesM3SharedMemoryTest.php
- backend/routes/api.php only if route naming changes; no new route is expected.

Create:

- backend/tests/Feature/Hades/HadesOrganismGraphTest.php

---

### Task 0: Canonical Graph Foundation Gate

**Files:**
- Read: docs/superpowers/plans/2026-07-11-canonical-graph-foundation.md
- Verify: hermes_cli/hades_graph_contract.py
- Verify remotely: backend/app/Services/Graph/CanonicalGraphNormalizer.php

**Interfaces:**
- Consumes: GRAPH_CONTRACT_VERSION = "hades.graph_artifact.v1"
- Produces: a proven prerequisite state; no source changes

- [ ] **Step 1: Verify the local prerequisite**

Run:

~~~bash
test -f hermes_cli/hades_graph_contract.py
python - <<'PY'
from hermes_cli.hades_graph_contract import GRAPH_CONTRACT_VERSION
assert GRAPH_CONTRACT_VERSION == "hades.graph_artifact.v1"
print("canonical-local-ready")
PY
~~~

Expected: canonical-local-ready.

- [ ] **Step 2: Verify the backend prerequisite**

Run:

~~~bash
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && test -f backend/app/Services/Graph/CanonicalGraphNormalizer.php && echo canonical-backend-ready'
~~~

Expected: canonical-backend-ready.

- [ ] **Step 3: Stop cleanly when either check fails**

If Step 1 or Step 2 fails, execute
docs/superpowers/plans/2026-07-11-canonical-graph-foundation.md completely,
including both repositories and its verification commands. Then repeat Steps 1
and 2. Do not start Task 1 against the pre-foundation graph contracts.

- [ ] **Step 4: Record the exact prerequisite commits**

Run:

~~~bash
git rev-parse HEAD
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && git rev-parse HEAD'
~~~

Copy the two hashes into the execution log. Do not create a commit for this
gate.

---

### Task 1: Organism Contract and Stable Identity

**Files:**
- Create: hermes_cli/gnothi/__init__.py
- Create: hermes_cli/gnothi/contract.py
- Test: tests/hermes_cli/test_gnothi_contract.py

**Interfaces:**
- Consumes: finalize_graph_artifact() from hermes_cli.hades_graph_contract
- Produces: ORGANISM_SCHEMA, ORGANISM_CONTRACT_VERSION, stable_id(),
  new_artifact(), add_node(), add_edge(), validate_artifact()

- [ ] **Step 1: Write contract tests**

Create tests/hermes_cli/test_gnothi_contract.py with:

~~~python
from hermes_cli.gnothi.contract import (
    ORGANISM_CONTRACT_VERSION,
    ORGANISM_SCHEMA,
    add_edge,
    add_node,
    new_artifact,
    stable_id,
    validate_artifact,
)


def test_stable_id_is_order_independent():
    assert stable_id("tool", {"name": "terminal", "owner": "core"}) == stable_id(
        "tool", {"owner": "core", "name": "terminal"}
    )


def test_artifact_requires_evidence_for_verified_capability():
    artifact = new_artifact(
        revision_id="rev-1",
        generation_id="git:abc",
        generation_scope="stable",
        head_commit="abc",
        collected_at="2026-07-11T00:00:00Z",
    )
    add_node(
        artifact,
        node_id="capability:terminal",
        kind="capability",
        label="terminal",
        owner_class="core",
        owner_id="hermes",
        state={"declared": True, "verified": True},
        evidence_refs=[],
    )
    errors = validate_artifact(artifact)
    assert "verified_without_current_evidence:capability:terminal" in errors


def test_artifact_rejects_cross_generation_edges():
    artifact = new_artifact(
        revision_id="rev-1",
        generation_id="git:abc",
        generation_scope="stable",
        head_commit="abc",
        collected_at="2026-07-11T00:00:00Z",
    )
    add_node(artifact, node_id="a", kind="component", label="a",
             owner_class="core", owner_id="hermes",
             generation_scope="stable", evidence_refs=["source:a"])
    add_node(artifact, node_id="b", kind="component", label="b",
             owner_class="core", owner_id="hermes",
             generation_scope="candidate", evidence_refs=["source:b"])
    add_edge(artifact, edge_id="e", kind="depends_on", source="a", target="b",
             evidence_refs=["source:e"])
    assert "cross_generation_edge:e" in validate_artifact(artifact)


def test_contract_versions_are_exact():
    assert ORGANISM_SCHEMA == "hades.organism_graph.v1"
    assert ORGANISM_CONTRACT_VERSION == "hades.gnothi_seauton.v1"
~~~

- [ ] **Step 2: Run RED**

Run:

~~~bash
source .venv/bin/activate
pytest -q tests/hermes_cli/test_gnothi_contract.py
~~~

Expected: import failure for hermes_cli.gnothi.

- [ ] **Step 3: Implement the contract**

Create hermes_cli/gnothi/contract.py with these public shapes:

~~~python
from __future__ import annotations

import hashlib
import json
from typing import Any

ORGANISM_SCHEMA = "hades.organism_graph.v1"
ORGANISM_CONTRACT_VERSION = "hades.gnothi_seauton.v1"
GENERATION_SCOPES = {"stable", "candidate", "historical"}
CAPABILITY_STATE_KEYS = {
    "declared", "installed", "available", "active",
    "verified", "degraded", "candidate",
}


def stable_id(kind: str, identity: dict[str, Any]) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{kind}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def new_artifact(*, revision_id: str, generation_id: str,
                 generation_scope: str, head_commit: str | None,
                 collected_at: str) -> dict[str, Any]:
    if generation_scope not in GENERATION_SCOPES:
        raise ValueError(f"unsupported generation scope: {generation_scope}")
    return {
        "schema": ORGANISM_SCHEMA,
        "organism_contract": {
            "version": ORGANISM_CONTRACT_VERSION,
            "revision_id": revision_id,
            "generation": {"id": generation_id, "scope": generation_scope},
            "source": {"head_commit": head_commit},
            "collected_at": collected_at,
            "status": "building",
            "coverage": {},
        },
        "nodes": [],
        "edges": [],
        "redactions": 0,
        "truncated": False,
        "raw_source_included": False,
        "retention_class": "organism_metadata",
    }


def add_node(artifact: dict[str, Any], *, node_id: str, kind: str, label: str,
             owner_class: str, owner_id: str,
             generation_scope: str | None = None,
             state: dict[str, bool] | None = None,
             evidence_refs: list[str] | None = None,
             properties: dict[str, Any] | None = None,
             verified_at: str | None = None) -> None:
    scope = generation_scope or artifact["organism_contract"]["generation"]["scope"]
    artifact["nodes"].append({
        "id": node_id, "kind": kind, "label": label,
        "owner": {"class": owner_class, "id": owner_id},
        "generation_scope": scope,
        "state": state or {},
        "evidence_refs": evidence_refs or [],
        "properties": properties or {},
        "verified_at": verified_at,
    })


def add_edge(artifact: dict[str, Any], *, edge_id: str, kind: str,
             source: str, target: str,
             evidence_refs: list[str] | None = None,
             properties: dict[str, Any] | None = None) -> None:
    artifact["edges"].append({
        "id": edge_id, "kind": kind, "from": source, "to": target,
        "evidence_refs": evidence_refs or [],
        "properties": properties or {},
    })


def validate_artifact(artifact: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if artifact.get("schema") != ORGANISM_SCHEMA:
        errors.append("invalid_schema")
    contract = artifact.get("organism_contract")
    if not isinstance(contract, dict) or contract.get("version") != ORGANISM_CONTRACT_VERSION:
        errors.append("invalid_organism_contract")
    nodes = {str(row.get("id")): row for row in artifact.get("nodes", []) if isinstance(row, dict)}
    if len(nodes) != len(artifact.get("nodes", [])):
        errors.append("duplicate_or_invalid_node_id")
    for node_id, node in nodes.items():
        state = node.get("state") if isinstance(node.get("state"), dict) else {}
        evidence = node.get("evidence_refs") if isinstance(node.get("evidence_refs"), list) else []
        if state.get("verified") is True and (not evidence or not node.get("verified_at")):
            errors.append(f"verified_without_current_evidence:{node_id}")
    for edge in artifact.get("edges", []):
        if not isinstance(edge, dict):
            errors.append("invalid_edge")
            continue
        edge_id = str(edge.get("id") or "")
        source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
        if source not in nodes or target not in nodes:
            errors.append(f"dangling_edge:{edge_id}")
            continue
        if nodes[source].get("generation_scope") != nodes[target].get("generation_scope"):
            errors.append(f"cross_generation_edge:{edge_id}")
    return errors
~~~

Export the six public names from hermes_cli/gnothi/__init__.py.

- [ ] **Step 4: Run GREEN**

Run:

~~~bash
pytest -q tests/hermes_cli/test_gnothi_contract.py
ruff check hermes_cli/gnothi tests/hermes_cli/test_gnothi_contract.py
~~~

Expected: all pass.

- [ ] **Step 5: Commit**

~~~bash
git add hermes_cli/gnothi tests/hermes_cli/test_gnothi_contract.py
git commit -m "feat(gnothi): define organism graph contract"
~~~

---

### Task 2: Recursive Redaction Boundary

**Files:**
- Create: hermes_cli/gnothi/redaction.py
- Test: tests/hermes_cli/test_gnothi_redaction.py

**Interfaces:**
- Produces: redact_value(value, workspace_root=None) -> tuple[value, count]
- Produces: safe_exception_class(exc) -> str

- [ ] **Step 1: Write failing tests**

~~~python
from pathlib import Path

from hermes_cli.gnothi.redaction import redact_value, safe_exception_class


def test_redacts_secret_keys_and_workspace_paths(tmp_path: Path):
    value = {
        "api_key": "sk-private",
        "nested": {"cookie": "session=private"},
        "path": str(tmp_path / "agent" / "tool.py"),
        "message": "safe",
    }
    redacted, count = redact_value(value, workspace_root=tmp_path)
    assert redacted == {
        "api_key": "[REDACTED]",
        "nested": {"cookie": "[REDACTED]"},
        "path": "agent/tool.py",
        "message": "safe",
    }
    assert count == 3
    assert "private" not in str(redacted)


def test_bounds_untrusted_strings():
    redacted, count = redact_value({"message": "x" * 5000})
    assert len(redacted["message"]) == 1000
    assert count == 1


def test_exception_exposes_class_only():
    assert safe_exception_class(RuntimeError("/private/path token=secret")) == "RuntimeError"
~~~

- [ ] **Step 2: Run RED**

Run pytest on the file. Expected: missing module.

- [ ] **Step 3: Implement exact policy**

Create SECRET_KEY_PATTERN matching token, secret, password, passwd, api_key,
authorization, cookie, private_key, credential, and bearer, case-insensitively.
Implement recursive handling for dict, list, tuple, scalar, and Path. Convert
workspace-contained paths to POSIX-relative paths; replace other absolute paths
with [ABSOLUTE_PATH]. Bound every string to 1000 characters. Count each
replacement or truncation.

Do not import credential values or inspect .env.

- [ ] **Step 4: Run GREEN and contract regression**

~~~bash
pytest -q tests/hermes_cli/test_gnothi_redaction.py tests/hermes_cli/test_gnothi_contract.py
ruff check hermes_cli/gnothi/redaction.py tests/hermes_cli/test_gnothi_redaction.py
~~~

- [ ] **Step 5: Commit**

~~~bash
git add hermes_cli/gnothi/redaction.py tests/hermes_cli/test_gnothi_redaction.py
git commit -m "feat(gnothi): enforce organism redaction boundary"
~~~

---

### Task 3: Immutable Local Revision Store

**Files:**
- Create: hermes_cli/gnothi/store.py
- Test: tests/hermes_cli/test_gnothi_store.py

**Interfaces:**
- Produces: OrganismRevisionStore(root: Path | None = None)
- Methods: publish(), current(), get(), list_revisions(), previous_healthy()

- [ ] **Step 1: Write failing tests**

Test that publish writes revisions/<revision_id>.json, refuses an existing ID
with different bytes, atomically updates current.json, returns revisions newest
first, and previous_healthy ignores partial revisions.

Use monkeypatch to set HERMES_HOME to tmp_path before constructing the store.

- [ ] **Step 2: Run RED**

Expected: OrganismRevisionStore import failure.

- [ ] **Step 3: Implement the store**

Use get_hermes_home() / "gnothi_seauton" by default. Store compact,
sort_keys=True JSON. Compute sha256 from the exact encoded bytes. The pointer is:

~~~python
{
    "schema": "hades.gnothi_pointer.v1",
    "revision_id": revision_id,
    "sha256": digest,
    "published_at": published_at,
}
~~~

Write both revision and pointer through utils.atomic_replace. chmod files 0600
best-effort. Validate the artifact before any write. A byte-identical repeat is
idempotent; a conflicting repeat raises ValueError.

- [ ] **Step 4: Run GREEN**

Run the store, contract, and redaction tests plus ruff.

- [ ] **Step 5: Commit**

Commit store and test with message:

~~~text
feat(gnothi): persist immutable organism revisions
~~~

---

### Task 4: Collector Protocol and Source Collector

**Files:**
- Create: hermes_cli/gnothi/collectors/__init__.py
- Create: hermes_cli/gnothi/collectors/base.py
- Create: hermes_cli/gnothi/collectors/source.py
- Test: tests/hermes_cli/test_gnothi_collectors.py

**Interfaces:**
- Produces: CollectorContext, CollectorResult, Collector protocol
- Produces: SourceCollector.collect(context) -> CollectorResult
- Consumes: execute_job() for sync_git_tree and populate_backend_ast

- [ ] **Step 1: Write protocol and source tests**

Use a tiny temporary Python workspace. Assert:

- CollectorResult has name, status, nodes, edges, evidence, fingerprint,
  verified_at, error_code.
- SourceCollector emits a workspace component, source-file nodes, symbol nodes,
  contains edges, and evidence refs.
- Absolute temp paths never appear.
- Running twice without changes produces the same fingerprint.
- A parser failure returns status=partial and error_code equal to the exception
  class name, without its message.

- [ ] **Step 2: Run RED**

Expected: collectors.base import failure.

- [ ] **Step 3: Implement base types**

~~~python
@dataclass(frozen=True)
class CollectorContext:
    workspace_root: Path
    generation_id: str
    generation_scope: str
    head_commit: str | None
    collected_at: str
    previous_artifact: dict[str, Any] | None = None


@dataclass
class CollectorResult:
    name: str
    status: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    fingerprint: str
    verified_at: str | None
    error_code: str | None = None


class Collector(Protocol):
    name: str
    def collect(self, context: CollectorContext) -> CollectorResult: ...
~~~

Allow statuses current, partial, missing, stale.

- [ ] **Step 4: Implement SourceCollector**

Call execute_job({"capability": "sync_git_tree", ...}) and
execute_job({"capability": "populate_backend_ast", ...}) with bounded defaults:
max_files=10_000, max_bytes=2_000_000, max_symbols=5_000, max_edges=10_000.
Adapt canonical graph nodes and relationships without reparsing them.

Use source evidence IDs based on schema, relative path, checksum, and head
commit. Never include raw source. Catch exceptions at the collector boundary
and use safe_exception_class().

- [ ] **Step 5: Run GREEN**

~~~bash
pytest -q tests/hermes_cli/test_gnothi_collectors.py -k source
ruff check hermes_cli/gnothi/collectors tests/hermes_cli/test_gnothi_collectors.py
~~~

- [ ] **Step 6: Commit**

~~~bash
git add hermes_cli/gnothi/collectors tests/hermes_cli/test_gnothi_collectors.py
git commit -m "feat(gnothi): collect canonical source anatomy"
~~~

---

### Task 5: Capability Collector

**Files:**
- Create: hermes_cli/gnothi/collectors/capabilities.py
- Modify: tests/hermes_cli/test_gnothi_collectors.py

**Interfaces:**
- Produces: CapabilityCollector.collect(context) -> CollectorResult
- Reads: tools.registry.registry, COMMAND_REGISTRY, skill roots,
  PluginManager.list_plugins(), config.mcp_servers

- [ ] **Step 1: Add failing fixture tests**

Create isolated fixtures for:

- one registered tool with toolset and availability check;
- one CommandDef;
- one local skill with requires_tools metadata;
- one enabled and one disabled plugin info row;
- one enabled and one disabled MCP configuration.

Assert provider nodes, capability nodes, provides and requires edges, ownership
classes, and state dimensions. Assert config values named env, token, headers,
or api_key never appear.

- [ ] **Step 2: Run RED**

Expected: CapabilityCollector import failure.

- [ ] **Step 3: Implement registry adapters**

Call tools.registry.discover_builtin_tools() before reading registry. Use:

- registry.get_all_tool_names()
- registry.get_toolset_for_tool()
- registry.check_toolset_requirements()
- COMMAND_REGISTRY
- agent.skill_utils.get_all_skills_dirs() and parse_frontmatter()
- hermes_cli.plugins.discover_plugins() and get_plugin_manager().list_plugins()
- hermes_cli.config.load_config()["mcp_servers"]

Read SKILL.md only for frontmatter and relative package inventory. Do not embed
the body. Derive ownership from bundled root, local HERMES_HOME root, external
skill dirs, plugin source, and pip entry-point source.

- [ ] **Step 4: Implement capability state**

For each provider and capability set booleans independently. In particular:

- tool declared=true, installed=true, available=toolset check,
  active=registered;
- command declared/installed/available/active=true;
- skill installed=true and active only when platform/config conditions pass;
- plugin installed=true and active=enabled with degraded=true when error exists;
- MCP installed=true, active=enabled, available=false until a live MCP tool is
  registered or its configured health is known.

Set verified=false unless a collector has a current probe evidence ref.

- [ ] **Step 5: Run GREEN**

Run all collector tests and the existing registry/plugin/skill discovery tests:

~~~bash
pytest -q tests/hermes_cli/test_gnothi_collectors.py tests/tools/test_registry.py tests/hermes_cli/test_plugins.py
~~~

The focused existing plugin regression file is
tests/hermes_cli/test_plugins.py.

- [ ] **Step 6: Commit**

Commit with message feat(gnothi): inventory installed capabilities.

---

### Task 6: Runtime and Dependency Collectors

**Files:**
- Create: hermes_cli/gnothi/collectors/runtime.py
- Create: hermes_cli/gnothi/collectors/dependencies.py
- Modify: tests/hermes_cli/test_gnothi_collectors.py

**Interfaces:**
- Produces: RuntimeCollector.collect(), DependencyCollector.collect()
- Runtime output never includes secret values.

- [ ] **Step 1: Add failing tests**

Assert RuntimeCollector emits Python/platform/Hades generation, active profile,
effective config key paths, backend configured state, and current process.
Assert it never emits config values for secret-shaped keys.

Assert DependencyCollector reads pyproject.toml/package.json requirements,
uses importlib.metadata.version() for Python packages and shutil.which() for
declared binaries, and creates requires edges to capabilities.

- [ ] **Step 2: Run RED**

Expected: missing runtime/dependencies modules.

- [ ] **Step 3: Implement RuntimeCollector**

Generation ID is git:<HEAD> when git rev-parse succeeds, otherwise
release:<hermes-version>. Emit config key paths and non-secret scalar values
only after redact_value(). Do not enumerate environment variables. Reuse
hades_backend_status.load_backend_status_payload() but retain only configured,
degraded, awareness.status, and binding counts.

- [ ] **Step 4: Implement DependencyCollector**

Parse project dependency declarations without installing or importing packages.
Probe only names already declared. Binary probes use shutil.which and store a
boolean, never the returned absolute path. External services are declared from
enabled plugins/MCP config and store hostless opaque IDs, not URLs containing
credentials or query strings.

- [ ] **Step 5: Run GREEN and commit**

Run collector tests and ruff. Commit both collectors together because runtime
availability and dependency availability form one reviewable capability slice.

---

### Task 7: Contract Collector

**Files:**
- Create: hermes_cli/gnothi/collectors/contracts.py
- Create: hermes_cli/gnothi/invariants.yaml
- Modify: tests/hermes_cli/test_gnothi_collectors.py

**Interfaces:**
- Produces: ContractCollector.collect()
- Produces versioned invariant IDs and protected_by edges.

- [ ] **Step 1: Add failing tests**

Assert the collector emits these exact invariant IDs:

- invariant:prompt-cache-stability
- invariant:message-role-alternation
- invariant:approval-boundaries
- invariant:profile-isolation
- invariant:artifact-backward-compatibility
- invariant:no-secret-artifacts

Each invariant must have at least one evidence ref and at least one protecting
test or versioned document edge.

- [ ] **Step 2: Run RED**

Expected: missing module or invariant manifest.

- [ ] **Step 3: Create the manifest**

Store id, title, description, and evidence_globs. Evidence globs must resolve to
real files. Include AGENTS.md and focused tests already present in the checkout.
Do not encode line numbers because they drift; evidence stores file checksum.

- [ ] **Step 4: Implement ContractCollector**

Load the bundled manifest with importlib.resources. Expand globs relative to the
repo, reject paths leaving the repo, checksum matched files, and emit partial
status when an invariant has no evidence. Do not parse prose into new
invariants.

- [ ] **Step 5: Run GREEN and commit**

Run collector tests and ruff. Commit manifest, collector, and tests.

---

### Task 8: Structured Experience Events

**Files:**
- Create: hermes_cli/gnothi/events.py
- Create: hermes_cli/gnothi/collectors/experience.py
- Modify: agent/tool_executor.py
- Create: tests/hermes_cli/test_gnothi_events.py
- Create: tests/run_agent/test_gnothi_experience.py

**Interfaces:**
- Produces: emit_experience_event(...)
- Produces: ExperienceCollector.collect()
- File: HERMES_HOME/logs/organism-events.jsonl

- [ ] **Step 1: Write event writer tests**

Assert one JSON object per line, chmod 0600 best-effort, bounded_signature does
not contain raw result text, event IDs are deterministic for identical bounded
fields, and writes from 20 threads produce 20 valid lines.

- [ ] **Step 2: Run RED**

Expected: missing events module.

- [ ] **Step 3: Implement event writer**

Signature:

~~~python
def emit_experience_event(
    *,
    event_type: str,
    generation_id: str,
    component_id: str,
    capability_id: str | None,
    operation: str,
    failure_class: str | None,
    severity: str,
    retry_count: int = 0,
    task_impact: str = "unknown",
    recovered: bool = False,
    evidence_refs: list[str] | None = None,
    occurred_at: str | None = None,
) -> None:
~~~

Use a process-local Lock, append one compact JSON line, fsync after write, and
store only allowed fields. bounded_signature is sha256 of generation,
component, capability, operation, and failure_class. Do not accept a message
parameter.

- [ ] **Step 4: Add failed-tool integration tests**

Patch emit_experience_event and execute one sequential and one concurrent tool
failure. Assert both paths emit event_type=tool.failed,
component_id=tool:<name>, capability_id=capability:<name>, and failure_class
equal to the detected error class/category, never the result preview.

- [ ] **Step 5: Integrate both executor paths**

Call the event writer immediately after _detect_tool_failure() returns true in
both concurrent and sequential paths. Wrap emission in try/except and log only
the emitter exception class at DEBUG so observability cannot break tool
execution.

- [ ] **Step 6: Implement ExperienceCollector**

Read at most the newest 10,000 lines and 8 MiB. Reject malformed lines. Aggregate
by bounded_signature with count, first_seen, last_seen, severity, recovered,
generation, component, and capability. Emit observation nodes and observed_on
edges. Set partial when malformed/truncated input exists.

- [ ] **Step 7: Run GREEN and regressions**

~~~bash
pytest -q tests/hermes_cli/test_gnothi_events.py tests/run_agent/test_gnothi_experience.py tests/run_agent/test_background_review.py
ruff check hermes_cli/gnothi/events.py hermes_cli/gnothi/collectors/experience.py agent/tool_executor.py
~~~

- [ ] **Step 8: Commit**

Commit event writer, collector, executor integration, and tests.

---

### Task 9: Builder, Partial Revisions, and Fingerprints

**Files:**
- Create: hermes_cli/gnothi/builder.py
- Test: tests/hermes_cli/test_gnothi_builder.py

**Interfaces:**
- Produces: build_organism_revision(workspace_root, generation_scope="stable",
  collectors=None, store=None, now=None) -> dict
- Consumes all collectors and OrganismRevisionStore.

- [ ] **Step 1: Write failing pipeline tests**

Use fake collectors to assert:

- deterministic collector order;
- one failing collector does not block healthy collectors;
- coverage contains status, fingerprint, verified_at, and error_code;
- previous healthy nodes for a failed domain carry forward with original
  verified_at and properties.carried_forward=true;
- artifact status is current only when every required collector is current;
- validation errors block publication;
- unchanged organism produces the same semantic fingerprint but a new revision
  only when --force is requested.

- [ ] **Step 2: Run RED**

Expected: missing builder module.

- [ ] **Step 3: Implement collector orchestration**

Default order is source, capabilities, runtime, contracts, dependencies,
experience. Normalize and redact every CollectorResult before adding it. Build
revision_id as rev:<UTC compact timestamp>:<semantic hash prefix>. Put semantic
hash in organism_contract.semantic_fingerprint.

Required collectors are source, capabilities, runtime, and contracts.
Dependency and experience may be missing without making the model unusable,
but status remains partial.

- [ ] **Step 4: Implement carry-forward**

Use previous_healthy(). Copy only nodes whose properties.collector matches the
failed collector. Preserve evidence_refs and verified_at. Add
properties.carried_forward=true and properties.carried_from_revision=<id>.
Never copy candidate nodes into stable scope.

- [ ] **Step 5: Implement unchanged behavior**

If semantic fingerprint equals current revision, return the current artifact
with build_result=unchanged and do not publish. force=True publishes a new
revision for operational audit.

- [ ] **Step 6: Run GREEN and commit**

Run builder plus all gnothi unit tests and ruff. Commit with message
feat(gnothi): build evidence-backed organism revisions.

---

### Task 10: Status, Inspect, Explain, and Semantic Diff

**Files:**
- Create: hermes_cli/gnothi/query.py
- Test: tests/hermes_cli/test_gnothi_query.py

**Interfaces:**
- Produces: OrganismQuery(store)
- Methods: status(), inspect(component), explain(capability), diff(a, b)

- [ ] **Step 1: Write failing tests**

Build two small artifacts. Assert:

- status returns revision, generation, overall status, coverage, counts,
  unknown_domains, and actions;
- inspect exact ID wins, then case-insensitive label match, with ambiguity
  returned explicitly;
- explain follows provides/requires/depends_on edges and includes blockers;
- diff reports added/removed capabilities, changed state dimensions,
  dependency changes, invariant impact, runtime changes, and quality changes;
- all responses are bounded to 200 nodes/edges and report truncated.

- [ ] **Step 2: Run RED**

Expected: missing query module.

- [ ] **Step 3: Implement indexed queries**

Build nodes_by_id, outgoing, and incoming once per artifact. Do not use model
inference. explain performs bounded BFS depth 4. A state is blocked when any
required provider/dependency has available=false or degraded=true. Unknown
evidence remains unknown.

- [ ] **Step 4: Run GREEN and commit**

Run query, builder, contract tests and ruff. Commit query and tests.

---

### Task 11: Deterministic Generated Wiki

**Files:**
- Create: hermes_cli/gnothi/wiki.py
- Test: tests/hermes_cli/test_gnothi_wiki.py

**Interfaces:**
- Produces: render_wiki(artifact) -> str

- [ ] **Step 1: Write failing golden-invariant tests**

Do not snapshot the whole document. Assert section order, stable sorting,
evidence anchors for every rendered entity, explicit Partial/Unknown coverage,
absence of secret fixture text, and byte-identical output for input lists in a
different order.

- [ ] **Step 2: Run RED**

Expected: missing wiki module.

- [ ] **Step 3: Implement renderer**

Render exactly:

- Anatomy
- Capabilities
- Dependencies
- Contracts and invariants
- Runtime state
- Known degradation
- Generations and rollback history
- Coverage, freshness, and unknown areas
- Evidence index

Use Markdown tables capped at 200 rows per section. State omitted counts. Add a
header saying the page is generated and manual edits are discarded. Never
write the wiki from this function.

- [ ] **Step 4: Run GREEN and commit**

Run wiki/query/redaction tests and ruff. Commit renderer and tests.

---

### Task 12: Top-Level CLI Surface

**Files:**
- Create: hermes_cli/hades_gnothi_cmd.py
- Modify: hermes_cli/main.py
- Create: tests/hermes_cli/test_hades_gnothi_cmd.py

**Interfaces:**
- Produces: build_gnothi_parser(subparsers, cmd_gnothi)
- Produces: gnothi_command(args) -> int

- [ ] **Step 1: Write parser and command tests**

Test exact commands:

- hades gnothi-seauton status [--json]
- hades gnothi-seauton rebuild [--json] [--force] [--workspace PATH]
- hades gnothi-seauton inspect COMPONENT [--json]
- hades gnothi-seauton explain CAPABILITY [--json]
- hades gnothi-seauton diff REVISION_A REVISION_B [--json]
- hades gnothi-seauton wiki [--output PATH]

Assert status exits 1 with actionable text when no revision exists; rebuild
exits 0 for current/partial but exits 1 on invalid publication; wiki prints to
stdout unless --output is present.

- [ ] **Step 2: Run RED**

Expected: parser does not recognize gnothi-seauton.

- [ ] **Step 3: Implement command module**

Keep argparse and output formatting in this module. Put all data work in
builder/query/wiki. For --output, reject a directory, create parents only below
an already existing parent chosen by the user, and write through atomic_replace.
Return JSON with sort_keys=True.

- [ ] **Step 4: Register in main.py**

Add cmd_gnothi next to cmd_backend and call build_gnothi_parser next to
build_backend_parser. Do not add an env-var entry point.

- [ ] **Step 5: Run GREEN and CLI smoke**

~~~bash
pytest -q tests/hermes_cli/test_hades_gnothi_cmd.py
HERMES_HOME="$(mktemp -d)" python -m hermes_cli.main gnothi-seauton status --json
~~~

Expected: tests pass; smoke exits 1 with a bounded missing-revision JSON result.

- [ ] **Step 6: Commit**

Commit CLI module, main registration, and tests.

---

### Task 13: Local Artifact Upload and Deduplication

**Files:**
- Modify: hermes_cli/hades_backend_sync.py
- Modify: tests/hermes_cli/test_hades_backend_sync_runner.py

**Interfaces:**
- Consumes: hades.organism_graph.v1 from current local revision
- Produces: existing _upload_job_artifact path accepts organism artifact

- [ ] **Step 1: Add failing upload tests**

Assert organism schema uploads, artifact lookup uses its checksum, unchanged
artifact skips, changed semantic fingerprint uploads, and file manifest derives
paths from node.properties.path without absolute paths.

- [ ] **Step 2: Run RED**

Expected: _upload_job_artifact returns zero uploads because schema is not
allowlisted.

- [ ] **Step 3: Extend the existing allowlist**

Add hades.organism_graph.v1 to the accepted schema set. Extend
_artifact_file_manifest() to inspect nodes and relationships without removing
existing files/routes/symbols/edges handling.

Add a helper that loads the current OrganismRevisionStore revision during
run_backend_sync for the current mapped workspace and passes it through the
same upload path with job_id=None. Do not build a revision implicitly during
sync.

- [ ] **Step 4: Run GREEN and regressions**

~~~bash
pytest -q tests/hermes_cli/test_hades_backend_sync_runner.py tests/hermes_cli/test_gnothi_store.py
ruff check hermes_cli/hades_backend_sync.py
~~~

- [ ] **Step 5: Commit**

Commit with message feat(hades): sync organism graph artifacts.

---

### Task 14: Backend Artifact, Awareness, Search, and Traversal

**Repository:** /home/ubuntu/dev-sandbox

**Files:**
- Modify: backend/app/Http/Controllers/Hades/ArtifactController.php
- Modify: backend/app/Http/Controllers/Hades/GraphTraversalController.php
- Modify: backend/app/Http/Controllers/Hades/CapabilitiesController.php
- Modify: backend/app/Services/Hades/HadesProjectAwareness.php
- Modify: backend/app/Services/Hades/HadesSearchDocumentIndexer.php
- Create: backend/tests/Feature/Hades/HadesOrganismGraphTest.php
- Modify: backend/tests/Feature/Hades/HadesM5MvpCompletionTest.php
- Modify: backend/tests/Feature/Hades/HadesM3SharedMemoryTest.php

**Interfaces:**
- Accepts schema hades.organism_graph.v1
- Adds graph traversal scope=project|organism, default project
- Adds coverage.organism_graph
- Preserves every existing response when scope is omitted

- [ ] **Step 1: Write artifact acceptance tests**

Post an authenticated organism artifact containing two nodes and one edge.
Assert 201, stored schema, dedup lookup, materialized search document, and no
source-slice candidates created.

Post invalid organism contract, dangling edge, and verified capability without
verified_at/evidence. Assert 422 with bounded error codes.

- [ ] **Step 2: Run RED**

~~~bash
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && docker compose -f docker-compose.devboard.yaml exec -T app sh -lc "APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesOrganismGraphTest.php"'
~~~

Expected: schema validation rejects the request.

- [ ] **Step 3: Extend ArtifactController**

Add schema to lookup/store Rule lists. Add private
validateOrganismArtifact(array $artifact): ?JsonResponse called only for the
organism schema. Validate exact contract version, unique node IDs, valid
generation scopes, resolvable edges, and verified evidence. Do not accept
arbitrary source content fields.

Skip HadesSourceSliceCandidateService for organism artifacts because bounded
source slice candidates come from code graph artifacts.

- [ ] **Step 4: Add organism awareness coverage tests**

Assert coverage.organism_graph reports missing, current, partial, and stale.
Current requires schema, contract status=current, matching head commit, and all
required collectors current.

- [ ] **Step 5: Implement HadesProjectAwareness.organismGraphCoverage()**

Select the newest organism artifact for project and binding. Return status,
count, revision_id, generation, semantic_fingerprint, updated_at, collector
coverage, and stale_reason. Add it to coverage without changing
diagnosable_without_source, which remains a bug-diagnosis gate.

- [ ] **Step 6: Add traversal scope tests**

Call /api/hades/v1/graph/traverse with scope=organism and assert organism
artifact selection, canonical nodes/edges, generation provenance, and bounded
BFS. Assert omitted scope still selects only code graph schemas.

- [ ] **Step 7: Implement scope selection**

Validate scope with Rule::in(["project", "organism"]). Select
hades.organism_graph.v1 only for organism; retain current graph schemas for
project. Include scope in response/version/provenance. The existing normalizer
must accept canonical nodes/relationships; adapt organism edges from
from/to/kind without a new traversal engine.

- [ ] **Step 8: Bound search indexing**

For organism artifacts, HadesSearchDocumentIndexer indexes labels, kinds,
owner IDs, state keys, dependency edge kinds, revision, generation, and
coverage. It must not dump the complete JSON body. Cap at 200,000 characters.

- [ ] **Step 9: Advertise the scope**

In CapabilitiesController keep graph_traverse route unchanged and add:

~~~php
'graph_scopes' => ['project', 'organism'],
'organism_graph_schema' => 'hades.organism_graph.v1',
~~~

- [ ] **Step 10: Run GREEN and backend regressions**

~~~bash
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && docker compose -f docker-compose.devboard.yaml exec -T app sh -lc "APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesOrganismGraphTest.php tests/Feature/Hades/HadesM5MvpCompletionTest.php tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/PluginAuthTest.php"'
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && docker compose -f docker-compose.devboard.yaml exec -T app vendor/bin/pint app/Http/Controllers/Hades/ArtifactController.php app/Http/Controllers/Hades/GraphTraversalController.php app/Http/Controllers/Hades/CapabilitiesController.php app/Services/Hades/HadesProjectAwareness.php app/Services/Hades/HadesSearchDocumentIndexer.php tests/Feature/Hades/HadesOrganismGraphTest.php'
~~~

- [ ] **Step 11: Commit backend work**

~~~bash
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && git add backend/app backend/tests/Feature/Hades && git commit -m "feat(hades): store and query organism graphs"'
~~~

Record the backend commit in docs/backend-agent-coordination.md during Task 18.

---

### Task 15: Organism Scope in Existing Agent Tools

**Files:**
- Modify: plugins/memory/hades_backend/__init__.py
- Modify: tests/agent/test_hades_backend_memory_provider.py

**Interfaces:**
- GRAPH_SEARCH_TOOL_SCHEMA gains scope enum project|organism
- GRAPH_TRAVERSE_TOOL_SCHEMA gains the same enum
- Existing calls default to project

- [ ] **Step 1: Add failing schema and dispatch tests**

Assert schemas expose scope with default project. Assert backend traversal sends
scope=organism. Assert local fallback reads the current OrganismRevisionStore
artifact when organism is selected. Assert project behavior remains byte-for-
byte compatible in normalized results.

- [ ] **Step 2: Run RED**

Expected: additionalProperties rejects scope or backend call omits it.

- [ ] **Step 3: Implement scoped dispatch**

Add GRAPH_SCOPES = ("project", "organism"). Validate before backend calls.
Pass scope through HadesBackendClient.graph_traverse(). For graph search,
restrict backend memory search to schema hades.organism_graph.v1 when organism.
For local search/traversal, use OrganismRevisionStore.current() rather than the
code-graph cache.

Keep the same tool names and descriptions. This changes neither tool count nor
mid-conversation schemas; updated schemas apply only when a new agent session
is built.

- [ ] **Step 4: Run GREEN and cache regressions**

~~~bash
pytest -q tests/agent/test_hades_backend_memory_provider.py -k "graph_search or graph_traverse or tool_schema"
pytest -q tests/run_agent/test_background_review_cache_parity.py
ruff check plugins/memory/hades_backend/__init__.py
~~~

- [ ] **Step 5: Commit**

Commit provider and tests with message feat(hades): query organism graph scope.

---

### Task 16: Slash Command Across CLI, Gateway, and TUI

**Files:**
- Create: agent/gnothi_prompt.py
- Modify: hermes_cli/commands.py
- Modify: cli.py
- Modify: gateway/run.py
- Modify: tui_gateway/server.py
- Modify: tests/hermes_cli/test_commands.py
- Create: tests/cli/test_gnothi_command.py
- Create: tests/gateway/test_gnothi_command.py
- Create: tests/tui_gateway/test_gnothi_command.py
- Modify: tests/test_tui_gateway_server.py

**Interfaces:**
- Command: /gnothi_seauton [status|inspect X|explain X|diff A B|wiki]
- Produces a stable normal-turn prompt; it does not inject a system message.

- [ ] **Step 1: Write prompt-builder tests**

Assert a bare command requests status plus a concise self-summary. Assert
subcommands map to exact CLI semantics and instruct the agent to use existing
Hades graph tools with scope=organism. Assert no request authorizes mutation,
research, download, installation, or autopoiesis.

- [ ] **Step 2: Implement agent/gnothi_prompt.py**

Expose build_gnothi_prompt(user_request: str) -> str. Keep the fixed prefix
byte-stable. It must say that gnothi_seauton is read-only, evidence-backed, and
must report stale/partial/unknown coverage.

- [ ] **Step 3: Register the command**

Add:

~~~python
CommandDef(
    "gnothi_seauton",
    "Inspect Hades's evidence-backed self model",
    "Info",
    aliases=("know-thyself",),
    args_hint="[status|inspect|explain|diff|wiki]",
)
~~~

Do not make it cli_only: extensions must work on gateways.

- [ ] **Step 4: Wire classic CLI**

Mirror /learn behavior: _handle_gnothi_command extracts args, builds the prompt,
and queues it as a normal user turn. Do not run a nested agent.

- [ ] **Step 5: Wire gateway**

Mirror /learn fall-through: optionally acknowledge, replace event.text with
build_gnothi_prompt(args), and continue normal processing. Do not return early
after rewriting.

- [ ] **Step 6: Wire TUI/desktop gateway**

In command.dispatch return {"type": "send", "message": build_gnothi_prompt(arg)}.
The desktop extension-command allow path must remain unchanged.

- [ ] **Step 7: Run command regressions**

Run the exact registry, classic CLI, gateway, and TUI command tests:

~~~bash
pytest -q \
  tests/hermes_cli/test_commands.py \
  tests/cli/test_gnothi_command.py \
  tests/gateway/test_gnothi_command.py \
  tests/tui_gateway/test_gnothi_command.py \
  tests/test_tui_gateway_server.py \
  tests/run_agent/test_background_review_cache_parity.py
~~~

- [ ] **Step 8: Commit**

Commit prompt builder, command registration, all three dispatchers, and tests.

---

### Task 17: Drift Detection and Targeted Refresh

**Files:**
- Modify: hermes_cli/gnothi/builder.py
- Modify: hermes_cli/gnothi/store.py
- Modify: hermes_cli/hades_gnothi_cmd.py
- Modify: tests/hermes_cli/test_gnothi_builder.py
- Modify: tests/hermes_cli/test_hades_gnothi_cmd.py

**Interfaces:**
- Produces: drift_status(workspace_root, current) -> dict
- Rebuild accepts --collector NAME repeatedly
- Status reports invalidated domains and actions

- [ ] **Step 1: Add failing drift tests**

Build a revision, then change exactly one of: source file, skill manifest,
plugin manifest, MCP config, dependency manifest, structured event log. Assert
only the corresponding collector fingerprint is invalidated. Assert unrelated
domains remain current.

- [ ] **Step 2: Run RED**

Expected: status has no drift data.

- [ ] **Step 3: Implement cheap fingerprints**

Each collector exposes probe_fingerprint(context) that reads only metadata or
small manifests, never builds full graphs. builder.drift_status compares probes
to coverage fingerprints and returns current/stale/unknown per collector.

- [ ] **Step 4: Implement targeted rebuild**

--collector may name source, capabilities, runtime, contracts, dependencies, or
experience. The builder runs selected collectors and carries every unselected
domain from current with unchanged freshness and carried_forward=false. Reject
unknown collector names before doing work.

- [ ] **Step 5: Run GREEN and equivalence regression**

Test that a sequence of targeted rebuilds converges semantically with a full
rebuild after the same changes.

- [ ] **Step 6: Commit**

Commit drift, targeted refresh, CLI flags, and tests.

---

### Task 18: Real-Path E2E, Documentation, and Final Verification

**Files:**
- Create: tests/hermes_cli/test_gnothi_e2e.py
- Modify: docs/hades/backend.md
- Modify: docs/backend-agent-coordination.md

**Interfaces:**
- Proves all acceptance criteria through real imports and temporary state.
- Records backend commit and route/contract decisions.

- [ ] **Step 1: Write the E2E test**

Create a temporary checkout fixture containing:

- Python source and test;
- one user skill with requires_tools;
- one user plugin manifest and register function;
- one MCP declaration with no secrets;
- one dependency manifest;
- one structured failure event.

Use isolated HERMES_HOME. Run build_organism_revision(), publish, status,
inspect, explain, diff after changing the skill, and render_wiki(). Assert:

- the full fixture organism is inventoried;
- capability dimensions are distinct;
- dependency explanation reaches evidence;
- drift is detected;
- semantic diff identifies the skill change;
- wiki has evidence links;
- stale/partial coverage is explicit;
- fixture secrets and absolute paths are absent;
- no fixture file except HERMES_HOME/gnothi_seauton and the event log changes.

- [ ] **Step 2: Run RED**

Expected: at least one integration assertion fails before final wiring fixes.

- [ ] **Step 3: Fix only integration seams**

Use failures to connect existing public interfaces. Do not add new architecture
or relax assertions. Every code edit in this step must be shown in the
execution log with its failing assertion and rerun command.

- [ ] **Step 4: Document operators**

In docs/hades/backend.md document commands, artifact schema, read-only
guarantee, coverage states, backend sync, organism graph scope, generated wiki,
and troubleshooting. State explicitly that gnothi_seauton does not implement
autopoiesis.

In docs/backend-agent-coordination.md record local commit range, remote backend
commit, tests, route compatibility, and any deployment requirement.

- [ ] **Step 5: Run full focused local verification**

~~~bash
source .venv/bin/activate
pytest -q \
  tests/hermes_cli/test_gnothi_contract.py \
  tests/hermes_cli/test_gnothi_redaction.py \
  tests/hermes_cli/test_gnothi_store.py \
  tests/hermes_cli/test_gnothi_collectors.py \
  tests/hermes_cli/test_gnothi_events.py \
  tests/hermes_cli/test_gnothi_builder.py \
  tests/hermes_cli/test_gnothi_query.py \
  tests/hermes_cli/test_gnothi_wiki.py \
  tests/hermes_cli/test_hades_gnothi_cmd.py \
  tests/hermes_cli/test_gnothi_e2e.py \
  tests/run_agent/test_gnothi_experience.py \
  tests/hermes_cli/test_hades_backend_sync_runner.py
ruff check hermes_cli/gnothi hermes_cli/hades_gnothi_cmd.py agent/gnothi_prompt.py agent/tool_executor.py plugins/memory/hades_backend/__init__.py
~~~

Expected: all pass, no lint findings.

- [ ] **Step 6: Run backend verification**

~~~bash
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && docker compose -f docker-compose.devboard.yaml exec -T app sh -lc "APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades/HadesOrganismGraphTest.php tests/Feature/Hades/HadesM5MvpCompletionTest.php tests/Feature/Hades/HadesM3SharedMemoryTest.php tests/Feature/PluginAuthTest.php"'
ssh ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && docker compose -f docker-compose.devboard.yaml exec -T app sh -lc "php artisan route:list --path=hades/v1"'
~~~

Expected: tests pass; existing artifact, awareness, and graph routes remain.

- [ ] **Step 7: Run privacy assertions over real artifacts**

~~~bash
HERMES_HOME="$(mktemp -d)" python -m hermes_cli.main gnothi-seauton rebuild --json > /tmp/gnothi-result.json
python - <<'PY'
import json
from pathlib import Path
result = json.loads(Path("/tmp/gnothi-result.json").read_text())
text = json.dumps(result)
for forbidden in ("api_key", "private_key", "authorization", "cookie"):
    assert forbidden not in text.lower(), forbidden
print("gnothi-privacy-smoke-ok")
PY
~~~

Expected: gnothi-privacy-smoke-ok. Delete /tmp/gnothi-result.json afterward.

- [ ] **Step 8: Commit final local slice**

~~~bash
git add tests/hermes_cli/test_gnothi_e2e.py docs/hades/backend.md docs/backend-agent-coordination.md
git commit -m "test(gnothi): verify complete organism awareness"
~~~

- [ ] **Step 9: Review final history and worktrees**

Run git status --short and git log --oneline from both repositories. Expected:
clean worktrees, separate focused commits, and the backend commit recorded in
coordination docs.

## Acceptance Traceability

- Full installed organism inventory: Tasks 4–9 and 18.
- Capability state dimensions: Tasks 5, 6, 10, and 18.
- Capability dependency explanation: Task 10 and E2E Task 18.
- Drift after a real change: Task 17 and E2E Task 18.
- Semantic revision comparison: Task 10 and E2E Task 18.
- Fully derived evidence-linked wiki: Task 11 and E2E Task 18.
- Stale, partial, and unknown coverage: Tasks 9, 10, 14, and 18.
- No secrets and no organism mutation: Tasks 2, 8, 14, and 18.
- Prompt and tool-schema stability: Tasks 15, 16, and regression checks.
- Future Evolution Blueprint input: contract, invariants, diff, and evidence
  produced by Tasks 1, 7, 9, and 10.
