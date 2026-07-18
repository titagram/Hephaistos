# Finite Adapter Acceptance Gates

**Date:** 2026-07-17

**Status:** Approved design

**Applies to:** Graph Lifecycle v2 framework adapters, beginning with FastAPI Task 11

## 1. Problem

The current adapter workflow has no finite acceptance boundary. FastAPI Task 11
has accumulated seventeen review-and-repair rounds. Its focused suite grew from
7 to 109 tests, while the adapter grew to 7,188 lines and its test file to 7,808
lines. Later findings increasingly concern general Python abstract-interpretation
semantics rather than common FastAPI request-lifecycle behavior.

The failure is procedural, not a lack of test rigor or model capability. An
adversarial reviewer can always construct another valid language edge case. A
gate defined as "continue until no new edge case can be imagined" is therefore
unbounded.

## 2. Goals

1. Preserve soundness: claim `exact` only when the result is demonstrated.
2. Make every adapter task finite and its completion criteria knowable before
   implementation begins.
3. Cover common, real framework behavior rather than merely happy-path examples.
4. Make uncertainty explicit through `partial` or `unresolved` results.
5. Prevent silent loss, fabricated topology, invalid contracts, and
   nondeterministic artifacts.
6. Limit a task to one complete review, one repair, and one scoped re-review.
7. Allow two file-disjoint adapters to be implemented concurrently without
   weakening independent review.
8. Defer the existing FastAPI complexity reduction until after Plan 1 so that it
   does not block the v2 critical path.

## 3. Non-goals

- Implementing a complete Python or JavaScript interpreter.
- Making every syntactically valid program produce an exact lifecycle.
- Removing useful uncertainty merely to improve an exactness metric.
- Refactoring the current FastAPI adapter before Task 11 can be approved.
- Allowing wall-clock pressure to override soundness or contract validity.

## 4. Precision Contract

The adapter uses a conservative precision contract:

- `exact`: every material fact represented by the result is proven by the
  supported static semantics.
- `partial`: some useful facts are proven, but identified facts, paths, targets,
  or effects remain uncertain.
- `unresolved`: the adapter cannot safely establish the material lifecycle fact
  at the boundary in question.

Unsupported constructs must degrade explicitly. They must not be omitted
silently and must never be represented as exact through guessing.

An out-of-matrix construct that is correctly reported as `partial` or
`unresolved` is not a task blocker. It is a backlog candidate. This rule does
not excuse invariant violations such as a false `exact`, an invalid reference,
or silent data loss.

## 5. Bounded Corpus

Before implementation, the matrix author records a reproducible corpus manifest
for the framework and version under review. The manifest contains:

1. The official framework documentation, including the exact documentation
   version or immutable source revision.
2. Three independent public codebases representative of ordinary production
   use, each pinned to an immutable commit.
3. One owned target codebase when the framework is used by an active target
   project, also pinned to an immutable revision.
4. Minimal local fixtures that isolate every accepted pattern.

The matrix cannot be frozen if a required corpus entry is not pinned. Repeated
web research during implementation or review is prohibited; agents reuse the
recorded corpus manifest.

A pattern is common and therefore eligible for mandatory exact support when at
least one of these rules holds:

- it is the officially documented idiom for a core framework capability;
- it appears in at least two independent corpus codebases;
- it is necessary to express a fundamental lifecycle stage such as routing,
  middleware, dependencies, authorization, input, domain dispatch, persistence,
  exception handling, background dispatch, or response termination;
- it appears in an owned target codebase and is an ordinary framework idiom
  rather than an application-specific metaprogramming technique.

Patterns that satisfy none of these rules may still be supported, but cannot be
added as blocking requirements after the matrix is frozen.

## 6. Acceptance Matrix

Each adapter has a versioned acceptance matrix stored with its corpus manifest.
Every matrix item includes:

```yaml
id: FASTAPI-ROUTE-001
framework_version: "pinned by corpus manifest"
sources:
  - kind: official_docs
    reference: "immutable reference from corpus manifest"
construct: decorator_route
common_rule: official_core_idiom
expected_precision: exact
required_facts:
  - method
  - normalized_path
  - endpoint
allowed_unknowns: []
negative_variants:
  - id: FASTAPI-ROUTE-001-N1
    construct: dynamically_computed_decorator_target
    expected_precision: partial
test_nodes:
  - tests/hermes_cli/test_hades_lifecycle_fastapi.py::test_decorator_route
```

For every mandatory exact item, the matrix includes a finite negative envelope
covering the relevant ways proof can be lost. The negative envelope verifies
explicit degradation; it is not an invitation to enumerate the entire host
language.

The orchestrator canonicalizes the reviewed matrix and corpus manifest to JSON
with sorted keys and compact separators, calculates a SHA-256 digest, and
records that digest in the task brief. Implementation and review reports must
quote the same digest. Any semantic matrix change produces a new digest and
requires the task to return to the pre-implementation gate.

## 7. Blocking and Non-blocking Findings

The following findings block approval:

- a mandatory matrix item fails;
- a negative-envelope case fails to degrade as specified;
- a result claims exactness without sufficient proof;
- material uncertainty or omission is not represented in coverage or the
  relevant uncertainty structure;
- nodes, edges, stages, outcomes, references, ownership, or provenance violate
  the locked contract;
- output depends on filesystem order, hash iteration, or another uncontrolled
  ordering source;
- the adapter crashes on a bounded input, violates privacy, or exceeds a locked
  resource budget;
- a repair introduces a regression directly covered by the frozen matrix.

The following findings do not block approval:

- a new out-of-matrix construct that degrades conservatively;
- a request for broader exact coverage without qualifying corpus evidence;
- stylistic preferences or refactoring not required by a contract invariant;
- a theoretical edge case that produces neither false exactness nor silent
  loss;
