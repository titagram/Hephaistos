# Autopoiesis Project F — Authentic Pilot and MVP Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select one genuinely recurring local capability gap, evolve Hades through the complete governed lifecycle, prove the new capability in a fresh session, inject a controlled hard failure, verify automatic rollback, and audit every MVP acceptance criterion.

**Architecture:** Pilot selection is a deterministic read over Project B evidence plus explicit eligibility checks and user acceptance. The real run executes in a private clone/profile so it is repeatable and cannot endanger the user's stable profile, while retaining sanitized references to the authentic recurring events. After success, bounded replay fixtures preserve regression coverage but never substitute for the authentic acceptance record.

**Tech Stack:** Projects A–E public CLI/services, real local event evidence, temporary profile, local fixtures where external services are needed, pytest E2E, content-addressed acceptance record.

## Global Constraints

- Read design sections “Real-path end-to-end pilot” and “MVP Acceptance
  Criteria” in full.
- A qualifying suggestion must originate in recurring local experience events.
  Do not fabricate events solely to make the MVP pass.
- User acceptance of the selected suggestion is required and recorded through
  the host-owned approval surface.
- The pilot may use a skill or extension pack. It needs no new credential,
  privileged/global install, core patch, production write, or remote backend.
- The real task and original limitation must be deterministically reproducible
  in an isolated profile.
- Tests may use explicit fixture servers, but not mocks for Hades discovery,
  loaders, approvals, ledger, pointers, session pinning, supervisor, or rollback.
- If no suggestion qualifies, stop with a durable `pilot_not_ready` report.
  Do not declare the MVP complete.

---

## File and Module Map

| Path | Responsibility |
|---|---|
| `hermes_cli/evolution/pilot.py` | Eligibility, stable ordering, user selection, acceptance record |
| `hermes_cli/evolution/command.py` | Pilot candidate/select/run/status commands |
| `tests/fixtures/autopoiesis/pilot_mvp/` | Sanitized replay inputs captured only after authentic selection |
| `tests/e2e/test_autopoiesis_mvp_pilot.py` | Full lifecycle replay through real interfaces |
| `tests/e2e/test_autopoiesis_mvp_rollback.py` | Controlled hard-failure rollback |
| `docs/autopoiesis/pilot-mvp/` | Runbook, sanitized provenance, acceptance evidence |

## Task 1: Deterministic pilot eligibility and selection

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Select only the highest-ranked suggestion that satisfies all six
design conditions and bind user acceptance to that exact evidence set.

**Non-goals:** No build or modification of suggestion ranking.

**Files:**
- Create: `hermes_cli/evolution/pilot.py`
- Modify: `hermes_cli/evolution/command.py`
- Modify: `hermes_cli/subcommands/evolution.py`
- Create: `tests/hermes_cli/evolution/test_pilot_selection.py`

**Context pack:** Project B suggestion records/scoring; Project C approval
surface; exact pilot algorithm from design.

**Interfaces:**

```python
@dataclass(frozen=True)
class PilotEligibility:
    suggestion_id: str
    eligible: bool
    reason_codes: tuple[str, ...]
    recurrence: int
    task_impact: float
    risk: float
    evidence_digest: str

def evaluate_pilot_candidates(ledger: EvolutionLedger) -> tuple[PilotEligibility, ...]: ...
def select_pilot(suggestion_id: str, *, user_confirmation_digest: str) -> PilotSelection: ...
```

- [ ] **Step 1: Write the six-condition truth table**

Test real recurring evidence, isolated reproduction, deterministic success
oracle, no credential/privilege/core/production write, skill-or-extension
solution, and explicit user acceptance independently.

- [ ] **Step 2: Write failing stable-order tests**

Order by higher recurrence, higher task impact, lower risk, then suggestion
ID. Ineligible higher-score suggestions are skipped with reason codes.

- [ ] **Step 3: Write failing no-candidate behavior**

Return `pilot_not_ready`, leave all lifecycle state unchanged, and give exact
non-sensitive missing conditions. Do not create synthetic evidence.

- [ ] **Step 4: Run red tests**

Run: `scripts/run_tests.sh tests/hermes_cli/evolution/test_pilot_selection.py -q`

- [ ] **Step 5: Implement pure eligibility and host confirmation**

