# Finite Adapter Acceptance Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unbounded framework-adapter review loops with frozen, reproducible acceptance matrices, apply the gate to FastAPI Task 11, and prepare Express and Next.js for bounded parallel execution.

**Architecture:** A controller-side JSON validator freezes a corpus manifest and acceptance matrix into a canonical SHA-256 lock without adding a model tool or changing the agent core. Each adapter is implemented and reviewed against the frozen digest, with one complete review batch, one repair, and one scoped re-review; correctly declared out-of-matrix uncertainty goes to a versioned backlog. FastAPI is closed from the current candidate before Express and Next.js matrices are prepared in parallel.

**Tech Stack:** Python 3.11+, `argparse`, existing `hermes_cli.hades_graph_v2.identity.sha256_jcs`, JSON fixtures, pytest 9, Ruff 0.15.10, Git worktrees, Codex subagents (`gpt-5.6-terra/high` implementers and `gpt-5.6-sol/xhigh` reviewers).

## Global Constraints

- Use the approved design at `docs/superpowers/specs/2026-07-17-finite-adapter-acceptance-gates-design.md` as the authority.
- Claim `exact` only for demonstrated behavior; unsupported behavior must be `partial` or `unresolved` with explicit uncertainty.
- A new out-of-matrix construct that degrades conservatively is backlog, not a blocker.
- False exactness, silent loss, invalid references, nondeterminism, crashes, privacy violations, budget violations, and frozen-matrix failures are blockers.
- Freeze each corpus and matrix before implementation; any semantic change produces a new digest and returns the task to the matrix gate.
- Permit one complete review, one repair, and one scoped re-review. Do not start an exploratory Round 19.
- Reviewers do not edit production code or spawn nested reviewers. Implementers do not approve their own work.
- Use at most two file-disjoint implementation worktrees concurrently. The orchestrator alone edits shared integration files.
- Keep `progress.md` and review reports out of source commits; commit controller documentation separately.
- Preserve the existing unstaged `.superpowers/sdd/progress.md` change until the dedicated ledger task.
- Do not refactor the 7,188-line FastAPI adapter before the Plan 1 exit gate.
- Do not add a core model tool, environment variable, dependency, or prompt mutation for this workflow.

---

## File Map

| Path | Responsibility |
|---|---|
| `scripts/hades_adapter_acceptance.py` | Validate closed corpus/matrix documents and create or verify their canonical lock. |
| `tests/scripts/test_hades_adapter_acceptance.py` | Exercise closed-key validation, source qualification, unique IDs, negative envelopes, digest stability, and the real FastAPI bundle. |
| `tests/fixtures/hades/adapter_acceptance/fastapi/corpus.json` | Pinned FastAPI 0.115.0 / Starlette 0.37.2 corpus manifest. |
| `tests/fixtures/hades/adapter_acceptance/fastapi/matrix.json` | Frozen common FastAPI lifecycle capabilities and finite negative envelope. |
| `tests/fixtures/hades/adapter_acceptance/fastapi/lock.json` | Canonical corpus, matrix, and combined SHA-256 digests. |
| `.superpowers/sdd/task-11-acceptance-freeze.json` | Controller timestamp, candidate commit, and digest captured when the FastAPI matrix freezes. |
| `.superpowers/sdd/task-11-review-round18-brief.md` | Read-only reviewer contract for the one bounded FastAPI review. |
| `.superpowers/sdd/task-11-review-round18.md` | Complete Round 18 verdict and evidence. |
| `.superpowers/sdd/task-11-repair-round18-brief.md` | Conditional one-batch repair assignment. |
| `.superpowers/sdd/progress.md` | Controller ledger updated only after Task 11 reaches a terminal gate. |
| `.superpowers/sdd/adapter-acceptance-backlog.json` | Versioned out-of-matrix capability suggestions; review agents cannot promote entries. |
| `.superpowers/sdd/adapter-acceptance-metrics.jsonl` | One immutable measurement record per completed adapter gate. |
| `tests/fixtures/hades/adapter_acceptance/express/{corpus,matrix,lock}.json` | Frozen Express gate prepared before Task 12. |
| `tests/fixtures/hades/adapter_acceptance/nextjs/{corpus,matrix,lock}.json` | Frozen Next.js gate prepared before Task 13. |
| `docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md` | Canonical Plan 1 updated to require the new gate for Tasks 11–13. |

---

### Task 1: Add the Acceptance Bundle Validator

**Files:**
- Create: `scripts/hades_adapter_acceptance.py`
- Create: `tests/scripts/test_hades_adapter_acceptance.py`

**Interfaces:**
- Consumes: JSON corpus and matrix documents described below.
- Produces: `validate_corpus(document) -> None`, `validate_matrix(document, corpus) -> None`, `build_lock(corpus, matrix) -> dict[str, str]`, and a CLI with `freeze` and `validate` subcommands.
- Exit codes: `0` success, `2` invalid document or lock mismatch.

- [ ] **Step 1: Create RED tests for a valid closed bundle**

Create `tests/scripts/test_hades_adapter_acceptance.py` with a local module loader and these fixtures:

```python
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "hades_adapter_acceptance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("hades_adapter_acceptance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _corpus() -> dict[str, object]:
    return {
        "schema": "hades.adapter_acceptance_corpus.v1",
        "framework": "fastapi",
        "framework_versions": ["fastapi==0.115.0", "starlette==0.37.2"],
        "sources": [
            {
                "kind": "official_docs",
                "name": "fastapi-official",
                "url": "https://github.com/fastapi/fastapi.git",
                "revision": "4" * 40,
                "paths": ["docs/en/docs"],
            },
            *[
                {
                    "kind": "public_repository",
                    "name": f"public-{index}",
                    "url": f"https://example.test/public-{index}.git",
                    "revision": str(index) * 40,
                    "paths": ["."],
                }
                for index in range(1, 4)
            ],
        ],
    }


def _matrix() -> dict[str, object]:
    return {
        "schema": "hades.adapter_acceptance_matrix.v1",
        "framework": "fastapi",
        "framework_versions": ["fastapi==0.115.0", "starlette==0.37.2"],
        "items": [
            {
                "id": "FASTAPI-ROUTE-001",
                "construct": "decorator_route",
                "common_rule": "official_core_idiom",
                "expected_precision": "exact",
                "sources": ["fastapi-official", "public-1"],
                "required_facts": ["method", "normalized_path", "endpoint"],
                "allowed_unknowns": [],
                "negative_variants": [
                    {
                        "id": "FASTAPI-ROUTE-001-N1",
                        "construct": "dynamic_decorator_target",
                        "expected_precision": "partial",
                        "required_uncertainty": ["dynamic_registration"],
                        "test_nodes": ["tests/example.py::test_dynamic_route"],
                    }
                ],
                "test_nodes": ["tests/example.py::test_decorator_route"],
            }
        ],
    }


def test_build_lock_is_permutation_stable_and_validates() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    lock = module.build_lock(corpus, matrix)
    module.validate_lock(corpus, matrix, lock)
    assert lock["schema"] == "hades.adapter_acceptance_lock.v1"
    assert len(lock["bundle_sha256"]) == 64
```

