AUTOPOIESIS PROJECT B — REPAIR PASS 2.2
PYTHON FOUNDATION RECOVERY — IMPLEMENTATION, TDD, NO TYPESCRIPT

MODEL

Use DeepSeek-V4-Pro for the entire pass.

Do not delegate security, migration, concurrency, lifecycle, prompt-cache, or
post-delivery work to a lightweight model.

OBJECTIVE

Repair the Python implementation of Autopoiesis Project B after the independent
audit found that the nominally green implementation does not satisfy its
security and integration contracts.

This is an IMPLEMENTATION pass, not a plan-writing pass.

The goal is not merely to make tests green. The goal is to restore the actual
behavioral contracts:

1. A model-controlled terminal cannot approve, activate, or roll back Telos.
2. Persistent SQLite rows alone are never sufficient authorization.
3. Existing profile-scoped Project A state is never silently bypassed.
4. Global organism paths and identity creation are race- and symlink-safe.
5. Session pinning is wired into real session lifecycle paths.
6. Global configuration is actually global.
7. Real CLI parser actions reach their handlers.
8. Observer work and notices happen only after visible response delivery.
9. Tests prove these behaviors without weakening the original expectations.

Do not start Ink TUI or Electron Desktop TypeScript work in this pass.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REPOSITORY AND STATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Repository:
  /Users/gabriele/Dev/Hephaistos

Worktree:
  /Users/gabriele/Dev/Hephaistos/.worktrees/autopoiesis-project-b-design

Required branch:
  codex/autopoiesis-project-b-design

Expected starting HEAD:
  ff66e0bbf403d359a231071ed0ca18418c680cdc

Expected branch state:
  20 commits ahead of origin

Known unrelated untracked file:
  autopoiesis_plan_repair_2.1.3_prompt.md

Do not modify, delete, rename, stage, or commit that file.

Authoritative specification:
  docs/superpowers/specs/2026-07-24-autopoiesis-project-b-opportunity-observer-design.md

Implementation plan:
  docs/superpowers/plans/2026-07-24-autopoiesis-project-b-global-observer.md

Read AGENTS.md completely before acting.

If branch or HEAD differs, stop and report the mismatch.
Do not reset, rebase, squash, amend, or discard the existing 20 commits.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REMOTE BACKEND EXCLUSION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Do not touch the remote backend.

Do not touch:

- Graph Explorer;
- canonical graph projector;
- backend query/explorer service;
- dashboard graph reader;
- ImportGraphToNeo4j;
- Graph/Hades/DeltaSync backend tests;
- backend project databases;
- backend deployment or service configuration.

No SSH, deploy, restart, migration, push, or PR.

Work only in the local Project B worktree.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ALLOWED SCOPE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Expected Python production scope:

- hermes_cli/evolution/telos_approval.py
- hermes_cli/evolution/telos_store.py
- hermes_cli/evolution/ledger.py
- hermes_cli/evolution/organism_home.py
- hermes_cli/evolution/organism_identity.py
- hermes_cli/evolution/lifecycle_global.py
- hermes_cli/evolution/migration_discovery.py
- hermes_cli/evolution/global_config.py
- hermes_cli/evolution/command.py
- hermes_cli/evolution/session_pinning.py
- hermes_cli/evolution/observer_service.py
- hermes_cli/evolution/notices.py
- hermes_cli/subcommands/evolution.py
- hermes_state.py
- run_agent.py
- agent/conversation_loop.py
- agent/turn_finalizer.py
- cli.py
- gateway/run.py
- gateway/slash_commands.py
- tui_gateway/server.py

Expected tests:

- tests/hermes_cli/evolution/
- focused existing CLI tests;
- focused existing Gateway tests;
- focused existing TUI Gateway Python tests;
- prompt-cache/session lifecycle tests.

Do not modify:

- ui-tui/src/
- apps/desktop/src/
- package manifests or lockfiles;
- the specification;
- the implementation plan;
- .gitignore.

If another Python file is genuinely required, explain why before including it
in a commit. Do not perform unrelated refactors.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY PROCESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use systematic debugging and strict TDD.

