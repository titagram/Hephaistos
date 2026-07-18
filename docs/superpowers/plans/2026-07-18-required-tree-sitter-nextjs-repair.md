# Required Tree-sitter and Next.js Ownership Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Tree-sitter a guaranteed Hades Agent dependency, fail graph indexing before publication when the parser installation is broken, and replace the fragile Next.js middleware-return ownership regex with Tree-sitter structural ownership.

**Architecture:** Hades ships one pinned Tree-sitter runtime and four pinned official precompiled grammar wheels for JavaScript/TypeScript, PHP, and Python. Grammar loading is offline and never creates a runtime download cache. The graph-index boundary runs fixed in-memory canaries for detected supported languages; after that boundary, only a failure confined to an ordinary source file becomes partial coverage. `SyntaxIR` records byte spans and nearest callable ownership without retaining source, and the Next.js adapter uses those facts to classify only returns owned by the exported middleware function. The Codex plugin remains a thin Hades CLI orchestrator.

**Tech Stack:** Python 3.11–3.13, `jsonschema==4.26.0`, `tree-sitter==0.26.0`, `tree-sitter-javascript==0.25.0`, `tree-sitter-typescript==0.23.2`, `tree-sitter-php==0.24.1`, `tree-sitter-python==0.25.0`, pytest, uv, Hades CLI, Codex personal plugin marketplace.

## Global Constraints

- Work in `/Users/gabriele/Dev/Hephaistos/.worktrees/tree-sitter-required-indexer` on `codex/tree-sitter-required-indexer`.
- Treat `docs/superpowers/specs/2026-07-16-graph-lifecycle-v2-design.md` and the amended Plan 1 Task 16 as normative.
- Do not add `hades-indexer`, `graph-index`, another dependency extra, or a lazy dependency group.
- Do not add a core model tool or a second HTTP/backend implementation to the Codex plugin.
- Do not retain raw source or a native Tree-sitter tree in `SyntaxIR`.
- A missing/incompatible required parser blocks graph publication; a source-file-specific parse failure after successful canaries is partial.
- The repair may change only parser ownership facts and Next.js middleware return ownership. Existing route, matcher, rewrite, redirect, and Pages API semantics remain unchanged.
- Use RED/GREEN TDD and one scoped commit per task.
- Preserve the untracked `.codex-peer` and `.superpowers/brainstorm` directories in the parent checkout.

---

### Task 1: Freeze Mandatory Dependency and Failure Contracts

**Files:**
- Modify: `docs/superpowers/specs/2026-07-16-graph-lifecycle-v2-design.md`
- Modify: `docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-master.md`
- Modify: `docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/test_project_metadata.py`

**Interfaces:**
- Produces mandatory exact requirements for Tree-sitter plus the official JavaScript, TypeScript, PHP, and Python grammar wheels in `project.dependencies`.
- Guarantees `project.optional-dependencies` has no `hades-indexer` key and `tools.lazy_deps.LAZY_DEPS` contains neither parser package.

- [x] **Step 1: Add the RED metadata test**

Add this helper and test to `tests/test_project_metadata.py`:

```python
def _load_dependencies():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        return tomllib.load(handle)["project"]["dependencies"]


def test_tree_sitter_is_required_and_never_lazy_installed():
    dependencies = set(_load_dependencies())
    optional_dependencies = _load_optional_dependencies()

    assert {
        "jsonschema==4.26.0",
        "tree-sitter==0.26.0",
        "tree-sitter-javascript==0.25.0",
        "tree-sitter-typescript==0.23.2",
        "tree-sitter-php==0.24.1",
        "tree-sitter-python==0.25.0",
    } <= dependencies
    assert "hades-indexer" not in optional_dependencies

    from tools.lazy_deps import LAZY_DEPS

    flattened = {
        requirement
        for value in LAZY_DEPS.values()
        for requirement in ((value,) if isinstance(value, str) else value)
    }
    assert not any("tree-sitter" in requirement for requirement in flattened)
```

- [x] **Step 2: Run RED**

