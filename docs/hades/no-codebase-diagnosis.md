# No-Codebase Bug Diagnosis

This runbook is for diagnosing a project bug when the local agent does not have
the source tree mounted. The goal is not to guess from memory. The agent should
use backend project awareness, current indexed artifacts, bounded evidence, and
verified diagnosis gates before making a precise root-cause claim.

## Preconditions

Run from any device that is logged into the same Hades backend project:

```bash
hades backend status --json
```

The minimum safe state is:

- backend configured for the project;
- at least one linked workspace binding exists for the project;
- project memory is available from the backend;
- the target binding has current project artifacts, source slices, and bug
  evidence coverage;
- `diagnosable_without_source=true` for the target binding;
- no current sync error is recorded for that binding.

If the current device is not mapped to the workspace, source-free diagnosis can
still use existing backend project memory and evidence. It cannot create fresh
indexes for a new local checkout until the workspace is linked and synced.

## Keep Awareness Current

On a device that has the source tree, refresh the backend index before relying
on source-free diagnosis:

```bash
hades backend sync
hades backend status --json
```

If a backend job is waiting for local confirmation, review it explicitly:

```bash
hades backend jobs
hades backend approve-job <job_id>
```

Use `refuse-job` instead of approving a broad or unclear job:

```bash
hades backend refuse-job <job_id> --reason "too broad"
```

Do not treat old raw notes or raw chunks as authoritative project memory. Use
the note backfill preview and backend proposal review flow:

```bash
hades backend backfill-note <path>
hades backend backfill-note <path> --create-proposals
hades backend sync
```

## Capture The Bug

Use the dashboard Backend page `Bug intake` panel, or the CLI:

```bash
hades backend bug-intake \
  --title "Checkout fails" \
  --symptom "POST /checkout returns 500" \
  --steps "Open cart, submit payment" \
  --expected "Order is created" \
  --actual "HTTP 500" \
  --severity high \
  --environment production \
  --test-output /tmp/failing-test.txt \
  --log /tmp/runtime.log \
  --deploy-commit <deployed-sha> \
  --request-url "https://app.example/checkout" \
  --response-status 500 \
  --json
```

The dashboard and CLI both use bounded evidence and redaction. The dashboard
also previews redactions before submit for pasted or uploaded test/log evidence.

Prefer evidence that points to the execution path:

- failing test output;
- runtime log excerpt with stack frame;
- HTTP method, URL, and status;
- deployed commit versus indexed workspace head;
- source slice references when already approved by policy.

Do not paste `.env`, cookies, bearer tokens, private keys, raw database dumps,
or full source files. If evidence is rejected by policy, reduce the payload or
redact it before retrying.

## Diagnose Without Local Source

The agent should use this sequence before giving a precise cause:

1. Check project awareness for the target binding.
2. Search bug evidence and evidence packs for the report.
3. Search current project memory for verified facts and resolved bugs.
4. Traverse the backend graph from the route, symbol, file, class, or method
   suggested by the evidence.
5. Fetch bounded source slices only when the policy permits source-content
   access and the diagnosis needs exact lines.
6. Produce a diagnosis with root cause, mechanism, evidence refs, freshness,
   affected symbols, and confidence.

High or medium confidence is valid only when all of these are true:

- `freshness.status=current`;
- `evidence_refs` is non-empty;
- `awareness.diagnosable_without_source=true`;
- the claim is supported by current graph/source-slice/evidence references.

If any gate is missing, the correct result is an insufficient diagnosis with
the missing gate and next action. Do not promote a precise root cause from stale
artifacts or unverified notes.

## Record And Review Quality

Run the quality gate manually after new evidence or indexing changes:

```bash
hades backend quality-report --record
hades backend status --json
```

Create or update the periodic local audit job:

```bash
hades backend schedule-quality --schedule "0 8 * * *"
```

For release or regression work, include the no-codebase fixture:

```bash
hades backend schedule-quality \
  --schedule "0 8 * * *" \
  --no-codebase-eval tests/fixtures/hades/no_codebase_bug_cases.json
```

The eval JSON can include normalized `runs` or `trajectory_runs` entries that
point to saved `.json`/`.jsonl` trajectories. Trajectory runs are parsed for
ShareGPT `<tool_call>` blocks, OpenAI-style tool calls, final diagnosis JSON,
forbidden source/file/shell tool use, evidence refs, freshness, awareness, and
diagnosis persistence.

The quality report records blocker/warning actions locally. A failed report
should block claims that the project is ready for source-free diagnosis.

## Recover Common Blockers

| Blocker | Meaning | Recovery |
| --- | --- | --- |
| `current_workspace_mapped=false` | This device is not linked to the workspace. | Link/sync from a checkout, or use an existing backend binding only for read-only source-free diagnosis. |
| `diagnosable_without_source=false` | Coverage is incomplete or stale. | Run `hades backend sync`, approve required source/index jobs, and capture current bug evidence. |
| `bug_evidence` missing | The backend has no current evidence for this bug. | Use dashboard `Bug intake` or `hades backend bug-intake`. |
| `source_slices` missing | Exact line evidence is not available. | Approve a bounded source-slice job or keep the diagnosis insufficient. |
| `freshness.status=stale` | Indexed artifacts do not match the relevant head/deploy context. | Sync the current workspace or capture deploy mismatch evidence. |
| `diagnosis_evidence_refs_required` | A high/medium report lacks evidence references. | Attach evidence refs or downgrade confidence. |
| `diagnosis_awareness_not_diagnosable` | Backend awareness does not support source-free diagnosis. | Repair coverage before a precise claim. |

## Support Bundle

For support, collect metadata first:

```bash
hades backend support-report --json
hades backend privacy-export --json
hades logs --level WARNING --session latest
```

Only use `privacy-export --include-content` after explicit user approval and a
secure sharing channel. Content export is not required for ordinary setup,
coverage, or quality-gate debugging.
