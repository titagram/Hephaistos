#!/usr/bin/env bash
set -u
SESSION=/home/ubuntu/hephaistos-graph-lifecycle-v2/.codex-peer/nextjs-repair-2-recovery-20260718T093153Z
PROMPT="$SESSION/prompt.md"
cd /home/ubuntu/.worktrees/hephaistos-nextjs-lifecycle-v2 || exit 97
/usr/local/bin/codex exec \
  --model gpt-5.6-terra \
  --config 'model_reasoning_effort="high"' \
  --dangerously-bypass-approvals-and-sandbox \
  --dangerously-bypass-hook-trust \
  --json \
  --output-last-message "$SESSION/last-message.md" \
  - < "$PROMPT" > "$SESSION/codex.jsonl" 2>&1
status=$?
printf '%s\n' "$status" > "$SESSION/exit-code"
exit "$status"
