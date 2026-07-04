---
name: backend-knowledge-sync
description: "Sync project knowledge, wiki folders, and documentation corpora to a shared/backend memory system with exact chunking, manifests, and verification."
version: 1.0.0
author: Hades Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [backend, shared-memory, wiki, documentation, sync, chunking, verification]
    related_skills: [hades-coordination]
---

# Backend Knowledge Sync

Use this skill when the user asks to send, upload, learn, mirror, or update a
shared backend with project knowledge, documentation, wiki folders, logbooks, or
other large local knowledge corpora.

## Core rule

Do not over-summarize by default. If the user asks to update the backend or wiki
from a folder, preserve the material in chunks with provenance and verification.
Summaries are useful for orientation, but exact source content belongs in the
backend when the user says "without omitting anything", "tutto", "cartella e
sottocartelle", or similar.

If the user corrects you for being too synthetic, switch immediately to smaller,
richer chunks and record enough detail for reconstruction.

## Workflow

1. Identify the backend integration and its accepted write paths.
   - For Hades backend, check `hades backend status --json` and sync before and
     after work.
   - Backend shared memory proposals are acceptable when no dedicated wiki or
     document endpoint exists.
2. Recursively inventory the requested source path before writing anything.
   - Include every file unless the user explicitly excludes it.
   - For "without omissions", include dotfiles and binary files too.
   - Record relative path, byte count, encoding, and SHA-256 per file.
3. Choose representation.
   - UTF-8 text: store exact text chunks.
   - Binary or non-UTF-8: store base64 chunks and mark encoding as `base64`.
4. Create a manifest with batch ID, source root, file count, total bytes, chunk
   size, and per-file path, bytes, SHA-256, encoding, payload length, and chunk
   count.
5. Upload chunks conservatively.
   - Keep each backend write below known payload or summary limits.
   - If Hades memory proposals reject `summary > 4000`, use about 2500 content
     characters plus a compact JSON header.
   - Use stable markers such as `---BEGIN_CONTENT---` and `---END_CONTENT---`.
6. Sync until the final batch has no pending proposals.
   - If rate limited, wait and rerun sync; do not stop early.
   - If an initial attempt used too-large chunks, cancel only unsynced or pending
     proposals from that batch and start a new batch ID.
7. Verify reconstruction.
   - Reassemble accepted manifest and file chunks.
   - Decode according to encoding.
   - Compare every reconstructed file byte-for-byte and by SHA-256 against the
     source.
8. Report evidence concisely: batch ID, file count and total bytes, manifest and
   file chunk counts, backend sync result, final status counts, and
   reconstruction/hash verification result.