Run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/test_project_metadata.py::test_tree_sitter_is_required_and_never_lazy_installed
```

Expected: FAIL because neither exact requirement is in `project.dependencies`.

- [x] **Step 3: Add the exact base requirements**

Add these entries to the core `dependencies` array in `pyproject.toml`, next to other built-in capability dependencies:

```toml
  # Imported directly by the mandatory Hades graph-v2 contract validator.
  "jsonschema==4.26.0",
  # Required structural parser for Hades graph indexing. Official grammar
  # wheels are available offline and produce the same structural truth.
  "tree-sitter==0.26.0",
  "tree-sitter-javascript==0.25.0",
  "tree-sitter-typescript==0.23.2",
  "tree-sitter-php==0.24.1",
  "tree-sitter-python==0.25.0",
```

Do not edit `tools/lazy_deps.py`.

- [x] **Step 4: Regenerate the lock and run GREEN**

Run:

```bash
uv lock
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/test_project_metadata.py
```

Expected: all metadata tests PASS and `uv.lock` contains both exact packages.

- [x] **Step 5: Verify the wheel footprint and commit**

Run:

```bash
git diff --check
git diff -- pyproject.toml uv.lock tests/test_project_metadata.py docs/superpowers/specs/2026-07-16-graph-lifecycle-v2-design.md docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-master.md docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md
```

Commit:

```bash
git add pyproject.toml uv.lock tests/test_project_metadata.py docs/superpowers/specs/2026-07-16-graph-lifecycle-v2-design.md docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-master.md docs/superpowers/plans/2026-07-16-graph-lifecycle-v2-01-contract-indexer-agent.md docs/superpowers/plans/2026-07-18-required-tree-sitter-nextjs-repair.md
git commit -m "build(hades): require lifecycle parser dependencies"
```

### Task 2: Add Parser Canaries and Callable Ownership Facts

**Files:**
- Modify: `hermes_cli/hades_index/tree_sitter_adapter.py`
- Modify: `hermes_cli/hades_index/resolution.py`
- Modify: `hermes_cli/hades_index/__init__.py`
- Modify: `tests/hermes_cli/test_hades_index_enrichment.py`
- Modify: `tests/hermes_cli/test_hades_lifecycle_control_flow.py`

**Interfaces:**
- Produces `RequiredParserUnavailable(RuntimeError)` with a sorted tuple of public language names and no raw exception text.
- Produces `TreeSitterAdapter.require_languages(languages: Iterable[str]) -> None`.
- Extends `StructuralSymbol` with `structural_path: str`.
- Extends `SyntaxControl` with `owner_structural_path: str`, `start_byte: int`, and `end_byte: int`.
- `SyntaxIR` still retains no source bytes or native tree.
- The public `typescript` canary validates both `language_typescript` and `language_tsx`; `.tsx` selects the latter without changing the public language value.
- `build_graph_for_workspace()` re-raises `RequiredParserUnavailable`; its generic optional-enricher fallback must not swallow an installation defect.

- [x] **Step 1: Add RED canary and ownership tests**

Add tests that assert these exact invariants:

```python
def test_required_parser_canary_fails_before_graph_enrichment(tmp_path):
    adapter = TreeSitterAdapter(parser_loader=lambda _language: None)
    with pytest.raises(RequiredParserUnavailable) as raised:
        adapter.require_languages(("typescript", "javascript"))
    assert raised.value.languages == ("javascript", "typescript")


def test_return_controls_retain_nearest_callable_owner_and_byte_span():
    source = b"""export function middleware() {
  function unused({ value }) { return NextResponse.redirect('/ghost') }
  for await (const item of items) { if (item) return NextResponse.redirect('/loop') }
  return NextResponse.next()
}
"""
    result = TreeSitterAdapter().parse_bytes(
        source, path="middleware.ts", language="typescript"
    )
    assert result.status == "parsed"
    returns = [control for control in result.syntax.controls if control.kind == "return"]
    assert len(returns) == 3
    assert returns[0].owner_structural_path != returns[1].owner_structural_path
    assert returns[1].owner_structural_path == returns[2].owner_structural_path
    assert all(source[item.start_byte:item.end_byte].lstrip().startswith(b"return") for item in returns)
```

Add a separate fake-parser test proving that `require_languages()` passes canaries, then `parse_bytes()` can still return `parser_failed` with `CoverageOutcome.PARTIAL` for a later ordinary file.

- [x] **Step 2: Run RED**

Run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py
```

