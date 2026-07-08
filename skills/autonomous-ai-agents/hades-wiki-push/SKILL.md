---
name: hades-wiki-push
description: Prepare project wiki updates for a Hades-linked backend. Use when the user asks the local Hades/Hermes agent to push, populate, generate, rebuild, refresh, or update a project wiki in the backend, either by deeply inspecting the current project autonomously or by using existing documentation at user-provided paths.
---

# Hades Wiki Push

## Overview

Use this skill to turn a local project into backend wiki content deliberately. Start by interviewing the user, then choose either autonomous deep project analysis or an existing-documentation import path.

## First Checks

1. Run `hades backend status --json`.
2. If the backend is configured, run `hades backend sync` before inspecting or preparing wiki material.
3. Confirm the current working directory is the project the user wants documented.
4. Do not upload secrets, API keys, private tokens, `.env` contents, raw credentials, or unrelated personal notes.

## Interview

Ask the user the minimum questions needed before doing heavy work:

1. Should I infer the project structure autonomously with a deep codebase review, or should I use existing documentation?
2. If documentation already exists, which path or paths should I use?
3. Should the wiki cover the whole project or only specific areas?
4. Should generated pages be written as drafts/needs-verification or as verified pages backed by code evidence?

If the user already answered these in the prompt, proceed without asking again.

## Mode A: Deep Autonomous Wiki

Use this when the user wants the agent to understand the project in depth and create the backend wiki.

1. Inspect project structure, manifests, framework entry points, config, tests, scripts, docs, and deployment files.
2. Identify the main workflows, modules, data model, integrations, operational risks, and how to run/test the project.
3. Build a bounded wiki outline before writing content. Prefer sections such as overview, architecture, data model, operations/runbook, agent workflows, risks, and verification.
4. Tie claims to evidence: file paths, commands, tests, artifacts, or code references.
5. Mark uncertain or inferred claims as `needs_verification`.
6. Create or update wiki content through the Hades backend path available in the installed client. Prefer explicit wiki commands if present; otherwise use backend sync/job capability for `populate_project_wiki`.

## Mode B: Existing Documentation

Use this when the user gives paths to existing docs.

1. Read only the provided paths and direct dependencies needed to understand them.
2. Preserve the user's structure when it is coherent; split very long docs into wiki pages with stable slugs.
3. Convert local-only links to backend-readable references when possible.
4. Keep provenance for each page: source path, source hash if cheaply available, and import timestamp.
5. Ask before replacing backend pages if the docs conflict with existing wiki content and the correct direction is ambiguous.

## Backend Push Contract

Prefer backend writes that are auditable and reversible:

- Use `source_type` such as `manual_documentation`, `agent_generated`, or the backend-supported closest equivalent.
- Use `source_status` conservatively: `developer_provided` for user docs, `needs_verification` for agent-generated summaries without strong evidence, and `verified_from_code` only when evidence refs exist.
- Include page title, slug, markdown body, evidence refs, and provenance.
- After pushing, run `hades backend sync` and report created/updated pages, pending proposals/jobs, and any backend errors.

## Handoff

End with:

- Chosen mode and sources inspected.
- Wiki pages created or queued.
- Backend project and workspace binding ids when available.
- Verification commands run.
- Any pages left as drafts or `needs_verification`.
