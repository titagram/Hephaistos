# Hades curated `hermes-lcm` review

Date: 2026-07-26

## Reviewed artifact

- Repository: `https://github.com/stephenschoettler/hermes-lcm.git`
- Release: `v0.20.0`
- Commit: `49e99a272d2d461e5c90732e7ef2bc20e96f0826`
- License: MIT

Hades installs this artifact as source under the active Hermes home. It is not
installed into the Python environment and its repository-provided install or
update scripts are not executed.

## Supply-chain controls

- The curated installer clones the named release and then compares the resolved
  commit to the immutable commit above.
- A tag move cannot change the accepted code because the commit comparison is
  required before publication.
- The staged tree and destination are rejected if they contain symlinks.
- The plugin manifest must identify the plugin as `hermes-lcm`.
- The clone's `.git` directory is removed before publication.
- Only targets bearing Hades' managed marker for the same repository may be
  updated. Unmanaged installations are preserved.
- Replacement is backup-first and restores the prior directory if publication
  fails.

## Runtime review

- Default operation is local: messages and DAG state are persisted in
  profile-scoped SQLite storage.
- Embeddings, reranking, proactive recall, extraction, and large-output
  externalization are disabled by default.
- Network embedding providers are reached only after explicit LCM provider
  configuration.
- The plugin's runtime Git subprocess path is dormant in a curated install
  because the `.git` directory is removed.
- Plugin tools use Hades' existing context-engine dispatch path. No new
  always-present core model tool is added.
- `context.engine: compressor` prevents the curated plugin module from loading,
  including its post-turn persistence hook. This is the supported immediate
  rollback.

## Privacy notes

LCM is intentionally lossless and therefore persists conversation content, as
the existing session store already does. Its optional named-pattern redaction is
disabled by the upstream default to preserve losslessness. Users should treat
the profile home and `lcm.db` as sensitive local data. External embeddings
remain off unless explicitly configured.

## Verification

- Hades installer, activation, idempotence, ownership, symlink, rollback, and
  fail-soft tests pass.
- A real pinned plugin checkout loaded through Hades, cloned safely for an
  `AIAgent`, bound to a session, and exposed all ten `lcm_*` tools.
- Switching a temporary profile to `compressor` prevented plugin loading;
  switching back to `lcm` restored the engine and tools.
- Upstream path, containment, ingest-protection, packaging, and host-capability
  tests produced 183 passes. Two upstream assertions compare macOS'
  `/var/...` spelling with the resolved `/private/var/...` spelling; the
  containment checks themselves accepted the same directory and no security
  boundary failed.

## Result

No reportable security regression was found in the reviewed pinned artifact or
the Hades integration. Updating to another upstream commit requires changing
the explicit commit pin and repeating this review.