Expected: FAIL because the exception, canary method, byte spans, and callable owner do not exist.

- [x] **Step 3: Implement one pinned parser loader**

Replace `_load_parser()` compatibility probing with exactly:

```python
def _load_parser(language: str) -> Any | None:
    grammar_name = "typescript" if language == "typescript" else language
    try:
        tree_sitter = importlib.import_module("tree_sitter")
        grammar_module, factory_name = _GRAMMAR_FACTORIES[grammar_name]
        grammar = importlib.import_module(grammar_module)
        return tree_sitter.Parser(tree_sitter.Language(getattr(grammar, factory_name)()))
    except (ImportError, AttributeError, TypeError, ValueError):
        return None
```

Do not probe `tree_sitter_language_pack` or `tree_sitter_languages`, and never download a grammar at runtime.

- [x] **Step 4: Implement canaries and callable ownership**

Use this fixed canary table:

```python
_LANGUAGE_CANARIES = {
    "javascript": b"function hadesCanary() { return 1; }\n",
    "typescript": b"function hadesCanary(): number { return 1; }\n",
    "tsx": b"function HadesCanary() { return <main>ok</main>; }\n",
    "php": b"<?php function hades_canary(): int { return 1; }\n",
    "python": b"def hades_canary() -> int:\n    return 1\n",
}
```

`require_languages()` sorts and deduplicates supported names, loads each parser, parses its canary directly, and raises once with every failed public language. The public `typescript` language requires both the TypeScript and TSX variants. Do not call `parse_bytes()` from the canary because `parse_bytes()` intentionally converts installation failures into per-file results.

During `visit()`, carry `owner_structural_path`. Set it to the current node path when entering a JavaScript/TypeScript/PHP/Python executable callable node; branches, loops, and `for await` never replace it. Emit every control with the nearest owner plus exact node byte offsets. Fill `StructuralSymbol.structural_path` from the symbol node path.

- [x] **Step 5: Enforce fail-fast only at the graph-index boundary**

In `enrich_graph_for_workspace()`:

1. derive the sorted set of supported languages from candidate suffixes;
2. instantiate `TreeSitterAdapter`;
3. call `adapter.require_languages(detected_languages)` before merging any facts;
4. remove the `tree_sitter=false` disabled branch;
5. keep existing per-file `ParseResult.failed(...)` coverage handling after the canary.

In `build_graph_for_workspace()`, re-raise `RequiredParserUnavailable` before the generic optional-enricher `except Exception` fallback. Add a regression through this real publication boundary; a direct `enrich_graph_for_workspace()` test alone is insufficient.

- [x] **Step 6: Run GREEN, privacy checks, and commit**

Run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py
/Users/gabriele/Dev/Hephaistos/.venv/bin/ruff check hermes_cli/hades_index/tree_sitter_adapter.py hermes_cli/hades_index/resolution.py tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py
git diff --check
```

Commit:

```bash
git add hermes_cli/hades_index/tree_sitter_adapter.py hermes_cli/hades_index/resolution.py tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py
git commit -m "feat(hades): require structural parser canaries"
```

### Task 3: Replace Next.js Middleware Return Regex Ownership

**Files:**
- Add from reviewed branch: `hermes_cli/hades_index/lifecycle/frameworks/nextjs.py`
- Add from reviewed branch: `tests/hermes_cli/test_hades_lifecycle_nextjs.py`
- Modify: `hermes_cli/hades_index/lifecycle/frameworks/nextjs.py`
- Modify: `tests/hermes_cli/test_hades_lifecycle_nextjs.py`

**Interfaces:**
- Consumes `StructuralSymbol.structural_path` and `SyntaxControl.owner_structural_path/start_byte/end_byte` from Task 2.
- `_middleware_rules(source: str, path: str, syntax: SyntaxIR) -> tuple[_ConfigRule, ...]` classifies only return controls owned by the exported `middleware` callable.
- No `_nested_callable_bodies()` function or brace/method ownership regex remains.

- [x] **Step 1: Import the reviewed remote commits without merging them**

Fetch the server-local branch and cherry-pick its three existing commits in order:

```bash
git fetch ssh://ubuntu@162.19.229.31/home/ubuntu/dev-sandbox codex/nextjs-lifecycle-v2
git cherry-pick e5ed5bc54 0b412baa4 4ddf8dbba
```

Verify exactly the expected two code/test paths were introduced by those commits. Do not treat `4ddf8dbba` as approved; its review found the two regressions below.

- [x] **Step 2: Add exact RED regressions**

Extend `test_unproven_middleware_outcome_is_partial` with both sources and assert:

```python
destructured = """export function middleware() {
  function unused({ value }) { return NextResponse.redirect('/ghost') }
  return NextResponse.next()
}
"""
assert _middleware_roles(tmp_path, destructured) == ["middleware_next"]

