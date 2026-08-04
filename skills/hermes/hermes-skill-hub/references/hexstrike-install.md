# Worked example: installing Hexstrike (clawhub, community, DANGEROUS verdict)

User flow (Italian): "Verifica se è disponibile la skill/plugin hexstrike" → "Si installa" → blocked → user chose "Usa --force".

## Availability check

- Not installed: `skills_list` clean, no `*hexstrike*` in `~/.hermes/skills/` or `~/.hermes/plugins/`.
- Hub hit in `~/.hermes/skills/.hub/index-cache/hermes-index.json`:
  `{"name": "Hexstrike", "description": "Cybersecurity assistant for CTF...", "source": "clawhub", "identifier": "hexstrike", "trust_level": "community", "tags": ["ctf","hexstrike","pentest","recon","scanning","vulnerability"]}`
- `hermes skills search hexstrike` → 1 result, identifier `hexstrike`.

## Blocked scan (first install attempt)

```
Decision: BLOCKED — Blocked (community source + dangerous verdict, 7 findings).
  CRITICAL traversal      references/ctf-playbook.md:48   "**Directory Traversal**: `../../../etc/passwd`, `....//..../"
  HIGH     privilege_escalation SKILL.md:77   "- **Debian/Ubuntu**: `sudo apt install <package>`"
  HIGH     privilege_escalation SKILL.md:80   "- **Kali Linux**: Most tools pre-installed; `sudo apt instal"
  HIGH     traversal      references/ctf-playbook.md:48  (duplicate of CRITICAL)
  MEDIUM   obfuscation    references/ctf-playbook.md:208 "python3 -c \"import codecs; print(codecs.decode('<DATA>', 'ro..."
  MEDIUM   execution      references/ctf-playbook.md:56  "**Command Injection**: `; id`, `| id`, `$(id)`, `` `id` ``"
  MEDIUM   traversal      references/ctf-playbook.md:48  (duplicate)
```

Assessment given to user: every finding is educational payload text inside a CTF playbook (the skill TEACHES these attacks) — keyword-scanner false positive, no executable malware. User confirmed forcing.

## Retry pitfall (the key lesson)

`hermes skills install hexstrike --force` → `Error: Could not fetch 'hexstrike' from any source.`

Meanwhile `hermes skills inspect hexstrike` still fetched and previewed the SKILL.md fine. The blocked run left the fetch cache in a state where the immediate force-install couldn't re-fetch.

Fix: plain retry —
`hermes skills install hexstrike --force --yes` → success.

## Result

Installed files: `references/ctf-playbook.md`, `references/recon-methodology.md`, `references/tool-reference.md`, `scripts/tool-check.sh`, `skill-card.md`, `SKILL.md`, `_meta.json` → `~/.hermes/skills/hexstrike/`.
`hermes skills list | grep hexstrike` → `hexstrike | clawhub | community | enabled`. Quarantine dir empty afterwards.
