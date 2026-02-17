# cliProxyAPI Agent Guide

多后端 AI API 聚合网关（模型路由 + 多实例编排）。

```text
客户端请求 -> LB (8145) -> 物理实例 (8146/8147) -> 上游 API
```

## 1. 必须先看（高优先级规则）

- 配置入口只有 `providers.toml`（不要手改 `lb.js` / `instances/*.yaml` / `ecosystem.config.js`）。
- 修改配置后必须执行：`python3 generate_config.py`。
- 在本机部署形态下，重载请从外层目录执行：`/Volumes/ext/env/cliproxyapi/reload_proxy.sh`。
- 不要在 `source_code/` 子目录执行同名 `reload_proxy.sh`（会导致 auth-dir/key 路径偏移，触发 `500`）。

## 2. 高频操作

```bash
python3 generate_config.py
pm2 status
pm2 logs
./cliproxy --login        # Google/Gemini OAuth
./cliproxy --codex-login  # OpenAI OAuth
```

## 3. 配置变更最小流程

1. 改 `providers.toml`（路由或实例）。
2. 执行 `python3 generate_config.py`。
3. 从外层目录执行 `./reload_proxy.sh`。
4. 用 `pm2 status` + 一次 `/v1/messages` 请求做验收。

## 4. 深入文档（按需）

- 配置语法：`docs/agent/shared/providers-toml.md`
- 重载与认证排障：`docs/agent/shared/runbook-reload-and-auth.md`
- 开发记录：`docs/agent/shared/development-notes.md`

## 5. 文档同步规则

共享规则修改时，`AGENTS.md` 与 `CLAUDE.md` 必须同步更新（仅“专用”段落允许不同）。