For every defect:

1. Trace the real production call path.
2. State the root cause.
3. Add a failing behavioral test.
4. Run it and capture the expected RED output.
5. Implement the smallest coherent fix.
6. Run the focused GREEN test.
7. Run the relevant regression group.
8. Commit only that coherent repair.

Forbidden:

- production code before the failing test;
- changing an assertion merely to match current behavior;
- replacing a real-path test with direct calls to lower-level helpers;
- mocks when a temporary real database/filesystem path is practical;
- inspect.getsource or source-snapshot tests;
- comment-only tests;
- skip, xfail, deselection, archival, or deletion of failing tests;
- swallowing errors with `except Exception: pass` on security/state paths;
- claiming a review gate passed without an independent SOL review.

Before changing tests, write down:

  “What production defect would make this test fail?”

If there is no concrete answer, redesign the test.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BASELINE VERIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Run and record before modification:

  git status --short --branch
  git rev-parse HEAD
  git diff --check f7e7c84a5..HEAD

  /Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q \
    tests/hermes_cli/evolution/

Expected current baseline from independent review:

  1239 passed, 1 skipped

Also run the exact 14 formerly deferred tests and record their current output.

Do not interpret their current green state as proof: several have been
semantically weakened.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS A — RESTORE HONEST SECURITY TESTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before touching production authorization code, add tests reproducing the audit.

A1. Forged persistent rows cannot activate Telos

Create a real temporary organism and v4/v5 ledger.

Insert directly through SQLite:

- request:
  - organism_id = organism A
  - digest = B
  - action = activate
  - expected context = context A

- approved decision:
  - wrong actor;
  - wrong surface;
  - wrong context digest.

- grant:
  - organism_id = organism C
  - digest = C
  - action = rollback.

- consumption:
  - organism_id = organism D
  - digest = D
  - action = rollback.

Create a revision file for digest A.

Attempt:

  store.activate_revision(digest_A, grant_id="forged-grant", ...)

Required result:

- TelosStoreError;
- no active pointer;
- no LKG pointer;
- no activation event;
- no mutation to Telos state.

This test must fail on starting HEAD. The independent audit reproduced
`bypass == true`.

A2. Grant for revision A cannot activate revision B

Use the official broker flow to create and consume a legitimate grant for A.
Attempt to activate B.

Required result:

- fail closed;
- A and B pointers remain unchanged;
- the grant is not reusable.

A3. Rollback requires exact rollback authorization

An activate grant must never authorize rollback.
A rollback grant for A must never roll back to B.

A4. Capability binding and lifecycle

Test:

- foreign registry;
- unregistered capability;
- already-consumed capability;
- revoked capability;
- mismatched request ID;
- mismatched organism;
- mismatched digest;
- mismatched action;
- mismatched surface;
- mismatched actor;
- mismatched session/channel;
- mismatched nonce;
- mismatched context digest;
- expired request;
- expired capability;
- expired grant;
- deny;
- replay;
- concurrent double approval.

A5. Restore the original store contract test

`test_telos_store_save_activate_rollback` must again exercise:

1. save revision A;
2. unapproved activation fails;
3. exact host-approved activation of A succeeds;
4. replay fails;
5. amendment B succeeds with exact approval;
6. LKG becomes A before B is active;
7. exact approved rollback to A succeeds;
8. later history remains immutable.

Do not leave this test as only two “missing grant raises” assertions.

A6. Restore the initial Telos E2E test

It must explicitly demonstrate:

1. draft/revision exists but is inert;
2. direct activation fails;
3. model-facing command cannot self-approve;
4. real host decision approves the exact digest;
5. activation succeeds;
6. replay fails.

Do not perform approval inside a setup helper before the test begins.

Commit after this pass only when all new tests are RED for the intended
production defects.

Suggested commit message after the later GREEN fix, not while RED:

  fix(autopoiesis): enforce live host authority for telos transitions

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS B — REBUILD THE HOST APPROVAL BOUNDARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Root cause already established:

- HostApprovalCapability has a public constructor.
- CapabilityRegistry.register accepts arbitrary constructed instances.
- A model-controlled Python process can create its own capability, registry,
  and broker.
