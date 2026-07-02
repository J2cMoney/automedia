#!/bin/bash
# Hook: PostToolUse（命令工具）— Strict Release 且明确允许时，commit 后自动 push
# Codex 的 matcher 只过滤 tool name，git commit 判断在脚本里
# 命令字段按 .tool_input.command 与 .command 双重兼容读取，适配不同 Codex 版本的 input 格式

INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // .command // empty' 2>/dev/null)
else
  CMD=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("command") or d.get("command") or "")' 2>/dev/null)
fi
echo "$CMD" | grep -qE "git commit" || exit 0

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

if [ "${ALLOW_AUTO_PUSH:-}" != "1" ] && [ ! -f "$ROOT/.codex/allow-auto-push" ]; then
  exit 0
fi

BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)

case "$BRANCH" in
  main|master)
    echo "⚠️ 当前在 $BRANCH 分支，已跳过自动 push。保护分支需手动 push 或走 PR。" >&2
    exit 0
    ;;
  "")
    exit 0
    ;;
esac

PUSH_OUT=$(git -C "$ROOT" push 2>&1)
if [ $? -ne 0 ]; then
  echo "❌ 自动 push 失败，请手动检查：" >&2
  echo "$PUSH_OUT" >&2
fi

exit 0
