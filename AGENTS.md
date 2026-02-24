# AGENTS.md

`cliproxyapi/source_code/` 是网关代码仓（配置生成、路由策略、统计脚本）。

## L0 快速规则

- 不直接修改生成产物（`lb.js`、`instances/*.yaml`、`ecosystem.config.js`）。
- 配置源始终是上层 `../providers.toml`。
- 本地部署重载从上层执行 `../reload_proxy.sh`。

## 高频命令

```bash
python3 ../generate_config.py
../reload_proxy.sh
pm2 status
pm2 logs
```

## 典型流程

1. 修改 `../providers.toml`
2. 运行 `python3 ../generate_config.py`
3. 运行 `../reload_proxy.sh`
4. 用 `pm2 status` 与一次接口请求做验收

## 深入文档

- `docs/agent/shared/providers-toml.md`
- `docs/agent/shared/runbook-reload-and-auth.md`
- `docs/agent/shared/development-notes.md`

## 备注

本目录遵循根仓治理：`AGENTS.md` 单一真源，`CLAUDE.md/GEMINI.md` 使用软链接。