- record_host_decision verifies only membership in the caller’s registry.
- TelosStore trusts SQLite grant/consumption row presence.
- stored rows are therefore treated as authorization.
- capability binding, expiry, revocation, and exact-scope checks are incomplete.

Required architecture:

1. Reuse or extend the existing host approval surface in `tools/approval.py`
   rather than creating a parallel self-contained approval universe that a
   model process can instantiate.

2. Telos approval must have a separate domain:

     domain = "telos"

3. It must never inherit:

   - approve all;
   - session approval;
   - always/permanent approval;
   - YOLO/off mode;
   - dangerous-command approval cache;
   - environment/config bypass;
   - clarify.

4. Model-facing operations may:

   - create a pending request;
   - inspect status;
   - cancel their own still-pending request if the contract allows.

   They may not:

   - record approval;
   - issue an authoritative grant;
   - consume authorization;
   - activate;
   - roll back.

5. The real host adapter receives the human decision and performs the transition
   inside that live host flow.

6. Any capability/receipt must be:

   - minted by the live host approval process;
   - bound to exact request ID;
   - organism ID;
   - Telos digest;
   - activate/rollback action;
   - host surface;
   - actor;
   - session/channel;
   - nonce;
   - context digest;
   - expiry;
   - single-use.

7. SQLite rows are append-only audit evidence, not standalone authorization.

8. TelosStore activation/rollback must require the live host-authorized
   transition object in addition to any opaque row handle.

9. On crash before pointer publication:

   - preserve audit evidence;
   - do not infer authority from rows on restart;
   - require a fresh host approval;
   - remove or quarantine incomplete staged pointer state;
   - never auto-publish from a consumption row alone.

10. Do not treat Python naming conventions such as `_private` as a security
    boundary.

11. Do not claim containment from arbitrary malicious same-UID code. The
    required proof is that the official model-controlled terminal/CLI path
    cannot complete a Telos transition without a live host decision.

Before implementing, write a concise trust-boundary note in the commit message
or final report explaining:

- trusted process;
- untrusted/model-controlled process;
- volatile authority;
- persistent audit state;
- transition boundary.

If you cannot demonstrate a coherent boundary using the existing host approval
architecture, stop and report the architectural blocker. Do not fabricate a
security claim.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS C — REPAIR APPROVAL SCHEMA COMPATIBLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The current v4 schema does not ensure relational coherence.

Required invariants:

- one decision per request;
- one grant per approved request/decision;
- one consumption per grant;
- decision request matches the stored request;
- decision context equals expected request context;
- grant request and decision refer to the same request;
- grant organism/digest/action equal request organism/digest/action;
- consumption organism/digest/action equal grant values;
- denied decision cannot produce a grant;
- missing decision cannot produce a grant;
- expired request cannot be approved;
- expired grant cannot be consumed;
- UPDATE and DELETE remain forbidden;
- foreign_keys is always ON.

Because schema version 4 has already been committed and may already exist, do
not silently redefine the v4 contract in place.

Inspect whether the required repair can be applied additively and safely.
If existing v4 databases require different schema objects or table rebuilding,
introduce an explicit v4→v5 migration.

If using v5:

- preserve every v4 row;
- validate or quarantine incoherent historical Telos approval rows;
- do not treat historical incoherent rows as authority;
- update SCHEMA_VERSION;
- update SuggestionRepository’s current-version gate;
- add v4→v5 rollback-on-failure tests;
- add v1→v2→v3→v4→v5 chain tests;
- add fresh-v5 tests;
- add repeated-open idempotency tests.

Do not erase old tables or rows without a documented migration rule.

Required adversarial SQL tests:

- request A + decision B;
- request A + grant from decision B;
- modified organism;
- modified digest;
- modified action;
- modified context;
- denied decision;
- missing decision;
- duplicate decision;
- duplicate grant;
- duplicate consumption;
- UPDATE;
- DELETE.

Commit:

  fix(autopoiesis): make telos approval audit chain coherent

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS D — FIX ORGANISM PATH AND IDENTITY SECURITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

