# Graph v2 capability matrix

This matrix describes native producer capability. It is not a compatibility
promise for removed graph-v1 artifacts or readers.

## Native Laravel/PHP effects

The required Tree-sitter parser produces source-free structural call facts.
Laravel effect extraction runs only after that parser succeeds; there is no
regex or raw-source fallback.

| Family | Native v2 status | Evidence and safety boundary |
| --- | --- | --- |
| DB/query reads and writes | Supported | Laravel `DB` facade/query-builder calls and source-proven `app/Models` classes; exact static table/model only. |
| Cache reads and writes | Supported | `Cache` facade, including imported aliases; only a bounded static key is public. |
| Storage reads and writes | Supported | `Storage` facade, including aliases/chains; only a bounded static path is public. |
| Outbound HTTP | Supported | `Http` facade; endpoint is scheme/host/safe-path only, or the privacy-safe `http` boundary when dynamic or unsafe. |
| Mail and notifications | Supported | `Mail`/`Notification` send paths with a static mailable/notification class; recipients and payloads are never emitted. |
| Events | Supported | `event(new Type)` and `Event::dispatch` when the event class is present in `app/Events`. |
| Jobs | Supported | `dispatch(new Job)`, source-proven `Job::dispatch`, and `Bus::dispatch` when the job class is present in `app/Jobs`. |
| Queue dispatch | Supported | `Queue::push`, `later`, and `bulk` with a source-proven job class. |

Computed receiver, method, target, class, table, key, or unsafe resource does
not produce a verified generic resource. The producer records partial coverage
and, where the frozen IR permits it, a typed unresolved external target. It
never preserves source bytes, query strings, credentials, recipients, headers,
or request/body payloads.

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
