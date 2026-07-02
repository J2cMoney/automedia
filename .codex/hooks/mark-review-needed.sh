#!/bin/bash
# PostToolUse hook: Strict Release 模式下，代码文件被编辑/创建后标记需要 review
# Fast Patch / Normal Feature 不做强制 review 门禁

INPUT=$(cat)
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

if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
else
  FILE_PATH=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("file_path") or "")' 2>/dev/null)
fi

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

case "$FILE_PATH" in
  [A-Za-z]:\\*|[A-Za-z]:/*)
    if command -v wslpath >/dev/null 2>&1; then
      FILE_PATH="$(wslpath -u "$FILE_PATH" 2>/dev/null || printf '%s' "$FILE_PATH")"
    elif command -v cygpath >/dev/null 2>&1; then
      FILE_PATH="$(cygpath -u "$FILE_PATH" 2>/dev/null || printf '%s' "$FILE_PATH")"
    fi
    ;;
esac

# 只管项目目录内的文件，/tmp 等外部路径不触发
case "$FILE_PATH" in
  "$ROOT"/*) ;;
  *) exit 0 ;;
esac

# 无扩展名的文件（脚本草稿、数据、内容稿等）不是项目代码，不触发
case "$(basename "$FILE_PATH")" in
  *.*) ;;
  *) exit 0 ;;
esac

# 排除框架元目录和非代码文件，其余才标记需要 review
case "$FILE_PATH" in
  */.claude/*|*/.codex/*|*/.agents/*|*.md|*.txt|*.json|*.yaml|*.yml|*.toml|*.lock|*.log|*.env|*.env.*|*.gitignore|*.prettierrc|*.eslintrc)
    ;;
  *)
    echo "needs_review" > "$ROOT/.codex/.needs-review"
    ;;
esac

exit 0