D1. Symlink ancestor reproduction

Create:

  real/
  link -> real/
  requested = link/nonexistent-organism

Call `ensure_organism_directories(requested)`.

Current behavior creates `real/nonexistent-organism`.

Required behavior:

- raise OrganismHomeError;
- no directory created through the symlink;
- no chmod through the symlink.

D2. Validate every relevant component

The resolver must not stop checking when the leaf is missing. It must continue
toward existing ancestors.

Reject:

- symlink at root;
- symlink at any ancestor;
- symlink at any organism subdirectory;
- wrong object type;
- wrong owner;
- unsafe mode;
- replacement between validation and open.

Prefer directory-FD-relative operations and `O_NOFOLLOW` where supported.
Maintain a portable fail-closed fallback.

Do not call `.resolve()` as the security mechanism.

D3. Identity publication

Current problems:

- PID-only temporary name;
- same-process competing writer can unlink another writer’s temp path;
- `os.rename()` can replace a concurrently published identity;
- parent fsync happens before publication rather than after durable publication;
- no organism lifecycle lock protects the full initialization transition.

Required:

- serialize under the global organism lock;
- unique temp name;
- exclusive/no-replace publication;
- file fsync;
- atomic no-replace publish;
- parent directory fsync after publication;
- loser loads and validates the winner;
- malformed existing identity is never replaced;
- identity replacement remains unsupported.

Add process/thread concurrency tests proving one immutable organism ID.

Commit:

  fix(autopoiesis): harden organism root and identity publication

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS E — IMPLEMENT REAL LEGACY-TO-GLOBAL MIGRATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Root cause:

`ensure_global_lifecycle_initialized()` never calls discovery or migration.
When global identity is absent, it always creates a fresh global lineage.

Required production flow:

1. Acquire global organism lifecycle lock.
2. Inspect interrupted staging state.
3. If a verified global organism already exists:
   - open it;
   - report legacy state;
   - never adopt or mutate legacy roots.

4. If no global organism exists:
   - scan known default and named-profile roots read-only;
   - never create a backend client;
   - classify canonical Project A state.

5. Outcomes:

   no legacy:
     propose/perform fresh initialization according to the existing command
     contract.

   exactly one coherent baseline-only:
     build an explicit migration proposal;
     require user confirmation;
     create a new organism-bound equivalent baseline;
     record source chain/pointer/manifest digests in an import event;
     archive only a provenance manifest.

   multiple byte-identical baseline-only:
     summarize together;
     never merge rows;
     still require confirmation.

   divergent/non-baseline/malformed/unknown:
     block automatic initialization;
     preserve all source state;
     return bounded diagnostic.

6. Stage the complete global root outside the final root.
7. Validate identity, ledger, generation, pointers, owner and modes.
8. Atomically publish the complete root.
9. On interruption:
   - resume only if every staged object verifies;
   - otherwise discard/quarantine staging before accepting any pointer.

10. Never mutate profile-scoped legacy files.
11. Never store a raw profile path/name in public global state.

Required tests:

- no legacy;
- one canonical baseline;
- multiple identical;
- multiple non-identical;
- non-baseline attempt;
- divergent generation;
- malformed DB;
- unknown schema;
- global already exists;
- confirmation denied;
- interrupted staging before publish;
- interrupted publish;
- idempotent retry;
- source bytes unchanged;
- no backend client constructed.

Use canonical historical Project A fixtures, not minimal fake databases containing
only `schema_version` and `generations`.

Commit:

  feat(autopoiesis): complete explicit global lifecycle migration

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS F — MAKE GLOBAL CONFIG AND COMMAND ROUTING REAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

F1. Global config isolation

Current bug:

`global_config.py` computes `<default-root>/config.yaml` but then calls the
profile-aware `load_config()` and `save_config()` without using that path.

Required test:

- default-root config says autopoiesis.enabled=false;
- named-profile config says true;
- active profile is the named profile;
- load_global_config returns false;
- save_global_config modifies only default-root config;
- unrelated default-root keys are preserved;
- named-profile bytes remain unchanged.

