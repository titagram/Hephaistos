# Task 8 Report — Canonical Dashboard Graph Reads

## Result

Implemented and committed on remote branch `feature/canonical-graph-foundation-20260712`.

- Commit: `c8731a8a67c1a388c88fca075e8c7a90ab2dc92f`
- Commit message: `fix(dashboard): read canonical project graphs`
- Live PostgreSQL / Neo4j: not accessed or modified
- Pre-existing dirty file preserved: `backend/vendor/pestphp/pest/.temp/test-results`

## Files

- `backend/app/Dashboard/DashboardApiReader.php`
- `backend/tests/Feature/Dashboard/DashboardApiContractTest.php`

## RED

The two initial canonical contract tests failed as expected before implementation:

- single Hades scope returned `stats.nodes = 0` instead of `1`;
- multiple scopes returned no `projection_status` instead of `scope_required`.

RED result: 2 failed, 4 assertions.

## Implementation

- Preserved the public `graph(?string $projectId, ?string $snapshotId, ?string $runId)` interface.
- Preserved the legacy snapshot path whenever `snapshot_id` or `run_id` is explicit.
- Canonical default selection is used only for project-scoped reads without explicit legacy selectors.
- Zero scopes returns an empty graph with `projection_status=unavailable`.
- One scope returns a bounded normalized preview plus `source_scope`, `graph_version`, `quality`, and `projection_status`.
- Multiple scopes returns an empty graph with `projection_status=scope_required` and bounded scope metadata; no scope is selected arbitrarily.
- Scope metadata contains only type, ID, quality, head commit, creation time, and projection status. Workspace `display_path` is never returned.
- Existing project authorization checks and project-scoped repository/projection lookups remain enforced.

## GREEN / Verification

Focused canonical tests: 2 warnings, 16 assertions, zero failures.

Required suite:

- `DashboardApiContractTest.php`
- `ProjectKickstartDashboardApiTest.php`
- Result: 9 warnings, 158 assertions, zero failures.

Relevant canonical regressions:

- `Plugin/GraphQueryApiTest.php`
- `Hades/CanonicalHadesGraphProjectionTest.php`
- Result: 38 warnings, 239 assertions, zero failures.

Formatting and hygiene:

- Pint: 2 files passed.
- `git diff --check`: passed.
- Remote status after commit: only the pre-existing Pest temp result file remains dirty.

The warnings are the already-known test-environment warning caused by the absent `/workspace/backend/.env`; they are not assertion failures and existed outside this task.

## Risks / Review Notes

- `listScopes()` treats every declared repository or linked workspace binding as a scope even if it has no graph artifact. For a single empty scope the API correctly reports `unavailable`; for mixed populated/empty scopes it deliberately requires explicit scope selection rather than guessing.
- The canonical preview uses the existing dashboard node/edge projection and existing 200-node / 300-edge bounds, so response shape remains compatible with the current dashboard consumer.

## Review Fix — 2026-07-13

The two independent-review findings were fixed in a single follow-up commit:

- Commit: `1d4ee7d0e6dfc2619739c3180c571a29e695e2f9`
- Commit message: `fix(dashboard): bound canonical scope metadata`
- Live PostgreSQL / Neo4j: not accessed or modified
- Pre-existing dirty file preserved: `backend/vendor/pestphp/pest/.temp/test-results`

### P1 — Bounded multi-scope metadata

- Added `CanonicalGraphRepository::listScopeMetadata()` with a hard maximum of 50 dashboard scopes.
- Scope declarations are fetched in two bounded scalar queries; the latest projection metadata for the bounded set is fetched in one batch query using `ROW_NUMBER()` partitioning.
- The multi-scope path no longer calls `latestForScope()`, reads `hades_agent_artifacts`, decodes graph JSON, or normalizes nodes for each scope.
- Responses expose `scopes_truncated=true` when more scopes exist, while continuing to return `scope_required` with no arbitrary graph selection.
- RED evidence: the regression failed because `scopes_truncated` was absent and the old response was unbounded.
- GREEN evidence: a 76-scope fixture returns exactly 50 scope records, uses exactly three canonical metadata queries, and performs zero reads from `hades_agent_artifacts`.

### P2 — Local path privacy