Reproduction and oracle references must point to verified bounded evidence
artifacts. Selection uses the same host-owned approval semantics as other
evolution decisions and appends the evidence digest.

- [ ] **Step 6: Add commands and commit**

Add `evolution pilot candidates|select|status`.

Commit: `evolution-f: select an authentic pilot deterministically`

**Escalate if:** eligibility relies on model confidence/prose, a fixture-only
gap qualifies, or selection changes the underlying score.

## Task 2: Prepare the accepted real task and reproducible isolated profile

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Capture the minimum sanitized evidence and deterministic oracle
needed to reproduce the accepted task without copying private content.

**Non-goals:** No solution implementation or promotion.

**Files:**
- Create: `docs/autopoiesis/pilot-mvp/README.md`
- Create: `docs/autopoiesis/pilot-mvp/source-manifest.json`
- Create: `tests/fixtures/autopoiesis/pilot_mvp/reproduction.json`
- Create: `tests/fixtures/autopoiesis/pilot_mvp/oracle.json`
- Create: `tests/e2e/test_autopoiesis_pilot_reproduction.py`

**Context pack:** Accepted `PilotSelection`; referenced bounded events;
reproduction/oracle evidence; no unrelated logs or sessions.

- [ ] **Step 1: Reproduce the original limitation before authoring a solution**

Run the real stable path in a temporary profile and assert the exact bounded
failure class/capability/operation from the suggestion. If it cannot be
reproduced, mark the pilot ineligible and return to Task 1.

- [ ] **Step 2: Define the deterministic success oracle**

Use exact exit/status/schema/invariant relationships, not subjective output
quality. The stable path must fail the target check and still pass common
invariants.

- [ ] **Step 3: Sanitize replay inputs**

Replace private payloads with semantically equivalent minimal data while
retaining the real evidence IDs/digests and a statement of what was redacted.
Ensure paths, usernames, hostnames, tokens, prompts, and content bodies are
absent.

- [ ] **Step 4: Create a private profile clone helper**

Clone only required config/state metadata, not secrets or arbitrary histories.
Initialize a baseline through the real CLI and verify it is independent of the
user's active profile.

- [ ] **Step 5: Run reproduction twice**

Run in fresh isolated profiles; results and oracle bytes must match.

- [ ] **Step 6: Document and commit**

Commit: `evolution-f: capture reproducible pilot limitation`

**Escalate if:** reproduction needs a production credential/write, private
content is required for the oracle, or the failure occurs only once.

## Task 3: Execute the complete authentic evolution

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Drive the accepted pilot through suggestion, research,
Blueprint, build, canary, promotion approval, activation, and fresh-session
success using only real product interfaces.

**Non-goals:** No direct ledger/pointer/test backdoor.

**Files:**
- Create: `tests/e2e/test_autopoiesis_mvp_pilot.py`
- Create: `docs/autopoiesis/pilot-mvp/runbook.md`
- Create: `docs/autopoiesis/pilot-mvp/blueprint.json`
- Modify: `tests/fixtures/autopoiesis/pilot_mvp/` only with approved local source artifacts

No production files are planned in this execution task. A reproduced defect
must become a focused failing regression and a separate repair task in the
owning Project A–E plan before this task is rerun.

**Context pack:** Task 2 reproduction; public commands/APIs from A–E; selected
suggestion. Exclude implementation internals unless a test exposes a defect.

- [ ] **Step 1: Start through `/autopoiesis <suggestion-id>`**

Assert the Observer did not research or mutate before this action and that the
Workshop reads the correct evidence.

- [ ] **Step 2: Exercise research consent**

Create and explicitly approve the scoped request. Use either approved local
documents or web fixture sources through real `web_search`/`web_extract`.
Record sanitized provenance.

- [ ] **Step 3: Produce and approve the exact Blueprint**

The Blueprint describes the skill/extension, sources/licenses/dependencies,
real task, canary, side effects, resources, expected Gnothi diff, and rollback.
Display “what Hades will become,” then record explicit build approval.

- [ ] **Step 4: Build the immutable generation**

Use the real Builder/adapter/quarantine/sandbox/store. Assert core/stable
source, current pointers, config, memory, and production credentials remain
unchanged.

- [ ] **Step 5: Run real canary and candidate Gnothi diff**