for_await = """export async function middleware(request) {
  for await (const item of request.items) {
    if (item) return NextResponse.redirect('/loop')
  }
  return NextResponse.next()
}
"""
assert _middleware_roles(tmp_path, for_await) == [
    "middleware_redirect",
    "middleware_next",
]
```

Update the test `_syntax()` helper to parse the actual fixture bytes with `TreeSitterAdapter`; do not fabricate empty `SyntaxIR` for JavaScript/TypeScript adapter acceptance.

- [x] **Step 3: Run RED**

Run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_lifecycle_nextjs.py
```

Expected: the destructured parameter leaks `middleware_redirect` and `for await` loses it, matching review findings `T13-NEXT-005-R2A` and `T13-NEXT-005-R2B`.

- [x] **Step 4: Implement Tree-sitter return ownership**

In `_build_snapshot()`, pass the current `SyntaxIR` to `_middleware_rules()`. Locate exactly one exported middleware function symbol by `name == "middleware"`, `kind == "function"`, and non-empty `structural_path`. Select `return` controls whose `owner_structural_path` equals that symbol path. Sort them by `start_byte`, slice only `source.encode("utf-8")[start_byte:end_byte]`, and apply the existing bounded outcome patterns to that exact return statement.

If the middleware symbol is absent or ambiguous, emit one `_ConfigRule(kind="unresolved", reasons=("middleware_outcome_unresolved",))`; never fall back to the removed ownership regex. Preserve literal redirect destinations and existing rule ordering.

- [x] **Step 5: Run focused and aggregate GREEN gates**

Run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_lifecycle_nextjs.py
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/hermes_cli/test_hades_lifecycle_framework_adapter.py tests/hermes_cli/test_hades_lifecycle_symfony.py tests/hermes_cli/test_hades_lifecycle_laravel.py tests/hermes_cli/test_hades_lifecycle_django.py tests/hermes_cli/test_hades_lifecycle_fastapi.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
/Users/gabriele/Dev/Hephaistos/.venv/bin/ruff check hermes_cli/hades_index/lifecycle/frameworks/nextjs.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
git diff --check
```

Run the locked Next.js acceptance validator from `docs/superpowers/plans/2026-07-17-finite-adapter-acceptance-gates-implementation.md` and require its checked-in digest to remain valid unless the new test corpus is deliberately versioned.

- [x] **Step 6: Commit**

```bash
git add hermes_cli/hades_index/lifecycle/frameworks/nextjs.py tests/hermes_cli/test_hades_lifecycle_nextjs.py
git commit -m "fix(hades): derive Next.js returns from syntax"
```

### Task 4: Update and Reinstall the Thin Codex Plugin

**Files:**
- Modify in the personal plugin source: `/Users/gabriele/plugins/hades-backend/skills/hades-backend/SKILL.md`
- Reinstall through marketplace: `/Users/gabriele/.agents/plugins/marketplace.json`

**Interfaces:**
- The plugin never imports Tree-sitter and never calls graph HTTP endpoints directly.
- A user-authorized `hades backend sync` or `bootstrap-awareness` automatically receives mandatory parser validation from the installed Hades Agent.
- The plugin reports `RequiredParserUnavailable` as an installation defect and stops; it does not retry sync or describe the result as partial.

- [x] **Step 1: Add the graph-index contract to the source skill**

Add a `Graph Index And Lifecycle` section stating:

```markdown
## Graph Index And Lifecycle

