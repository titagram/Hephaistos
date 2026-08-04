---
name: hermes-skill-hub
description: Search, inspect, install, update, and audit skills from the Hermes skills hub (skills.sh, ClawHub, GitHub taps, direct URLs) — including the security-scan/quarantine flow, --force overrides for false-positive DANGEROUS verdicts on security/CTF skills, and the transient fetch-cache retry pitfall. Use whenever the user asks to check availability, install, or manage a hub skill.
---

# Hermes Skill Hub Management

Manage skills available via `hermes skills` (registries: skills.sh, ClawHub, GitHub taps, direct URLs). Complementary to the bundled `hermes-agent` skill (which covers general config) and `hermes-agent-skill-authoring` (which covers writing in-repo SKILL.md files — not installing).

## Checking availability (no install)

- `skills_list` tool → installed skills only.
- Installed files: `~/.hermes/skills/<name>/` (search_files target='files').
- Plugins: `~/.hermes/plugins/` (separate from skills).
- Hub availability (installable, not installed): grep the hub index cache — `grep -i "<name>" ~/.hermes/skills/.hub/index-cache/hermes-index.json`. Entries carry `identifier`, `source` (e.g. clawhub), `trust_level` (official/community), `tags`.
- CLI search: `hermes skills search <term>` — canonical; shows Name/Description/Source/Trust/Identifier table.

## Install flow

1. `hermes skills inspect <identifier>` — preview SKILL.md WITHOUT installing (works even when the install fetch is broken).
2. `hermes skills install <identifier>` — fetches → quarantines to `~/.hermes/skills/.hub/quarantine/<name>` → runs security scan.
3. On block, see below. On success: files land in `~/.hermes/skills/<name>/`; verify with `hermes skills list | grep <name>` (status shows `enabled`).

Identifiers: bare names (`hexstrike`) resolve via registries; `owner/repo/path` form and direct HTTP(S) URLs to a SKILL.md also work. `--name` overrides frontmatter name when installing from URL.

## Security scanner — false positives

Community-source skills that document attack techniques (CTF/security/pen-test skills) reliably trip the keyword scanner. Typical findings, all doc-text, not code:

| Finding | Trigger text (in docs) |
|---|---|
| CRITICAL/HIGH traversal | `../../../etc/passwd`, `....//` |
| HIGH privilege_escalation | `sudo apt install <package>` |
| MEDIUM execution | `; id`, `\| id`, `$(id)` (command-injection examples) |
| MEDIUM obfuscation | `codecs.decode(...)` |

Verdict printed: `BLOCKED — Blocked (community source + dangerous verdict, N findings). Use --force to override.`

**Workflow for blocked installs (security-gated decision):** always read WHICH file/line each finding points to before judging severity — a CRITICAL verdict on a `references/*.md` doc line is not a critical code issue. Present the findings table to the user with your assessment (false positive vs real risk) and let THE USER decide on `--force` — never silently bypass the gate. `hermes skills install <id> --force` installs anyway; add `--yes` to skip the confirmation prompt.

## Pitfalls

1. **Transient fetch failure after a blocked attempt**: the first blocked install can poison the fetch state — the immediate retry of `install --force` fails with `Error: Could not fetch '<id>' from any source.` while `inspect <id>` still works fine. Fix: retry `install --force --yes` — it succeeds on the second attempt. Don't debug the resolver; just retry.
2. **Quarantine cleanup**: `.hub/quarantine/` is emptied after a successful install — don't rely on it afterwards for content inspection.
3. **New skills load only in new sessions** (skill catalog is built at session start). To use immediately in the current session: `hermes chat --skills <name>`.

## Reference

- `references/hexstrike-install.md` — worked example: availability check, blocked-scan transcript, forced install, retry recovery.