Use existing atomic YAML/config helpers with an explicit path if possible.
Do not add an environment variable.

F2. Enabled-state rules

- autopoiesis.enabled can become true only after an active verified Telos exists;
- `resume` without verified active Telos fails closed;
- pause does not delete history;
- observer scans/notices are suppressed while disabled;
- init/status/doctor/Telos draft and approval request remain available while
  disabled.

F3. Real parser routing

The parser currently emits:

- observer_status
- observer_scan
- telos_status
- telos_draft
- telos_history
- telos_approve
- telos_rollback

`evolution_command()` currently handles only action `"telos"`.

Add real parser-to-handler tests using the actual CLI parser/dispatch path.

Required:

- every parser action has one canonical handler;
- unknown action returns bounded error;
- no duplicate shadow action;
- `telos approve` creates/resumes a host approval request only;
- it never activates by itself;
- `telos rollback` creates a rollback approval request only;
- observer status is read-only;
- observer scan respects enabled/Telos/breaker gates.

Remove or fix stale `--receipt` semantics if they expose self-approval.
Do not keep a parser option that the secure flow forbids.

Commit:

  fix(autopoiesis): bind global config and evolution command routing

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS G — WIRE SESSION PINNING INTO REAL LIFECYCLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Root cause:

`session_pinning.py` is referenced only by tests. No production code calls its
helpers.

It also reads `SessionDB._conn` directly despite claiming otherwise.

Required:

1. Add narrow public SessionDB methods built on existing locking and
   `_execute_write` infrastructure.

2. Do not access `SessionDB._conn` from `session_pinning.py`.

3. Persist under:

     model_config._autopoiesis_pin

4. Preserve:

   - _delegate_from;
   - _branched_from;
   - provider/model configuration;
   - all unrelated model_config keys.

5. Pin fields:

   - organism_id;
   - active Telos digest;
   - active executable generation ID;
   - gnothi_seauton revision digest when present;
   - profile/persona revision;
   - workspace route or explicit unbound state.

6. Lifecycle behavior:

   fresh session:
     load current organism state and persist pin before first model call.

   resume:
     load persisted pin; never replace it with current global state.

   /new:
     create a fresh pin from current verified state.

   branch/delegate:
     inherit parent pin according to existing session semantics.

   compression:
     logical session keeps/inherits the same pin.

   Telos changes:
     existing session unchanged; new session sees new digest.

   malformed pin:
     fail closed with bounded diagnostic; do not silently replace it.

7. Prompt cache:

   - do not inject full Telos/organism graph;
   - do not rebuild current system prompt;
   - do not mutate tool schema;
   - system prompt prefix stays byte-identical during a live conversation.

Required real-path tests:

- actual SessionDB creation;
- actual agent session creation path;
- resume;
- `/new`;
- branch;
- delegate;
- compression;
- process restart;
- Telos changes between two new sessions;
- existing-session prompt-prefix hash unchanged.

Commit:

  feat(autopoiesis): wire immutable organism pins into sessions

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS H — REPAIR POST-DELIVERY OBSERVER AND NOTICE FLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Current problems:

- Observer scan runs synchronously inside finalize_turn();
- finalize_turn occurs before visible response delivery;
- Gateway drains immediately after run_conversation, before adapter send;
- queued entries are dictionaries, not AgentNotice objects;
- existing notice callbacks expect AgentNotice attributes;
- drain clears entries even when emission fails;
- exceptions are silently swallowed;
- pause/enabled/interval/threshold/rate-limit rules are ignored.

Required architecture:

1. `finalize_turn()` must not perform database scan, suggestion projection, or
   frontend delivery.

2. A shared bounded post-delivery service may be called only after a frontend
   confirms response delivery.

3. Per-surface order:

   Classic CLI:
     render final response successfully;
     then schedule post-delivery observer work.

   Gateway:
     adapter send succeeds;
     then schedule post-delivery observer work.

   TUI Gateway:
     emit `message.complete`;
     then schedule post-delivery observer work.

4. Produce real `AgentNotice` objects through the existing notice policy.

5. Never inject a synthetic user, assistant, or system message.

