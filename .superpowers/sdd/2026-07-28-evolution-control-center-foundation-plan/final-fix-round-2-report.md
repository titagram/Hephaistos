# Foundation Final Fix Round 2

## Result

Corrected the Gnothi operator documentation to identify the canonical
cross-profile store as `<default Hermes root>/organism/gnothi_seauton/`.
The surrounding text now consistently describes one global organism shared
across profiles and never included in remote synchronization.

## Verification

- Confirmed the documented path against `hermes_constants.get_organism_home()`
  and `OrganismRevisionStore`.
- `git diff --check` passed.

## Files changed

- `docs/hades/backend.md`
