# cliProxyAPI Gateway

多后端 AI API 聚合网关（模型路由 + 多实例编排）。

## 架构

```text
客户端请求 -> LB(8145) -> 物理实例(8146/8147) -> 上游 API
```

## 30 秒上手

```bash
./setup.sh
# 编辑 .env 与 providers.toml
make start
```

常见后续操作：

```bash
./cliproxy --login        # Google/Gemini OAuth
./cliproxy --codex-login  # OpenAI OAuth
./reload_proxy.sh         # 生成配置 + 重载 proxy 进程
```

## 日常运维

```bash
python3 generate_config.py
./reload_proxy.sh
pm2 status
pm2 logs
```

## 关键边界（必读）

只在外层部署目录（例如 `/Volumes/ext/env/cliproxyapi`）执行重载脚本：

```bash
./reload_proxy.sh
```

不要在 `source_code/` 子目录执行同名脚本。详细原因、症状与恢复见：

- `docs/agent/shared/runbook-reload-and-auth.md`

## cld 快速用法

```bash
cld
cld cp
cld cp opus
cld official
```

`cld` 详细行为（场景映射、缓存、环境变量）按需查看：

- `cld` 脚本本体：`cld`
- 路由与模型配置：`providers.toml`

## 深入阅读（按需）

- 配置语法：`docs/agent/shared/providers-toml.md`
- 运维与排障：`docs/agent/shared/runbook-reload-and-auth.md`
- 开发记录：`docs/agent/shared/development-notes.md`

## License

MIT
