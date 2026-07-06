---
name: hades-bug-diagnosis
description: "Diagnose project bugs with Hades backend awareness, bug evidence, graph artifacts, and bounded source slices before making precise root-cause claims."
version: 1.0.0
author: Hades Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hades, bug-diagnosis, root-cause, source-free, project-awareness]
    related_skills: [hades-coordination]
---

# Hades Bug Diagnosis

Use this skill when diagnosing a bug in a Hades-linked project, especially when
the agent may not have live filesystem access to the codebase. The backend is
the evidence source; do not infer precise causes from memory alone.

## Required Workflow

1. Check `hades_backend_project_awareness_status`.
2. If freshness is `stale`, `unknown`, or coverage is missing, state the exact
   missing coverage before any precise claim.
3. Search typed evidence with `hades_backend_bug_evidence_search` using the
   symptom, exception, route, test, frame, or deploy term.
4. Search project graph/artifacts with `hades_backend_graph_search`.
5. Fetch only the minimal relevant source with
   `hades_backend_source_slice_fetch` after evidence or graph results identify a
   concrete file, symbol, or line.
6. Compare evidence, graph, source slices, and freshness. If the evidence does
   not uniquely support a cause, say `insufficient evidence` and list the exact
   missing item.
7. Save the outcome with `hades_backend_diagnosis_report_create` when the
   workflow has enough evidence for a final report or when preserving an
   insufficient-evidence result would avoid redoing the investigation.

## Output Contract

Return a compact diagnosis with:

- `root_cause`: the precise cause, or `not determined`.
- `mechanism`: how the bug happens at runtime.
- `evidence_refs`: bug evidence ids, graph artifact ids, and source slice ids.
- `freshness`: current/stale/unknown status and commit comparison.
- `confidence`: high, medium, low, or insufficient.
- `next_verification`: the smallest test, log, or source slice needed next.

## Guardrails

- Do not claim exact line-level cause without a current source slice covering
  that line.
- Do not claim exact call path without a current graph artifact.
- Do not use raw source/wiki chunks from ordinary memory as automatic evidence.
- Do not ask for full repository access when a bounded source slice would answer
  the question.
