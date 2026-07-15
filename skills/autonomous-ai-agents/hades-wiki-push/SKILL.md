---
name: hades-wiki-push
description: Use when creating or refreshing a Hades project wiki.
---

# Hades Wiki Push

## Overview

Create a structural wiki baseline, human-authored narrative drafts, or both. Structural generation and narrative authoring are separate paths with separate verification states.

## Workflow

1. Confirm the current workspace binding, then sync:

   ```bash
   hades backend status --json
   hades backend sync
   ```

   Stop if the workspace is unmapped or points at the wrong backend project. Confirm whether a dirty worktree or `HEAD` is the intended source snapshot.

2. Choose one or both inputs:

   - **Structural baseline from code:** run `hades backend bootstrap-awareness --json`. Add `--yes` only when the user has authorized generated source-slice approvals. This path publishes deterministic, bounded pages from local tree and code-graph artifacts.
   - **Existing documentation:** select the user-provided docs and their direct dependencies, preserve coherent structure, and create bounded narrative draft JSON files.
   - **Both:** generate the structural baseline first, then add narrative drafts for concepts the structural pages do not explain well.

3. Create each narrative page with the real draft interface:

   ```bash
   hades backend wiki draft --from-file /path/to/page.json --json
   ```

   Each file must contain `slug`, `title`, `page_type`, `content_markdown`, and optional bounded `evidence_refs`. Allowed page types are `business`, `technical`, `runbook`, and `audit`. Use safe relative paths and current hashes in file evidence. Do not add `source_status`; the draft interface assigns narrative pages `needs_verification`.

4. Sync and collect every pending page ID:

   ```bash
   hades backend sync
   hades backend wiki list --status needs_verification --limit 50 --json
   ```

   Follow `next_cursor` until all pending pages are collected. Keep the page ID returned by every draft command even if it is not present on the first list page.

## Verification Boundary

Never mark agent-authored narrative as verified and never run `hades backend wiki verify` from this skill. Structural pages may be `verified_from_code` because Hades generates them from bounded artifacts; narrative drafts remain `needs_verification` until a separate review.

Hand off the pending page IDs, draft outputs, source paths, unresolved claims, and current workspace binding to `hades-wiki-verify`. If that skill is unavailable, report the pending IDs and stop rather than self-verifying.

## Final Report

Report the chosen input mode, structural bootstrap result, narrative pages drafted, all pending page IDs, sources used, backend binding, sync result, and the `hades-wiki-verify` handoff.
