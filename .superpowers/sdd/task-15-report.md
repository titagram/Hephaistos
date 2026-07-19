# Task 15 Report — Atomic Graph Pruning and Deterministic Bundles

## Status

Complete. Graph v2 now selects whole semantic units under exact serialized
byte/chunk ceilings and emits deterministic, resumable, privately spooled
bundles without entity-count truncation. The implementation preserves every
surviving public ID, recomputes authoritative omission/coverage/completeness
ledgers and the artifact digest, and validates the selected artifact before it
can be written. The independent-review blockers C1 and I1 are repaired: the
shared validator closes every derivable file/entrypoint gap against explicit
omission provenance, and the bundle boundary distinguishes recoverable
record-derived capacity from an irreducibly oversized required envelope. The
round-two repair additionally closes omissions per authoritative language and
preserves the union of semantic capability provenance across overlapping
rejected units.

## TDD evidence

The initial complete Task 15 test tranche was run before production
implementation and produced `18 failed, 7 passed`. The failures independently
showed the missing pruner, non-canonical chunk permutations, an unbound resume
manifest, indistinguishable digest failures, absent lock/cleanup/delete
behavior, and the wrong oversized-record failure.

Two later audit counterexamples were also observed RED before repair:

- an incomplete stale spool containing only `.lock` and a temporary chunk was
  not discovered by cleanup (`1 failed`);
- forcing the native-Windows lock branch failed because `fcntl` was assumed
  unconditionally (`1 failed`).

Both cases are now covered in the final suite.

The independent review then supplied three additional counterexamples. All
three were reproduced through both `validate_artifact` and
`GraphBundleWriter.write` before the repair:

- a missing represented file with `files.budget_omitted=1` but a zero bundle
  omission ledger was accepted;
- a real pruner result with one missing detected entrypoint remained accepted
  after changing only its omission ledger from 16 to zero;
- a represented `budget_omitted` file with one observable budget event was
  accepted with global and language reason counts of 999.

The focused RED run was `3 failed` because neither entrypoint raised. Two
additional closure counterexamples were also RED: a single ledger unit could
cover disjoint missing-file and missing-entrypoint gaps, and arbitrary budget
reason counts were accepted when a nonzero ledger existed. The repaired
focused tranche is `5 passed`; the full Task 15 tranche below covers these
cases through both public entrypoints.

Round two added the missing PHP-language counterexample through those same two
public boundaries plus total-capacity and record-derived-manifest regressions
for overlapping rejected units. The focused RED run was `5 failed`: the
validator accepted a missing detected PHP file while PHP remained `full`, and
the pruner raised raw `coverage_omission_completeness` errors at total 5000 and
manifest ceilings 4000, 4025, and 4050. The identical focused command is now
`5 passed`; an expanded compatibility check including the round-one zero-ledger
precedence is `6 passed`.

## Atomic selection

`GraphBudgetPruner` implements the section 8.2 order and dependency rules:

1. entrypoint-flow units ordered by entrypoint-kind ordinal, normalized label,
   and ID, including recursive async-flow/step closure and every referenced
   topology, uncertainty candidate, owner, and evidence file;
2. residual structural weakly connected components ordered by their smallest
   `(safe_path_or_empty, public_id)`, excluding entrypoint-dependent topology;
3. remaining file inventory units ordered by path and ID.

Each unit is rejected whole if any record cannot fit one exact JCS chunk. Every
other tentative unit is finalized with all rejections seen so far, serialized
through the deterministic bundle planner, and accepted only if the exact
manifest plus uncompressed chunk wrappers/records fits both total bytes and
chunk count. Rejection never stops consideration of a smaller later unit.

The all-accepted path plans the already-validated full artifact once and
returns it unchanged when it fits. This is semantically identical to accepting
every unit and avoids quadratic work for uncapped high-cardinality graphs. The
chunk partitioner was likewise made linear by canonicalizing each record once
and assembling the exact JCS wrapper once per chunk instead of reserializing
the growing chunk after every record.

Finalization now accumulates capability impact independently from reason
precedence. Every rejected occurrence contributes its semantic capability set
to the omitted token; a later `record_too_large` rejection may still win the
reason code without discarding earlier/later impacts. Consequently, an omitted
file that participates first in an entrypoint or structural unit still gains
the required `inventory` provenance when its inventory unit is rejected.

## Coverage-contract resolution

The old validator equated detected entrypoints/files with represented public
records, which made honest atomic omission impossible. The narrowly scoped
contract change keeps detection authoritative while still closing every
represented count:

- entrypoint `detected` and `by_kind` remain the original discovery ledger;
- `analyzed` is the exact represented entrypoint count;
- `partial` is the represented partial synchronous-flow count plus rejected
  detected entrypoints (the fields intentionally overlap for a represented
  entrypoint with a partial flow);
- file `discovered`/`hashed` and language detected counts remain authoritative,
  selected statuses/analyzed counts close exactly, and missing file records are
  counted as `budget_omitted`;
- `records.omitted_by_bundle_budget` counts each unique excluded public chunk
  record exactly once, and any nonzero omission requires an explicit
  `resource_budget_reached` or `record_too_large` capability reason.

The repair removes the remaining silent-omission escape hatches without
restoring the invalid `detected == len(entrypoints)` assumption. Missing file
records and missing detected entrypoints form a derivable, disjoint lower
bound for `records.omitted_by_bundle_budget`. Each affected semantic family
must be `partial` with budget evidence (`inventory` for files,
`entrypoint_discovery` for entrypoints), global completeness must be partial,
and the global ledger must carry a budget reason. Budget reason counts remain
exact when the omission ledger is zero; with pruning provenance they are
bounded by observable events plus that explicit ledger, including a guard
against double-counting both budget reason codes inside one capability.

