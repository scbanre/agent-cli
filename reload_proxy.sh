#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SHOW_LOGS=0
if [[ "${1:-}" == "--logs" ]]; then
  SHOW_LOGS=1
fi

PM2_CMD=()
if command -v pm2 >/dev/null 2>&1; then
  PM2_CMD=(pm2)
elif [[ -x "node_modules/.bin/pm2" ]]; then
  PM2_CMD=("$SCRIPT_DIR/node_modules/.bin/pm2")
elif command -v npx >/dev/null 2>&1; then
  PM2_CMD=(npx pm2)
else
  echo "错误: 未找到 pm2。请先安装依赖 npm install 或全局安装 pm2" >&2
  exit 1
fi

if [[ ! -f "providers.toml" ]]; then
  echo "错误: 未找到 providers.toml" >&2
  exit 1
fi

if [[ ! -f "generate_config.py" ]]; then
  echo "错误: 未找到 generate_config.py" >&2
  exit 1
fi

echo "[1/2] 生成配置..."
python3 generate_config.py

echo "[2/2] 重启服务..."
if ! "${PM2_CMD[@]}" restart all; then
  if [[ -f "ecosystem.config.js" ]]; then
    echo "未检测到可重启进程，尝试首次启动 ecosystem.config.js ..."
    "${PM2_CMD[@]}" start ecosystem.config.js
  else
    echo "错误: pm2 restart all 失败，且未找到 ecosystem.config.js" >&2
    exit 1
  fi
fi

echo "完成: 配置已更新并重启。"
"${PM2_CMD[@]}" status

if [[ "$SHOW_LOGS" -eq 1 ]]; then
  echo
  echo "进入日志查看 (Ctrl+C 退出)..."
  "${PM2_CMD[@]}" logs
fi
