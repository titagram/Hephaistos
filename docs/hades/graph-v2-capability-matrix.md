# Graph v2 capability matrix

This matrix describes native producer capability. It is not a compatibility
promise for removed graph-v1 artifacts or readers.

## Native Laravel/PHP effects

The required Tree-sitter parser produces source-free structural call facts.
Laravel effect extraction runs only after that parser succeeds; there is no
regex or raw-source fallback.

| Family | Native v2 status | Evidence and safety boundary |
| --- | --- | --- |
| DB/query reads and writes | Supported | Exact Laravel `DB` facade/query-builder calls and source-proven `app/Models` classes; table identity is a strict static identifier correlated to its own fluent receiver chain. |
| Cache reads and writes | Supported | The canonical `Illuminate\\Support\\Facades\\Cache` facade (or an exact import alias); only a static, non-secret, non-path key is public. |
| Storage reads and writes | Supported | The canonical `Illuminate\\Support\\Facades\\Storage` facade (or an exact import alias); only a static, relative, sanitised path is public. |
| Outbound HTTP | Supported | `Http` facade; endpoint is scheme/host/safe-path only, or the privacy-safe `http` boundary when dynamic or unsafe. |
| Mail and notifications | Supported | `Mail`/`Notification` send paths with a static mailable/notification class; recipients and payloads are never emitted. |
| Events | Supported | `event(new Type)` and `Event::dispatch` with an exact `app/Events` declaration; a dynamic target becomes an `ASYNC_TARGET` boundary uncertainty. |
| Jobs | Supported | `dispatch(new Job)`, source-proven `Job::dispatch`, and `Bus::dispatch` with an exact `app/Jobs` declaration; a dynamic target becomes an `ASYNC_TARGET` boundary uncertainty. |
| Queue dispatch | Supported | `Queue::push`, `later`, and `bulk` with an exact `app/Jobs` declaration; a dynamic target becomes an `ASYNC_TARGET` boundary uncertainty. |

Computed receiver, method, target, class, table, key, or unsafe resource does
not produce a verified generic resource. Receiver ownership is resolved through
exact imports and namespaces: local symbols, unrelated aliases, and unrelated
fully-qualified application classes are never collapsed to a Laravel facade or
model by basename. Each published effect is attached to its exact emitted
structural block; declaration-entry fallback is not used.

The producer records partial coverage and a typed unresolved boundary when a
recognised target is dynamic. Dynamic event/job/queue calls use
`ASYNC_TARGET` with `EMITS`/`DISPATCHES` and `ASYNC` flow. It never preserves
source bytes, query strings, credentials, tokens, absolute/home or traversal
paths, recipients, headers, or request/body payloads. Artifact validation
independently rejects private resource names should a producer regress.

## Deliberately not reconstructed in this change

The following historical families are intentionally not recreated as v1
compatibility behavior:

- logs;
- sessions, cookies, request input, and uploaded files;
- transactions;
- Eloquent metadata, traits, observers, scopes, accessors, and serialization;
- API-resource field metadata;
- Blade, Livewire, Alpine, and template metadata;
- test-to-symbol relations;
- aggregate `route_*` relations.

Each needs its own frozen Graph v2 semantics and a dedicated adapter. Treating
these as generic call strings would overstate certainty and weaken the
privacy/source-free contract.