- [ ] **Step 2: Add RED tests for every failure class**

Append parametrized or single-purpose tests that assert `AcceptanceError` for:

```python
def test_corpus_requires_exactly_three_public_repositories() -> None:
    module = _load_module()
    corpus = _corpus()
    corpus["sources"] = corpus["sources"][:-1]
    with pytest.raises(module.AcceptanceError, match="exactly 3 public_repository"):
        module.validate_corpus(corpus)


def test_documents_reject_unknown_keys() -> None:
    module = _load_module()
    corpus = _corpus()
    corpus["surprise"] = True
    with pytest.raises(module.AcceptanceError, match="unknown keys"):
        module.validate_corpus(corpus)


def test_source_revision_must_be_immutable_sha() -> None:
    module = _load_module()
    corpus = _corpus()
    corpus["sources"][0]["revision"] = "main"
    with pytest.raises(module.AcceptanceError, match="40 lowercase hex"):
        module.validate_corpus(corpus)


def test_matrix_ids_are_unique_across_items_and_negatives() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    matrix["items"][0]["negative_variants"][0]["id"] = "FASTAPI-ROUTE-001"
    with pytest.raises(module.AcceptanceError, match="duplicate matrix id"):
        module.validate_matrix(matrix, corpus)


def test_exact_item_requires_a_finite_negative_envelope() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    matrix["items"][0]["negative_variants"] = []
    with pytest.raises(module.AcceptanceError, match="negative variant"):
        module.validate_matrix(matrix, corpus)


def test_matrix_sources_must_exist_in_corpus() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    matrix["items"][0]["sources"] = ["missing"]
    with pytest.raises(module.AcceptanceError, match="unknown corpus source"):
        module.validate_matrix(matrix, corpus)


def test_lock_mismatch_is_rejected() -> None:
    module = _load_module()
    corpus = _corpus()
    matrix = _matrix()
    lock = module.build_lock(corpus, matrix)
    lock["matrix_sha256"] = "0" * 64
    with pytest.raises(module.AcceptanceError, match="lock mismatch"):
        module.validate_lock(corpus, matrix, lock)
```

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
scripts/run_tests.sh -q tests/scripts/test_hades_adapter_acceptance.py
```

Expected: FAIL while loading `scripts/hades_adapter_acceptance.py` because the file does not exist.

- [ ] **Step 4: Implement the validator without adding dependencies**

Create `scripts/hades_adapter_acceptance.py`. Use `sha256_jcs` from the locked v2 identity implementation. Implement closed-key checks for the exact shapes exercised above:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.hades_graph_v2.identity import sha256_jcs


_SHA_RE = re.compile(r"[0-9a-f]{40}")
_PRECISIONS = {"exact", "partial", "unresolved"}
_COMMON_RULES = {
    "official_core_idiom",
    "two_independent_repositories",
    "fundamental_lifecycle_stage",
    "owned_ordinary_idiom",
}


class AcceptanceError(ValueError):
    pass


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AcceptanceError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AcceptanceError(f"{label} must be a non-empty string")
    return value


def _closed(document: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(document) - allowed)
    missing = sorted(allowed - set(document))
    if unknown:
        raise AcceptanceError(f"{label} has unknown keys: {unknown}")
    if missing:
        raise AcceptanceError(f"{label} is missing keys: {missing}")


def validate_corpus(document: Any) -> None:
    corpus = _object(document, "corpus")
    _closed(corpus, {"schema", "framework", "framework_versions", "sources"}, "corpus")
    if corpus["schema"] != "hades.adapter_acceptance_corpus.v1":
        raise AcceptanceError("unsupported corpus schema")
    _string(corpus["framework"], "corpus.framework")
    versions = _list(corpus["framework_versions"], "corpus.framework_versions")
    if not versions or any(not isinstance(row, str) or "==" not in row for row in versions):
        raise AcceptanceError("framework_versions must contain pinned name==version strings")
    sources = _list(corpus["sources"], "corpus.sources")
    names: set[str] = set()
    counts = {"official_docs": 0, "public_repository": 0, "owned_repository": 0}
    for index, raw in enumerate(sources):
        source = _object(raw, f"source[{index}]")
        _closed(source, {"kind", "name", "url", "revision", "paths"}, f"source[{index}]")
        kind = _string(source["kind"], f"source[{index}].kind")
        if kind not in counts:
            raise AcceptanceError(f"source[{index}].kind is unsupported")
        counts[kind] += 1
        name = _string(source["name"], f"source[{index}].name")
        if name in names:
            raise AcceptanceError(f"duplicate source name: {name}")
        names.add(name)
        _string(source["url"], f"source[{index}].url")
        revision = _string(source["revision"], f"source[{index}].revision")
        if _SHA_RE.fullmatch(revision) is None:
            raise AcceptanceError(f"source[{index}].revision must be 40 lowercase hex characters")
        paths = _list(source["paths"], f"source[{index}].paths")
        if not paths or any(not isinstance(path, str) or not path for path in paths):
            raise AcceptanceError(f"source[{index}].paths must contain non-empty strings")
    if counts["official_docs"] < 1:
        raise AcceptanceError("corpus requires at least 1 official_docs source")
    if counts["public_repository"] != 3:
        raise AcceptanceError("corpus requires exactly 3 public_repository sources")
    if counts["owned_repository"] > 1:
        raise AcceptanceError("corpus permits at most 1 owned_repository source")


def validate_matrix(document: Any, corpus_document: Any) -> None:
    validate_corpus(corpus_document)
    corpus = _object(corpus_document, "corpus")
    matrix = _object(document, "matrix")
    _closed(matrix, {"schema", "framework", "framework_versions", "items"}, "matrix")
    if matrix["schema"] != "hades.adapter_acceptance_matrix.v1":
        raise AcceptanceError("unsupported matrix schema")
    if matrix["framework"] != corpus["framework"]:
        raise AcceptanceError("matrix framework does not match corpus")
    if matrix["framework_versions"] != corpus["framework_versions"]:
        raise AcceptanceError("matrix framework_versions do not match corpus")
    source_names = {source["name"] for source in corpus["sources"]}
    seen_ids: set[str] = set()
    items = _list(matrix["items"], "matrix.items")
    if not items:
        raise AcceptanceError("matrix.items cannot be empty")
    for index, raw in enumerate(items):
        item = _object(raw, f"item[{index}]")
        _closed(
            item,
            {
                "id", "construct", "common_rule", "expected_precision", "sources",
                "required_facts", "allowed_unknowns", "negative_variants", "test_nodes",
            },
            f"item[{index}]",
        )
        item_id = _string(item["id"], f"item[{index}].id")
        if item_id in seen_ids:
            raise AcceptanceError(f"duplicate matrix id: {item_id}")
        seen_ids.add(item_id)
        _string(item["construct"], f"item[{index}].construct")
        if item["common_rule"] not in _COMMON_RULES:
            raise AcceptanceError(f"item[{index}].common_rule is unsupported")
        precision = item["expected_precision"]
        if precision not in _PRECISIONS:
            raise AcceptanceError(f"item[{index}].expected_precision is unsupported")
        sources = _list(item["sources"], f"item[{index}].sources")
        if not sources or any(source not in source_names for source in sources):
            raise AcceptanceError(f"item[{index}] references an unknown corpus source")
        facts = _list(item["required_facts"], f"item[{index}].required_facts")
        _list(item["allowed_unknowns"], f"item[{index}].allowed_unknowns")
        tests = _list(item["test_nodes"], f"item[{index}].test_nodes")
        if not tests or any("::test_" not in node for node in tests):
            raise AcceptanceError(f"item[{index}].test_nodes must name pytest tests")
        negatives = _list(item["negative_variants"], f"item[{index}].negative_variants")
        if precision == "exact" and (not facts or not negatives):
            raise AcceptanceError(f"exact item {item_id} requires facts and a negative variant")
        for negative_index, raw_negative in enumerate(negatives):
            negative = _object(raw_negative, f"item[{index}].negative[{negative_index}]")
            _closed(
                negative,
                {"id", "construct", "expected_precision", "required_uncertainty", "test_nodes"},
                f"item[{index}].negative[{negative_index}]",
            )
            negative_id = _string(negative["id"], "negative.id")
            if negative_id in seen_ids:
                raise AcceptanceError(f"duplicate matrix id: {negative_id}")
            seen_ids.add(negative_id)
            _string(negative["construct"], "negative.construct")
            if negative["expected_precision"] not in {"partial", "unresolved"}:
                raise AcceptanceError(f"negative {negative_id} must be partial or unresolved")
            if not _list(negative["required_uncertainty"], "negative.required_uncertainty"):
                raise AcceptanceError(f"negative {negative_id} requires uncertainty")
            negative_tests = _list(negative["test_nodes"], "negative.test_nodes")
            if not negative_tests or any("::test_" not in node for node in negative_tests):
                raise AcceptanceError(f"negative {negative_id} must name pytest tests")


def build_lock(corpus: Any, matrix: Any) -> dict[str, str]:
    validate_matrix(matrix, corpus)
    return {
        "schema": "hades.adapter_acceptance_lock.v1",
        "framework": corpus["framework"],
        "corpus_sha256": sha256_jcs(corpus),
        "matrix_sha256": sha256_jcs(matrix),
        "bundle_sha256": sha256_jcs({"corpus": corpus, "matrix": matrix}),
    }


def validate_lock(corpus: Any, matrix: Any, lock: Any) -> None:
    expected = build_lock(corpus, matrix)
    if lock != expected:
        raise AcceptanceError("acceptance lock mismatch")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("freeze", "validate"):
        child = subparsers.add_parser(command)
        child.add_argument("--corpus", type=Path, required=True)
        child.add_argument("--matrix", type=Path, required=True)
        child.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        corpus = _read(args.corpus)
        matrix = _read(args.matrix)
        if args.command == "freeze":
            args.lock.write_text(
                json.dumps(build_lock(corpus, matrix), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            validate_lock(corpus, matrix, _read(args.lock))
        print(build_lock(corpus, matrix)["bundle_sha256"])
        return 0
    except (AcceptanceError, OSError, json.JSONDecodeError) as exc:
        print(f"acceptance error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run focused GREEN and quality checks**

Run:

```bash
scripts/run_tests.sh -q tests/scripts/test_hades_adapter_acceptance.py
.venv/bin/ruff format scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py
.venv/bin/ruff check scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py
.venv/bin/python -m py_compile scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py
git diff --check -- scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py
```

Expected: all tests pass; Ruff reports no errors; compile and diff checks exit 0.

- [ ] **Step 6: Commit the validator only**

```bash
git add scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py
git commit -m "build(hades): validate finite adapter acceptance gates"
```

Expected: the pre-existing `.superpowers/sdd/progress.md` modification remains unstaged.

---

### Task 2: Freeze the Retroactive FastAPI Acceptance Bundle

**Files:**
- Create: `tests/fixtures/hades/adapter_acceptance/fastapi/corpus.json`
- Create: `tests/fixtures/hades/adapter_acceptance/fastapi/matrix.json`
- Create: `tests/fixtures/hades/adapter_acceptance/fastapi/lock.json`
- Create: `.superpowers/sdd/task-11-acceptance-freeze.json`
- Modify: `tests/scripts/test_hades_adapter_acceptance.py`

**Interfaces:**
- Consumes: Task 11 base `114cdcd36`, candidate `534a8d70f`, FastAPI `0.115.0`, Starlette `0.37.2`, and the Task 1 validator.
- Produces: one immutable bundle digest consumed by the Round 18 brief and all Task 11 reports.

- [ ] **Step 1: Write the pinned corpus document**

Create `corpus.json` with exactly these sources and revisions. The three public
application revisions are historical snapshots selected at or before 2024-10-01,
so the corpus reflects the FastAPI 0.115 release period rather than 2026 HEADs:

```json
{
  "schema": "hades.adapter_acceptance_corpus.v1",
  "framework": "fastapi",
  "framework_versions": ["fastapi==0.115.0", "starlette==0.37.2"],
  "sources": [
    {
      "kind": "official_docs",
      "name": "fastapi-official",
      "url": "https://github.com/fastapi/fastapi.git",
      "revision": "40e33e492dbf4af6172997f4e3238a32e56cbe26",
      "paths": ["docs/en/docs", "fastapi/routing.py", "fastapi/applications.py"]
    },
    {
      "kind": "official_docs",
      "name": "starlette-official",
      "url": "https://github.com/Kludex/starlette.git",
      "revision": "554f368809e0d891a699667faf0cfbb20057eeb2",
      "paths": ["docs", "starlette/routing.py", "starlette/applications.py"]
    },
    {
      "kind": "public_repository",
      "name": "full-stack-fastapi-template",
      "url": "https://github.com/fastapi/full-stack-fastapi-template.git",
      "revision": "88e1a607b0b4f26e1990c55bb81c82beeff853bb",
      "paths": ["backend/app"]
    },
    {
      "kind": "public_repository",
      "name": "netflix-dispatch",
      "url": "https://github.com/Netflix/dispatch.git",
      "revision": "3c90e4bb604724708e3e3c4b470a51fc40ce8274",
      "paths": ["src/dispatch"]
    },
    {
      "kind": "public_repository",
      "name": "fastapi-realworld-example-app",
      "url": "https://github.com/nsidnev/fastapi-realworld-example-app.git",
      "revision": "029eb7781c60d5f563ee8990a0cbfb79b244538c",
      "paths": ["app"]
    },
    {
      "kind": "owned_repository",
      "name": "hephaistos-dashboard",
      "url": "https://github.com/titagram/Hephaistos.git",
      "revision": "534a8d70f8fdb6c8eeb6158e8ce5e4b273311ee2",
      "paths": ["hermes_cli/web_server.py"]
    }
  ]
}
```

- [ ] **Step 2: Create the finite FastAPI matrix**

Create `matrix.json` using the closed shape from Task 1. Include exactly these top-level IDs and map the named existing tests. Every `exact` item receives the listed negative test as a finite negative variant:

| ID | Precision | Required behavior | Positive test | Negative test |
|---|---|---|---|---|
| `FASTAPI-ROUTE-001` | exact | decorator/API route method, normalized path, endpoint | `test_nested_routers_methods_dependencies_cache_cleanup_and_background_child` | `test_dynamic_expression_registrations_are_partial_without_invented_routes` |
| `FASTAPI-ROUTER-001` | exact | nested include prefix, repeated include identity, dependency inheritance | `test_repeated_router_inclusion_has_distinct_registry_safe_pipeline_identity` | `test_computed_router_configuration_is_reported_partial_and_never_guessed` |
| `FASTAPI-METHOD-001` | exact | reviewed FastAPI and Starlette method defaults | `test_reviewed_route_method_defaults_and_signatures_are_exact` | `test_unreviewed_route_method_contracts_are_partial_and_not_invented` |
| `FASTAPI-STARLETTE-001` | exact | plain Starlette route excludes FastAPI-only stages | `test_plain_starlette_routes_exclude_fastapi_only_pipeline_stages` | `test_unknown_starlette_order_is_a_partial_boundary_not_a_guess` |
| `FASTAPI-DEPENDENCY-001` | exact | decorator, endpoint, and nested dependency ordering and identity | `test_add_api_route_merges_registration_and_endpoint_dependencies` | `test_dependency_proof_supports_annotated_and_bounds_fake_and_dynamic_cache` |
| `FASTAPI-SECURITY-001` | exact | security scopes participate in dependency cache identity | `test_security_scopes_participate_in_dependency_cache_identity` | `test_annotated_metadata_requires_proven_dependency_or_nondependency` |
| `FASTAPI-MIDDLEWARE-001` | exact | middleware continuation is exact only when must-call is proven | `test_middleware_binds_continuation_and_requires_must_call_proof` | same test node; its negative fixture is the finite envelope |
| `FASTAPI-EXCEPTION-001` | exact | handler type identity and MRO specificity | `test_exception_handlers_use_proven_exact_type_and_mro_specificity` | `test_unproven_exception_ancestry_is_a_boundary_not_a_guessed_handler` |
| `FASTAPI-LIFESPAN-001` | exact | explicit lifespan and legacy event exclusivity/composition | `test_literal_none_lifespan_preserves_routes_and_legacy_events` | `test_lifespan_target_uses_constructor_occurrence_binding` |
| `FASTAPI-BACKGROUND-001` | exact | proven `BackgroundTasks.add_task` receiver and child dispatch | `test_background_tasks_prove_receiver_and_use_runtime_and_lexical_bindings` | `test_background_receiver_binding_at_call_controls_dispatch` |
| `FASTAPI-RESPONSE-001` | exact | response annotation/identity determines serialization boundary | `test_return_annotation_drives_serialization_unless_explicitly_opted_out` | `test_response_annotations_prove_response_identity_and_subclass_boundaries` |
| `FASTAPI-IMPORT-001` | exact | imported router and dependency identity across files | `test_imported_router_and_dependency_cache_use_resolved_callable_identity` | `test_imported_app_alias_rebind_invalidates_reference_and_lost_registrations` |
| `FASTAPI-DYNAMIC-001` | partial | dynamic/conditional registration emits uncertainty without a route guess | `test_dynamic_control_flow_registration_is_partial_without_invented_route` | no negative variants because the item is already partial |
| `FASTAPI-REBIND-001` | partial | rebound application/framework objects invalidate stale exact identity | `test_rebound_application_object_is_partial_not_an_old_registration` | no negative variants because the item is already partial |

Use these source names:

- FastAPI route/router/dependency/security/exception/lifespan/background/response: `fastapi-official` plus every public repository in which the construct occurs;
- Starlette route/method/middleware behavior: `starlette-official` plus every public repository in which the construct occurs;
- imported router: the three public repositories;
- dynamic and rebind boundaries: `fastapi-starlette-official` and `hephaistos-dashboard`, justified as fundamental soundness boundaries.

Use one negative ID per exact item by appending `-N1`. Set its precision to `partial` unless the existing assertion requires `unresolved`. Set `required_uncertainty` to the exact omission or partial reason asserted by that test. Do not add the Round 5–17 host-language edge cases as new matrix items; they remain regression tests.

- [ ] **Step 3: Freeze and validate the bundle**

Run:

```bash
.venv/bin/python scripts/hades_adapter_acceptance.py freeze \
  --corpus tests/fixtures/hades/adapter_acceptance/fastapi/corpus.json \
  --matrix tests/fixtures/hades/adapter_acceptance/fastapi/matrix.json \
  --lock tests/fixtures/hades/adapter_acceptance/fastapi/lock.json
