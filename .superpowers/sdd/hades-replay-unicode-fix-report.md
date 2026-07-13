# Hades canonical replay and locator hardening report

Date: 2026-07-13

Baseline: `f396099f2`

## Scope and contract

- Path-bearing canonical nodes now publish a bounded, non-reversible
  `properties.identity_fingerprint` with the strict grammar
  `sha256:[0-9a-f]{64}`. It is the private canonical path/locator identity
  component; no raw path is stored in it.
- A nodes-only replay prefers that fingerprint in the path slot of the node's
  semantic identity. The generated ID therefore remains bound to the private
  identity component and the public kind/name/signature/namespace fields.
- A malformed fingerprint is rejected. A reserved generated ID paired with a
  different valid fingerprint or changed public identity is also rejected.
- Canonical relationship replay uses the already canonical source and target
  IDs and remains idempotent, including relationships to inferred external
  file nodes.
- Endpoint validation rejects all Windows drive-prefixed forms, every
  case-variant of the `file:` scheme, root-relative Windows source paths,
  unknown schemes carrying file-like payloads, and Unicode `Cc`/`Cf`
  characters. A leading-backslash FQCN remains a semantic locator.
- Relative source placeholders use an ASCII, bounded, traversal-free segment
  grammar and a known source extension. The indexer's explicit `test:` source
  reference is the sole prefixed source-path form currently allowlisted; its
  payload must satisfy the same grammar. Only its fingerprint and basename are
  published.
- Route safety checks strict percent triplets and recursively decodes a bounded
  number of layers for validation. Decoded dot segments, slash, backslash,
  malformed UTF-8, and Unicode controls are rejected; safe encoded parameters,
  spaces, braces, and literal percent data remain valid.
- Extractor loss is derived from actual node/edge/capacity omissions, not from
  warning count. An unused ambiguous alias remains observable but does not turn
  a complete node inventory into `canonicalization_omissions`; an edge that
  actually uses the alias does.

## Root causes

1. Canonical output exposed only a basename, while its ID had been derived from
   a full private path hash. A second nodes-only finalization therefore had no
   input from which it could reproduce the original ID.
2. Path and route checks covered common absolute forms but not drive-relative
   Windows paths, bare `file:` URIs, Unicode format controls, or percent-decoded
   route syntax.
3. `issues_count` mixed non-loss warnings with actual omissions, so an unused
   ambiguous alias incorrectly degraded extractor quality.
4. The first strict source-path allowlist treated the indexer's documented
   `test:tests/...php` references as unknown schemes. The real retention probe
   caught the resulting regression before commit; a narrow, payload-validated
   `test:` exception restored the baseline without admitting arbitrary schemes.

## TDD evidence

The first focused RED run produced five failing behavior groups:

- missing fingerprint and failed nodes-only replay;
- no strict fingerprint validation;
- accepted drive-relative/file/unknown-scheme/Unicode locators;
- accepted encoded route traversal, separators, and controls;
- warning count incorrectly treated as canonicalization loss.

A separate RED/GREEN cycle preserved a safe encoded literal percent (`%25`).
The Carnovali regression then received its own RED/GREEN guard for the known
`test:` source-reference scheme.

Fresh selected verification after the final change:

```text
113 passed in 3.51s
ruff check: PASS
ruff format --check: PASS
git diff --check: PASS
```

The selection contains graph-contract, backend-job, backend-sync,
golden-indexer, and index-enrichment tests. The graph-contract file contains 34
tests, including two-pass absolute/relative/external replay, permutation,
relationship preservation, fingerprint mismatch, exact Unicode cases, and
truthful ambiguous-alias accounting.

## Bounded scale check

An in-memory 5,000-node / 10,000-relationship graph completed without
persistence:

```text
real: 0.31s
maximum RSS: 56,098,816 bytes
nodes emitted: 5000
relationships emitted: 10000
edges omitted: 0
closed endpoints: PASS
fingerprint grammar: PASS
```

## Carnovali no-upload retention

The native indexer ran directly against the local Carnovali workspace with the
existing bounded configuration (`5000` files/symbols/nodes, `10000` edges,
`512000` bytes per file). The first candidate lost 1,398+ test-reference edges;
the final candidate restored the established baseline exactly:

```text
canonical nodes: 5000
canonical relationships: 9552
edges omitted: 398
unrecognized endpoints: 349
ambiguous endpoints: 49
closed endpoints: PASS
unique node IDs: PASS
unique edge IDs: PASS
nodes carrying private identity fingerprints: 3340
quality: partial
fallback_reason: canonicalization_omissions
```

## Remote normalizer, in memory

The exact final Carnovali artifact was streamed over SSH standard input into
the running Laravel `CanonicalGraphNormalizer`; it was not uploaded or stored.
The normalizer accepted 5,000 nodes and 9,552 relationships and preserved all
3,340 identity fingerprints in node properties.

The independent remote normalizer suite also passed:

```text
16 tests, 35 assertions, 0 failures
```

## Live-state guarantee

No Hades sync, artifact upload, database migration, PostgreSQL write, Neo4j
write, projection, deployment, container restart, or live rollout was run.