6. No additional model call or network operation.

7. Scan must respect:

   - global enabled;
   - observer enabled;
   - active verified Telos;
   - circuit breaker;
   - scan interval;
   - event bound;
   - score threshold;
   - material-revision rate limit.

8. Failure:

   - response remains delivered;
   - error is recorded through bounded observer diagnostics/breaker;
   - failed notice stays pending or is safely retryable;
   - queue is not cleared as if delivery succeeded.

9. Desktop/Ink TypeScript delivery remains explicitly pending after this pass.
   Do not claim all surfaces complete.

Required tests:

- final response event precedes observer hook;
- no scan inside finalize_turn;
- Gateway send precedes observer hook;
- TUI message.complete precedes observer hook;
- correct AgentNotice type;
- callback receives bounded text;
- disabled suppresses;
- paused suppresses;
- duplicate material revision suppresses;
- callback failure preserves retry state;
- observer failure does not alter response;
- role alternation unchanged;
- prompt bytes unchanged.

Commit:

  fix(autopoiesis): run observer notices only after response delivery

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS I — REPAIR CIRCUIT BREAKER DURABILITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Current breaker persistence is not fully safe.

Required:

- reject symlink;
- reject wrong owner;
- reject wrong mode;
- reject wrong object type;
- malformed JSON preserves original file and opens circuit;
- state write uses unique temp file;
- file fsync;
- atomic rename;
- parent fsync;
- no symlink race;
- no silent `except Exception: pass`;
- reset only through explicit user command;
- notify once;
- process restart preserves state;
- normal Hades work continues.

Add tests for every failure class and concurrent writer behavior.

Commit:

  fix(autopoiesis): make observer breaker fail-closed and durable

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASS J — RESTORE REAL-PATH E2E COVERAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The current Project B E2E file consists primarily of direct service tests.

The “project isolation” scenario currently:

- creates no two workspaces;
- creates no two backend bindings;
- creates no distinct tokens/caches/logs;
- uses source_project_ref=None;
- performs no ambiguous-route check;
- does not prove absence of backend/network construction.

Replace or extend it with real-path tests against a temporary default Hermes
root containing:

- two profiles;
- two workspace roots;
- two distinct local backend-binding fixtures;
- distinct token references;
- distinct cache roots;
- distinct raw logs;
- one global organism identity;
- one host-approved Telos;
- compatible and incompatible observations.

Prove four scenarios:

1. Initial Telos approval boundary
2. Missing webcam capability
3. Performance feedback
4. Project isolation

Project isolation must prove:

- both profiles resolve one organism/Observer;
- raw logs/memories remain separate;
- compatible generalized signals deduplicate;
- output contains aggregate counts only;
- no profile/project/session identity leaks;
- ambiguous route prevents evidence dereference;
- no default agent/token fallback;
- no backend or network client constructed;
- no remote backend state touched.

Also validate that an incoming envelope’s organism_id matches the active
organism before ingestion.

Commit:

  test(autopoiesis): restore real-path project b scenarios

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PYTHON HOST SURFACES IN THIS PASS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Complete the Python portion of:

- Gateway `/approve telos <request-id>`;
- Classic CLI host prompt;
- TUI Gateway JSON-RPC `domain="telos"` protocol.

Gateway requirements:

- derive actor/session/channel from real MessageEvent;
- match exact pending request;
- approve/deny only;
- no all/session/always inheritance;
- complete the live transition, not merely record a decision;
- revoke live authority in finally;
- test through real slash dispatch.

Classic CLI requirements:

- dedicated prompt_toolkit host callback;
- timeout/EOF/Ctrl-C/invalid input = deny;
- callback wired into real request flow;
- no clarify;
- no model-generated free text accepted as approval.

TUI Gateway requirements:

- separate Telos request/response domain;
- exact request/session binding;
- stale/mismatched response rejected;
- dangerous-command approval remains unchanged.

Do not modify Ink or Desktop in this pass.
Report them as pending.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REQUIRED REVIEW GATES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

After Pass C:
  mark implementation ready for independent SOL R2A/R2B review.
  Do not self-declare the gate passed.

