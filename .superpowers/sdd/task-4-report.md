# Task 4 report — Source-scoped graph repository

## Status

Complete on remote branch `feature/canonical-graph-foundation-20260712`.

## Commit

`b002f657 feat(graph): resolve source-scoped graph artifacts`

The commit contains only `backend/app/Services/Graph/CanonicalGraphRepository.php` and `backend/tests/Feature/CanonicalGraphRepositoryTest.php`. The pre-existing `backend/vendor/pestphp/pest/.temp/test-results` modification remains unstaged and untouched by the commit.

## Implementation

- Added strict source-scoped latest, exact-identity, and scope-listing repository APIs.
- Enforced linked-binding/project and repository/project ownership checks with no cross-scope fallback.
- Adapted only trusted selected legacy payloads in memory and exposed canonical identity without display paths.
- Did not touch runtime database state, migrations, stored payloads, or Neo4j.

## Verification

- RED observed: 3 expected failures because the repository class did not exist.
- Focused GREEN: 3 tests, 17 assertions.
- Pint completed for both Task 4 files.
- Fresh requested regression run: 644 assertions, 0 failures across the repository, Hades privacy, and project lifecycle suites.
- Commit scope and whitespace checks passed.

## Concern

The container reports its existing missing `/workspace/backend/.env` warning for every test (57 warnings in the full run). There were no test failures; no environment file was created because runtime/environment changes were out of scope.

## Review follow-up — exact identity isolation

Commit: `3f740587 test(graph): enforce source identity isolation`

Files committed:

- `backend/tests/Feature/CanonicalGraphRepositoryTest.php`

Added table-driven negative coverage for wrong projects, wrong binding/repository scope IDs, unlinked bindings, and mismatched snapshot artifact ownership. Added explicit no-fallback coverage in both directions and recursive privacy checks for both `latestForScope()` and `findByIdentity()` across both source types.

RED was verified through an uncommitted mutation that removed the Hades project constraints, since the reviewed production implementation was already correct:

```bash
APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= \
  php artisan test tests/Feature/CanonicalGraphRepositoryTest.php
```

Result with mutation: 1 failed, 5 warnings, 30 assertions. The exact-identity isolation test caught the wrong-project graph leak. The committed production source was then restored byte-for-byte.

Focused GREEN after restoration and Pint:

```bash
APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= \
  php artisan test tests/Feature/CanonicalGraphRepositoryTest.php
```

Result: 0 failures, 6 warnings, 35 assertions.

Full requested regression command:

```bash
APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test \
  tests/Feature/CanonicalGraphRepositoryTest.php \
  tests/Feature/Hades/HadesM3SharedMemoryTest.php \
  tests/Feature/Dashboard/ProjectLifecycleDashboardApiTest.php
```

Result: 0 failures, 60 warnings, 662 assertions. The warnings remain exclusively the known missing `/workspace/backend/.env` warning. No production file changed; the pre-existing vendor Pest temp result remains unstaged.