- Removed `properties.path` as a dashboard node-label fallback. A missing/empty `name` now falls back to the canonical node external ID.
- The regression embeds `/srv/private/project/app/SecretController.php` in `properties.path` with no `name`, verifies the external ID label, and asserts that neither the node path nor binding display path appears anywhere in the serialized response.
- RED evidence: the old reader returned the absolute node path as `nodes.0.label`.
- GREEN evidence: the focused four canonical dashboard tests pass with 31 assertions.

### Fresh verification after commit

- Selected Task 8 + graph/Hades regression suite: 610 assertions, zero failures.
- Known test-environment warnings: 76, all from the pre-existing absent `/workspace/backend/.env` condition.
- Pint `--test`: 3 files passed.
- `git diff --check c8731a8a..HEAD`: passed.
- Remote status: only the pre-existing Pest temp result file is dirty.

## Second Review Fix — 2026-07-13

The two residual independent-review findings were resolved in one remote commit:

- Commit: `94ccfcac868fa9c9a279df9eabd793fb535b3a3b`
- Commit message: `fix(dashboard): align canonical metadata privacy`
- Live PostgreSQL / Neo4j: not accessed or modified
- Pre-existing dirty file preserved: `backend/vendor/pestphp/pest/.temp/test-results`

### P1 — Metadata follows the latest real graph artifact

- `listScopeMetadata()` now batch-selects the latest eligible Hades artifact and repository snapshot artifact for the bounded set of at most 50 scopes.
- The Hades artifact query selects metadata columns only; it does not select or decode the JSON `artifact` payload.
- Projection metadata is fetched in one bounded batch and accepted only for the exact latest `(artifact_type, artifact_id)` and matching source scope.
- A newer unprojected artifact therefore reports its artifact `created_at`, null `quality`/`head_commit`, and `projection_status=unavailable` instead of exposing an older ready projection.
- A scope with no graph artifact reports null graph metadata; repository/binding creation timestamps are no longer presented as graph timestamps.
- The `limit + 1` sentinel and `scopes_truncated` behavior remain unchanged; the path is bounded and has no per-scope artifact/projection query.

### P2 — Central dashboard-preview path sanitization

- Both canonical and explicit legacy previews pass through one `sanitizeGraphPreview()` boundary.
- Unsafe POSIX, Windows-drive, UNC, and `file://` identifiers are deterministically pseudonymized.
- Node IDs, labels, and `source.ref` use the same public identifier mapping; edge endpoints are rewritten through that mapping and edges without valid preview endpoints are rejected.
- Unsafe edge IDs are pseudonymized; any remaining nested preview value is recursively redacted.
- The stored canonical artifact is never modified.
- The regression recursively scans the complete response JSON, checks all four path forms are absent, requires four unique public node IDs, and verifies every edge endpoint references a returned node.

### TDD and fresh post-commit verification

- RED: the old implementation returned the older projection for a scope with a newer unprojected artifact, invented metadata for an empty scope, performed no latest-artifact metadata query, and exposed local paths throughout preview identifiers/labels/endpoints.
- Focused GREEN: 3 tests, 44 assertions, zero failures.
- Full Task 8 + canonical graph + Hades verification: 646 assertions, zero failures.
- Known warnings: 78, all from the pre-existing missing `/workspace/backend/.env` test condition.
- Pint `--test`: 3 files passed.
- `git show --check HEAD`: passed.
- Remote status after commit: only the pre-existing Pest temp result file is dirty.

## Final Privacy and Identity Fix — 2026-07-13

The final independent-review findings were resolved in one remote commit:

- Commit: `f49782e5449b75064c0bc382c00fc41835ecedce`
- Commit message: `fix(dashboard): harden graph identity privacy`
- Live PostgreSQL / Neo4j: not accessed or modified
- Pre-existing dirty file preserved and excluded from the commit: `backend/vendor/pestphp/pest/.temp/test-results`

### Embedded path-token privacy

