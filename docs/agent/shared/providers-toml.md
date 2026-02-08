# providers.toml 配置说明

## 1. 全局配置

```toml
[global]
host = "0.0.0.0"
main_port = 8145        # LB 对外端口
proxy = "http://..."    # 可选代理
```

## 2. 物理实例

```toml
[instances.official]
port = 8146
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

## 端口分配

| 端口 | 服务 |
|------|------|
| 8145 | 主入口 LB (lb.js) |
| 8146 | official 实例 (OAuth) |
| 8147 | zenmux 实例 (API Key) |

## 生成文件 (gitignore)

| 文件 | 说明 |
|------|------|
| `instances/*.yaml` | 各物理实例的 cliproxy 配置 |
| `lb.js` | Node.js 负载均衡器 |
| `ecosystem.config.js` | PM2 进程配置 |
| `cliproxy` | 编译的二进制文件 |

## cld 模型 Tier 映射

`cld` 脚本通过环境变量预设模型映射，Claude Code 的 Task 工具通过 `model` 参数选择 tier，实际请求经 cliproxyapi 路由到对应后端。

映射关系在 `cld` 脚本中维护，随时可能变更，以脚本实际配置为准。
