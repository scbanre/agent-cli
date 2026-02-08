# cliProxyAPI Gateway

多后端 AI API 聚合网关，按 model 名 + 权重路由到不同实例。

## 架构

```
客户端请求 → LB (8145) → 物理实例 (8146/8147) → 上游 API
```

## 核心文件

| 文件 | 说明 |
|------|------|
| `providers.toml` | 主配置：实例定义 + 路由规则 |
| `generate_config.py` | 配置生成器：TOML → YAML + LB + PM2 |
| `lb.js` | Node.js 负载均衡器 |
| `cld` | CLI 启动脚本（FZF 选模型） |
| `source_code/` | 核心代理 (git submodule) |

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

## 详细文档

- `providers.toml` 语法：[docs/agent/shared/providers-toml.md](docs/agent/shared/providers-toml.md)
- 核心代理文档：[source_code/README.md](source_code/README.md)

## 目录结构

```
agent-cli/
├── providers.toml.example  # 配置模板
├── .env.example            # 密钥模板
├── generate_config.py      # 配置生成器
├── lb.js                   # 负载均衡器
├── cld                     # CLI 启动脚本
├── ecosystem.config.js     # PM2 配置
├── docs/                   # 文档
├── scripts/                # 辅助脚本
├── instances/              # 生成的实例配置 (gitignore)
├── logs/                   # 运行日志 (gitignore)
└── source_code/            # 核心代理 (submodule)
```

## License

MIT
