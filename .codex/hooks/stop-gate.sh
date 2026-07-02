#!/bin/bash
# Stop hook: Strict Release 模式下，代码文件被修改但未 review 时阻止停止
# Fast Patch / Normal Feature 不拦截结束

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"

if [ -z "$ROOT" ]; then
  exit 0
fi

MODE_FILE="$ROOT/.codex/workflow-mode"
MODE="fast"
if [ -f "$MODE_FILE" ]; then
  MODE="$(cat "$MODE_FILE" 2>/dev/null | tr -d '\000\357\273\277\376\377[:space:]' | tr '[:upper:]' '[:lower:]')"
fi

if [ "$MODE" != "strict" ]; then
  exit 0
fi

STATE_FILE="$ROOT/.codex/.needs-review"

if [ ! -f "$STATE_FILE" ]; then
  exit 0
fi

STATE=$(cat "$STATE_FILE" 2>/dev/null | tr -d '[:space:]')

case "$STATE" in
  "clean"|"")
    rm -f "$STATE_FILE"
    exit 0
    ;;
  *)
    echo '{"decision": "block", "reason": "代码已修改但未通过 code review。请派发 code-reviewer 两阶段审查，通过后写入 clean。用 /goal 自驱时，把 code-reviewer 通过写进 /goal 完成条件。"}'
    exit 0
    ;;
esac
