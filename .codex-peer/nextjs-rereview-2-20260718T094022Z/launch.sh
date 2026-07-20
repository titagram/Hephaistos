#!/usr/bin/env bash
set -u
SESSION=/home/ubuntu/hephaistos-graph-lifecycle-v2/.codex-peer/nextjs-rereview-2-20260718T094022Z
PROMPT="$SESSION/prompt.md"
cd /home/ubuntu/.worktrees/hephaistos-nextjs-lifecycle-v2 || exit 97
/usr/local/bin/codex exec resume \
  --model gpt-5.6-sol \
  --config 'model_reasoning_effort="xhigh"' \
  --dangerously-bypass-approvals-and-sandbox \
  --dangerously-bypass-hook-trust \
  --json \
  --output-last-message "$SESSION/last-message.md" \
  019f7462-3a97-72f1-9773-6d15e376c37c \
  - < "$PROMPT" > "$SESSION/codex.jsonl" 2>&1
status=$?
printf '%s\n' "$status" > "$SESSION/exit-code"
exit "$status"
