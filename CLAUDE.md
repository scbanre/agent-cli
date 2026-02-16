# cliProxyAPI

多后端 AI API 聚合网关，语义分类 + 权重路由到不同实例。

```
客户端请求 → LB (8145) → 物理实例 (8146/8147) → 上游 API
```

## 核心文件

| 文件 | 说明 |
|------|------|
| `providers.toml` | 主配置：实例定义 + 路由规则 |
| `generate_config.py` | 配置生成器：TOML → YAML + LB + PM2 |
| `cld` | 客户端启动脚本 (FZF 选模型) |
| `scripts/usage_stats.py` | 路由用量统计工具 |
| `scripts/router_optimizer.py` | Auto 路由分析（类别命中分布 + 阈值优化） |
| `.env` | API Keys (不提交) |

## 常用命令

```bash
python3 generate_config.py          # 生成配置
pm2 start ecosystem.config.js       # 启动
pm2 restart all                     # 重启
pm2 logs                            # 查看日志
./cliproxy --antigravity-login      # OAuth 登录 (Google)
./cliproxy --codex-login            # OAuth 登录 (OpenAI)
```

## 变更流程

**添加/修改模型路由**: 编辑 `providers.toml` → `python3 generate_config.py` → `pm2 restart all`

**添加新后端**: 在 `[instances.xxx]` 新增实例 → 在 `[routing]` 引用 → 生成配置并重启

## 按需查阅

| 需求 | 文档 |
|------|------|
| providers.toml 完整语法 | `docs/agent/shared/providers-toml.md` |
| 端口分配 / 生成文件 | `docs/agent/shared/providers-toml.md` |
| cld tier 映射 | `docs/agent/shared/providers-toml.md` |

## 文档同步

修改 `CLAUDE.md` / `AGENTS.md` 的共享规则时，必须同步更新另一份。
仅 "专用" 标记的段落可以不同。
