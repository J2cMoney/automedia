#!/bin/bash
# Hook: UserPromptSubmit
# 检测修正信号，即时入队，不阻塞用户。消化由 evolution-runner 在 session 启动同步消化。
# 采集偏召回：宁可多抓，evolution-runner 在 session 启动同步消化用语义判断滤噪。
# hook 只抓措辞明显的；措辞平和、隐晦的修正由主 Agent 识别后补记一条（见 AGENTS.md）。

INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)
else
  PROMPT=$(printf '%s' "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("prompt") or "")' 2>/dev/null)
fi

if [ -z "$PROMPT" ]; then
  exit 0
fi

if echo "$PROMPT" | grep -qE "不是这样|不是这个意思|不应该|搞错|你错|又错|理解错|弄错|不合理|不通用|不对劲|这不对|完全不对|去掉|删掉|删除|改成|换成|改为|不需要|没必要|多余|你漏|漏掉|漏了|你忘|忘了|没提到|没有提到|你没提|少了|每次都|怎么又|怎么还|我说过|说过了|提醒过|强调过|不是让你|没复用|你没按|没生效|没有生效|没执行|不喜欢|不太喜欢|我的意思是|我是说|其实应该|应该是|应该写"; then
  QUEUE="$(git rev-parse --show-toplevel)/.codex/evolution/signals.jsonl"
  mkdir -p "$(dirname "$QUEUE")"
  if command -v jq >/dev/null 2>&1; then
    ESCAPED_PROMPT="$(printf '%s' "$PROMPT" | jq -Rs .)"
  else
    ESCAPED_PROMPT="$(printf '%s' "$PROMPT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read(), ensure_ascii=False))' 2>/dev/null)"
  fi
  printf '{"type":"correction","prompt":%s}\n' "$ESCAPED_PROMPT" >> "$QUEUE" 2>/dev/null
fi

exit 0
