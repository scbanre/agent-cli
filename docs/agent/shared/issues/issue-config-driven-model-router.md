# feat(lb): 配置驱动自动选模（让 Claude Code 仅作前端）

## 背景

当前模型选择逻辑仍有前端参与，且部分自动选模阈值在代码中固化。  
目标是把“任务复杂度 -> 模型选择”的决策完全下沉到 LB + cliproxyapi，前端只负责发请求。

相关方案文档：`docs/agent/shared/lb-config-driven-model-router-proposal.md`

---

## 目标

- 支持配置驱动选模（规则不写死在代码）
- 前端（Claude Code）可固定发 `auto`，由 LB 决策真实模型
- 保留 cliproxyapi 作为执行层（实例内重试/账号切换/quota 策略）
- 提供影子模式、灰度开关、一键回滚

---

## 范围

### In Scope

- `providers.toml` 增加 `global.lb_model_router` 配置块
- LB 规则评估引擎（priority + match + when）
- `requested_model -> resolved_model` 改写
- 决策日志字段（命中规则/因子/trace）
- 影子模式（只记日志不改写）

### Out of Scope（一期不做）

- 在线学习自动调参
- 成本/成功率自动闭环调权

---

## 配置草案

```toml
[global.lb_model_router]
enabled = true
activation_models = ["auto"]
default_model = "g3f"
log_factors = true
# config_file = "router/agent-model-router.toml"

[[global.lb_model_router.rules]]
name = "high_complexity"
priority = 300
target_model = "opus4.6"
match = "any"

[[global.lb_model_router.rules.when]]
field = "messages_count"
op = ">="
value = 40
```

---

## 实施任务

- [ ] 解析 `global.lb_model_router` 配置（含外部 `config_file` 可选加载）
- [ ] 实现规则引擎（`field/op/value` + `any/all` + `priority`）
- [ ] 接入 LB 请求路径：在路由前计算 `resolved_model`
- [ ] 保持现有 sticky/cooldown/retry 语义不被破坏
- [ ] 增加日志字段：`requested_model/resolved_model/hit_rule/factors/eval_trace`
- [ ] 增加影子模式 `shadow_only = true`
- [ ] 补充文档与示例

---

## 验收标准

- [ ] `activation_models=["auto"]` 时，前端固定发 `auto` 可稳定路由到真实模型
- [ ] 修改规则无需改代码，仅改配置并 reload/restart 生效
- [ ] 影子模式下线上行为不变，但日志可看到“建议模型”
- [ ] 关闭开关后可立即回退到现有路由逻辑
- [ ] 日志可用于统计规则命中率、失败率、延迟、token 成本

---

## 上线计划

1. Phase 1：影子模式（只观测）
2. Phase 2：`auto-canary` 小流量
3. Phase 3：`auto` 主流量
4. 回滚：`enabled = false`