Tree-sitter is owned and versioned by the separately installed Hades Agent. Never install parser packages from this plugin and never reproduce parsing with shell scripts or model inference.

For a user-authorized graph refresh, run `hades backend sync` or the broader `hades backend bootstrap-awareness --yes --record-quality-report --json`, then verify with `hades backend status --json` and `hades backend quality-report --json`.

If Hades reports a required parser or grammar canary failure, stop. Report it as a broken Hades installation; do not retry, downgrade it to partial coverage, or call backend graph endpoints directly.

Use lifecycle query commands only when they appear in `hades backend --help` for the installed version. Until then, do not invent command names; use the frontend for lifecycle exploration.
```

- [x] **Step 2: Reinstall and verify the plugin**

Run:

```bash
codex plugin add hades-backend@personal
codex plugin list
```

Verify the enabled plugin path/version changed and the installed cached `skills/hades-backend/SKILL.md` contains `Graph Index And Lifecycle`. Start a new Codex task only for the final manual plugin smoke because the current task's skill catalog is immutable.

- [x] **Step 3: Run agent-to-plugin smoke checks**

From a temporary Hades workspace configuration, run read-only `hades backend status --json` and `hades backend quality-report --json`. Do not run a state-changing graph sync without a linked test project and explicit authorization. Confirm the plugin instructs Codex to delegate to Hades and exposes no duplicate parser dependency.

### Task 5: Final Verification and Handoff

**Files:**
- Verify only; modify nothing unless a gate exposes a regression.

- [x] **Step 1: Run the focused full tranche suite**

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q tests/test_project_metadata.py tests/hermes_cli/test_hades_index_enrichment.py tests/hermes_cli/test_hades_lifecycle_control_flow.py tests/hermes_cli/test_hades_lifecycle_framework_adapter.py tests/hermes_cli/test_hades_lifecycle_symfony.py tests/hermes_cli/test_hades_lifecycle_laravel.py tests/hermes_cli/test_hades_lifecycle_django.py tests/hermes_cli/test_hades_lifecycle_fastapi.py tests/hermes_cli/test_hades_lifecycle_nextjs.py tests/scripts/test_hades_adapter_acceptance.py
```

- [x] **Step 2: Prove a clean install can parse every supported canary**

Create a disposable virtual environment under `/private/tmp`, install the current checkout with development dependencies, and run:

```python
from hermes_cli.hades_index.tree_sitter_adapter import TreeSitterAdapter

TreeSitterAdapter().require_languages(("javascript", "typescript", "php", "python"))
```

Expected: exit 0 with no output.

- [x] **Step 3: Review and publish**

Run:

```bash
git status --short --branch
git log --oneline --decorate main..HEAD
git diff --check main...HEAD
```

Review dependency policy, parser fail-fast behavior, privacy, TSX coverage, and both Next.js regressions. When session policy permits delegation, request an independent reviewer; otherwise record that the primary agent performed the review and rely on the executable gates below. After approval, merge `codex/tree-sitter-required-indexer` into `main`, push `main`, update the installed Hades Agent, and rerun the four-language canary with the installed interpreter.

**Completion record (2026-07-18):**

- Primary-agent review found and repaired two publication-critical gaps before merge: `language_tsx` was not canary-tested and `build_graph_for_workspace()` swallowed `RequiredParserUnavailable`. Session policy did not permit starting an independent subagent reviewer.
- The focused tranche passed `292` tests before and after the merge; Ruff and `git diff --check` passed.
- A clean virtual environment installed the checkout with `jsonschema==4.26.0` and the five exact Tree-sitter packages. JavaScript, TypeScript, TSX, PHP, and Python canaries passed without `tree_sitter_language_pack` or `tree_sitter_languages`.
- `main` was merged and pushed at `c86447722`; the managed installation at `~/.hermes/hermes-agent` was updated to that commit and repeated the same parser canaries successfully.
- The thin Codex plugin was validated, cache-busted, reinstalled, and confirmed to delegate graph work to the Hades CLI without its own parser or graph HTTP client.

This focused repair does **not** complete Plan 1 Task 17. The v2 job/upload/sync/cache cutover remains the next integration task; in particular, legacy graph finalization must not be presented as a completed graph-v2 publication path until Task 17 replaces it.