.venv/bin/python scripts/hades_adapter_acceptance.py validate \
  --corpus tests/fixtures/hades/adapter_acceptance/fastapi/corpus.json \
  --matrix tests/fixtures/hades/adapter_acceptance/fastapi/matrix.json \
  --lock tests/fixtures/hades/adapter_acceptance/fastapi/lock.json
```

Expected: both commands print the same 64-character bundle digest and exit 0. Copy that digest into the Round 18 brief in Task 3; do not hand-type or predict it in advance.

Immediately run `date -u +%Y-%m-%dT%H:%M:%SZ`, `date -u +%s`, and
`git rev-parse HEAD`. Create `.superpowers/sdd/task-11-acceptance-freeze.json`
with schema `hades.adapter_acceptance_freeze.v1`, the printed bundle digest,
the two exact UTC values, and the exact HEAD. This controller evidence is not
part of the fixture commit in Step 6.

- [ ] **Step 4: Add a repository-level freeze test**

Append:

```python
def test_fastapi_acceptance_bundle_is_frozen() -> None:
    module = _load_module()
    base = ROOT / "tests" / "fixtures" / "hades" / "adapter_acceptance" / "fastapi"
    corpus = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
    matrix = json.loads((base / "matrix.json").read_text(encoding="utf-8"))
    lock = json.loads((base / "lock.json").read_text(encoding="utf-8"))
    module.validate_lock(corpus, matrix, lock)
    assert {item["id"] for item in matrix["items"]} == {
        "FASTAPI-ROUTE-001", "FASTAPI-ROUTER-001", "FASTAPI-METHOD-001",
        "FASTAPI-STARLETTE-001", "FASTAPI-DEPENDENCY-001", "FASTAPI-SECURITY-001",
        "FASTAPI-MIDDLEWARE-001", "FASTAPI-EXCEPTION-001", "FASTAPI-LIFESPAN-001",
        "FASTAPI-BACKGROUND-001", "FASTAPI-RESPONSE-001", "FASTAPI-IMPORT-001",
        "FASTAPI-DYNAMIC-001", "FASTAPI-REBIND-001",
    }
