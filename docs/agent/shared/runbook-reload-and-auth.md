# Reload 与认证排障 Runbook

本文档只覆盖部署与重载链路中最常见的故障，按“先恢复、后定位”的顺序组织。

## 1. 正确入口

在外层部署目录执行：

```bash
cd /Volumes/ext/env/cliproxyapi
./reload_proxy.sh
```

不要在 `source_code/` 子目录执行同名脚本。

## 2. 常见故障信号

- `cld` 或 API 请求大量 `500`
- 响应包含 `auth_unavailable: no auth available`
- 响应包含 `unknown provider for model ...`

## 3. 高概率根因

在错误目录执行重载后，PM2 可能加载 `source_code/instances/*.yaml`，导致：

- `auth-dir` 指向 `source_code/`，OAuth 凭据未加载
- `.env` 未按预期替换，出现 `${ZENMUX_KEY}` 这类未展开值

## 4. 快速恢复

```bash
cd /Volumes/ext/env/cliproxyapi
./reload_proxy.sh
```

若仍异常，再执行：

```bash
pm2 kill
pm2 resurrect
./reload_proxy.sh
```

## 5. 恢复后校验

```bash
pm2 describe cliproxy-official | rg "script args|exec cwd|status"
```

预期应指向外层实例配置：

- `-config /Volumes/ext/env/cliproxyapi/instances/official.yaml`

再做一次最小请求验证：

```bash
curl -sS --noproxy '*' \
  -H 'Authorization: Bearer dummy-key' \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:8145/v1/messages \
  -d '{"model":"g3f","max_tokens":32,"messages":[{"role":"user","content":"hello"}]}'
```