- `looksLikeLocalPath()` now detects file URIs, prefixed POSIX paths, Windows drive paths, UNC paths, and paths introduced by brackets, parentheses, assignment, comma, semicolon, or pipe delimiters.
- The detector handles graph identifiers such as `file:/home/...`, `node:/home/...`, `path:/var/...`, and `method:C:\\Users\\...` without treating normal HTTP(S) URLs, route identifiers, URNs, or namespace-qualified method identifiers as local files.
- The same central sanitizer remains active for canonical and explicit legacy previews. Stored artifacts are not modified.
- Endpoint tests recursively inspect every string in the complete JSON response, cover ten unsafe forms and six safe controls, and verify the unsafe tokens are absent from canonical and legacy output.

### Deterministic, collision-safe public identities

- Node mappings are precomputed from the complete preview set in sorted raw-ID order rather than assigned on first encounter.
- Unsafe IDs and raw IDs occupying the reserved `hades-public-v1-{node|edge}-` namespace are deterministically hashed into that namespace.
- Candidate collisions are expanded deterministically from sorted semantic identities, independent of artifact ordering.
- Edge identity includes raw ID, raw endpoints, kind, and duplicate occurrence; missing IDs and repeated raw IDs receive stable unique public IDs.
- Tests deliberately construct collisions with the previous `node-<24 hex>` / `edge-<24 hex>` scheme, reserved-namespace inputs, repeated edge IDs, and a missing edge ID. Reversing both node and edge input order yields the same semantic node/edge mapping.
- Response invariants assert unique node IDs, unique edge IDs, and that every edge endpoint references a returned node.

### TDD and final verification

- RED: the two initial tests failed as expected: embedded path tokens survived the response and repeated/colliding edge IDs were not unique.
- Focused GREEN: 3 tests, 46 assertions, zero failures.
- Fresh selected Task 8 + graph/Hades projection/hardening suite: 798 assertions, zero failures; 107 known missing-`.env` warnings.
- Pint `--test`: 2 files passed.
- `git diff --check 94ccfcac --` and pre-commit staged diff check: passed.
- Commit contains exactly the dashboard reader and its contract test.

### Pre-existing broad-suite failure intentionally excluded

The broader exploratory command that included all of `tests/Feature/Hades` found one independently reproducible pre-existing failure:

```text
APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= \
php artisan test tests/Feature/Hades/HadesCausalPackTest.php \
  --filter="creates lists shows and replays"
```

Observed failure: expected HTTP 201, received 500 because `CanonicalGraphNormalizer.php:27` raises `InvalidArgumentException: Canonical graph node id is missing.` The fixture submits a `hades.php_graph.v1` node without an `id`. The stack is `ArtifactController -> CanonicalGraphRepository -> CanonicalGraphNormalizer`; it does not enter `DashboardApiReader` or the Task 8 test file. The failure reproduces in isolation and is unrelated to the two Task 8 files changed by `f49782e5`, so it was documented but not corrected in this commit.

## Generic Path Payload Classifier Fix — 2026-07-13

The final classifier review findings were fixed on the remote feature branch:

- Commit: `0cfb80e7eb335cc40d9bce6340d987e4f1128101`
- Commit message: `fix(dashboard): classify graph path payloads`
- Live PostgreSQL / Neo4j: not accessed or modified
- Commit scope: exactly `DashboardApiReader.php` and `DashboardApiContractTest.php`

### Root cause and implementation

- The prior detector combined a closed semantic-prefix list with a generic absolute-slash rule. New wrappers such as `symbol:/etc/passwd` bypassed the former, while routes such as `/api/orders` were incorrectly caught by the latter.
- The replacement treats wrapper names as opaque. It detects path payloads only after generic token boundaries, so nested/arbitrary semantic prefixes, delimiters, brackets, and HTTP verbs need no allowlist.
- Windows drive paths, UNC paths, and file URI payloads are classified independently of wrapper names.
- POSIX classification extracts the first path root. OS/workspace roots are sensitive, while route-shaped roots (`api`, versioned `vN`) are explicitly allowed.
- HTTP(S), route/API/version identifiers, URNs, and class/method identifiers remain public.

### TDD and fresh post-commit verification

- RED: canonical and legacy endpoint tests both failed against the old classifier because new unsafe identifiers survived and safe API routes were removed.
- Focused GREEN: 2 endpoint tests, 76 assertions, zero failures.
- Fresh selected Task 8 suite: 641 assertions, zero failures; 73 known missing-`.env` warnings.
- The matrix covers 21 unsafe and 15 safe identifiers across complete canonical and legacy JSON responses, while existing collision, determinism, endpoint-integrity, metadata, and bounded-query regressions remain green.
- Pint `--test`: 2 files passed.
- `git show --check HEAD`: passed.
- Remote status after commit: only the pre-existing Pest temp result file remains dirty.

