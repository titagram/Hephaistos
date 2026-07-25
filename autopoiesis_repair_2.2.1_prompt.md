# AUTOPOIESIS PROJECT B — REPAIR PASS 2.2.1
## Host authority and approval-ledger coherence

Use **DeepSeek-V4-Pro** for the whole pass.

Do not use V4-Flash for architecture, authorization, schema migration, host
integration, or security tests. V4-Flash may be used only after the Pro work is
complete for purely mechanical commands such as formatting, compileall, and
rerunning an already-defined test matrix. It may not edit security-sensitive
code or tests.

Do not claim any SOL review gate passed. SOL gates require a later independent
`GPT-5.6-Sol high` review and are not part of this execution pass.

## Objective

Repair only the Telos host-approval boundary and its persistent audit schema.
Do not proceed to TypeScript or to the remaining Project B repairs in this
pass.

This is a corrective pass over the nine commits ending at:

```text
5924446cd507c72c9edfb37f416f07c9e52ec54a
```

The current implementation is not accepted. A fresh independent audit
reproduced authorization and schema defects even though the selected tests
pass.

## Repository and branch

Work only here:

```text
/Users/gabriele/Dev/Hephaistos/.worktrees/autopoiesis-project-b-design
```

Required branch:

```text
codex/autopoiesis-project-b-design
```

Expected starting HEAD:

```text
5924446cd507c72c9edfb37f416f07c9e52ec54a
```

Before editing, run:

```bash
git status --short --branch
git rev-parse HEAD
git log --oneline -12
```

There is an unrelated untracked prompt file:

```text
autopoiesis_plan_repair_2.2_prompt.md
```

Do not stage, edit, delete, or commit it. The current prompt may also be
untracked; do not commit it.

## Authoritative documents

Read completely before editing:

```text
docs/superpowers/specs/2026-07-24-autopoiesis-project-b-opportunity-observer-design.md
docs/superpowers/plans/2026-07-24-autopoiesis-project-b-global-observer.md
AGENTS.md
```

Also inspect the existing host approval implementation and all of its real
callers before designing the repair:

```text
tools/approval.py
gateway/slash_commands.py
gateway/run.py
cli.py
tui_gateway/server.py
hermes_cli/evolution/telos_approval.py
hermes_cli/evolution/telos_store.py
hermes_cli/evolution/ledger.py
hermes_cli/subcommands/evolution.py
hermes_cli/evolution/command.py
```

Use `git log -p -S` on the relevant approval symbols to understand the original
dangerous-command approval intent. Do not guess how the existing host boundary
works.

## Hard scope constraints

Do not modify:

- `ui-tui/src/**`;
- `apps/desktop/src/**`;
- the approved spec;
- the implementation plan;
- `.gitignore`;
- the remote backend;
- canonical graph, projector, Graph Explorer, query/explorer service,
  dashboard graph reader, `ImportGraphToNeo4j`, Graph/Hades/DeltaSync tests;
- unrelated Hermes behavior.

Do not deploy, restart, migrate a live database, push, amend, squash, rebase, or
force anything.

Make focused commits on top of the current HEAD.

## TDD rules

For every defect:

1. Add or restore the smallest behavior test.
2. Run the exact node and capture a genuine RED result caused by the defect.
3. Implement the minimal production fix.
4. Run the same node and capture GREEN.
5. Run the relevant regression set.
6. Inspect the diff before committing.

Do not:

- weaken existing tests;
- change expected secure behavior to match current code;
- convert failures into skips or xfails;
- catch broad exceptions merely to make tests green;
- use monkeypatches that bypass the real approval path;
- create test-only production APIs such as `_test_create`;
- use Python naming conventions as a security boundary;
- pre-approve in a fixture before an approval-boundary test begins.

Every security test must name the production change that would make it fail.

## Independently reproduced defects

Treat the following as facts to repair, not hypotheses.

### 1. A registered but unrelated capability authorizes forged SQLite rows

The current tests reject only an **unregistered** capability. They do not test
a live capability registered for the wrong actor/surface/request.

The audit performed this sequence:

1. create a real organism and Telos revision;
2. directly insert request, approved decision, grant, and consumption rows;
3. construct `HostApprovalCapability("unrelated_surface",
   "unrelated_actor")`;
4. call public `set_host_capability(capability)`;
5. call `TelosStore.activate_revision(...)`.