Stable reproduces the limitation, candidate satisfies the oracle, common
invariants pass, actual diff matches expected, and every mandatory check has
verified evidence.

- [ ] **Step 6: Approve and promote exact report**

Approve generation/report/expected active sequence. Start a fresh session and
use the new capability through the real discovery/invocation path. Keep a
pre-promotion session open and prove it does not see the capability.

- [ ] **Step 7: Repeat the full isolated run**

Run from a new baseline profile and prove the same generation ID when source/
dependency bytes are identical, or explain a content-derived difference.

- [ ] **Step 8: Commit**

Commit: `evolution-f: prove authentic capability evolution`

**Escalate if:** any lifecycle stage is called directly from the test instead
of its real interface, a mandatory result is mocked/skipped, or the new
capability appears in an old session.

## Task 4: Inject a controlled hard failure and prove automatic rollback

**Model:** `gpt-5.6-terra`, reasoning `high`

**Objective:** Demonstrate supervisor-owned hard rollback and last-known-good
restoration after the real pilot is active.

**Non-goals:** No subjective/quality failure and no manual rollback command.

**Files:**
- Create: `tests/e2e/test_autopoiesis_mvp_rollback.py`
- Create: `docs/autopoiesis/pilot-mvp/rollback-evidence.json`

No production files are planned in this rollback proof. A reproduced defect
must become a focused failing regression and a separate Project E repair task
before the proof is rerun.

**Context pack:** Task 3 isolated active profile; Project E closed hard triggers
and three-session proof.

- [ ] **Step 1: Select one deterministic hard trigger**

Use a generation/component digest mismatch in the isolated profile by altering
one copied candidate byte after activation and before a new-session load. Do
not alter the real user profile or stable/LKG bytes.

- [ ] **Step 2: Start the external supervisor path**

Detect the mismatch independently of candidate output. Assert automatic
rollback begins without a new consent because it restores an already approved
LKG.

- [ ] **Step 3: Verify the rollback transaction**

Active becomes prior LKG, failed generation remains immutable/inspectable,
stabilization is invalidated, unsafe event and trigger evidence are appended,
and the visible critical notice contains only safe IDs/codes.

- [ ] **Step 4: Verify session behavior**

The session pinned to the failed generation is stopped/restart-required without
schema mutation. A fresh session resolves LKG and cannot discover the pilot
capability. The original pre-promotion LKG session remains unchanged.

- [ ] **Step 5: Reconcile after injected crashes**

Repeat with process death at each rollback pointer boundary and assert the same
final LKG plus complete ledger explanation.

- [ ] **Step 6: Run twice and commit**

Commit: `evolution-f: prove automatic last-known-good rollback`

**Escalate if:** rollback needs candidate cooperation/manual file repair, an
unsafe session silently swaps tools, or the failed generation is deleted.

## Task 5: Privacy, provenance, and authority audit

**Model:** `gpt-5.6-sol`, reasoning `high`

**Objective:** Audit the authentic artifacts and implementation against the
privacy, provenance, and authority boundaries before functional acceptance.

**Non-goals:** No feature expansion.

**Files:**
- Create: `docs/autopoiesis/pilot-mvp/security-audit.md`
- Create: `tests/e2e/test_autopoiesis_mvp_privacy.py`

No production files are planned in this audit. A validated finding blocks the
gate and must be repaired through a separate focused task with its own files,
red test, green test, and commit.

**Context pack:** Pilot ledger/generation/report/evidence outputs; design
Privacy/Security and exclusions; diff from approved design commit to current.

- [ ] **Step 1: Enumerate every durable artifact**

Ledger, pointers, manifests, components, locks, reports, Gnothi revisions,
events, workspaces/evidence, session generation metadata, and notices.

- [ ] **Step 2: Scan for forbidden content**

Seed canary markers for prompt/transcript, Unix/Windows/relative/file URI
paths, API tokens, credential values, raw tool output, and stack traces.
Inspect SQLite text and every JSON/JSONL/manifest/report artifact.

- [ ] **Step 3: Trace every authority**

For research, build, canary, report, promotion, stabilization, and rollback,
identify the owning process, input digest, grant/event, and state transition.
Candidate-owned code must own none.

- [ ] **Step 4: Verify provenance**

