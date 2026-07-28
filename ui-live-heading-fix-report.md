# Evolution live heading fix

## Change

- The host page remains the sole owner of the `Evolution` page-level `h1`.
- `EvolutionShell` now renders its visual title as `<h2 className="evo-shell__title">`.
- The compiled bundle and stylesheet preserve the existing title presentation.
- The rendered accessibility regression now mounts a host `h1` with the plugin and asserts one page heading, no plugin `h1`, and an `h2` plugin title.

## Strict TDD evidence

- RED: `npm --workspace web test -- src/plugins/evolution-accessibility.test.tsx` failed with two `h1` elements before the implementation change.
- GREEN: the same focused command passed with 8 tests after the title was demoted.

## Fresh verification

- `npm --workspace web run check:evolution` — passed (TypeScript, Evolution bundle rebuild, and bundle syntax check).
- `npm --workspace web test` — passed (20 files, 90 tests).
- `npm --workspace web run build` — passed. Vite emitted its existing non-fatal chunk-size advisory.
- `git diff --check` — passed.

## Commit scope

Only Evolution shell source, its generated dashboard artifacts, the rendered accessibility test, and this report are intended for the fix commit. Pre-existing `.superpowers/sdd/task-8-report.md` and `task-7-report.md` changes are excluded.
