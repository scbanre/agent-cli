# cliProxyAPI Gateway

多后端 AI API 聚合网关，按 model 名 + 权重路由到不同实例。

## 架构

```
客户端请求 → LB (8145) → 物理实例 (8146/8147) → 上游 API
```

## 这个项目有什么用

这个仓库是 `cliproxyapi` 的网关编排层，用来把多个后端实例统一成一个稳定入口，适合以下场景：

- 一个入口承接多种模型/账号，不让客户端感知后端差异
- 按模型名做路由，并用权重控制流量分配
- 在 OAuth 实例与 API Key 实例之间做混合调度
- 降低多实例部署和变更成本（改一份 TOML，自动生成运行配置）

## 相比 cliproxyapi 补充了什么

`source_code/` 提供的是核心代理能力；本仓库补充的是“多实例编排 + 运维自动化”：

| 维度 | cliproxyapi（核心） | 本仓库（补充） |
|------|---------------------|----------------|
| 角色 | 单实例代理与协议转换 | 多实例聚合网关 |
| 配置入口 | 各实例独立 YAML/配置 | 单一 `providers.toml` 统一定义 |
| 路由能力 | 实例内 provider/account 轮询 | 基于 `model` 的跨实例加权路由 |
| 运行编排 | 需手动组织进程与端口 | 自动生成 `lb.js` + `ecosystem.config.js` + `instances/*.yaml` |
| 客户端接入 | 直连单实例端口 | 统一从 LB 端口接入（默认 8145） |
| 工具链 | 核心二进制 | 额外提供 `generate_config.py`、`cld`、`deploy_cld.sh` |

边界说明：本仓库不替代 `cliproxyapi` 核心功能，而是作为其上层的部署与路由控制面。

## 核心文件

| 文件 | 说明 |
|------|------|
| `providers.toml` | 主配置：实例定义 + 路由规则 |
| `generate_config.py` | 配置生成器：TOML → YAML + LB + PM2 |
| `cld` | CLI 启动脚本（FZF 选模型） |
| `deploy_cld.sh` | 部署 cld 到 zsh fpath |
| `source_code/` | 核心代理 (git submodule) |

## cld 脚本说明

`cld` 是面向 Claude Code 的启动封装，目标是用最少命令在不同后端模式间切换，并把模型选择映射到环境变量。

### 支持模式

- `cp`: 通过本项目 LB（默认 `http://127.0.0.1:8145`）接入，支持场景预设和模型记忆
- `ag`: 直连 Antigravity（调试用），依赖 `ANTI_API_TOKEN`
- `official`: 走 Anthropic 官方默认配置（不注入 `BASE_URL` / `API_KEY`）

### 常用用法

```bash
cld                    # 交互选择模式 + 模型
cld cp                 # 直连网关模式
cld cp opus            # 按关键字快速匹配主模型并启动
cld ag                 # 直连 Antigravity
cld official           # 官方模式
```

### 行为要点

- `cp` 模式优先从 `CLD_TOML` 指定的 `providers.toml` 读取 `[routing]` 模型列表；读不到时使用内置模型列表
- `cp` 模式会分别设置 `ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_HAIKU_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL`
- `cp` 模式会把最近一次选择缓存到 `~/.cache/cld/cp/last_*`，下次可复用
- 子 Agent 模型通过 `CLAUDE_CODE_SUBAGENT_MODEL` 跟随 fast 角色
- `cp` 场景选择支持 `Auto Upgrade` 开关：当 Main 为 `g3f/g3p` 时可切到 `g3f.auto/g3p.auto`（由 LB 按阈值自动升档到 `opus4.6`）
- `ag` 模式可用 `CLD_AG_CODE_MODEL` / `CLD_AG_DOC_MODEL` / `CLD_AG_FAST_MODEL` 覆盖默认模型

## 快速开始

```bash
# 1. 克隆并初始化 submodule
git clone --recursive https://github.com/yourname/agent-cli.git
cd agent-cli

# 2. 配置
cp providers.toml.example providers.toml
cp .env.example .env
# 编辑 providers.toml 和 .env，填入你的配置

# 3. 安装依赖
npm install

# 4. 编译核心代理 (或下载 Release)
cd source_code && go build -o ../cliproxy ./cmd/cliproxy && cd ..

# 5. 生成配置
python3 generate_config.py

# 6. (optional) OAuth 登录 (如使用 antigravity/codex)
./cliproxy --antigravity-login
./cliproxy --codex-login

# 7. 启动
pm2 start ecosystem.config.js

# 8. 部署 cld 到 zsh fpath (可选)
./deploy_cld.sh
```

## 常用命令

```bash
python3 generate_config.py   # 重新生成配置
pm2 restart all              # 重启服务
pm2 logs                     # 查看日志
./cliproxy --antigravity-login  # OAuth 登录 (Google)
./cliproxy --codex-login        # OAuth 登录 (OpenAI)
```

## 变更流程

**添加/修改模型路由**: 编辑 `providers.toml` → `python3 generate_config.py` → `pm2 restart all`

**添加新后端**: 在 `[instances.xxx]` 新增实例 → 在 `[routing]` 引用 → 生成配置并重启

### 路由参数注意事项

- `lb.js` 是由 `generate_config.py` 自动生成的产物，不要手改（会在下次生成时被覆盖）
- 当 Gemini 3 Pro 走 relay（如 `google/gemini-3-pro-preview`）时，建议配置：
  - `params = { "reasoning_effort" = "high", "thinking_budget_max" = 24576 }`
- 该上限用于约束过大的 `thinking.budget_tokens`，避免被后端映射为不支持的 `xhigh` 并返回 400
- 可用 `params.max_tokens_max` / `params.max_tokens_default` 控制输出上限与默认值，优化延迟与 token 开销
- Claude 1M context 建议通过路由 `params.anthropic_beta` 配置（可选，默认不加）
- 如需注入其他请求头，可使用 `params.extra_headers`（按路由目标生效）
- `providers.toml` 支持实例级 `request_retry/max_retry_interval` 与全局 `streaming_*` / `quota_switch_*` 透传到 cliproxy 配置

## 详细文档

- `providers.toml` 语法：[docs/agent/shared/providers-toml.md](docs/agent/shared/providers-toml.md)
- 开发记录（上游对齐）：[docs/agent/shared/development-notes.md](docs/agent/shared/development-notes.md)
- 核心代理文档：[source_code/README.md](source_code/README.md)

## 目录结构

```
agent-cli/
├── providers.toml.example  # 配置模板
├── .env.example            # 密钥模板
├── generate_config.py      # 配置生成器
├── cld                     # CLI 启动脚本
├── deploy_cld.sh           # 部署脚本
├── docs/                   # 文档
├── scripts/                # 辅助脚本
├── instances/              # 生成的实例配置 (gitignore)
├── logs/                   # 运行日志 (gitignore)
└── source_code/            # 核心代理 (submodule)
```

## License

MIT