Every shipped byte has source/author/license/version/digest, every dependency
has resolved artifact/lock digest, and every claim links to bounded evidence.

- [ ] **Step 5: Verify exclusions**

No core patch, global install, new credential, production write, remote
backend change, arbitrary third-party core integration, or live-session
mutation occurred.

- [ ] **Step 6: Run privacy test and commit**

Commit: `evolution-f: audit pilot privacy and authority`

**Escalate if:** any forbidden marker survives, provenance is incomplete, or a
candidate path possesses lifecycle authority.

## Task 6: Final MVP acceptance audit

**Model:** `gpt-5.6-sol`, reasoning `xhigh`

**Objective:** Independently map fresh evidence to all 20 acceptance criteria
and declare the MVP complete only when every required relationship is proven.

**Non-goals:** No waiver for skipped evidence and no new architecture.

**Files:**
- Create: `docs/autopoiesis/pilot-mvp/mvp-acceptance.json`
- Create: `docs/autopoiesis/pilot-mvp/mvp-acceptance.md`
- Create: `tests/e2e/test_autopoiesis_mvp_acceptance_record.py`
- Modify only for validated blockers: Projects A–F and exact regression tests

**Context pack:** Approved design; all project handoffs; fresh full-suite
output; authentic pilot/rollback/security artifacts. Do not rely on earlier
“green” summaries without rerunning commands.

**Acceptance record shape:**

```json
{
  "schema_version": 1,
  "pilot_suggestion_id": "es_<public-id>",
  "generation_id": "<sha256>",
  "promotion_report_digest": "<sha256>",
  "rollback_event_digest": "<sha256>",
  "criteria": [
    {
      "number": 1,
      "status": "passed",
      "evidence_digests": ["<sha256>"],
      "test_ids": ["<pytest-node-id>"]
    }
  ],
  "overall_status": "passed"
}
```

`overall_status` is `passed` only when criteria 1–20 are all `passed`; no
`skipped`, `waived`, or `inconclusive` status is promotable to pass.

- [ ] **Step 1: Rerun every project and E2E suite**

```bash
scripts/run_tests.sh tests/hermes_cli/evolution -q
scripts/run_tests.sh tests/integration/test_autopoiesis_*.py -q
scripts/run_tests.sh tests/e2e/test_autopoiesis_*.py -q
scripts/run_tests.sh tests/hermes_cli/engineering_review -q
scripts/run_tests.sh tests/hermes_cli/test_gnothi_*.py -q
scripts/run_tests.sh tests/agent/test_autopoiesis_prompt.py tests/run_agent/test_generation_pinning.py tests/run_agent/test_unsafe_generation.py -q
scripts/run_tests.sh tests/tools/test_mcp_*.py tests/hermes_cli/test_plugins.py -q
```

- [ ] **Step 2: Run failure injection/concurrency loops**

Repeat pointer promotion/rollback crash matrices and session-concurrency tests
twenty times. Record command, exit code, test count, and output digest.

- [ ] **Step 3: Map criteria one by one**

For each design criterion, cite at least one immutable evidence digest and one
real test node. Inspect the referenced content; do not trust names alone.

- [ ] **Step 4: Re-audit prompt/cache/session behavior**

Compare canonical prompt/tool fingerprints across old/candidate/restored
sessions and inspect persisted message role alternation.

- [ ] **Step 5: Decide without optimism**

If any criterion is missing/failed/inconclusive, set `overall_status` to
`blocked`, list exact blockers, and do not claim MVP completion. Repair only
reproduced defects within the approved design, rerun all affected suites, then
restart this audit from Step 1.

- [ ] **Step 6: Verify acceptance-record self-consistency**

Test every digest, generation/report/rollback linkage, criterion uniqueness,
and overall-status derivation.

- [ ] **Step 7: Commit final evidence**

Commit: `evolution-f: certify autopoiesis mvp acceptance`

The final handoff states:

- what capability Hades actually acquired;
- which authentic recurring limitation it addresses;
- active and LKG generation IDs after the rollback proof;
- how to inspect history and repeat the isolated run;
- any residual non-MVP limitations, especially the local same-OS-user threat
  model.

**Escalate if:** a criterion is supported only by a mock, a digest cannot be
reopened, prompt/tool identity changed in an existing session, or completion
would require weakening an acceptance rule.