Activation succeeded.

Root causes include:

- public `HostApprovalCapability.__init__`;
- public `set_host_capability`;
- registry membership is not bound to exact request context;
- `TelosStore` verifies only generic registry membership plus row presence;
- the capability is not consumed/revoked by the store transition;
- persistent rows and a generic live token compose into authority.

Required RED test:

```text
test_registered_unrelated_capability_cannot_authorize_forged_rows
```

It must use a capability that is genuinely live for a different request,
actor, surface, session/channel, nonce, context digest, organism, digest, or
action. Activation must fail with no pointer or LKG mutation.

### 2. Approval rows can be cross-wired

The v4 schema accepts:

```text
request A
approved decision B -> request B
grant A -> request A + decision B
```

The existing trigger checks that the decision is approved and separately
checks the grant fields against request A. It never proves that the decision's
`request_id` equals the grant's `request_id`.

Required RED tests:

```text
test_grant_rejects_decision_for_different_request
test_decision_rejects_wrong_host_context
```

Run them against:

- a fresh current-version database;
- a migrated v4 database.

### 3. The Gateway host flow is not a complete transition

`gateway/slash_commands.py::_handle_telos_approve_command` currently:

- constructs a public capability;
- registers it in a private `self._telos_registry`;
- records only a decision;
- revokes it;
- returns “approved”.

It does not:

- use the registry checked by `TelosStore`;
- bind the stored request fields to the event;
- issue a grant;
- consume authority;
- activate or roll back;
- complete an atomic live host transition.

Therefore the command reports success while no Telos transition occurs.

Required real-dispatch tests:

```text
test_gateway_telos_approve_completes_exact_pending_activation
test_gateway_telos_approve_rejects_wrong_actor_or_session
test_gateway_telos_approve_rejects_expired_request
test_gateway_telos_approve_rejects_replay
test_gateway_telos_approve_does_not_inherit_all_session_always
test_gateway_telos_deny_records_denial_without_transition
```

Exercise real Gateway slash dispatch with a real `MessageEvent`. Do not call
the broker directly as a substitute.

### 4. Existing host surfaces are not actually wired

Current production reference scan shows:

- `telos_approval_prompt()` has no production caller;
- no TUI Gateway `domain="telos"` approval request/response protocol exists;
- Gateway is the only production constructor of `HostApprovalCapability`;
- Gateway does not complete a transition.

In this pass, implement the shared Python host-owned transition service and
wire the Gateway fully.

Classic CLI and TUI Gateway may be left for the next bounded repair pass only
if the shared service is explicitly designed for them and the final report
lists both as pending. Do not claim all Python surfaces complete.

### 5. TelosStore closes caller-owned ledgers

Both `activate_revision()` and `rollback()` close `ledger.connection` in
`finally` even when the caller supplied the ledger.

Required tests:

```text
test_activate_does_not_close_caller_owned_ledger
test_rollback_does_not_close_caller_owned_ledger
```

Track ownership explicitly. Close only ledgers created inside the method.

### 6. Approval tests still prove a weaker contract

The current adversarial test uses an unregistered capability and therefore
does not reproduce the stronger attack. The current helpers call
`_test_create()` and `set_host_capability()` and can make tests green without
exercising a real host decision.

Remove test-only authority shortcuts from end-to-end security tests. A unit
test may use a narrowly scoped fixture factory only if that factory cannot be
called by production and the E2E tests still traverse the real host adapter.

## Required architecture

Before changing production code, write a concise trust-boundary note in the
first commit message body or final report covering:

- trusted live host process;
- model-controlled command/terminal process;
- volatile transition authority;
- persistent append-only audit evidence;
- exact transition boundary;
- why a restart cannot convert rows into authority.

Use or extend the existing host approval architecture in `tools/approval.py`.
Do not create another self-contained approval universe that any caller can
instantiate and use against the ledger.

Telos approval must be a separate domain:

```text
domain = "telos"
```

It must never inherit:

- approve-all;
- session approval;
- always/permanent approval;
- YOLO/off mode;
- dangerous-command approval cache;
- config or environment bypass;
- clarify;
- model-generated free-text confirmation.

The model-facing CLI may only:

- create/resume a pending exact request;
- inspect status;
- cancel its own still-pending request if supported.

It may not:

- record a host decision;
- issue authoritative transition authority;
- consume it;
- activate;
- roll back.