After Pass E:
  mark implementation ready for independent SOL R1 review.

After Pass G/H/J:
  mark implementation ready for independent SOL R3/R4 review.

A previous claim that R1/R2A passed is not valid evidence for the repaired
implementation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL VERIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Run focused suites after every commit.

At the end run:

1. Exact adversarial Telos tests.

2. Exact 14 formerly deferred tests.

3. Full Evolution suite:

   /Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q \
     tests/hermes_cli/evolution/

4. Existing dangerous-command regression:

   /Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest -q \
     tests/gateway/test_approve_deny_commands.py

5. New Gateway Telos tests.

6. New Classic CLI Telos tests.

7. New TUI Gateway Telos tests.

8. Session/prompt-cache tests.

9. Real-path Project B E2E tests.

10. Project A foundation and migration tests.

11. Static checks on every changed Python file:

    /Users/gabriele/Dev/Hephaistos/.venv/bin/python -m ruff check ...
    /Users/gabriele/Dev/Hephaistos/.venv/bin/python -m compileall -q ...
    git diff --check

Fix the existing extra blank line at EOF in:

  hermes_cli/evolution/command.py

Do not report a combined count without listing the commands used to obtain it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY ADVERSARIAL ACCEPTANCE CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before declaring completion, prove all are rejected:

- direct SQLite forgery;
- standalone grant ID;
- consumed row without live host authority;
- grant for A used on B;
- activate grant used for rollback;
- wrong organism;
- wrong context;
- expired request;
- expired grant;
- revoked capability;
- replay;
- `/approve all`;
- `/approve session`;
- `/approve always`;
- `hermes evolution telos approve <digest>` without real host action;
- model-generated prompt answer;
- crash recovery based only on persistent rows.

For every item give:

- exact test node ID;
- exact command;
- PASS output.

If one item lacks a test, the pass is incomplete.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMMIT POLICY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Make focused commits on top of current HEAD.

Do not amend or squash the existing 20 commits.

Before each commit:

- focused GREEN test;
- relevant regression;
- git diff --check for that diff;
- inspect staged diff;
- confirm unrelated untracked prompt is not staged.

Do not push.

Suggested commit sequence:

1. fix(autopoiesis): enforce live host authority for telos transitions
2. fix(autopoiesis): make telos approval audit chain coherent
3. fix(autopoiesis): harden organism root and identity publication
4. feat(autopoiesis): complete explicit global lifecycle migration
5. fix(autopoiesis): bind global config and evolution command routing
6. feat(autopoiesis): wire immutable organism pins into sessions
7. fix(autopoiesis): run observer notices only after response delivery
8. fix(autopoiesis): make observer breaker fail-closed and durable
9. test(autopoiesis): restore real-path project b scenarios

Split a commit further if needed. Do not combine unrelated passes merely to
reduce commit count.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return:

1. Starting and ending HEAD.
2. Commit list created in this pass.
3. Files changed per commit.
4. Root cause and implemented correction for Passes A–J.
5. Exact RED output captured before each repair.
6. Exact GREEN output after each repair.
7. Exact final test commands and counts.
8. Exact adversarial acceptance matrix.
9. Migration version decision:
   - remained v4 additively, or
   - introduced v5;
   - explain compatibility reasoning.
10. Confirmation that legacy profile bytes remain unchanged.
11. Confirmation that no backend/network client was constructed in E2E.
12. Confirmation that system prompt and tool schemas remain stable.
13. Remaining work:
   - Ink TUI TypeScript;
   - Desktop TypeScript;
   - independent SOL R1/R2A/R2B/R3/R4 reviews.
14. Git status including the unrelated untracked prompt file.
15. Explicit confirmations:
   - no TypeScript changed;
   - no spec/plan changed;
   - no .gitignore changed;
   - no backend touched;
   - no push;
   - no tests skipped/xfail/archived;
   - no weakened assertions.

Do not say “Python backend complete” unless every acceptance item above has
fresh evidence.

If any security, migration, pinning, or ordering item remains unresolved, say
exactly which item is blocked and stop before TypeScript work.