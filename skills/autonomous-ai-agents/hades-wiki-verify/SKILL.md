---
name: hades-wiki-verify
description: Use when Hades wiki pages need evidence verification.
---

# Hades Wiki Verify Skill

Verify pending wiki revisions against the current project workspace and the artifacts Hades has accepted for that workspace. This procedure verifies existing Markdown; it does not rewrite pages, invent evidence, or treat search results as proof.

## When to Use

Use this skill to review Hades wiki pages whose current revision is `needs_verification`. Use `hades-wiki-push` instead when pages must first be created or refreshed.

## Prerequisites

- Run from the local workspace linked to the intended Hades backend project.
- Use the `terminal` tool to run the `hades` CLI and inspect local code. Use `read_file` and `search_files` when they are the safer way to inspect a complete file or locate symbols.
- Treat invocation as permission to verify only fully supported current revisions. Do not edit, draft, or delete wiki content.

## How to Run

Start by confirming the binding and synchronizing current workspace evidence:

```bash
hades backend status --json
hades backend sync
hades backend wiki list --status needs_verification --limit 20 --json
```

Stop before any verification if status is unconfigured, the workspace is unmapped, the project or binding is not the intended one, sync fails, or the reported workspace head does not match the source snapshot being reviewed.

## Quick Reference

| Purpose | Command or rule |
|---|---|
| List a queue page | `hades backend wiki list --status needs_verification --limit 20 --json` |
| Continue the queue | Add `--cursor NEXT_CURSOR` using the exact returned `next_cursor` |
| Read the current revision | `hades backend wiki show PAGE_ID --json` |
| Full-content gate | Continue only when `content_truncated` is explicitly `false` |
| Verify with compare-and-swap | `hades backend wiki verify PAGE_ID --expected-revision REVISION_ID --evidence-file EVIDENCE_FILE --json` |
| Safe evidence | Non-empty JSON array containing at most 80 current `artifact_ref` or `file_ref` objects |
| Unsafe result | Defer without a verify call, or record a conflict returned by the backend |

## Procedure

1. Initialize counters named `verified`, `deferred`, and `conflicted` to zero. Keep a short result entry for every examined page.

2. Read the first queue response from:

   ```bash
   hades backend wiki list --status needs_verification --limit 20 --json
   ```

   Save its ordered `items` and its exact `next_cursor`. Do not increase the limit above the CLI bound.

3. Process exactly one page and its current revision at a time. For each queued ID, fetch it again immediately before review:

   ```bash
   hades backend wiki show PAGE_ID --json
   ```

   If the page is missing, no longer `needs_verification`, or its shown current revision differs from the queued revision, do not reuse the old revision. Record the exact reason as deferred or conflicted, then continue.

   Check `wiki_page.content_truncated` before reading claims. Continue only when `content_truncated` is explicitly the boolean `false`. If it is missing or `true`, defer immediately and do not call `hades backend wiki verify`: part of the full content remains unseen, and the verification endpoint would copy that unseen full Markdown into a verified revision.

4. Read the whole `content_markdown`, not an excerpt, and enumerate every checkable claim before collecting evidence. Include claims in prose, headings, tables, examples, paths, routes, component names, dependencies, and operational instructions. Classify each claim as:

   - supported by current code;
   - contradicted by current code; or
   - not provable from current code.

   A page is atomic: verify it only if every material checkable claim is supported. If the page has zero code-verifiable material claims, or if any material claim is contradicted or cannot be proved, leave the page `needs_verification` and record the exact reason.

   Create a per-material-claim evidence ledger before building the backend evidence file. Its required mapping is:

   ```text
   claim -> evidence object(s) -> why the schema or file content proves the claim
   ```

   For each material claim, record the exact claim, classification, proposed `artifact_ref` or `file_ref`, the inspected schema entry or source location, and the reasoning that connects that content to the claim. One evidence object may support multiple ledger rows, but every material claim needs its own explicit mapping. Defer the whole page for any unmapped material claim.

5. Use local code first. Inspect complete relevant files and follow direct callers, dependencies, configuration, tests, or routes needed to validate the claim. Hades graph or memory results are discovery only: they may identify candidate paths or symbols, but they are never final proof.

