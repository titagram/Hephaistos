# Hades canonical graph ID/privacy hardening report

Date: 2026-07-13

Baseline: `6f37fb3d4`

## Scope

- Make node and edge identity handling deterministic and collision-safe.
- Reject unsafe, path-like, control-character, reserved-namespace, and
  over-512-byte explicit IDs.
- Bound endpoint locator processing before alias or placeholder materialization.
- Synthesize placeholders only for safe semantic forms: FQCN/dotted classes,
  `Class::method`, `Class@method`, bounded bare symbols, known HTTP verb routes,
  safe route names, data references, and relative source paths.
- Preserve legacy `symbols` and `edges` lists exactly, including invalid
  non-dictionary entries, while publishing filtered canonical
  `nodes`/`relationships` separately.
- Separate exact deduplication from actual omissions in both counters and
  extractor quality.

## TDD evidence

The focused test selection was run before production changes. Eight tests
failed on the expected missing behavior:

- reserved generated edge-ID hijack;
- explicit edge-ID collision groups;
- duplicate/omission counter separation;
- truthful all-edges-omitted fallback;
- path/control/oversize explicit IDs;
- 100 kB and unrecognized endpoint locators;
- privacy-safe path placeholders;
- non-dictionary legacy entries.

Additional focused RED/GREEN cycles covered blank IDs, bounded bare symbols,
and structured route/data locator grammar with negative injection cases.

Final focused and regression verification:

```text
101 passed in 3.31s
ruff check: PASS
ruff format: PASS
git diff --check: PASS
```

The 101-test selection comprised graph-contract, backend-job, backend-sync,
golden-indexer, and index-enrichment tests.

## Deterministic identity behavior

- Generated node and edge IDs remain SHA-256 IDs in reserved versioned
  namespaces.
- A caller may replay a generated reserved ID only when it equals that
  entity's own derived semantic ID.
- A reserved ID copied from another semantic entity is rejected.
- Final edge candidates are grouped before emission. Exact semantic duplicates
  collapse; semantically distinct candidates sharing an ID cause the complete
  collision group to be omitted with `edge_id_collision`.
- Candidate selection and collision handling are independent of input order.

## Truthful accounting

- `nodes_deduplicated` and `edges_deduplicated` do not count as omissions and
  do not degrade quality.
- Invalid shapes, unsafe IDs/locators, ambiguities, collisions, and capacity
  losses count as omissions.
- Non-empty raw edge input that produces no canonical relationship reports
  `inventory_only / canonicalization_omissions`.
- Genuinely empty raw edge input reports
  `inventory_only / no_relationships_extracted`.
- Issue details are capped at 50 records; aggregate counters remain complete.
  Locator values are represented only by bounded fingerprints in issue data.

## Real no-upload retention capture

The native indexer was run directly against the Carnovali workspace with this
bounded configuration, without calling Hades sync or any artifact-upload API:

```text
max_files=5000
max_symbols=5000
max_edges=10000
max_graph_nodes=5000
max_file_bytes=512000
```

Result:

```text
legacy symbols / edges: 5000 / 10000
canonical nodes / relationships: 5000 / 9552
node inputs: 5000
node deduplicated: 2
node invalid or collided: 0
node capacity omissions: 1708
synthetic nodes emitted: 1710
isolated nodes emitted: 84
edge inputs: 10000
resolved endpoints: 10140
synthesized endpoints: 9462
ambiguous endpoints: 49
unrecognized or unselected endpoints: 349
edge ID collisions: 0
edge deduplicated: 50
edges omitted: 398
edges emitted: 9552
closed endpoints: PASS
unique node and edge IDs: PASS
```

The extractor truthfully reports `partial / bounded_or_omitted_input`. The 349
residual locators are intentionally not broadened: they are dynamic,
variable-bearing, or do not satisfy the safe semantic grammar. The result is
above the prior 5000-node/6964-edge reference, so the stricter privacy rules do
not introduce the suspected retention regression.

## Remote validator

The exact real capture was streamed over SSH stdin to the backend
`CanonicalGraphNormalizer` and normalized in process. It was not sent through
the artifact API, persisted to a file, inserted into PostgreSQL, or projected
to Neo4j.

```text
status: accepted
contract: hades.graph_artifact.v1
nodes: 5000
relationships: 9552
```

The independent remote normalizer suite also passed: 16 tests / 35 assertions.

## Live-state guarantee

No Hades sync, artifact upload, database migration, PostgreSQL write, Neo4j
write, projection, deployment, container restart, or live rollout was run.