For every authoritative language record, the validator also derives the gap
between `detected_file_count` and represented file nodes for that language and
includes represented `budget_omitted` file statuses. A positive affected-file
count requires the language row and its inventory capability to be partial,
with a language-scoped budget/record-size reason whose count covers the gap and
is bounded by the shared observable-plus-ledger reconciliation. A global
language-null reason cannot substitute for evidence in a known affected
language, and existing global/language scope reconciliation closes the two
ledgers.

The Task 14 builder now emits `entrypoints.analyzed == len(entrypoints)` so a
represented partial flow does not falsely become “not analyzed.” The adjacent
501-test producer/contract matrix proves this stronger overlapping invariant is
compatible with full, partial, polyglot, and framework graphs.

## Deterministic and resumable bundle behavior

- exact chunk-kind order, contiguous indexes, 0–512 chunks, one or more records
  per emitted chunk, strict increasing IDs across same-kind chunks;
- exact JCS manifest/chunk wrappers and byte/digest descriptors;
- gzip level 6, `mtime=0`, flags byte zero, OS byte 255, one member only;
- configured/local/backend byte ceilings, 8 MiB wire ceilings, 100:1 streaming
  decompression guard, CRC/EOF/trailing/second-member rejection;
- private `0700` spool directories and `0600` files, atomic temp-file rename,
  POSIX `fcntl` and native-Windows `msvcrt` exclusive locks;
- canonical resume state bound to the exact manifest and independently checked
  compressed/uncompressed chunk bytes;
- locked/uploading spools survive cleanup, complete and incomplete unlocked
  stale spools are removed, and published/canceled spools are explicitly
  deleted.

## Typed capacity and envelope failures

Bundle planning now exposes stable typed failures instead of parsing generic
exception text:

- `GraphUnitRecordTooLargeError` (`record_too_large`) is recoverable by
  rejecting the containing atomic semantic unit;
- `GraphManifestCapacityError` and `GraphChunkCapacityError`
  (`resource_budget_reached`) represent capacity that whole-unit pruning can
  legally reduce;
- `GraphEnvelopeTooLargeError` (`graph_record_too_large`) means the required
  manifest remains above 4 MiB even after all public chunk records and
  descriptors are removed;
- `GraphBundleBudgetTooSmallError` (`graph_bundle_budget_too_small`) is
  reserved for a final valid empty selected artifact that exceeds total
  capacity.

The pruner catches only the recoverable capacity subclasses. Schema, digest,
integrity, and other generic bundle failures propagate rather than being
misreported as an ordinary unit rejection. Boundary tests construct a valid
zero-public-record envelope one byte below, exactly at, and one byte above
4 MiB and exercise both writer and pruner. A separate record-derived manifest
overflow proves that descriptor-driven overflow is still recoverable by
atomic pruning.

## Verification

Final Task 15 suite:

```text
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_graph_budget_pruner.py \
  tests/hermes_cli/test_hades_graph_bundle.py -q
```

Result after independent-review repair: `36 passed in 639.79s`. This includes
all original coverage plus the five validator/writer counterexamples, the real
below/at/above 4 MiB required-envelope boundary, and recoverable
record-derived manifest overflow. The original coverage includes a single over-budget flow,
accepted/rejected shared topology, a smaller later unit, oversized record,
exact byte ceiling, zero-chunk envelope, more than 5,000 nodes, more than
10,000 edges, more than 500 routes, permutation-invariant bytes, referential
reassembly, deterministic gzip, trailing/concatenated members, both digest
mismatches, resume after chunk index 2, mutation after acknowledgements,
private modes, locks, stale cleanup, and explicit terminal cleanup.

Round-two focused non-volumetric Task 15 verification:

```text
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_graph_budget_pruner.py \
  tests/hermes_cli/test_hades_graph_bundle.py -q \
  -k 'not more_than_5000 and not more_than_10000 and \
      not more_than_500_routes and not manifest_envelope'
```

Result: `35 passed, 6 deselected in 7.58s`. This is intentionally reported as
a focused run, not a new full-suite result. The three high-cardinality tests
and three real-4 MiB tests were excluded because round two changes only
validation, rejection-capability attribution, and the focused pruner tests;
bundle planning and the 4 MiB fixture were untouched. The cumulative full
result above remains the last executed complete Task 15 suite.

Adjacent Task 14/contract/golden suite after round two: `501 passed in 16.68s`
across the 12 modules listed by the Task 14 report.

Fresh final static verification:

```text
ruff format --check  # scoped repair files already formatted
ruff check           # All checks passed
python -m py_compile # exit 0
git diff --check     # exit 0
```

## Files

- `hermes_cli/hades_graph_v2/pruning.py`
- `hermes_cli/hades_graph_v2/bundle.py`
- `hermes_cli/hades_graph_v2/validation.py`
- `hermes_cli/hades_index/lifecycle/builder.py`
- `tests/hermes_cli/test_hades_graph_budget_pruner.py`
- `tests/hermes_cli/test_hades_graph_bundle.py`
- `.superpowers/sdd/task-15-report.md`

The shared Task 17 changes in `hermes_cli/hades_backend_client.py`,
`tests/hermes_cli/test_hades_backend_client.py`, and
`.superpowers/sdd/progress.md` were neither modified for Task 15 nor staged.
The original implementation commit is
`84640d689 feat(hades): bundle graph v2 without silent truncation`; the review
repairs are recorded as separate commits.