6. Apply schema-specific proof rules. A current artifact or file hash establishes identity and freshness, not semantics; a generic current artifact hash is not blanket semantic proof. Inspect the referenced artifact content or full local file and use only the facts its schema or content actually represents:

   - A `file_ref`, resolved through `hades.git_tree.v1`, proves inventory, path, and hash at the synchronized head. The git-tree entry alone never proves behavior, control flow, or runtime behavior. To support a semantic claim, inspect the full current local file, cite the exact source location in the ledger, explain why that content proves the claim, and bind that inspected content to the synchronized tree with its exact hash.
   - A `hades.git_tree.v1` `artifact_ref` has the same inventory-only boundary. Do not use it as evidence for implementation behavior.
   - A `hades.symbols.v1` `artifact_ref` proves only symbol facts explicitly present in the inspected payload, such as existence, kind, qualified name, and recorded path or location. It does not prove callers, dependencies, routes, or runtime semantics unless those facts are explicitly represented by that schema and mapped in the ledger.
   - A `hades.code_graph.v1` or `hades.php_graph.v1` `artifact_ref` proves only inspected nodes, edges, and metadata actually encoded in that payload. A dependency or call claim requires the matching edge type, direction, and endpoints; graph presence still does not prove runtime behavior or an unencoded condition.
   - If the artifact payload, relevant schema entry, or full file content cannot be inspected, do not submit its hash. Defer the page instead.

   Use an `artifact_ref` only with an exact SHA-256 from the inspected current synchronized artifact. Include `schema` only when known from that same artifact. Use a `file_ref` only with a safe workspace-relative `path` and the exact current file SHA-256 represented by the latest synchronized git-tree artifact. Re-read or recompute the local file hash after the initial sync if a file may have changed. If the workspace changed, run `hades backend sync` again and re-evaluate the page before verifying.

   Never fabricate evidence, recycle a hash from an older revision, use evidence from another project or binding, or assume that a graph/memory hit proves a claim.

7. Derive `EVIDENCE_FILE` from the completed ledger. Include only evidence objects mapped to at least one material claim, and verify that every material claim has a supported ledger row before continuing. The JSON array must be non-empty, contain at most 80 objects, and use only the allowed verification fields `kind`, `schema`, `sha256`, `hash`, and `path`. Prefer the smallest mapped set that proves all claims. Example shape:

   ```json
   [
     {
       "kind": "file_ref",
       "path": "src/example.py",
       "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
     },
     {
       "kind": "artifact_ref",
       "schema": "hades.symbols.v1",
       "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
     }
   ]
   ```

   Each digest must be exactly 64 hexadecimal characters. `artifact_ref` requires `sha256`; `file_ref` requires `path` and either `sha256` or `hash`.

8. Use the revision ID returned by the fresh `show` response as the optimistic compare-and-swap value:

   ```bash
   hades backend wiki verify PAGE_ID \
     --expected-revision REVISION_ID \
     --evidence-file EVIDENCE_FILE \
     --json
   ```

   Increment `verified` only when the response confirms `verified_from_code`. For `revision_conflict`, increment `conflicted`; do not retry with a new revision until that revision has been read and reviewed from the beginning. For missing, stale, or conflicting evidence, including `evidence_invalid` and `evidence_stale`, increment `deferred` and leave the page `needs_verification`. Preserve the backend error code and exact reason.

9. Finish the saved items one by one. If the queue response had a non-empty `next_cursor`, request the next bounded page with the exact cursor:

   ```bash
   hades backend wiki list --status needs_verification --limit 20 --cursor NEXT_CURSOR --json
   ```

   Save that response's `next_cursor` and repeat. Continue until `next_cursor` is empty. Never stop after only the first page of results.

10. Synchronize once more and report totals:

    ```bash
    hades backend sync
    ```

    Report the project and workspace binding, reviewed page IDs and revision IDs, `verified/deferred/conflicted` totals, evidence paths or artifact schemas used, every exact deferral/conflict reason, pagination exhaustion, and final sync outcome.

## Pitfalls

- A plausible page is not a verified page; all material checkable claims need current code-derived proof.
- Bounded `show` output is not complete Markdown unless `content_truncated` is explicitly `false`.
- A search result, memory chunk, graph edge, filename, or stale artifact is a locator, not evidence.
- Artifact freshness and schema membership are not blanket semantic proof; connect inspected content to each claim in the ledger.
- A successful earlier `show` does not authorize verifying a later revision. Always pass the freshly reviewed current revision ID.
- Do not “fix” an unsupported page during verification. Defer it so a separate authoring workflow can revise the Markdown.
- Do not continue after a failed binding check or sync; subsequent hashes may belong to the wrong or stale workspace state.

## Verification

Before reporting completion, confirm that pagination reached an empty `next_cursor`; every attempted verification had `content_truncated: false`, at least one code-verifiable material claim, and a complete per-claim ledger; every successful page now reports `verified_from_code`; every skipped page remains `needs_verification`; no fabricated, cross-project, unmapped, or blanket-semantic evidence was submitted; and the final counters equal the number of examined pages.