- a proposed capability whose consumer belongs to a later matrix version.

Every blocking finding must contain a stable identifier, the violated matrix ID
or invariant category, a concrete reproducer, the actual result, the required
result, and the blocking rationale. A finding missing any of these fields is
returned to the reviewer as incomplete rather than sent to implementation.

## 8. Finite Per-task Workflow

### 8.1 Matrix gate

1. An independent reviewer using `gpt-5.6-sol` with `xhigh` reasoning prepares
   the corpus manifest, acceptance matrix, negative envelope, and tests.
2. The orchestrator checks corpus qualification, test mapping, ambiguity,
   contract consistency, and completeness of uncertainty expectations.
3. The orchestrator freezes and records the digest.

### 8.2 Implementation gate

1. An implementer using `gpt-5.6-terra` with `high` reasoning receives a closed
   brief containing the base commit, matrix digest, owned files, forbidden
   files, focused commands, completion definition, and stop conditions.
2. The implementer changes only the declared scope and runs focused tests plus
   format, lint, compile, and diff checks.
3. The implementer performs a self-review against every matrix ID before asking
   for independent review.

### 8.3 Independent review gate

1. A distinct `gpt-5.6-sol/xhigh` reviewer performs one complete pass without
   editing production code and without spawning another reviewer.
2. The reviewer reports all blocking findings in one batch.
3. The reviewer cannot expand the frozen matrix.
4. The original implementer receives at most one repair assignment containing
   the complete batch.
5. The same reviewer re-checks only the existing finding IDs and regressions
   directly caused within the frozen matrix.
6. No exploratory review round follows a successful scoped re-review.

### 8.4 Escalation instead of endless repair

If the single repair does not close the batch, the task enters `design
escalation`; it does not start another automatic repair round. The orchestrator
classifies the cause as a matrix defect, architectural defect, or implementation
defect and proposes a bounded new task version. The existing evidence and code
are retained. A new task version has a new brief and, when necessary, a new
matrix digest.

## 9. Parallel Execution

At most two implementations run concurrently. Each uses a separate Git
worktree and exclusive file ownership. Shared registry, lock, generated, or
integration files are reserved for the orchestrator.

For Plan 1 after FastAPI approval:

1. Freeze Express and Next.js matrices.
2. Implement Express and Next.js concurrently from the same approved FastAPI
   base.
3. Integrate Express first.
4. Rebase or transplant Next.js onto the integrated base and resolve only
   orchestrator-owned integration surfaces.
5. Run the aggregate adapter suite once on the integrated result.

The implementer runs focused tests. The independent reviewer runs matrix tests.
The orchestrator runs aggregate tests after integration. Full expensive suites
run at milestone gates rather than after every local edit.

## 10. Immediate FastAPI Transition

Task 11 is evaluated from current commit
`534a8d70f8fdb6c8eeb6158e8ce5e4b273311ee2`.

1. Build the retroactive matrix from the original task specification, pinned
   FastAPI/Starlette documentation, and the bounded corpus.
2. Map existing tests to matrix IDs. Existing tests remain regression evidence
   during Plan 1 but do not authorize requirements outside the frozen matrix.
3. Add only tests required to represent a missing frozen matrix item or its
   finite negative envelope.
4. Conduct Round 18 as the single independent review defined above.
5. If blocked, issue one complete repair batch and one scoped re-review.
6. After a clean verdict, run controller gates, update the progress ledger, mark
   Task 11 complete, and begin Tasks 12 and 13.
7. Do not conduct an exploratory Round 19. Any unresolved structural failure
   enters design escalation as a new task version.

## 11. Deferred FastAPI Debt Task

After the Plan 1 exit gate, create a separate behavior-preserving debt task.
Its inputs are the frozen FastAPI matrix, golden artifacts, and full regression
suite. Its goal is to extract reusable language semantics and reduce adapter
complexity without changing the locked output contract.

The debt task is not a prerequisite for Express, Next.js, traversal, pruning,
bundling, backend integration, or Plan 1 producer gates.

## 12. Backlog Governance

An unsupported-pattern backlog record contains:

- framework and version;
- normalized construct;
- immutable corpus evidence;
- observed frequency;
- current precision and uncertainty output;
- requested precision;
- user value;
- false-exact or silent-loss risk;
- proposed future matrix version.

Only the matrix gate of a new task version may promote a backlog record into a
blocking requirement. Review and repair agents cannot promote it.

## 13. Measurements

Record these metrics per adapter:

- number of complete review-and-repair cycles, target at most one;
- elapsed time from frozen digest to approval;
- focused, matrix, aggregate, and milestone test counts and durations;
- blocking findings by matrix ID or invariant category;
- out-of-matrix suggestions sent to backlog;
- percentage of mandatory common patterns producing exact results;
- percentage of negative-envelope cases degrading as declared;
- source and test line growth;
- model role and reasoning effort used for matrix, implementation, and review.

The strategy succeeds when Task 11 closes without another exploratory round,
Tasks 12 and 13 execute concurrently, all frozen exact and negative cases pass,
uncertainty remains explicit, and Plan 1 advances predictably to traversal,
bundling, integration, and producer gates.

## 14. Operational Invariants

- Prompt and tool surfaces remain stable within each long-lived agent
  conversation.
- Agents receive bounded, structured assignments and cannot redefine their own
  responsibilities.
- Reviewers do not edit the implementation they review.
- Implementers do not approve their own work.
- Controller-owned ledgers and reports are not staged in scoped source commits.
- Existing unrelated worktree modifications are preserved.
- A green test suite is necessary but does not override a false-exact or silent
  loss finding.
- A desire for more capability is not itself a correctness finding.