The live host flow must perform the exact transition and revoke volatile
authority in `finally`.

Any volatile transition object must be bound to all of:

- request ID;
- organism ID;
- Telos digest;
- action (`activate` or `rollback`);
- host surface;
- actor;
- session/channel;
- nonce;
- context digest;
- expiry;
- single-use lifecycle.

SQLite is audit evidence, not standalone authority.

Do not claim protection against arbitrary malicious same-UID code. The
required guarantee is narrower and testable: the official model-controlled
command/terminal path cannot complete a Telos transition without a live host
decision.

If this guarantee cannot be implemented coherently with the current process
model, stop without further code changes and report the exact architectural
blocker.

## Schema repair

Do not silently redefine schema version 4. It already exists in history.

Inspect whether additive v5 triggers are sufficient. If existing v4 rows can
be incoherent, introduce an explicit v4→v5 migration with:

- `SCHEMA_VERSION = 5`;
- fresh-v5 schema;
- v4→v5 migration;
- v1→v2→v3→v4→v5 chain;
- repeated-open idempotency;
- rollback on migration failure;
- preservation of every Project A row;
- no destructive table replacement without a documented rule.

Required v5 invariants:

- one decision per request;
- decision request exists;
- decision context equals the request's expected context;
- decision time is not after request expiry;
- one grant per request and decision;
- grant decision belongs to the same request;
- grant decision is approved;
- grant organism/digest/action equal request values;
- one consumption per grant;
- consumption organism/digest/action equal grant values;
- consumption time is not after grant expiry;
- denied or missing decision cannot produce a grant;
- UPDATE and DELETE remain forbidden;
- `PRAGMA foreign_keys=ON` on every repository connection.

Historical incoherent v4 rows must be preserved as audit evidence but
quarantined or otherwise excluded from valid authorization-chain queries.
They must never authorize a transition.

Add direct-SQL tests for:

- request A plus decision B;
- wrong context;
- wrong organism;
- wrong digest;
- wrong action;
- denied decision;
- missing decision;
- duplicate decision;
- duplicate grant;
- duplicate consumption;
- expired request;
- expired grant;
- UPDATE;
- DELETE.

## Required implementation order

### Phase 1 — honest RED tests

Add the stronger tests first. Run each and capture RED.

Do not commit tests that already pass for the wrong reason. If a proposed test
passes on starting HEAD, strengthen it until it reproduces the defect.

Keep the reproduced tests uncommitted until the corresponding production fix
is GREEN. Do not create an intentionally red commit.

### Commit 1 — host authority core

Implement the shared host-owned exact transition boundary. Remove or make
unusable the generic public authority path.

The transition must fail before pointer/LKG mutation on any binding mismatch.
Authority must be single-use and revoked in `finally`.

Commit the relevant Phase 1 tests together with this GREEN fix.

Suggested commit:

```text
fix(autopoiesis): bind telos transitions to live host decisions
```

### Commit 2 — schema v5 coherence

Implement the compatible migration and fresh schema rules. Preserve and
quarantine incoherent historical evidence.

Suggested commit:

```text
fix(autopoiesis): migrate telos approval audit chains to coherent v5
```

### Commit 3 — Gateway real host flow

Wire `/approve telos <request-id>` and the corresponding deny path through real
slash dispatch.

Approval must complete the exact activation/rollback before returning success.
Denial must append denial evidence and leave pointers unchanged.

Any failure response must be bounded and must not expose paths, tokens, raw
context, or actor identifiers.

Suggested commit:

```text
fix(autopoiesis): complete gateway telos approval transitions
```

### Commit 4 — ownership and regression cleanup

Fix caller-owned ledger lifetime, remove stale imports/test-only authority
helpers where possible, and restore full Telos store behavior tests:

1. revision A inert;
2. direct activation fails;
3. exact host activation succeeds;
4. replay fails;
5. amendment B succeeds;
6. LKG becomes A before B becomes active;
7. exact rollback to A succeeds;
8. later revision/history remains present;
9. caller ledger remains open.

Suggested commit:

```text
test(autopoiesis): restore exact telos lifecycle and ownership coverage
```

Split a commit if needed, but do not combine unrelated remaining Project B
work into this pass.

## Mandatory acceptance tests

Every row below needs:

- exact pytest node ID;
- exact command;
- RED output before the fix;
- GREEN output after the fix, where applicable.

Required:

