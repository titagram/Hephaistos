# Task 6 fix2 — regression coverage

Status: complete

Commit: `26f0fd46c80ee78a1d361026608ef2903e721ee0`

Coverage added:

- StrictMode deep-link effect replay completes and does not remain loading.
- Same-project repository-to-workspace back/forward change updates the select and sends every new symbol request with exact workspace scope fields.
- Path response A is ignored after the target changes to B; old returned/truncation state is cleared.
- `queryGraph` and URL update callback identities remain stable across same-project page rerenders.
- Request invariants are recomputed after the path call: every data request, including path, has exact scope; non-search requests have no cursor; none contains credential/plugin/internal fields; impact remains cursor-free.
- Existing parallel inbound/outbound neighborhood assertion remains active.

Final gate: 6 suites, 55 tests passed; TypeScript and production build passed.
