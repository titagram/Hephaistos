---
name: hierarchical-development
description: "Orchestrate multi-agent work with layered plan-execute-review."
version: 1.0.0
author: Hades Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [subagents, delegation, hierarchical, planning, orchestration, workflow]
    related_skills: [plan, hades-coordination, requesting-code-review, test-driven-development]
    config:
      delegation.max_concurrent_children:
        description: "Max parallel subagents (default 3)"
      delegation.max_spawn_depth:
        description: "Max nesting depth (default 2)"
      delegation.subagent_auto_approve:
        description: "Auto-approve dangerous commands in subagents (default false)"
      delegation.child_timeout_seconds:
        description: "Per-child timeout (default 600)"
---

# Hierarchical Development

Decompose complex features into layered, independently-executable work units
and dispatch them to focused subagents. Each level of the hierarchy handles
a narrower concern: plan → orchestrate → implement → review → integrate.

This is the execution counterpart to the `plan` skill: where `plan` produces
the blueprint, `hierarchical-development` runs it.

## When to Use

- Feature spans multiple files, layers, or concerns (frontend + backend + infra).
- Different parts benefit from different model strengths (planner on reasoning,
  implementer on coding, reviewer on correctness).
- Work can be parallelised (independent modules, separate concerns).
- Task has a natural hierarchy: parent decomposes, children execute.

**Do NOT use for:** single-file changes, trivial bug fixes, linear one-agent
tasks. Use `delegate_task` directly for a one-off subagent instead.

## Prerequisites

- `delegate_task` tool available (routed via `toolsets=['terminal', 'file', ...]`).
- `delegation.*` config knobs tuned in `config.yaml`:
  ```yaml
  delegation:
    max_concurrent_children: 3    # parallel subagents
    max_spawn_depth: 2             # planner → implementer → (no deeper)
    child_timeout_seconds: 600
    subagent_auto_approve: false   # safe default; true for cron/batch
    orchestrator_enabled: true
  ```
- (Optional) Model profiles in `config.yaml` for specialised subagent roles:
  ```yaml
  hades:
    planner:       {provider: openrouter, model: anthropic/claude-opus}
    implementer:   {provider: openrouter, model: deepseek/deepseek-chat}
    reviewer:      {provider: openrouter, model: openai/gpt-4o}
  ```

## How to Run

There is no single command. The workflow is a pattern you execute:

1. **Plan** → produce a decomposition plan (use `plan` skill, write a `.hermes/plans/` file).
2. **Orchestrate** → dispatch the plan's tasks via `delegate_task`, one subagent per task.
3. **Review** → verify each subagent's summary, check artifacts exist at claimed paths.
4. **Integrate** → merge, run the full test suite, commit.

## Quick Reference

| Step | Tool / Pattern | What happens |
|------|---------------|--------------|
| Decompose | `plan` skill or inline analysis | Task list with deps, files, test targets |
| Dispatch single | `delegate_task(goal=..., context=...)` | One subagent runs, result re-enters conversation |
| Dispatch parallel | `delegate_task(tasks=[...])` | Up to N subagents run concurrently |
| Verify | `read_file`, `terminal('pytest ...')` | Check output, test results, file presence |
| Chain | Orchestrator subagent → leaf subagents | Deep nesting when max_spawn_depth > 1 |

## Procedure

### Phase 1: Decompose

Read the feature request and break it into **independent work units**.
Each unit should be:

- **Small** — one file or one concern (model, view, test, migration, config).
- **Testable** — has clear pass/fail criteria.
- **Parallelisable** — no hard dependency on another unit's output.

Document the decomposition as a list with:
- Goal for each unit
- Files it touches
- Test commands to verify it

```markdown
Task A: Create User model + migration
  Files: app/Models/User.php, database/migrations/...
  Verify: php artisan migrate --pretend, php artisan test --filter=User

Task B: Add registration endpoint
  Depends: Task A
  Files: app/Http/Controllers/AuthController.php, routes/api.php
  Verify: curl -X POST /api/register, php artisan test --filter=Registration

Task C: Write frontend registration form
  Depends: Task B
  Files: resources/js/Pages/Register.tsx
  Verify: npm run build, browser test
```

### Phase 2: Orchestrate

For each independent unit, call `delegate_task`. Pass ALL context the
subagent needs — they start with zero conversation history:

```text
delegate_task(
    goal="Implement Task A: User model with email + password_hash",
    context='''
    Project: Laravel 11 at /var/www/project
    Convention: use HasFactory trait, guarded $fillable, casts for password
    Relevant files:
      - app/Models/User.php (exists, needs updating)
      - database/migrations/xxxx_create_users_table.php (exists, needs updating)
    Existing tests: tests/Unit/UserTest.php
    Verify with: php artisan test --filter=User
    ''',
    toolsets=['terminal', 'file']
)
```

**Batch independent tasks** (no dependency between them) in a single call:

```text
delegate_task(tasks=[
    {goal: "Task A", context: "..."},
    {goal: "Task C", context: "..."},
])
```

Tasks with dependencies chain naturally: dispatch the dependency first,
wait for its result, then dispatch dependents with the dependency's
output in context.

### Phase 3: Verify

Subagents return **self-reported summaries** — always verify:

```text
# Check the file exists
read_file("app/Models/User.php")

# Run the test
terminal("php artisan test --filter=User")
```

**Checklist:**
- [ ] File created at the expected path
- [ ] Tests pass (not just "said they pass")
- [ ] No orphaned imports or dead code
- [ ] Styling matches the project (linter clean)

When verification fails, dispatch a focused `delegate_task` with the
exact failure output to fix it, rather than re-running the whole task.

### Phase 4: Integrate

- Run the **full project test suite** (not just per-task tests).
- Run linters, formatters, type-checkers.
- Commit per-task or squash at the end — match the project's convention.

```text
git add -A
git commit -m "feat: implement registration (User model + endpoint + frontend)"
```

## Pitfalls

### Subagent summaries are NOT verified facts
A subagent that says "file written" or "test passed" may be wrong. **Always
verify by reading the file and running the test yourself.** This is the #1
source of bugs in hierarchical workflows.

### Too-wide tasks defeat the purpose
If a single `delegate_task` takes more than 30 iterations or touches 5+ files,
it's too broad. Split it further. A good task is one the subagent can complete
in 3-10 tool calls.

### Nesting depth = debugging cost
Each level of hierarchy makes it harder to trace failures. Keep max_spawn_depth
at 1-2 for most projects. Only increase when you have stable, well-tested
leaf tasks that rarely fail.

### Context starvation
Subagents have zero history. If you omit context they need (file paths,
conventions, error messages from prior failures), they'll guess wrong.
**Over-communicate in the `context` field.**

### Parallel subagents share nothing
Subagents running in parallel cannot see each other's files or results.
If two tasks both modify the same file, they WILL conflict. Plan for this:
serialise file writes, keep parallel tasks on disjoint file sets.

### Model mismatch
A cheap/fast model used for implementation may produce low-quality code.
Profile your subagent roles — use a strong model for the final reviewer
even if implementers use cheaper ones.

## Verification

After completing a hierarchical workflow:

1. Run the full test suite: `scripts/run_tests.sh` or equivalent
2. Run linters: `npm run lint`, `ruff check .`, etc.
3. Run type checker: `mypy .`, `tsc --noEmit`, etc.
4. Spot-check 2-3 files the subagents created — do they follow the project's patterns?
5. Verify edge cases the plan may have missed (empty states, auth, error boundaries)
