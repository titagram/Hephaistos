# Hades path identity and fallback truth fix

Date: 2026-07-13

Baseline: `b485832c5`

## Scope

- Reject path-like explicit node and edge IDs consistently, including relative
  paths, Windows separators, `file:/` and `file://` URIs, dot segments,
  traversal, controls, and oversized inputs.
- Keep opaque semantic IDs with safe colon separators valid and preserve
  idempotent replay of an entity's own reserved derived ID.
- Parse route names separately from route paths. Route paths require a leading
  slash, a bounded safe grammar, balanced placeholders, and no traversal.
- Derive path-bearing node identities from a fixed-size SHA-256 token of the
  canonical full path or URI. Only a bounded, narrow-grammar basename is
  published in canonical nodes.
- Preserve distinct identities for distinct POSIX, Windows-drive, UNC, and
  file-URI paths while deduplicating exact repeats deterministically.
- Report fallback reasons in loss-priority order: canonicalization loss,
  bounded or omitted input, then genuinely empty extraction.

## Root causes

1. The explicit-ID regex admitted `/`, while the unsafe-path helper rejected
   only absolute paths and `file://`. Relative paths, `file:/`, and dot-segment
   traversal therefore reached canonical IDs and endpoint placeholders.
2. The previous path normalizer replaced absolute and oversized paths with a
   basename before building semantic identity. Two distinct source paths with
   the same basename consequently collapsed into one node.
3. The zero-relationship fallback selected `no_relationships_extracted` from
   raw edge count alone, ignoring canonicalization issues, truncation, and
   failed files. With relationships present, bounded loss also incorrectly
   outranked canonicalization loss.

## TDD evidence

The new focused cases were run before production changes. Six of seven tests
failed for the expected missing behavior; the positive route grammar case was
already accepted. Separate RED/GREEN cycles then reproduced and fixed:

- relative/path/file-URI node and edge IDs;
- exact POSIX, relative, `file:/`, and `route:../../` reviewer cases;
- traversal and malformed route placeholders;
- absolute full-path basename collisions;
- oversized path output amplification;
- UNC/POSIX identity collision;
- unsafe display-basename characters;
- truthful empty/truncated/files-failed fallback priority;
- a retention regression for legitimate Symfony route names beginning `_`.

Final selected regression suite:

```text
105 passed in 3.49s
ruff check: PASS
ruff format --check: PASS
git diff --check: PASS
```

## Bounded scale check

An in-memory synthetic graph with 5,000 nodes and 10,000 relationships was
canonicalized without persistence:

```text
real: 0.31s
maximum RSS: 52,822,016 bytes
nodes input/emitted: 5000/5000
edges input/emitted/omitted: 10000/9998/2
closed endpoints: PASS
```

The two omitted edges referenced the single real node displaced by the global
5,000-node cap when the shared external endpoint was admitted.

## Carnovali no-upload retention capture

The native indexer was run directly against the Carnovali workspace using the
same bounded configuration as the baseline capture:

```text
max_files=5000
max_symbols=5000
max_edges=10000
max_graph_nodes=5000
max_file_bytes=512000
```

The first candidate grammar rejected 18 legitimate Symfony route-name edges
whose names begin with `_`. A dedicated failing test reproduced that exact
regression before route-name grammar was corrected. The final capture matches
the baseline retention exactly:

```text
canonical nodes: 5000
canonical relationships: 9552
edges omitted: 398
closed endpoints: PASS
unique node IDs: PASS
unique edge IDs: PASS
quality: partial
fallback_reason: canonicalization_omissions
```

The reason differs intentionally from the earlier report because the required
truth ordering now gives canonicalization omissions priority over bounded
input when both occurred.

## Remote validator

The exact final Carnovali artifact was streamed over SSH stdin into the remote
Laravel `CanonicalGraphNormalizer`. It was normalized in process and never
uploaded or stored:

```text
status: accepted
contract: hades.graph_artifact.v1
nodes: 5000
relationships: 9552
```

The independent remote normalizer unit suite also passed:

```text
16 tests, 35 assertions, 0 failures
```

## Live-state guarantee

No Hades sync, artifact upload, database migration, PostgreSQL write, Neo4j
write, projection, deployment, container restart, or live rollout was run.
