# providers.toml 配置说明

`providers.toml` 是本仓库的统一编排入口：实例定义、聚合路由、LB 规则均从这里生成到 `instances/*.yaml`、`lb.js`、`ecosystem.config.js`。

> 基线日期：2026-02-23
> 真值来源：`/Volumes/ext/env/cliproxyapi/providers.toml`

## 1. 全局配置（当前默认）

```toml
[global]
host = "0.0.0.0"
main_port = 8145
proxy = "${CLIPROXY_PROXY}"
request_retry = 3
max_retry_interval = 30
nonstream_keepalive_interval = 5
streaming_keepalive_seconds = 15
streaming_bootstrap_retries = 1
quota_switch_project = true
quota_switch_preview_model = true
lb_auth_cooldown_ms = 1800000
lb_validation_cooldown_ms = 43200000
lb_quota_cooldown_ms = 43200000
lb_max_target_retries = 1
lb_auto_upgrade_enabled = false
```

- `request_retry` / `max_retry_interval`: 默认重试策略，可被实例覆盖
- `nonstream_keepalive_interval` / `streaming_keepalive_seconds`: 保活与心跳
- `lb_*_cooldown_ms`: LB 侧冷却窗口
- `lb_auto_upgrade_enabled = false`: 当前默认关闭自动升档

## 2. 历史兼容项（非默认）

以下配置在部分历史分支/实验配置中出现，当前生产基线不启用：

- `lb_auto_upgrade_messages_threshold`
- `lb_auto_upgrade_tools_threshold`
- `lb_auto_upgrade_failure_streak_threshold`
- `lb_auto_upgrade_signature_enabled`
- `[global.lb_auto_upgrade_map]`（例如 `"g3f.auto" = "opus4.6"`）

使用这些项时请在文档中明确标注“实验/兼容”，避免误解为现网默认。

## 3. model_router（当前默认开启）

```toml
[global.lb_model_router]
enabled = true
shadow_only = false
activation_models = ["auto", "auto-canary"]
default_model = "M2.5"
log_factors = true
```

### 执行顺序

`categories`（语义分类）→ `rules`（阈值 fallback）→ `default_model`

- categories 按 `priority` 降序，首个命中即停止
- category 的 `signals` 为 OR 关系
- rules 仅在 category 未命中时执行

### categories（当前生效）

| category | priority | target_model | signals（节选） |
|----------|----------|--------------|-----------------|
| `architecture` | 500 | `opus4.6` | `task_category:architecture`, `system_prompt_type:plan_mode` |
| `code-review` | 400 | `gpt5.3` | `task_category:code-review`, `system_prompt_type:review` |
| `visual-coding` | 350 | `g3p` | `task_category:visual-coding`, `keyword:frontend\|UI\|CSS` |
| `coding` | 300 | `sonnet4.6` | `task_category:coding`, `has_code_context:true`, `tool_profile:coding` |
| `explore` | 200 | `M2.5` | `task_category:explore`, `tool_profile:read`, `tool_profile:explore` |
| `quick` | 100 | `M2.5` | `task_category:quick`, `messages_count:<=1` |

### rules（当前生效）

| rule | priority | target_model | 条件 |
|------|----------|--------------|------|
| `failure_recovery` | 500 | `M2.5` | `failure_streak >= 2` |
| `high_complexity` | 400 | `opus4.6` | `messages_count >= 50` 或 `tools_count >= 10` |
| `medium_high` | 300 | `sonnet4.6` | `messages_count >= 25` 或 `tools_count >= 6` 或 `prompt_chars >= 15000` |
| `medium` | 200 | `M2.5` | `messages_count >= 10` 或 `tools_count >= 3` 或 `prompt_chars >= 5000` |

### Signal 字段

| 字段 | 格式 | 说明 |
|------|------|------|
| `keyword` | `keyword:<regex>` | 用户消息关键词匹配 |
| `task_category` | `task_category:<name>` | 语义分类结果匹配 |
| `tool_profile` | `tool_profile:<name>` | 工具画像匹配 |
| `has_code_context` | `has_code_context:true\|false` | 是否检测到代码上下文 |
| `system_prompt_type` | `system_prompt_type:<tag>` | system prompt 类型（主字段） |
| `system_tag` | `system_tag:<tag>` | `system_prompt_type` 的兼容别名 |
| `messages_count` | `messages_count:<op><value>` | 数值阈值 |
| `prompt_chars` | `prompt_chars:<op><value>` | 数值阈值 |

## 4. 物理实例与 provider

```toml
[instances.official]
port = 8146
providers = [
  { type = "gemini", rotation_strategy = "round-robin" },
  { type = "codex", rotation_strategy = "round-robin" },
  { type = "minimax", base_url = "https://api.minimaxi.com/anthropic", api_keys = ["${MINIMAX_KEY}"] },
]

[instances.zenmux]
port = 8147
providers = [
  { type = "openai", base_url = "https://zenmux.ai/api/v1", api_keys = ["${ZENMUX_KEY}"] },
  { type = "anthropic", base_url = "https://zenmux.ai/api/anthropic", api_keys = ["${ZENMUX_KEY}"] },
  { type = "gemini", base_url = "https://zenmux.ai/api/vertex-ai", api_keys = ["${ZENMUX_KEY}"] },
]
```

`routing` 中每个目标的 `provider` 必须与实例内 provider type 一致，否则会报 `unknown provider for model ...`。

## 5. 路由示例（当前 tier）

```toml
[routing]
"opus4.6" = [
  { instance = "zenmux", provider = "anthropic", model = "anthropic/claude-opus-4.6", weight = 100, params = { "max_tokens_max" = 24000 } },
]
"sonnet4.6" = [
  { instance = "zenmux", provider = "anthropic", model = "anthropic/claude-sonnet-4.6", weight = 100, params = { "max_tokens_max" = 16000 } },
]
"M2.5" = [
  { instance = "official", provider = "minimax", model = "MiniMax-M2.5", weight = 100, params = { "max_tokens_max" = 204800 } },
]
"gpt5.3" = [
  { instance = "official", provider = "codex", model = "gpt-5.3-codex", weight = 1, params = { "reasoning_effort" = "high" } },
]
```

常用 `params`：`reasoning_effort`、`max_tokens_max`、`max_tokens_default`、`anthropic_beta`、`extra_headers`。

## 6. auto 统计口径

- `by_auto_model_category`: key 为 `resolved_model × category`
- `by_auto_category` 可能出现特殊值：
- `(non_category)`: 未命中 category，转 rules/default
- `(not_activated)`: decision 为 `not_activated`
- `(unknown)`: category 名为空或无法解析

## 7. 生成与重载

```bash
python3 generate_config.py
./reload_proxy.sh
pm2 status
pm2 logs
```
