---
name: supermemory-taxonomy
description: Use when storing or retrieving Supermemory memories with structured labels (project, topic, type) to prevent cross-contamination between unrelated knowledge domains.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [supermemory, memory, taxonomy, labels, organization]
    related_skills: [hermes-memory-providers]
---

# Supermemory Taxonomy — Structured Memory with Labels

## Overview

Supermemory stores all memories in a flat container. Without labels, a query about "debug Python" can return mixed results from cybersecurity CTF writeups, Hephaistos source fixes, and personal notes — creating confusion.

This skill defines a **taxonomy** (set of standard labels) and **conventions** to tag every memory at write time so retrieval stays clean and domain-specific.

**Key constraint:** `supermemory_search` does not expose a metadata filter. The workaround is to **embed taxonomy tags directly in the content text** — semantic search naturally clusters identically-tagged memories. Metadata in the `metadata` dict is still stored (useful for programmatic access if the API adds filtering later).

## When to Use

- Before storing any new memory — always tag it.
- When searching for memories in a specific domain — include the tag in the query.
- When cleaning up stale or misplaced memories.
- When the user asks to "organize" or "label" their Supermemory.

## Taxonomy

Always tag every stored memory with these three dimensions. Values are lowercase, hyphenated.

### `project` — Which project/context this belongs to

| Value | Scope |
|---|---|
| `hephaistos` | Hades Agent source code, issues, PRs, architecture decisions |
| `ctf` | Cybersecurity CTF challenges, pentesting, Hexstrike workflows |
| `infra` | Server setup, Docker, Traefik, rclone, Google Drive, backups |
| `personal` | Personal notes, preferences, non-technical facts |
| `skills` | Skill authoring, skill-hub, curator operations |
| `langchain` | LangChain/LangSmith platform docs knowledge base (project:langchain in supermemory) |

### `topic` — What area within the project

| Value | Scope |
|---|---|
| `debugging` | Bug fixes, root cause analysis, error traces |
| `config` | Configuration values, .env, config.yaml, setup |
| `workflow` | Step-by-step procedures, how-to, patterns |
| `api` | API endpoints, SDK quirks, integration notes |
| `architecture` | Design decisions, component structure, refactors |
| `security` | Vulnerabilities, patches, security review |
| `skill-authoring` | Writing, patching, validating skills |
| `tooling` | CLI tools, scripts, pip/uv, dev environment |

### `type` — What kind of knowledge

| Value | Purpose |
|---|---|
| `fact` | A discrete true statement (e.g. "calibre 7.26 is installed") |
| `procedure` | A how-to sequence (e.g. "to convert PDF to EPUB, run...") |
| `decision` | A choice made with rationale (e.g. "chose uv over pip because...") |
| `reference` | A pointer to external info (e.g. "docs at https://...") |
| `quirk` | A known gotcha or surprising behavior (e.g. "supermemory async ingestion takes 30-40s") |

## Storage Convention

Every memory **must** include taxonomy tags in the content text. Use this exact format:

```
[project:<value>] [topic:<value>] [type:<value>] <free-text memory content>
```

Example:

```
[project:hephaistos] [topic:config] [type:fact] Il provider memory attivo è supermemory, configurato in config.yaml con memory.provider=supermemory e base_url in supermemory.json.
```

Always pass the same tags as metadata too (for future API support):

```python
supermemory_store(
    content="[project:hephaistos] [topic:config] [type:fact] Il provider memory attivo è supermemory...",
    metadata={"project": "hephaistos", "topic": "config", "type": "fact"}
)
```

## Retrieval Patterns

### Pattern 1: Domain-specific recall

To search within a specific project or topic, **include the tag in the query**:

```
supermemory_search(query="[project:hephaistos] configurazione provider")
supermemory_search(query="[project:ctf] payload XSS")
supermemory_search(query="[topic:debugging] root cause")
```

Semantic search will naturally rank tag-matching memories higher.

### Pattern 2: Cross-domain discovery (use sparingly)

When you genuinely need everything:

```
supermemory_search(query="debug bash")
```

But **only do this when the user explicitly asks to search broadly**. By default, always scope the query with the relevant `[project:...]` tag.

### Pattern 3: Listing by project

Pre-filter mentally: search with `[project:X]` and review results, ignoring entries whose `memory` field is empty (those are still-ingesting documents — Supermemory quirk).

## Common Pitfalls

1. **Forgetting tags on store.** Without `[project:...]`, the memory joins the undifferentiated pool. Always tag — if you don't know the project/topic, ask the user.
2. **Using wrong project tag.** A Hephaistos memory tagged `[project:ctf]` will surface in CTF searches. Double-check the project before storing.
3. **Empty `memory` field in search results.** Supermemory ingests asynchronously (~30-40s). Hits with empty `memory` are document-level matches that haven't been AI-processed yet. Filter them out: only use results where `memory` is non-empty.
4. **Searching without the tag.** `supermemory_search(query="errore import")` returns everything. Always include `[project:X]` or `[topic:Y]` in the query unless the user asked for an unbounded search.
5. **Metadata-only tagging.** Don't rely on metadata alone — it's invisible to `supermemory_search`. Tags MUST be in the content text.
6. **Inconsistent tag spelling.** `hephaistos` not `hephastos` or `Hephaistos`. Stick to the taxonomy table exactly.
7. **Bulk store in a burst.** Supermemory v0.0.6 self-hosted goes OOM with rapid-fire stores (46 docs in 15s crashed the server). Store max 1 per 10-15s, max 3 in a row, then a long pause.

## Maintenance

### Audit: check for untagged memories

Search without tags and flag entries that need re-tagging:

```
supermemory_search(query="config", limit=10)
```

If you see memories without `[project:...]` in the content, they are orphans.

### Re-tagging a memory

You can't edit a memory in-place. The workflow is:
1. Read the old memory content.
2. `supermemory_forget(id=<old-id>)`
3. `supermemory_store(content="[project:X] [topic:Y] [type:Z] <old-content>", metadata={...})`

### Bulk organization

When reorganizing, list memories by project and confirm the user's taxonomy before starting. Don't re-tag without approval.

## Verification Checklist

- [ ] Every `supermemory_store` call includes `[project:...] [topic:...] [type:...]` in content
- [ ] Metadata dict is set with the same three fields
- [ ] Tag values match the taxonomy table exactly (lowercase, hyphens)
- [ ] Searches are scoped with `[project:X]` unless user asks for broad search
- [ ] Empty-`memory` results are filtered out before presenting to user
- [ ] New taxonomy values are added to this skill (not invented on the fly)
