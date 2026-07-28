# Task 5 fix round 3 report

## Change

`TelosStore` now captures the organism-root `(st_dev, st_ino)` without
creating any filesystem state.  Every `_TelosMutation` verifies the opened
root descriptor and the current root pathname against that pinned identity
before it can create Telos directories, write a temporary revision, or publish
a pointer.  A changed root fails with `telos_root_changed`.

A store constructed before its root exists has no mutation binding and fails
with `telos_root_unbound`.  Once the caller has initialized the root, it can
explicitly call `bind_mutation_root()`; that operation is descriptor-anchored
and cannot rebind an already-pinned store to a replacement root.

## TDD evidence

The root-replacement regression was added first and failed on the prior code:

```
scripts/run_tests.sh tests/hermes_cli/evolution/test_telos_contract_and_store.py -k ordinary_root_replacement --basetemp=/private/tmp/telos-round3-red-20260728
```

The failure was `Failed: DID NOT RAISE TelosStoreError`; the prior mutation
created Telos storage in the ordinary replacement directory.  The same test
passes after the change and asserts neither the replacement nor retained root
receives a Telos write.

## Verification

- `test_telos_contract_and_store.py`: 12 passed
- `test_telos_adversarial.py`: 14 passed
- `test_dashboard_confirmations.py`: 5 passed
- `test_telos_cli_approval.py`: 56 passed
- `test_dashboard_service.py`: 53 passed
- `python -m compileall -q hermes_cli/evolution/telos_store.py tests/hermes_cli/evolution/test_telos_contract_and_store.py`: passed
- `git diff --check`: passed

Each pytest invocation used a distinct `/private/tmp/telos-round3-*` base.
No push or internal review was performed.
