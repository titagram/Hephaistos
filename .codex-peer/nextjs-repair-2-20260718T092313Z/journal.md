# Next.js surgical repair 2

- Human authorization: one additional repair for `T13-NEXT-005-R1`.
- Root cause: `_returned_rules` does not exclude returns owned by nested callable scopes inside exported middleware.
- Base: `0b412baa45626d39b51f6243c9537ab78fa49725`.
- Scope: only Next.js adapter and focused test.
- Required method: RED first, minimal lexical ownership fix, fresh verification, independent scoped re-review.
- No further semantic repair is authorized.

## TDD recovery

- The first worker edited the regression and production in the same patch, so the first focused run was immediately green (`21/21`) and did not constitute a valid RED.
- The controller interrupted that worker before commit; HEAD remained `0b412baa45626d39b51f6243c9537ab78fa49725` with only the two expected files modified.
- Recovery requirement: restore only `nextjs.py` to HEAD while keeping the regression, observe the focused failure, then reimplement the production fix fresh and rerun all gates.