```

- [ ] **Step 5: Run every mapped matrix test and the freeze test**

Run the freeze test, then run the complete FastAPI file so no test-node typo or stale name can pass unnoticed:

```bash
scripts/run_tests.sh -q tests/scripts/test_hades_adapter_acceptance.py
scripts/run_tests.sh -q tests/hermes_cli/test_hades_lifecycle_fastapi.py
```

Expected: acceptance tests pass and all 109 FastAPI tests pass.

- [ ] **Step 6: Commit only the frozen bundle and its test**

```bash
git add tests/scripts/test_hades_adapter_acceptance.py tests/fixtures/hades/adapter_acceptance/fastapi
git commit -m "test(hades): freeze FastAPI adapter acceptance gate"
```

Expected: no adapter production source is modified.

---

### Task 3: Conduct the Single Bounded FastAPI Round 18 Review

**Files:**
- Create: `.superpowers/sdd/task-11-review-round18-brief.md`
- Create: `.superpowers/sdd/task-11-review-round18.md`
- Read: `hermes_cli/hades_index/lifecycle/frameworks/fastapi.py`
- Read: `tests/hermes_cli/test_hades_lifecycle_fastapi.py`

**Interfaces:**
- Consumes: candidate adapter commit `534a8d70f`, the frozen bundle digest from Task 2, and the approved design/spec.
- Produces: one complete verdict containing counts for Critical, Important, and Minor findings plus structured blocker records.

- [ ] **Step 1: Write the reviewer brief with the computed digest**

The brief must state all of the following literally, substituting only the digest printed by Task 2:

```text
Role: independent read-only reviewer.
Model: gpt-5.6-sol. Reasoning effort: xhigh.
Candidate production commit: 534a8d70f8fdb6c8eeb6158e8ce5e4b273311ee2.
Acceptance bundle digest: copy the single 64-character digest printed by Task 2 Step 3.
Review authority: docs/superpowers/specs/2026-07-17-finite-adapter-acceptance-gates-design.md.
Do not edit files. Do not spawn subagents. Do not expand the frozen matrix.
Inspect the cumulative Task 11 implementation and every frozen matrix item/negative.
You may report invariant violations outside an item only for false exactness, silent loss,
invalid contract/reference, nondeterminism, crash, privacy, or locked budget failure.
Return every blocker in one batch. Each blocker requires: stable ID, matrix ID or invariant
category, concrete reproducer, actual result, required result, and blocking rationale.
Suggestions for correctly declared out-of-matrix uncertainty go to a non-blocking backlog.
Write exactly one report: .superpowers/sdd/task-11-review-round18.md.
```

- [ ] **Step 2: Dispatch exactly one reviewer**

Use `spawn_agent` with:

```text
task_name="plan1_task11_review_round18"
model="gpt-5.6-sol"
reasoning_effort="xhigh"
fork_turns="none"
message=the complete contents of `.superpowers/sdd/task-11-review-round18-brief.md`, followed by the remote workspace and branch
```

Do not authorize that reviewer to delegate. While it runs, the orchestrator may prepare Task 6 corpus candidates but must not modify FastAPI source or tests.

- [ ] **Step 3: Validate reviewer output mechanically**

Reject the report as incomplete if it omits any of:

- candidate commit;
- acceptance digest;
- commands actually run and their exact results;
- Critical/Important/Minor counts;
- one structured record per blocker;
- an explicit statement that the reviewer did not edit files or delegate.

An incomplete report is corrected by the same reviewer as part of the same review turn; it does not create a new review round.

- [ ] **Step 4: Branch on the complete verdict**

- If the verdict is `0 Critical / 0 Important / 0 Minor`, skip Task 4 and continue to Task 5.
- If one or more blockers exist, perform Task 4 exactly once.
- If the report contains only backlog suggestions, record them as non-blocking and continue to Task 5.

Do not ask a second exploratory reviewer to search for more cases.

---

### Task 4: Apply One Complete FastAPI Repair Batch (Conditional)

**Files:**
- Create: `.superpowers/sdd/task-11-repair-round18-brief.md`
- Modify only when named by a Round 18 blocker: `hermes_cli/hades_index/lifecycle/frameworks/fastapi.py`
- Modify only when named by a Round 18 blocker: `tests/hermes_cli/test_hades_lifecycle_fastapi.py`

**Interfaces:**
- Consumes: the complete Round 18 blocker batch without additions.
- Produces: one repair commit and one scoped re-review verdict for the same finding IDs.

- [ ] **Step 1: Normalize the complete repair brief**

Copy every blocker record verbatim. Add the candidate base, acceptance digest, authorized files, focused test command, aggregate test command, static checks, and this stop condition:

```text
Do not broaden host-language exactness beyond the reproducer and its frozen matrix invariant.
For each blocker, first add or identify one RED test, run it alone, implement the minimum
sound repair, and run it GREEN. If a finding requires a matrix change, stop and report
DESIGN_ESCALATION_REQUIRED without modifying the matrix.
```

- [ ] **Step 2: Dispatch the repair to the original implementer role**

Use `gpt-5.6-terra/high`. Reuse the previous implementer session if it is available and healthy; otherwise spawn one implementer with no delegation and the complete repair brief. Do not split one coupled FastAPI repair batch across multiple agents.

- [ ] **Step 3: Require RED/GREEN evidence per finding**

For each finding ID, the report must contain:

```text
Finding ID
RED test node and failure
Production location changed
GREEN isolated result
Reason exact/partial behavior now satisfies the frozen invariant
```

- [ ] **Step 4: Run the complete post-repair gate**

```bash
scripts/run_tests.sh -q tests/hermes_cli/test_hades_lifecycle_fastapi.py
.venv/bin/ruff format --check hermes_cli/hades_index/lifecycle/frameworks/fastapi.py tests/hermes_cli/test_hades_lifecycle_fastapi.py
.venv/bin/ruff check hermes_cli/hades_index/lifecycle/frameworks/fastapi.py tests/hermes_cli/test_hades_lifecycle_fastapi.py
.venv/bin/python -m py_compile hermes_cli/hades_index/lifecycle/frameworks/fastapi.py tests/hermes_cli/test_hades_lifecycle_fastapi.py
git diff --check -- hermes_cli/hades_index/lifecycle/frameworks/fastapi.py tests/hermes_cli/test_hades_lifecycle_fastapi.py
```

Expected: every command passes. Commit only the two authorized files with a subject naming the repaired invariant class.

- [ ] **Step 5: Perform one scoped re-review**

Send the same reviewer only the original finding IDs, repair commit, relevant diff, and gate output. The reviewer may close or retain those findings and may report a regression only when it directly violates the frozen matrix. It may not introduce a new out-of-matrix requirement.

- [ ] **Step 6: Enforce the terminal branch**

- If all finding IDs close, continue to Task 5.
- If any remain, set Task 11 to `design escalation`, stop automatic implementation, and write a bounded architectural proposal. Do not start Round 19.

---

### Task 5: Close Task 11 and Update the Controller Ledger

**Files:**
- Modify: `.superpowers/sdd/progress.md`
- Create or modify: `.superpowers/sdd/adapter-acceptance-backlog.json`
- Create or append: `.superpowers/sdd/adapter-acceptance-metrics.jsonl`
- Include: `.superpowers/sdd/task-11-review-round18.md`
- Include conditionally: `.superpowers/sdd/task-11-repair-round18-brief.md`
- Modify: `docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md`

**Interfaces:**
- Consumes: a terminal Round 18 verdict and current candidate/repair HEAD.
- Produces: documented Task 11 completion and a canonical reference to the finite gate for Tasks 12–13.

- [ ] **Step 1: Re-run fresh controller evidence from the terminal HEAD**

```bash
.venv/bin/python scripts/hades_adapter_acceptance.py validate \
  --corpus tests/fixtures/hades/adapter_acceptance/fastapi/corpus.json \
  --matrix tests/fixtures/hades/adapter_acceptance/fastapi/matrix.json \
  --lock tests/fixtures/hades/adapter_acceptance/fastapi/lock.json
