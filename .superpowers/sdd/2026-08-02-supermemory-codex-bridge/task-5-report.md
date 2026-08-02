# Task 5 Report: Persistent Codex app-server orchestration

## Outcome

Implemented `CodexAppServer implements CodexRunner` around one lazily started, persistent `codex app-server --listen stdio://` process. Every inference creates a fresh ephemeral, read-only, no-approval thread with no capability roots or dynamic tools. The runner isolates concurrent turns, denies every server request, interrupts forbidden work, maps upstream failures to stable sanitized error kinds, restarts lazily after process death, and shuts down with `SIGTERM` followed by a five-second `SIGKILL` fallback.

The implementation uses injected process and RPC factories for deterministic tests. No live Codex process, account, API key, or network call is used in the suite.

## RED evidence

Initial focused command:

```text
$ cd services/supermemory-codex-bridge
$ npx tsx --test tests/codex-app-server.test.ts
Error [ERR_MODULE_NOT_FOUND]: Cannot find module '.../src/codex-app-server.js'
tests 1
pass 0
fail 1
```

The initial failure was the expected missing production module.

After the first GREEN pass, self-review introduced four additional regression cases and confirmed they failed for their intended reasons:

```text
$ npx tsx --test tests/codex-app-server.test.ts
tests 19
pass 15
fail 4
```

The failures proved that the tests caught:

- plural `Credentials` text not mapping to `authentication`;
- conflicting `threadId`/`turnId` metadata crossing concurrent outputs;
- a synchronous process-factory exception escaping the stable `unavailable` mapping;
- close during initialization sending `SIGTERM` twice.

## GREEN evidence

Focused orchestration suite after the fixes:

```text
$ npx tsx --test tests/codex-app-server.test.ts
tests 19
pass 19
fail 0
```

Fresh full bridge verification:

```text
$ npm test
tests 47
pass 47
fail 0
```

Fresh compile verification:

```text
$ npm run build
> tsc -p tsconfig.json
exit 0
```

## Coverage delivered

- exact process command, stdio pipes, dedicated `CODEX_HOME`, and API-key environment removal;
- serialized initialize/initialized handshake shared by concurrent callers;
- exact per-run `thread/start` and `turn/start` policy with a fresh ephemeral thread;
- latest completed `agentMessage` plus last-turn input/output token mapping;
- thread/turn keyed routing, including rejection of conflicting routing identifiers;
- immediate terminal failure before best-effort interrupt for every forbidden item type;
- exact JSON-RPC `-32601` denial for every server request and interruption for approvals;
- caller deadline abort, single interrupt, and `timeout` mapping;
- active-turn `unavailable` rejection on process death and exactly one lazy replacement startup;
- sanitized `authentication`, `rate_limit`, `structured_output`, `timeout`, `forbidden_tool`, `unavailable`, and `upstream` errors;
- graceful close, forced-kill fallback, startup-close race, and refusal of post-close runs;
- line-based stderr diagnostics that never retain or emit raw stderr content.

## Self-review

- Re-read every Task 5 lifecycle and policy case against the implementation and tests.
- Confirmed forbidden events become terminal before `turn/interrupt` is emitted, so late success cannot win.
- Confirmed startup state is shared until initialization completes; no caller can issue `thread/start` before `initialized`.
- Confirmed a dead process clears both client and startup state without clobbering a later replacement.
- Confirmed all caller-visible error messages are fixed strings and do not retain upstream secrets.
- Confirmed raw stderr is not stored even between newline-delimited chunks.
- Confirmed existing modified `baseline-home-report.md` and untracked `dist/` content were not included in Task 5 changes.

## Commits

- Implementation commit: `1605d6dbc feat: run isolated codex app server threads`.
- Report commit: `docs: report codex app server orchestration` (this report's containing commit; hash is included in the task handoff).

## Concerns

- Per the task constraint, protocol behavior is verified through an injected fake process/RPC peer rather than a live authenticated Codex app-server. The exact request/event shapes are contract-tested, but live credential and upstream availability remain deployment concerns.
