H(1,0,[],ROLE(side="local",mode="human-facing"),M(codec="ipc-ast-v1",session="nextjs-repair-2-recovery-20260718T093153Z"))
H(2,1,[],ROLE(side="remote",mode="worker-peer"),M(codec="ipc-ast-v1",session="nextjs-repair-2-recovery-20260718T093153Z"))
C(3,2,[],Q("contract","tdd_recovery","two_file_scope"),M(workspace="/home/ubuntu/.worktrees/hephaistos-nextjs-lifecycle-v2",autonomy="workspace_only",subagents=False))
K(4,3,[],V("max_len",4096,"max_depth",32,"max_nodes",512),M(dict=True,fallback="ipc-ast-v1"))
S(8,7,[],Q("checkpoint","red_invalid","production_edited_before_red"),M(action="restore_production_keep_test",commit=False))
A(9,8,[F("f1",524,565,"restore_head"),F("f2",453,500,"keep_regression")],Q("recover","observe_red","reimplement_verify"),M(want="valid_red_green_commit",scope="two_files",subagents=False))

You are a fresh recovery implementer. The previous worker was interrupted before commit because it changed test and production together; its immediate 21/21 run was not a valid RED.

Work only in `/home/ubuntu/.worktrees/hephaistos-nextjs-lifecycle-v2`. Required HEAD is `0b412baa45626d39b51f6243c9537ab78fa49725`. Read `.superpowers/sdd/task-13-rereview.md` and `.superpowers/sdd/task-13-repair-2-brief.md` completely.

Current expected state is exactly two modified files: the test contains the intended nested-function/arrow regression, while the production adapter contains an uncommitted attempted fix. Before any other production reasoning or edit, use `apply_patch` to remove the entire uncommitted production change and restore `hermes_cli/hades_index/lifecycle/frameworks/nextjs.py` byte-for-byte to HEAD. Do not revert the test. Prove:

```bash
git diff --quiet HEAD -- hermes_cli/hades_index/lifecycle/frameworks/nextjs.py
git diff --name-only HEAD
```

The first command must exit `0`; the second must list only the focused test. Then run the focused test file. It must fail for the expected nested-return role assertion. If it does not, stop without production edits and report why.

Only after that valid RED, implement a fresh minimal root-cause fix satisfying the brief, run the full GREEN/static/digest/reproducer gates, commit once with `fix(hades): exclude nested middleware returns`, and write `.superpowers/sdd/task-13-repair-2-report.md`. No delegation, subagents, scope expansion, integration, merge, or push.
