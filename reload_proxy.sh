#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SHOW_LOGS=0
if [[ "${1:-}" == "--logs" ]]; then
  SHOW_LOGS=1
fi

RELOAD_TIMEOUT_SECONDS="${RELOAD_TIMEOUT_SECONDS:-40}"

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

if ! command -v node >/dev/null 2>&1; then
  echo "错误: 未找到 node。无法解析 ecosystem.config.js" >&2
  exit 1
fi

run_with_timeout() {
  local timeout_seconds="$1"
  shift
  local -a cmd=("$@")

  "${cmd[@]}" &
  local cmd_pid=$!
  local start_ts
  start_ts="$(date +%s)"

  while kill -0 "$cmd_pid" >/dev/null 2>&1; do
    local now_ts
    now_ts="$(date +%s)"
    if (( now_ts - start_ts >= timeout_seconds )); then
      echo "警告: 命令执行超时 (${timeout_seconds}s): ${cmd[*]}" >&2
      kill "$cmd_pid" >/dev/null 2>&1 || true
      sleep 1
      kill -9 "$cmd_pid" >/dev/null 2>&1 || true
      wait "$cmd_pid" >/dev/null 2>&1 || true
      return 124
    fi
    sleep 1
  done

  wait "$cmd_pid"
}

echo "[1/2] 生成配置..."
python3 generate_config.py

echo "[2/2] 重启服务..."
if [[ ! -f "ecosystem.config.js" ]]; then
  echo "错误: 未找到 ecosystem.config.js" >&2
  exit 1
fi

TARGET_APPS=()
while IFS= read -r app_name; do
  [[ -n "$app_name" ]] && TARGET_APPS+=("$app_name")
done < <(
  node -e '
    const cfg = require(process.argv[1]);
    const apps = Array.isArray(cfg.apps) ? cfg.apps : [];
    for (const app of apps) {
      if (app && app.name) console.log(app.name);
    }
  ' "$SCRIPT_DIR/ecosystem.config.js"
)

if [[ "${#TARGET_APPS[@]}" -eq 0 ]]; then
  echo "错误: ecosystem.config.js 中未解析到任何应用名称" >&2
  exit 1
fi

APP_LIST_CSV="$(IFS=,; echo "${TARGET_APPS[*]}")"

do_reload() {
  run_with_timeout "$RELOAD_TIMEOUT_SECONDS" "${PM2_CMD[@]}" startOrRestart ecosystem.config.js --update-env
}

if ! do_reload; then
  echo "检测到 PM2 重载失败或超时，尝试自愈: pm2 kill + pm2 resurrect ..." >&2
  "${PM2_CMD[@]}" kill || true
  "${PM2_CMD[@]}" resurrect || true

  if ! do_reload; then
    echo "二次重载仍失败，尝试仅按当前 ecosystem 启动目标应用: ${APP_LIST_CSV}" >&2
    run_with_timeout "$RELOAD_TIMEOUT_SECONDS" "${PM2_CMD[@]}" start ecosystem.config.js --only "$APP_LIST_CSV" --update-env
  fi
fi

echo "完成: 配置已更新并按 ecosystem 应用列表重载。"
"${PM2_CMD[@]}" status

if [[ "$SHOW_LOGS" -eq 1 ]]; then
  echo
  echo "进入日志查看 (Ctrl+C 退出)..."
  "${PM2_CMD[@]}" logs
fi