scripts/run_tests.sh -q tests/scripts/test_hades_adapter_acceptance.py
scripts/run_tests.sh -q tests/hermes_cli/test_hades_lifecycle_fastapi.py
scripts/run_tests.sh -q tests/hermes_cli/test_hades_lifecycle_*.py
.venv/bin/ruff check scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py hermes_cli/hades_index/lifecycle/frameworks/fastapi.py tests/hermes_cli/test_hades_lifecycle_fastapi.py
.venv/bin/python -m py_compile scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py hermes_cli/hades_index/lifecycle/frameworks/fastapi.py tests/hermes_cli/test_hades_lifecycle_fastapi.py
git diff --check
```

Expected: all commands pass from a clean source state except the known controller documentation modifications.

- [ ] **Step 2: Update the Task 11 ledger atomically**

Append to Task 11:

- the acceptance bundle digest;
- corpus and matrix paths;
- Round 18 verdict and report path;
- repair commit and scoped re-review result when Task 4 ran;
- fresh controller command counts/results;
- `Status: complete` only when all blockers are closed;
- deferred debt note with the measured 7,188 production and 7,808 test lines;
- explicit next tasks: Express and Next.js matrix gates.

Remove or supersede the stale terminal line `Status: implementation complete; review blocked`; retain Round 1–17 history as evidence.

- [ ] **Step 3: Amend the canonical Plan 1 adapter rules**

Before Task 12, add a short normative section that links the approved design and requires:

```text
Every remaining framework adapter must have a validated corpus.json, matrix.json, and
lock.json before production implementation. The task brief records the lock digest.
One complete independent review, one repair, and one scoped re-review are permitted.
Correctly declared out-of-matrix partial/unresolved behavior is backlog, not a blocker.
```

Do not rewrite the existing technical requirements for Express or Next.js.

- [ ] **Step 4: Record backlog suggestions without promoting them**

Create `.superpowers/sdd/adapter-acceptance-backlog.json` with this closed root:

```json
{
  "schema": "hades.adapter_acceptance_backlog.v1",
  "items": []
}
```

For every non-blocking Round 18 suggestion, append one object with exactly these
keys: `id`, `framework`, `framework_versions`, `construct`, `source_name`,
`source_revision`, `observed_frequency`, `current_precision`,
`requested_precision`, `user_value`, `false_exact_risk`, and
`proposed_matrix_version`. Use report evidence for every value. Leave `items`
empty when the reviewer made no suggestion. Do not copy a blocker into backlog.

- [ ] **Step 5: Append one immutable metric record**

Append one JSON object line to
`.superpowers/sdd/adapter-acceptance-metrics.jsonl` with exactly these keys:

```json
{
  "schema": "hades.adapter_acceptance_metrics.v1",
  "framework": "fastapi",
  "framework_versions": ["fastapi==0.115.0", "starlette==0.37.2"],
  "bundle_sha256": "read from tests/fixtures/hades/adapter_acceptance/fastapi/lock.json",
  "candidate_commit": "read from git rev-parse HEAD after the terminal gate",
  "review_cycles": 1,
  "repair_performed": false,
  "matrix_exact_items": 12,
  "matrix_partial_items": 2,
  "negative_variants": 12,
  "focused_tests_passed": 109,
  "aggregate_tests_passed": 281,
  "blocking_findings": 0,
  "backlog_suggestions": 0,
  "matrix_model": "gpt-5.6-sol/xhigh",
  "implementation_model": "gpt-5.6-terra/high",
  "review_model": "gpt-5.6-sol/xhigh",
  "elapsed_seconds_from_freeze_to_approval": 0
}
```

Before applying this object to the JSONL file, replace the two descriptive
strings by reading the named sources, replace test/finding/backlog counts from
the terminal reports, set `repair_performed` from Task 4 execution, and compute
`elapsed_seconds_from_freeze_to_approval` as the current `date -u +%s` minus
`frozen_at_epoch` in `.superpowers/sdd/task-11-acceptance-freeze.json`. The
literal zeros above are valid only when the corresponding event did not occur.
`review_cycles` remains `1` because the scoped re-review belongs to the same
cycle. Serialize with sorted keys and no multiline formatting so later records
remain valid JSONL.

- [ ] **Step 6: Commit controller documentation separately**

First inspect staged paths. Then force-add ignored controller artifacts only when they are intended evidence:

```bash
git add .superpowers/sdd/progress.md docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md
git add -f .superpowers/sdd/task-11-review-round18.md .superpowers/sdd/task-11-acceptance-freeze.json .superpowers/sdd/adapter-acceptance-backlog.json .superpowers/sdd/adapter-acceptance-metrics.jsonl
git diff --cached --check
git commit -m "docs(hades): close bounded FastAPI adapter review"
```

If Task 4 created a repair brief/report required for audit, add those exact files with `git add -f` before the same documentation commit. Never stage unrelated `.superpowers/sdd` files by directory glob.

---

### Task 6: Prepare Express and Next.js Matrices in Parallel

**Files:**
- Create: `tests/fixtures/hades/adapter_acceptance/express/corpus.json`
- Create: `tests/fixtures/hades/adapter_acceptance/express/matrix.json`
- Create: `tests/fixtures/hades/adapter_acceptance/express/lock.json`
- Create: `tests/fixtures/hades/adapter_acceptance/nextjs/corpus.json`
- Create: `tests/fixtures/hades/adapter_acceptance/nextjs/matrix.json`
- Create: `tests/fixtures/hades/adapter_acceptance/nextjs/lock.json`
- Modify: `tests/scripts/test_hades_adapter_acceptance.py`

**Interfaces:**
- Consumes: completed Task 11 HEAD, Task 1 validator, and existing Plan 1 Task 12/13 requirements.
- Produces: two independent immutable digests suitable for parallel implementation briefs.

- [ ] **Step 1: Create two matrix-author assignments with disjoint directories**

Dispatch two `gpt-5.6-sol/xhigh` agents in parallel. They may research and write only their assigned fixture directory and their own temporary report. They do not edit production adapters or the shared validator test.

Express must cover exactly the existing Plan 1 categories:

- nested router mount and path prefix;
- same path with multiple verbs;
- `.all` without inventing one method;
- registration-ordered `use` and handlers;
- proven `next()`, `next('route')`, and `next(err)`;
- `send`, `json`, `end`, and redirect terminal outcomes;
- thrown errors and async rejection;
- error middleware arity;
- computed target as declared uncertainty.

Next.js must cover exactly:

- App Router exported GET/POST handlers;
- Pages API exhaustive method switch versus unrestricted fallback;
- route groups, dynamic segments, and catch-all patterns;
- detected-version precedence;
- middleware matcher, redirect, response, and next;
- static rewrite and redirect configuration;
- unresolved computed configuration;
- exclusion of server/client render graphs as HTTP entrypoints.

- [ ] **Step 2: Pin corpus revisions mechanically**

Each author uses at least one official source and exactly three public application repositories. Resolve every revision with `git ls-remote` using the repository URL selected in the corpus report and the literal ref `HEAD`, or use a verified tag dereference, then record the returned 40-character commit. Do not record branches such as `main`, mutable release pages, or downloaded archives without a digest.

The reviewer report records the exact command output that supports every revision. If a repository does not contain the claimed framework pattern at that revision, replace it before freezing; do not weaken the two-repository commonness rule.

- [ ] **Step 3: Encode finite negative envelopes**

For every exact item, include at least one concrete negative variant from the same semantic family. The variant must expect `partial` or `unresolved`, name the required uncertainty category, and map to a test node that the later adapter task will create before production code.

Do not add general JavaScript/TypeScript semantics unrelated to the listed lifecycle categories.

- [ ] **Step 4: Integrate and freeze each directory sequentially**

The orchestrator reviews Express first, writes its lock with `freeze`, and validates it. Then repeat for Next.js. Do not allow either matrix author to edit the other's files.

- [ ] **Step 5: Add two real-bundle tests centrally**

Extend the Task 2 freeze test pattern with:

```python
@pytest.mark.parametrize("framework", ["fastapi", "express", "nextjs"])
def test_adapter_acceptance_bundle_is_frozen(framework: str) -> None:
    module = _load_module()
    base = ROOT / "tests" / "fixtures" / "hades" / "adapter_acceptance" / framework
    corpus = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
    matrix = json.loads((base / "matrix.json").read_text(encoding="utf-8"))
    lock = json.loads((base / "lock.json").read_text(encoding="utf-8"))
    module.validate_lock(corpus, matrix, lock)
