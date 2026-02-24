#!/bin/bash
# deploy_cld.sh — 从 providers.toml 烘焙模型列表到 cld，部署到 zsh fpath
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLD_SRC="$SCRIPT_DIR/cld"
TOML_FILE="${1:-$SCRIPT_DIR/providers.toml}"
DEPLOY_DIR="$HOME/.zsh/functions"
DEPLOY_TARGET="$DEPLOY_DIR/cld"

if [[ ! -f "$CLD_SRC" ]]; then
  echo "错误: 找不到 cld 源文件: $CLD_SRC" >&2
  exit 1
fi

# 如果有 providers.toml，从中提取模型列表并烘焙到 cld
if [[ -f "$TOML_FILE" ]]; then
  models=$(awk '
    /^\[routing\][[:space:]]*$/ { in_routing = 1; next }
    /^\[[^]]+\][[:space:]]*$/ {
      if (in_routing) exit
    }
    in_routing && $0 ~ /^[[:space:]]*"/ {
      line = $0
      sub(/^[[:space:]]*"/, "", line)
      sub(/".*$/, "", line)
      print line
    }
  ' "$TOML_FILE")
  if [[ -n "$models" ]]; then
    # 用 python 做文本替换（跨平台可靠）
    python3 -c "
import sys
src = open('$CLD_SRC').read()
models = '''$models'''.strip().split('\n')
echo_lines = '\n'.join('  echo \"' + m + '\"' for m in models)
begin = '  # --- BEGIN MODELS ---'
end = '  # --- END MODELS ---'
i = src.index(begin) + len(begin)
j = src.index(end)
result = src[:i] + '\n' + echo_lines + '\n' + src[j:]
print(result, end='')
" > /tmp/cld_deploy_tmp
    count=$(echo "$models" | wc -l | tr -d ' ')
    echo "从 $TOML_FILE 烘焙了 $count 个模型"
  else
    echo "警告: providers.toml 中未找到路由模型，使用内置列表"
    cp "$CLD_SRC" /tmp/cld_deploy_tmp
  fi
else
  echo "未找到 ${TOML_FILE}，使用内置模型列表"
  cp "$CLD_SRC" /tmp/cld_deploy_tmp
fi

# 部署
mkdir -p "$DEPLOY_DIR"
if [[ -f "$DEPLOY_TARGET" ]]; then
  cp "$DEPLOY_TARGET" "$DEPLOY_TARGET.bak"
  echo "已备份 $DEPLOY_TARGET -> $DEPLOY_TARGET.bak"
fi

cp /tmp/cld_deploy_tmp "$DEPLOY_TARGET"
rm -f /tmp/cld_deploy_tmp
echo "已部署到 $DEPLOY_TARGET"
echo "新终端中执行 cld 即可使用"