## Raw Identity Provenance Boundary Fix — 2026-07-13

- Remote commit: `d17f658e` (`fix(dashboard): preserve private identity provenance`)
- Scope: `DashboardApiReader.php`, `CanonicalGraphRepository.php`, and `DashboardApiContractTest.php`
- Live PostgreSQL / Neo4j: not accessed or modified
- Pre-existing Pest result cache remained dirty and was excluded from the commit

### Root cause and implementation

- `CanonicalGraphNormalizer` intentionally drops top-level `external_id` and `source` metadata, so the public dashboard mapper could no longer tell that an otherwise grammar-safe label originated from a private identity.
- `CanonicalGraphRepository` now captures only node identity provenance before canonical normalization and carries it as internal, non-persisted graph metadata.
- Canonical and explicit legacy reads build per-node exact, case-folded identity-token sets from raw IDs, external/symbol IDs, source refs/paths, Windows/POSIX/UNC/file-URI path components, basenames, and filename stems.
- Label approval compares exact normalized tokens only. It does not use substring matching. Safe code labels and safe route labels remain readable when they are not sourced from private identity fields.
- Duplicate node IDs merge identity-token sets monotonically, preventing artifact order from erasing an earlier private identity.
- Public IDs, edge endpoints, collision handling, bounded previews, metadata selection, and the closed semantic-kind policy are unchanged.

### TDD and verification

- Initial RED: the endpoint test exposed `WindowsSourceService`, the basename of a raw Windows `source.ref`.
- Duplicate-ID RED: a later duplicate node overwrote the first node's private token set and exposed `DuplicateExternalFunction` on the legacy path.
- Focused GREEN: canonical + legacy boundary test passed with 30 assertions.
- Fresh selected dashboard/repository/query/projection suite: 815 assertions, zero failures; 87 known missing-`.env` warnings.
- Pint `--test`: PASS for all three changed files.
- `git diff --check`: PASS; commit contains exactly the three scoped files.

## Direct Path and Namespace Alias Privacy Fix — 2026-07-13

- Remote commit: `95e77cfa` (`fix(dashboard): normalize private graph aliases`)
- Scope: `DashboardApiReader.php`, `CanonicalGraphRepository.php`, and `DashboardApiContractTest.php`
- Live PostgreSQL / Neo4j: not accessed or modified
- Pre-existing Pest result cache remained dirty and was excluded from the commit

### Root cause and implementation

- Direct top-level and `properties.path` values were absent from both the pre-normalization provenance capture and the legacy reader token map. The repository and reader now collect those fields alongside the existing identity fields.
- Path tokenization now always emits the normalized full value plus each path segment, basename, and filename stem, including bare filenames as well as POSIX, Windows-drive, UNC, and `file://` inputs.
- Private code identities receive a comparison-only namespace token where `\\`, `::`, and `.` are equivalent and case-folded. The public label is never rewritten.
- Dotted lowercase domains require no code-namespace alias treatment, arbitrary prose/routes fail the namespace grammar, and a different safe code name remains public.
- Direct `path` on a semantically valid route is explicitly retained as approved route presentation data; nested `source.path` is never exempted from privacy handling.

### TDD and final verification

- Initial RED: both new endpoint tests failed, exposing `PosixPrivateService` and `App.Private.OrderService`.
- Regression RED: the first implementation classified route `properties.path` as private provenance and hid `/users`, `/media`, and `/api/orders`; the route exception was then narrowed to approved direct route paths.
- Focused GREEN after final test strengthening: 2 tests, 90 assertions, zero failures across canonical and legacy endpoints.
- Fresh final Task 8/query/projection suite: 1301 assertions, zero failures; 108 known missing-`.env` warnings.
- Pint `--test`: PASS for all three changed files.
- `git diff --check HEAD^..HEAD` and `git show --check`: PASS.
- Remote status after commit: only `backend/vendor/pestphp/pest/.temp/test-results` remains dirty.