```

Retain the FastAPI exact-ID assertion as a separate test.

- [ ] **Step 6: Run and commit the preparation gate**

```bash
scripts/run_tests.sh -q tests/scripts/test_hades_adapter_acceptance.py
.venv/bin/ruff check scripts/hades_adapter_acceptance.py tests/scripts/test_hades_adapter_acceptance.py
git diff --check -- tests/fixtures/hades/adapter_acceptance tests/scripts/test_hades_adapter_acceptance.py
git add tests/fixtures/hades/adapter_acceptance/express tests/fixtures/hades/adapter_acceptance/nextjs tests/scripts/test_hades_adapter_acceptance.py
git commit -m "test(hades): freeze Express and Next.js adapter gates"
```

Expected: both lock digests are committed before either production adapter exists.

---

### Task 7: Hand Off Bounded Parallel Adapter Implementation

**Files:**
- Create: `.superpowers/sdd/task-12-brief.md`
- Create: `.superpowers/sdd/task-13-brief.md`
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: completed Task 11 HEAD, Express digest, Next.js digest, and unchanged technical steps from canonical Plan 1 Tasks 12–13.
- Produces: two executable, file-disjoint briefs ready for `superpowers:subagent-driven-development`.

- [ ] **Step 1: Write the Express brief**

Include:

- exact base commit;
- Express bundle digest and paths;
- owned files `hermes_cli/hades_index/lifecycle/frameworks/express.py` and `tests/hermes_cli/test_hades_lifecycle_express.py`;
- no other writable source files;
- every technical requirement from canonical Task 12;
- `gpt-5.6-terra/high`, no delegation;
- focused test/static commands;
- stop condition for shared-file or matrix changes;
- one complete independent `gpt-5.6-sol/xhigh` review after implementation.

- [ ] **Step 2: Write the Next.js brief**

Include the same fields with the Next.js digest and owned files `hermes_cli/hades_index/lifecycle/frameworks/nextjs.py` and `tests/hermes_cli/test_hades_lifecycle_nextjs.py`. State that its final aggregate gate waits for Express integration, but its focused implementation may run concurrently.

- [ ] **Step 3: Validate file ownership and create worktrees at execution time**

Use `superpowers:using-git-worktrees` when implementation starts. Create two worktrees from the same Task 11-complete base. Do not create them while merely preparing this handoff.

- [ ] **Step 4: Record the ready state**

Update `progress.md` with both matrix digests, brief paths, base commit, and status `ready for parallel implementation`. Do not mark Tasks 12 or 13 in progress until their implementers start.

- [ ] **Step 5: Stop at the handoff gate**

Commit the two controller briefs and ready-state ledger separately:

```bash
git add .superpowers/sdd/progress.md
git add -f .superpowers/sdd/task-12-brief.md .superpowers/sdd/task-13-brief.md
git diff --cached --check
git commit -m "docs(hades): prepare bounded parallel adapter work"
```

Then report the exact branch, HEAD, clean/dirty state, matrix digests, and brief paths to the human. The next execution uses `superpowers:subagent-driven-development`; it does not reopen adapter design or matrix scope.

---

## Final Verification Checklist

- [ ] The validator has no new third-party dependency and imports only existing v2 canonicalization.
- [ ] Corpus and matrix objects reject unknown keys and mutable revisions.
- [ ] Every exact matrix item has a finite partial/unresolved negative envelope.
- [ ] FastAPI corpus uses the six pinned revisions listed in Task 2.
- [ ] FastAPI matrix contains exactly the fourteen top-level IDs listed in Task 2.
- [ ] FastAPI Round 18 uses one `gpt-5.6-sol/xhigh` reviewer with no delegation.
- [ ] At most one repair batch and one scoped re-review occur.
- [ ] No exploratory Round 19 occurs; unresolved failure enters design escalation.
- [ ] Task 11 controller tests, aggregate lifecycle tests, Ruff, compile, and diff gates are fresh and green before completion.
- [ ] Express and Next.js locks exist before their production adapters are implemented.
- [ ] Express and Next.js have disjoint briefs and can start in two worktrees from one base.
- [ ] The pre-existing controller ledger change is never accidentally staged in a source commit.
- [ ] No FastAPI debt refactor is added to the Plan 1 critical path.
