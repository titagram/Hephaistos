# Task 4 Report: Reusable Delegation Evidence Packets

## Status

Implemented versioned, bounded, content-addressed delegation evidence packets
and integrated them into completed child results without retaining child
messages, reasoning, or transcripts.

## Changes

- Added frozen `EvidencePacket` records with canonical JSON serialization,
  SHA-256 hashing, strict validation, and staleness checks for contract, base
  commit, resulting Git state, covered files, dependency hashes, and
  verification inputs.
- Added mandatory secret and trajectory-field rejection. Conclusions,
  verification records, and residual risks have documented size/count bounds.
- Added read-only Git snapshots covering committed tree objects, tracked
  worktree changes, deletions, symlinks, and untracked files. Missing Git facts
  produce `evidence_error`; no commit or diff identity is invented.
- Bound each child packet to the concrete delegated role/goal contract or the
  validated orchestrator contract, including contract schema version in its
  canonical hash.
- Integrated optional structured child verification, dependency hashes, and
  residual risks. Tool trace status corroborates named verification tools, but
  conversation trajectories are never copied into the packet.
- Preserved reviewer role, orchestrator contract parsing, capacity preflight,
  result summary, and existing aggregation interfaces.

## TDD Evidence

### RED 1: Evidence packet module

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py -q`

Observed result: collection failed with
`ModuleNotFoundError: No module named 'tools.delegation_evidence'` (exit 2).

### GREEN 1: Packet primitives

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py -q`

Observed result: `10 passed in 0.18s` (exit 0).

### RED 2: Child result integration

Command:

`.venv/bin/python -m pytest tests/tools/test_delegate.py -q -k DelegationEvidenceIntegration`

Observed result: 2 failures because `tools.delegate_tool` did not expose or
invoke `capture_git_state` (exit 1).

### GREEN 2: Child result integration

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q -k 'delegation_evidence or DelegationEvidenceIntegration'`

Observed result: `18 passed, 148 deselected in 2.10s` (exit 0).

### RED 3: Verification-input invalidation

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py -q -k changed_verification_input`

Observed result: failed with `TypeError` because `evidence_is_stale()` did not
yet accept current verification inputs (exit 1).

### GREEN 3: Verification-input invalidation

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py -q`

Observed result: `11 passed in 0.17s` (exit 0).

## Final Verification

- Focused suite:
  `.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q`
  -> pre-commit `167 passed in 17.89s`; post-commit `167 passed in 16.70s`
  (exit 0).
- Ruff on all touched Python files -> `All checks passed!`.
- `py_compile` on both production modules and `git diff --check` -> exit 0.

## Self-review

- Packet serialization has an exact allow-listed field set and recursively
  rejects trajectory-shaped keys, including nested verification metadata.
- Packet construction uses `redact_sensitive_text(..., force=True)` as a
  detector and rejects changed content instead of storing a redacted value as
  if it were original evidence.
- Git state is captured before and after the exact child run. Packet hashes are
  conservative in parallel runs: concurrent sibling changes can invalidate a
  packet, but cannot make stale evidence appear current.
- Non-orchestrator packets hash the actual delegated goal and effective role;
  orchestrator packets hash the already validated contract dataclass. No
  contract/reviewer/capacity schema was widened or replaced.
- The existing child `messages` list remains transiently consumed only for the
  pre-existing bounded tool trace and never enters `EvidencePacket.to_dict()`.

## Concerns

Git snapshotting is read-only. The original implementation walked the complete
tracked tree twice per child; Review Fix 4 below replaces that with `HEAD` plus
dirty-path content identities.

## Review Fixes

### RED 4: Runtime verification identity, provenance, dirty snapshots, result refs

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q -k 'changed_result_ref or git_snapshot_hashes_only or same_tool_different or error_result_claim or parallel_workspace_delta'`

Observed result: five expected failures (exit 1): `evidence_is_stale()` did not
accept `result_ref`; clean snapshots still serialized every tracked path;
same-tool/different-command and failed-result claims lacked an explicit
`unverified` state; and a sibling workspace change was included in the child's
`covered_files`.

### GREEN 4: Runtime-bound verification and explicit file provenance

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q`

Observed result: `172 passed in 14.90s` (exit 0).

### Provenance decisions

- Verification records store a normalized argument/command SHA-256 identity,
  never the raw command. A record is `verified` only when tool name and argument
  hash match an actual runtime call and its result is paired by `tool_call_id`
  with a non-error result. Missing matches and error results are retained only
  as explicitly `unverified` claims with a reason.
- Git snapshots now bind the clean repository through `HEAD` and serialize only
  dirty paths: staged, unstaged, untracked, deleted, and symlink state.
- `observed_files` names the complete before/after workspace delta.
  `covered_files` is limited to delta paths backed by the current child's
  file-state write record. Every remaining delta path is named in
  `unattributed_files`; the packet never describes workspace-delta files as
  authored by the child.
- Staleness compares the packet's `result_ref` with the caller's current
  `result_ref`, including transitions between a commit ref and `None`.

### RED 5: Canonical terminal identity, split Git identities, exact provenance partition

Command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q -k 'staged_blob_change or provenance_partition or structured_terminal_args'`

Observed result: four expected failures and one pass (exit 1). A documented
terminal `command` claim did not match the equivalent runtime JSON
`{"cmd": "..."}` arguments; staged-only index blob changes disappeared when
the worktree still matched HEAD; and validation accepted overlapping or
incomplete file provenance partitions. The different-command structured case
remained unverified.

### GREEN 5: Re-review findings resolved

Focused command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q -k 'staged_blob_change or hashes_only_dirty or provenance_partition or structured_terminal_args or same_tool_different or error_result_claim'`

Observed result: `8 passed, 169 deselected in 2.02s` (exit 0).

Full evidence/delegation command:

`.venv/bin/python -m pytest tests/tools/test_delegation_evidence.py tests/tools/test_delegate.py -q`

Observed result: `177 passed in 22.44s` (exit 0).

Implementation notes:

- Terminal verification hashes one canonical argument mapping for both the
  documented `command` form and runtime structured arguments. Only the hash is
  retained; mismatched commands and error results remain explicitly
  unverified.
- Git snapshots remain proportional to dirty paths, but each dirty tracked
  path now hashes its index mode/blob separately from worktree mode/content,
  including symlinks, executable mode, untracked files, and deletion.
- Packet construction and validation now require `covered_files` and
  `unattributed_files` to be disjoint and to union exactly to
  `observed_files`.
