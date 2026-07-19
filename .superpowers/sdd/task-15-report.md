# Task 15 Report — Atomic Graph Pruning and Deterministic Bundles

## Status

Complete. Graph v2 now selects whole semantic units under exact serialized
byte/chunk ceilings and emits deterministic, resumable, privately spooled
bundles without entity-count truncation. The implementation preserves every
surviving public ID, recomputes authoritative omission/coverage/completeness
ledgers and the artifact digest, and validates the selected artifact before it
can be written.

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

## Verification

Final Task 15 suite:

```text
/Users/gabriele/Dev/Hephaistos/.venv/bin/python -m pytest \
  tests/hermes_cli/test_hades_graph_budget_pruner.py \
  tests/hermes_cli/test_hades_graph_bundle.py -q
```

Result: `27 passed in 189.58s`. This includes a single over-budget flow,
accepted/rejected shared topology, a smaller later unit, oversized record,
exact byte ceiling, zero-chunk envelope, more than 5,000 nodes, more than
10,000 edges, more than 500 routes, permutation-invariant bytes, referential
reassembly, deterministic gzip, trailing/concatenated members, both digest
mismatches, resume after chunk index 2, mutation after acknowledgements,
private modes, locks, stale cleanup, and explicit terminal cleanup.

Adjacent Task 14/contract/golden suite: `501 passed in 16.16s` across the 12
modules listed by the Task 14 report.

Fresh final static verification:

```text
ruff format --check  # 6 files already formatted
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
The required commit subject is
`feat(hades): bundle graph v2 without silent truncation`.
