# providers.toml 配置说明

`providers.toml` 是本仓库的统一编排入口：它不替代 `cliproxyapi` 的核心代理能力，而是把多实例路由、LB 与 PM2 运行配置收敛到一份声明式配置中。

## 1. 全局配置

```toml
[global]
host = "0.0.0.0"
main_port = 8145        # LB 对外端口
proxy = "http://..."    # 可选代理
request_retry = 3
max_retry_interval = 30
nonstream_keepalive_interval = 5
streaming_keepalive_seconds = 15
streaming_bootstrap_retries = 1
quota_switch_project = true
quota_switch_preview_model = true
lb_auto_upgrade_enabled = true
lb_retry_auth_on_5xx = false
lb_auto_upgrade_messages_threshold = 80
lb_auto_upgrade_tools_threshold = 10
lb_auto_upgrade_failure_streak_threshold = 2
lb_auto_upgrade_signature_enabled = true
```

- `request_retry` / `max_retry_interval`: 实例默认重试策略（可被 `[instances.xxx]` 覆盖）
- `nonstream_keepalive_interval`: 非流式请求保活间隔（秒）
- `streaming_keepalive_seconds` / `streaming_bootstrap_retries`: 流式心跳与首包前安全重试
- `quota_switch_project` / `quota_switch_preview_model`: 配额超限时的自动切换策略
- `lb_retry_auth_on_5xx`: 是否允许认证类 5xx 在 LB 层跨后端重试（默认关闭，避免 `auth_unavailable` 循环）
- `lb_auto_upgrade_*`: LB 自动升档阈值开关（按请求复杂度与失败连击）

可选模型升档映射（常用于 `g3f.auto -> opus4.6`）：

```toml
[global.lb_auto_upgrade_map]
"g3f.auto" = "opus4.6"
```

## 2. 物理实例

```toml
[instances.official]
port = 8146
request_retry = 1
max_retry_interval = 5
providers = [
  { type = "antigravity", rotation_strategy = "round-robin" },
  { type = "codex", rotation_strategy = "round-robin" },
]

[instances.zenmux]
port = 8147
providers = [
  { type = "openai", base_url = "...", api_keys = ["${ZENMUX_KEY}"] },
]
```

- `request_retry` / `max_retry_interval` / `disable_cooling` / `routing_strategy` 支持实例级覆盖
- `streaming` 与 `quota_exceeded` 也支持实例级 table 覆盖（例如 `[instances.official.streaming]`）

## 3. 路由规则

```toml
[routing]
"opus4.5" = [
  { instance = "official", provider = "antigravity", model = "claude-opus-4-5-thinking", weight = 80 },
  { instance = "zenmux", provider = "anthropic", model = "anthropic/claude-opus-4.5", weight = 20 },
]
```

- `weight`: 权重越大，命中概率越高
- `provider`: 必须匹配实例中定义的 provider type，否则报 `unknown provider for model ...`
- `params`: 可选，按路由目标注入请求参数（由 `generate_config.py` 生成的 `lb.js` 在转发时应用）

## 4. 路由 params（可选）

常见可用参数：

- `reasoning_effort`: 例如 `low` / `medium` / `high`
- `thinking_budget_max`: 限制 `thinking.budget_tokens` 的上限，避免后端把过大预算映射到不支持的等级
- `max_tokens_max`: 限制 `max_tokens` 上限，降低超长输出导致的延迟与费用
- `max_tokens_default`: 客户端未传 `max_tokens` 时补默认值
- `anthropic_beta`: 追加到 `anthropic-beta` 请求头（会与客户端已有值去重合并）
- `extra_headers`: 注入额外请求头（键名不区分大小写；会忽略 `content-length` / `host`）

Gemini 3 Pro relay 推荐写法：

```toml
"gemini-pro" = [
  { instance = "official", provider = "antigravity", model = "gemini-3-pro-high", weight = 999 },
  { instance = "relay", provider = "openai", model = "google/gemini-3-pro-preview", weight = 1, params = { "reasoning_effort" = "high", "thinking_budget_max" = 24576 } },
]
```

自动升档别名示例：

```toml
"g3f.auto" = [
  { instance = "official", provider = "antigravity", model = "gemini-3-flash", weight = 999 },
  { instance = "relay", provider = "openai", model = "google/gemini-3-flash-preview", weight = 1 },
]
```

Claude 1M 上下文（可选，默认不加）示例：

```toml
"opus4.6" = [
  { instance = "official", provider = "antigravity", model = "claude-opus-4-6-thinking", weight = 80, params = { "anthropic_beta" = "context-1m-2025-08-07" } },
  { instance = "relay", provider = "anthropic", model = "anthropic/claude-opus-4.6", weight = 20, params = { "anthropic_beta" = "context-1m-2025-08-07" } },
]
```

## 5. 端口分配

| 端口 | 服务 |
|------|------|
| 8145 | 主入口 LB (lb.js) |
| 8146 | official 实例 (OAuth) |
| 8147 | zenmux 实例 (API Key) |

## 6. 生成文件 (gitignore)

| 文件 | 说明 |
|------|------|
| `instances/*.yaml` | 各物理实例的 cliproxy 配置 |
| `lb.js` | Node.js 负载均衡器 |
| `ecosystem.config.js` | PM2 进程配置 |
| `cliproxy` | 编译的二进制文件 |

## 7. cld 模型 Tier 映射

`cld` 脚本通过环境变量预设模型映射，Claude Code 的 Task 工具通过 `model` 参数选择 tier，实际请求经 cliproxyapi 路由到对应后端。

映射关系在 `cld` 脚本中维护，随时可能变更，以脚本实际配置为准。
