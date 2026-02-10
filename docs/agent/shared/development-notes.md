# 开发记录

## 2026-02-10 上游对齐（Antigravity-Manager）

本节记录与上游 `lbjlaq/Antigravity-Manager` 的对照结果，目的是把“可直接迁移的稳定性改进”沉淀到本仓库开发流程中。

### 已在本仓库落地

- `providers.toml` 路由参数文档化：`reasoning_effort` / `thinking_budget_max` / `anthropic_beta` / `extra_headers`
- `providers.toml.example` 增加 Claude 1M context 的可选配置示例（默认不启用）

### 建议优先引入（按优先级）

1. 404 退避策略改为 provider-aware 短退避
   - 上游实践：Google Cloud 场景对 404 做短延迟重试并轮换账号，且短锁定（5s）而非长冻结
   - 本仓库现状：`source_code/sdk/cliproxy/auth/conductor.go` 对 404 采用 `12h` 挂起
   - 目标：降低误判不可用造成的长时间不可恢复

2. 增加模型映射回归测试
   - 重点保护 `gemini-3-pro-image` 不被并入文本配额分组
   - 避免图像请求错误消耗文本模型配额

3. Thinking 签名异常的降级路径
   - 对历史 thinking block 签名缺失/失效的情况，增加“自动降级为普通文本”兜底
   - 目标：减少多轮对话中 `Invalid signature in thinking block` 的 400

### 说明

- 上游 `v4.1.13` Release 文案提及的部分修复（如 `#1790/#1794/#1786/#1781`）在 2026-02-10 的 `main` 可见提交列表中未直接定位到对应 commit，迁移前需以具体 commit 为准。

### 参考

- https://github.com/lbjlaq/Antigravity-Manager/releases/tag/v4.1.13
- https://github.com/lbjlaq/Antigravity-Manager/compare/v4.1.12...main
- https://github.com/lbjlaq/Antigravity-Manager/commit/842fbfa
- https://github.com/lbjlaq/Antigravity-Manager/commit/69724dd
- https://github.com/lbjlaq/Antigravity-Manager/commit/dc9558c
- https://github.com/lbjlaq/Antigravity-Manager/commit/cf9ff6c
- https://github.com/lbjlaq/Antigravity-Manager/commit/77d4d86
