# Wiki verification deployment peer

- Goal: validate and deploy `codex/wiki-verification-workflow` on the backend server before main integration.
- Guardrails: no destructive database operations; preserve all data; never print live tokens; do not merge main until local review and remote acceptance are green.
- Local review: approved after Hades 334 tests, backend 146 tests, frontend 102 tests, static analysis and production build.
- Remote baseline: `/home/ubuntu/dev-sandbox` clean on `main` at `2bee2dd28228787d4ef475fe7973a37bc6d6551f`.
- Persistent peer started in `tmux` as `codex-peer-wiki-verification-20260715`; remote Codex reports `gpt-5.6-luna xhigh`.
- H/C/K handshake acknowledged. Phase 1 task: checkout and validate the feature branch without merging, pushing main, or restarting live services.
- Recovery: the peer initially interpreted the target as a second SSH hop. Nested self-SSH failed harmlessly; the turn was interrupted and corrected to operate directly in its current remote workspace.