```text
direct SQLite rows + no live host authority -> rejected
direct SQLite rows + unrelated live capability -> rejected
standalone grant ID -> rejected
consumed row after process restart -> rejected
grant for revision A used on revision B -> rejected
activate grant used for rollback -> rejected
wrong organism -> rejected
wrong actor -> rejected
wrong surface -> rejected
wrong session/channel -> rejected
wrong nonce -> rejected
wrong context digest -> rejected
expired request -> rejected
expired grant -> rejected
revoked authority -> rejected
replay -> rejected
request A + decision B -> rejected
/approve all does not approve Telos
/approve session does not approve Telos
/approve always does not approve Telos
model-facing `hermes evolution telos approve` cannot activate
Gateway exact approval activates once
Gateway denial never activates
caller-owned ledger remains usable
```

Do not replace this matrix with a combined pass count.

## Required regression commands

At minimum run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q \
  tests/hermes_cli/evolution/test_telos_adversarial.py \
  tests/hermes_cli/evolution/test_telos_schema_integrity.py \
  tests/hermes_cli/evolution/test_telos_approval_security.py \
  tests/hermes_cli/evolution/test_telos_contract_and_store.py
```

Run the exact 14 formerly deferred tests and report their node IDs.

Run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q \
  tests/gateway/test_approve_deny_commands.py
```

Run the new Gateway Telos tests through actual slash dispatch.

Run the full Evolution suite:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q \
  tests/hermes_cli/evolution/
```

Important: current HEAD has a reproducible order-dependent failure:

```text
tests/hermes_cli/evolution/test_config_breaker_isolation.py::
test_global_config_isolated_from_profile
```

It is not an xdist or subprocess flake. `global_config.py` caches an imported
`get_default_hermes_root` reference, so suite order changes behavior.

Because this defect blocks the full suite, make only the narrowest repair
needed to remove import-order dependence and add explicit UTF-8 encodings.
Do not expand into the remaining global-config work in this pass.

Run:

```bash
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q \
  tests/hermes_cli/evolution/test_config_breaker_isolation.py
```

Then static checks on every changed Python file:

```bash
git diff --name-only --diff-filter=ACMR \
  5924446cd507c72c9edfb37f416f07c9e52ec54a..HEAD -- '*.py' \
  | xargs /Users/gabriele/Dev/Hephaistos/.venv/bin/python -m ruff check
git diff --name-only --diff-filter=ACMR \
  5924446cd507c72c9edfb37f416f07c9e52ec54a..HEAD -- '*.py' \
  | xargs /Users/gabriele/Dev/Hephaistos/.venv/bin/python -m compileall -q
git diff --check 5924446cd507c72c9edfb37f416f07c9e52ec54a..HEAD
```

Current HEAD has two Ruff errors in `global_config.py` for missing explicit
encoding. Do not report Ruff green until the exact command exits zero.

## Explicitly pending after this pass

Do not touch or claim completion of:

- identity publication race/no-replace;
- full legacy-to-global migration;
- complete command routing;
- real session pin lifecycle;
- post-delivery Observer/notice flow;
- circuit-breaker durability;
- real project-isolation E2E;
- Classic CLI Telos host prompt;
- TUI Gateway Telos domain;
- Ink TUI TypeScript;
- Electron Desktop TypeScript;
- SOL R1/R2A/R2B/R3/R4 reviews.

These will receive later bounded repair passes.

## Final output format

Return:

1. starting and ending HEAD;
2. exact commit list created;
3. files changed per commit;
4. trust-boundary design actually implemented;
5. schema version and migration path;
6. RED/GREEN receipt table with exact node IDs and commands;
7. Gateway real-dispatch results;
8. exact 14 deferred-test result;
9. full Evolution suite output;
10. dangerous-command regression output;
11. Ruff, compileall, and diff-check output;
12. `git status --short --branch`;
13. explicit pending list copied from above;
14. confirmation that no TypeScript, spec, plan, `.gitignore`, remote backend,
    deploy, restart, push, amend, squash, or rebase occurred.

Do not say “all SOL gates passed”, “implementation complete”, or “ready for
TypeScript”. The only permitted completion claim is:

```text
Repair Pass 2.2.1 host authority and approval-ledger scope complete,
ready for independent audit.
```

If any mandatory item is missing or any required command is red, say:

```text
Repair Pass 2.2.1 incomplete.
```
