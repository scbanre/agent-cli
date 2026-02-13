#!/usr/bin/env python3
import toml
import yaml
import os
import sys
import json
import re

# 配置路径 (使用脚本所在目录)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOML_FILE = os.path.join(BASE_DIR, "providers.toml")
ENV_FILE = os.path.join(BASE_DIR, ".env")
OUTPUT_DIR = os.path.join(BASE_DIR, "instances")
PM2_FILE = os.path.join(BASE_DIR, "ecosystem.config.js")

def load_env():
    """加载 .env 文件到环境变量"""
    if os.path.exists(ENV_FILE):
        print(f"📄 加载环境变量: {ENV_FILE}")
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

def substitute_env(data):
    """递归替换配置中的环境变量占位符 ${VAR}"""
    if isinstance(data, dict):
        return {k: substitute_env(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [substitute_env(v) for v in data]
    elif isinstance(data, str):
        # 查找 ${VAR} 模式
        return re.sub(r'\$\{([^}]+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), data)
    else:
        return data

def load_toml():
    if not os.path.exists(TOML_FILE):
        print(f"❌ 错误: 找不到配置文件 {TOML_FILE}")
        sys.exit(1)

    # 1. 加载 .env
    load_env()

    # 2. 读取 TOML
    raw_data = toml.load(TOML_FILE)

    # 3. 替换变量
    return substitute_env(raw_data)

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    raise ValueError(f"invalid boolean value: {value}")

def coerce_int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value.strip())
    raise ValueError(f"invalid integer value: {value}")

def pick_conf(instance_conf, global_conf, key):
    if key in instance_conf:
        return instance_conf[key]
    return global_conf.get(key)

def generate_instance_config(name, instance_conf, routing, global_conf):
    """生成物理实例配置 (兼容当前 cliproxy 配置格式)"""

    request_retry = instance_conf.get("request_retry", global_conf.get("request_retry", 3))
    max_retry_interval = instance_conf.get("max_retry_interval", global_conf.get("max_retry_interval", 30))
    routing_strategy = instance_conf.get("routing_strategy", global_conf.get("routing_strategy"))

    yaml_conf = {
        "host": global_conf.get("host", "0.0.0.0"),
        "port": instance_conf["port"],
        "proxy-url": global_conf.get("proxy", ""),
        "auth-dir": BASE_DIR,
        "request-retry": max(0, int(request_retry)),
        "max-retry-interval": max(0, int(max_retry_interval)),
    }
    if routing_strategy:
        yaml_conf["routing"] = {"strategy": routing_strategy}

    disable_cooling = pick_conf(instance_conf, global_conf, "disable_cooling")
    if disable_cooling is not None:
        yaml_conf["disable-cooling"] = coerce_bool(disable_cooling)

    nonstream_keepalive_interval = pick_conf(instance_conf, global_conf, "nonstream_keepalive_interval")
    if nonstream_keepalive_interval is not None:
        yaml_conf["nonstream-keepalive-interval"] = max(0, coerce_int(nonstream_keepalive_interval))

    instance_streaming = instance_conf.get("streaming", {}) if isinstance(instance_conf.get("streaming"), dict) else {}
    global_streaming = global_conf.get("streaming", {}) if isinstance(global_conf.get("streaming"), dict) else {}

    keepalive_seconds = (
        instance_streaming.get("keepalive_seconds", instance_streaming.get("keepalive-seconds"))
        if instance_streaming else None
    )
    if keepalive_seconds is None:
        keepalive_seconds = pick_conf(instance_conf, global_conf, "streaming_keepalive_seconds")
    if keepalive_seconds is None:
        keepalive_seconds = global_streaming.get("keepalive_seconds", global_streaming.get("keepalive-seconds"))

    bootstrap_retries = (
        instance_streaming.get("bootstrap_retries", instance_streaming.get("bootstrap-retries"))
        if instance_streaming else None
    )
    if bootstrap_retries is None:
        bootstrap_retries = pick_conf(instance_conf, global_conf, "streaming_bootstrap_retries")
    if bootstrap_retries is None:
        bootstrap_retries = global_streaming.get("bootstrap_retries", global_streaming.get("bootstrap-retries"))

    streaming_conf = {}
    if keepalive_seconds is not None:
        streaming_conf["keepalive-seconds"] = max(0, coerce_int(keepalive_seconds))
    if bootstrap_retries is not None:
        streaming_conf["bootstrap-retries"] = max(0, coerce_int(bootstrap_retries))
    if streaming_conf:
        yaml_conf["streaming"] = streaming_conf

    instance_quota = instance_conf.get("quota_exceeded", {}) if isinstance(instance_conf.get("quota_exceeded"), dict) else {}
    global_quota = global_conf.get("quota_exceeded", {}) if isinstance(global_conf.get("quota_exceeded"), dict) else {}

    switch_project = pick_conf(instance_conf, global_conf, "quota_switch_project")
    if switch_project is None:
        switch_project = instance_quota.get("switch_project", instance_quota.get("switch-project"))
    if switch_project is None:
        switch_project = global_quota.get("switch_project", global_quota.get("switch-project"))

    switch_preview_model = pick_conf(instance_conf, global_conf, "quota_switch_preview_model")
    if switch_preview_model is None:
        switch_preview_model = instance_quota.get("switch_preview_model", instance_quota.get("switch-preview-model"))
    if switch_preview_model is None:
        switch_preview_model = global_quota.get("switch_preview_model", global_quota.get("switch-preview-model"))

    quota_conf = {}
    if switch_project is not None:
        quota_conf["switch-project"] = coerce_bool(switch_project)
    if switch_preview_model is not None:
        quota_conf["switch-preview-model"] = coerce_bool(switch_preview_model)
    if quota_conf:
        yaml_conf["quota-exceeded"] = quota_conf

    claude_keys = []
    openai_compat = []
    vertex_keys = []

    # 1. 遍历 TOML 中定义的 Providers
    for idx, p_raw in enumerate(instance_conf.get("providers", [])):
        provider_type = p_raw["type"]
        provider_name = f"{name}-{provider_type}-{idx}"
        base_url = p_raw.get("base_url", "")
        api_keys = p_raw.get("api_keys", [])

        # 2. 根据 routing 规则收集该 provider 需要承载的内部模型
        models = []
        for expose_id, targets in routing.items():
            for target in targets:
                if target.get("instance") != name:
                    continue
                target_provider = target.get("provider")
                if not target_provider:
                    print(f"⚠️  警告: 路由 '{expose_id}' -> '{name}' 未指定 provider 类型，跳过")
                    continue
                if target_provider != provider_type:
                    continue
                internal_model = target["model"]
                if internal_model not in models:
                    models.append(internal_model)

        # 3. 按当前 cliproxy 支持的配置块输出
        # anthopic -> claude-api-key
        if provider_type == "anthropic":
            for key in api_keys:
                entry = {
                    "api-key": key,
                    "base-url": base_url,
                }
                if models:
                    entry["models"] = [{"name": m, "alias": m} for m in models]
                claude_keys.append(entry)
            continue

        # openai -> openai-compatibility
        if provider_type == "openai":
            entry = {
                "name": provider_name,
                "base-url": base_url,
                "api-key-entries": [{"api-key": key} for key in api_keys],
            }
            if models:
                entry["models"] = [{"name": m, "alias": m} for m in models]
            openai_compat.append(entry)
            continue

        # gemini(第三方 Vertex 风格 API) -> vertex-api-key
        if provider_type == "gemini":
            for key in api_keys:
                entry = {
                    "api-key": key,
                    "base-url": base_url,
                }
                if models:
                    entry["models"] = [{"name": m, "alias": m} for m in models]
                vertex_keys.append(entry)
            continue

        # antigravity/codex 等 OAuth 类型由 auth-dir 自动加载，无需额外配置

    if claude_keys:
        yaml_conf["claude-api-key"] = claude_keys
    if openai_compat:
        yaml_conf["openai-compatibility"] = openai_compat
    if vertex_keys:
        yaml_conf["vertex-api-key"] = vertex_keys

    return yaml_conf

def ensure_node_deps(base_dir):
    """确保 http-proxy 依赖已安装"""
    pkg_file = os.path.join(base_dir, "package.json")
    if not os.path.exists(pkg_file):
        with open(pkg_file, "w") as f:
            json.dump({
                "name": "cliproxy-lb",
                "version": "1.0.0",
                "private": True,
                "dependencies": {
                    "http-proxy": "^1.18.1"
                }
            }, f, indent=2)
        print("📦 初始化 package.json")

    node_modules = os.path.join(base_dir, "node_modules")
    if not os.path.exists(node_modules):
        print("📦 安装 Node.js 依赖 (http-proxy)...")
        os.system(f"cd {base_dir} && npm install")

def create_node_lb_script(path, routing, instances, port, global_conf):
    """生成 Node.js 版本的智能负载均衡器 (支持 HTTP/SSE/WebSocket + 完整日志)"""

    routes = {}
    default_target = ""

    for expose_id, targets in routing.items():
        route_targets = []
        route_weights = []

        for t in targets:
            inst_name = t["instance"]
            if inst_name in instances:
                port_num = instances[inst_name]["port"]
                target_url = f"http://127.0.0.1:{port_num}"
                if not default_target:
                    default_target = target_url
                weight = t.get("weight", 1)
                target_entry = {
                    "target": target_url,
                    "rewrite": t["model"],
                    "instance": inst_name,
                    "provider": t.get("provider", "unknown")
                }
                route_params = t.get("params")
                if isinstance(route_params, dict) and route_params:
                    target_entry["params"] = route_params
                route_targets.append(target_entry)
                route_weights.append(weight)

        if route_targets:
            routes[expose_id] = {
                "targets": route_targets,
                "weights": route_weights
            }

    auth_cooldown_ms = int(global_conf.get("lb_auth_cooldown_ms", 5 * 60 * 1000))
    validation_cooldown_ms = int(global_conf.get("lb_validation_cooldown_ms", 12 * 60 * 60 * 1000))
    transient_cooldown_ms = int(global_conf.get("lb_transient_cooldown_ms", 60 * 1000))
    transient_heavy_cooldown_ms = int(global_conf.get("lb_transient_heavy_cooldown_ms", 2 * 60 * 1000))
    signature_cooldown_ms = int(global_conf.get("lb_signature_cooldown_ms", 2 * 60 * 1000))
    quota_cooldown_ms = int(global_conf.get("lb_quota_cooldown_ms", 12 * 60 * 60 * 1000))
    max_target_retries = max(0, int(global_conf.get("lb_max_target_retries", 1)))
    retry_auth_on_5xx = coerce_bool(global_conf.get("lb_retry_auth_on_5xx", True))
    auto_upgrade_enabled = coerce_bool(global_conf.get("lb_auto_upgrade_enabled", False))
    auto_upgrade_messages_threshold = max(1, coerce_int(global_conf.get("lb_auto_upgrade_messages_threshold", 80)))
    auto_upgrade_tools_threshold = max(1, coerce_int(global_conf.get("lb_auto_upgrade_tools_threshold", 10)))
    auto_upgrade_failure_streak_threshold = max(1, coerce_int(global_conf.get("lb_auto_upgrade_failure_streak_threshold", 2)))
    auto_upgrade_signature_enabled = coerce_bool(global_conf.get("lb_auto_upgrade_signature_enabled", True))
    raw_auto_upgrade_map = global_conf.get("lb_auto_upgrade_map", {})
    auto_upgrade_map = {}
    if isinstance(raw_auto_upgrade_map, dict):
        for source_model, target_model in raw_auto_upgrade_map.items():
            source = str(source_model).strip()
            target = str(target_model).strip()
            if source and target and source != target:
                auto_upgrade_map[source] = target

    script_content = f"""
const http = require('http');
const httpProxy = require('http-proxy');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');

const PORT = {port};
const ROUTES = {json.dumps(routes, indent=2)};
const DEFAULT_TARGET = "{default_target}";
const LOG_DIR = path.join(__dirname, 'logs', 'requests');
const LOG_RETENTION_DAYS = 90;
const LOG_VERBOSE = process.env.LOG_VERBOSE === '1';
const RESPONSE_PREVIEW_LIMIT = LOG_VERBOSE ? 2000 : 500;
const STICKY_ROUTE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const STICKY_CLEANUP_MS = 10 * 60 * 1000;
const MAX_STICKY_KEYS = 500;
const TARGET_COOLDOWN_CLEANUP_MS = 10 * 1000;
const AUTH_COOLDOWN_MS = {auth_cooldown_ms};
const VALIDATION_COOLDOWN_MS = {validation_cooldown_ms};
const TRANSIENT_COOLDOWN_MS = {transient_cooldown_ms};
const TRANSIENT_HEAVY_COOLDOWN_MS = {transient_heavy_cooldown_ms};
const SIGNATURE_COOLDOWN_MS = {signature_cooldown_ms};
const QUOTA_COOLDOWN_MS = {quota_cooldown_ms};
const MAX_TARGET_RETRIES = {max_target_retries};
const RETRY_AUTH_ON_5XX = {str(retry_auth_on_5xx).lower()};
const AUTO_UPGRADE_ENABLED = {str(auto_upgrade_enabled).lower()};
const AUTO_UPGRADE_MODEL_MAP = {json.dumps(auto_upgrade_map, indent=2)};
const AUTO_UPGRADE_MESSAGES_THRESHOLD = {auto_upgrade_messages_threshold};
const AUTO_UPGRADE_TOOLS_THRESHOLD = {auto_upgrade_tools_threshold};
const AUTO_UPGRADE_FAILURE_STREAK_THRESHOLD = {auto_upgrade_failure_streak_threshold};
const AUTO_UPGRADE_SIGNATURE_ENABLED = {str(auto_upgrade_signature_enabled).lower()};
const MODEL_HEALTH_TTL_MS = 2 * 60 * 60 * 1000;
const MODEL_HEALTH_CLEANUP_MS = 10 * 60 * 1000;
const RETRYABLE_ROUTE_ACTIONS = new Set(['auth', 'transient']);
const stickyRoutes = new Map();
const targetCooldowns = new Map();
const modelHealth = new Map();

// 确保日志目录存在
if (!fs.existsSync(LOG_DIR)) {{
    fs.mkdirSync(LOG_DIR, {{ recursive: true }});
}}

// 获取当天日志文件路径
function getLogFile() {{
    const date = new Date().toISOString().split('T')[0];
    return path.join(LOG_DIR, `${{date}}.jsonl`);
}}

// 写入日志
function writeLog(logEntry) {{
    const logFile = getLogFile();
    const line = JSON.stringify(logEntry) + '\\n';
    fs.appendFile(logFile, line, (err) => {{
        if (err) console.error('Failed to write log:', err.message);
    }});
}}

// 清理过期日志
function cleanOldLogs() {{
    const cutoff = Date.now() - LOG_RETENTION_DAYS * 24 * 60 * 60 * 1000;
    fs.readdir(LOG_DIR, (err, files) => {{
        if (err) return;
        files.forEach(file => {{
            if (!file.endsWith('.jsonl')) return;
            const filePath = path.join(LOG_DIR, file);
            fs.stat(filePath, (err, stats) => {{
                if (err) return;
                if (stats.mtime.getTime() < cutoff) {{
                    fs.unlink(filePath, () => {{}});
                    console.log(`🗑️ Cleaned old log: ${{file}}`);
                }}
            }});
        }});
    }});
}}

// 启动时清理一次，之后每天清理
cleanOldLogs();
setInterval(cleanOldLogs, 24 * 60 * 60 * 1000);
setInterval(() => {{
    const now = Date.now();
    for (const [key, value] of stickyRoutes.entries()) {{
        if (value.expiresAt <= now) stickyRoutes.delete(key);
    }}
}}, STICKY_CLEANUP_MS);
setInterval(() => {{
    const now = Date.now();
    for (const [key, value] of targetCooldowns.entries()) {{
        if (value.expiresAt <= now) targetCooldowns.delete(key);
    }}
}}, TARGET_COOLDOWN_CLEANUP_MS);

function cleanupModelHealth() {{
    const now = Date.now();
    for (const [key, value] of modelHealth.entries()) {{
        if (!value || (value.updatedAt || 0) + MODEL_HEALTH_TTL_MS <= now) {{
            modelHealth.delete(key);
        }}
    }}
}}

setInterval(cleanupModelHealth, MODEL_HEALTH_CLEANUP_MS);

// 从 SSE 流中提取 usage 信息
function extractUsageFromSSE(chunks) {{
    let usage = null;
    const lines = chunks.split('\\n');
    for (const line of lines) {{
        if (line.startsWith('data: ')) {{
            try {{
                const data = JSON.parse(line.slice(6));
                if (data.usage) {{
                    usage = data.usage;
                }}
            }} catch (e) {{}}
        }}
    }}
    return usage;
}}

// 从 JSON 响应中提取 usage 信息
function extractUsageFromJSON(body) {{
    try {{
        const data = JSON.parse(body);
        return data.usage || null;
    }} catch (e) {{
        return null;
    }}
}}

function decodeResponseBody(rawBodyBuffer, proxyHeaders) {{
    if (!Buffer.isBuffer(rawBodyBuffer) || rawBodyBuffer.length === 0) {{
        return {{
            bodyText: '',
            decodedFromEncoding: null,
            decodeError: null
        }};
    }}

    const headerValue = proxyHeaders?.['content-encoding'] ?? proxyHeaders?.['Content-Encoding'] ?? '';
    const encoding = String(headerValue || '')
        .split(',')
        .map((part) => part.trim().toLowerCase())
        .find((part) => part.length > 0) || '';

    if (!encoding) {{
        return {{
            bodyText: rawBodyBuffer.toString('utf-8'),
            decodedFromEncoding: null,
            decodeError: null
        }};
    }}

    try {{
        if (encoding === 'gzip' || encoding === 'x-gzip') {{
            return {{
                bodyText: zlib.gunzipSync(rawBodyBuffer).toString('utf-8'),
                decodedFromEncoding: encoding,
                decodeError: null
            }};
        }}
        if (encoding === 'br') {{
            return {{
                bodyText: zlib.brotliDecompressSync(rawBodyBuffer).toString('utf-8'),
                decodedFromEncoding: encoding,
                decodeError: null
            }};
        }}
        if (encoding === 'deflate') {{
            return {{
                bodyText: zlib.inflateSync(rawBodyBuffer).toString('utf-8'),
                decodedFromEncoding: encoding,
                decodeError: null
            }};
        }}
    }} catch (error) {{
        return {{
            bodyText: rawBodyBuffer.toString('utf-8'),
            decodedFromEncoding: null,
            decodeError: error?.message || 'decode_failed'
        }};
    }}

    return {{
        bodyText: rawBodyBuffer.toString('utf-8'),
        decodedFromEncoding: null,
        decodeError: null
    }};
}}

function parseErrorSummary(contentType, body) {{
    const segments = [];
    if (contentType.includes('application/json')) {{
        try {{
            const payload = JSON.parse(body);
            const err = payload?.error ?? payload;
            if (typeof err === 'string') {{
                segments.push(err);
            }} else if (err && typeof err === 'object') {{
                const fields = ['message', 'code', 'type', 'status', 'reason'];
                for (const field of fields) {{
                    if (typeof err[field] === 'string') segments.push(err[field]);
                }}
                if (Array.isArray(err.details)) {{
                    for (const detail of err.details) {{
                        if (detail && typeof detail.reason === 'string') segments.push(detail.reason);
                        if (typeof detail?.domain === 'string') segments.push(detail.domain);
                    }}
                }}
            }}
        }} catch (e) {{}}
    }}
    if (segments.length === 0 && typeof body === 'string' && body.length > 0) {{
        segments.push(body.slice(0, RESPONSE_PREVIEW_LIMIT));
    }}
    return segments.join(' ').toLowerCase();
}}

function classifyResponse(statusCode, contentType, body, hasThinkingSignature) {{
    if (statusCode >= 200 && statusCode < 300) {{
        return {{ kind: 'success', clearSticky: false, cooldownMs: 0 }};
    }}

    const summary = parseErrorSummary(contentType, body);
    const isValidationError = statusCode === 403 &&
        (summary.includes('validation_required') ||
         summary.includes('verify your account') ||
         summary.includes('validation_url'));
    const isQuotaError = summary.includes('insufficient_quota') ||
        summary.includes('quota exceeded') ||
        summary.includes('quote_exceeded') ||
        summary.includes('subscription quota') ||
        summary.includes('quota limit') ||
        summary.includes('quota refresh');
    const isAuthError = summary.includes('auth_unavailable') ||
        summary.includes('auth_not_found') ||
        statusCode === 401 || statusCode === 403;
    if (isAuthError) {{
        let cooldownMs = AUTH_COOLDOWN_MS;
        if (isValidationError) {{
            cooldownMs = VALIDATION_COOLDOWN_MS;
        }} else if (isQuotaError) {{
            cooldownMs = QUOTA_COOLDOWN_MS;
        }}
        return {{ kind: 'auth', clearSticky: true, cooldownMs }};
    }}

    const isSignatureError = hasThinkingSignature &&
        summary.includes('signature') &&
        (statusCode === 400 || statusCode === 422 || statusCode === 500);
    if (isSignatureError) {{
        return {{ kind: 'signature', clearSticky: true, cooldownMs: SIGNATURE_COOLDOWN_MS }};
    }}

    if ([408, 429, 500, 502, 503, 504].includes(statusCode)) {{
        const cooldownMs = (statusCode === 429 || statusCode === 503)
            ? TRANSIENT_HEAVY_COOLDOWN_MS
            : TRANSIENT_COOLDOWN_MS;
        return {{ kind: 'transient', clearSticky: true, cooldownMs }};
    }}

    if (statusCode === 400 || statusCode === 422) {{
        return {{ kind: 'client', clearSticky: false, cooldownMs: 0 }};
    }}

    return {{
        kind: 'other',
        clearSticky: statusCode >= 500,
        cooldownMs: statusCode >= 500 ? TRANSIENT_COOLDOWN_MS : 0
    }};
}}

function maskSecret(value) {{
    if (typeof value !== 'string') return '***';
    if (value.length <= 10) return '***';
    return `${{value.slice(0, 6)}}...${{value.slice(-4)}}`;
}}

function sanitizeHeaders(headers) {{
    const out = {{}};
    const sensitive = new Set([
        'authorization', 'x-api-key', 'api-key', 'proxy-authorization', 'cookie', 'set-cookie'
    ]);
    for (const [rawKey, rawValue] of Object.entries(headers || {{}})) {{
        const key = rawKey.toLowerCase();
        if (sensitive.has(key)) {{
            if (Array.isArray(rawValue)) {{
                out[rawKey] = rawValue.map(maskSecret);
            }} else {{
                out[rawKey] = maskSecret(rawValue);
            }}
        }} else {{
            out[rawKey] = rawValue;
        }}
    }}
    return out;
}}

function summarizeRequestBody(body) {{
    if (body == null) return null;
    if (typeof body !== 'object') return body;
    const messages = Array.isArray(body.messages) ? body.messages : [];
    const modelValue = typeof body.model === 'string' ? body.model : null;
    const summary = {{
        model: modelValue,
        max_tokens: typeof body.max_tokens === 'number' ? body.max_tokens : null,
        stream: body.stream === true,
        temperature: typeof body.temperature === 'number' ? body.temperature : null,
        messages_count: messages.length,
        message_roles: messages.map((m) => m?.role || null),
        has_thinking_signature: hasThinkingSignature(body),
        tool_count: Array.isArray(body.tools) ? body.tools.length : 0,
        system_count: Array.isArray(body.system) ? body.system.length : 0
    }};
    if (body.metadata && typeof body.metadata === 'object') {{
        summary.metadata_keys = Object.keys(body.metadata).sort();
        if (typeof body.metadata.user_id === 'string') {{
            summary.metadata_user_hash = hashSessionKey(body.metadata.user_id);
        }}
    }}
    return summary;
}}

function shouldNormalizeErrorMessage(message) {{
    if (typeof message !== 'string' || message.length === 0) return false;
    const hasGzipMagic = message.length >= 2 &&
        message.charCodeAt(0) === 0x1f &&
        message.charCodeAt(1) === 0x8b;
    const controlChars = (message.match(/[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F]/g) || []).length;
    const replacementChars = (message.match(/\\uFFFD/g) || []).length;
    return hasGzipMagic || controlChars >= 3 || replacementChars >= 3;
}}

function maybeNormalizeJsonErrorBody(contentType, responseBody) {{
    if (!contentType.includes('application/json') || typeof responseBody !== 'string') {{
        return responseBody;
    }}
    try {{
        const payload = JSON.parse(responseBody);
        if (!payload?.error || typeof payload.error !== 'object') {{
            return responseBody;
        }}
        const message = payload.error.message;
        if (!shouldNormalizeErrorMessage(message)) {{
            return responseBody;
        }}
        const code = typeof payload.error.code === 'string' ? payload.error.code : null;
        payload.error.message = code === 'insufficient_quota'
            ? 'upstream quota exhausted; please switch account/key or wait for quota reset'
            : 'upstream returned unreadable compressed error details';
        return JSON.stringify(payload);
    }} catch (e) {{
        return responseBody;
    }}
}}

function targetIdentity(target) {{
    if (!target) return '';
    return `${{target.instance}}::${{target.target}}::${{target.rewrite}}`;
}}

function ensureTriedTargets(req) {{
    if (!req._triedTargets) {{
        req._triedTargets = new Set();
    }}
    return req._triedTargets;
}}

function markTriedTarget(req, target) {{
    const key = targetIdentity(target);
    if (!key) return;
    ensureTriedTargets(req).add(key);
}}

function hasTriedTarget(req, target) {{
    const key = targetIdentity(target);
    if (!key || !req._triedTargets) return false;
    return req._triedTargets.has(key);
}}

function toPositiveInt(value) {{
    const num = Number(value);
    if (!Number.isFinite(num)) return null;
    const asInt = Math.floor(num);
    return asInt > 0 ? asInt : null;
}}

function mergeCommaHeader(existingValue, appendValue) {{
    if (typeof appendValue !== 'string' || !appendValue.trim()) return existingValue;
    const existingParts = String(existingValue || '')
        .split(',')
        .map((part) => part.trim())
        .filter((part) => part.length > 0);
    const existingSet = new Set(existingParts.map((part) => part.toLowerCase()));
    const extraParts = appendValue
        .split(',')
        .map((part) => part.trim())
        .filter((part) => part.length > 0);
    for (const part of extraParts) {{
        const normalized = part.toLowerCase();
        if (!existingSet.has(normalized)) {{
            existingParts.push(part);
            existingSet.add(normalized);
        }}
    }}
    return existingParts.join(',');
}}

function summarizeTargetParams(params) {{
    if (!params || typeof params !== 'object' || Array.isArray(params)) return null;
    const summary = {{}};
    if (typeof params.reasoning_effort === 'string' && params.reasoning_effort.trim()) {{
        summary.reasoning_effort = params.reasoning_effort.trim();
    }}
    const thinkingBudgetMax = toPositiveInt(params.thinking_budget_max);
    if (thinkingBudgetMax) {{
        summary.thinking_budget_max = thinkingBudgetMax;
    }}
    const maxTokensMax = toPositiveInt(params.max_tokens_max);
    if (maxTokensMax) {{
        summary.max_tokens_max = maxTokensMax;
    }}
    const maxTokensDefault = toPositiveInt(params.max_tokens_default);
    if (maxTokensDefault) {{
        summary.max_tokens_default = maxTokensDefault;
    }}
    if (typeof params.anthropic_beta === 'string' && params.anthropic_beta.trim()) {{
        summary.anthropic_beta = params.anthropic_beta.trim();
    }}
    if (params.extra_headers && typeof params.extra_headers === 'object' && !Array.isArray(params.extra_headers)) {{
        summary.extra_header_keys = Object.keys(params.extra_headers)
            .map((key) => String(key).toLowerCase())
            .sort();
    }}
    return Object.keys(summary).length > 0 ? summary : null;
}}

function applyTargetHeaders(req, target) {{
    const baseHeaders = req._baseHeaders && typeof req._baseHeaders === 'object'
        ? req._baseHeaders
        : req.headers;
    req.headers = {{ ...baseHeaders }};
    const params = target?.params;
    if (!params || typeof params !== 'object' || Array.isArray(params)) return;

    if (typeof params.anthropic_beta === 'string' && params.anthropic_beta.trim()) {{
        req.headers['anthropic-beta'] = mergeCommaHeader(req.headers['anthropic-beta'], params.anthropic_beta);
    }}

    if (params.extra_headers && typeof params.extra_headers === 'object' && !Array.isArray(params.extra_headers)) {{
        for (const [rawKey, rawValue] of Object.entries(params.extra_headers)) {{
            const key = String(rawKey || '').trim();
            if (!key) continue;
            const lowerKey = key.toLowerCase();
            if (lowerKey === 'content-length' || lowerKey === 'host') continue;
            if (rawValue == null) continue;
            req.headers[key] = String(rawValue);
        }}
    }}
}}

function applyTargetParamsToPayload(payload, target) {{
    const params = target?.params;
    if (!params || typeof params !== 'object' || Array.isArray(params)) return payload;

    if (typeof params.reasoning_effort === 'string' && params.reasoning_effort.trim()) {{
        payload.reasoning_effort = params.reasoning_effort.trim();
    }}

    const thinkingBudgetMax = toPositiveInt(params.thinking_budget_max);
    if (thinkingBudgetMax && payload.thinking && typeof payload.thinking === 'object' && !Array.isArray(payload.thinking)) {{
        const currentBudget = toPositiveInt(payload.thinking.budget_tokens);
        if (currentBudget && currentBudget > thinkingBudgetMax) {{
            payload.thinking.budget_tokens = thinkingBudgetMax;
        }}
    }}

    const initialMaxTokens = toPositiveInt(payload.max_tokens);
    const maxTokensMax = toPositiveInt(params.max_tokens_max);
    if (maxTokensMax && initialMaxTokens && initialMaxTokens > maxTokensMax) {{
        payload.max_tokens = maxTokensMax;
    }}

    const maxTokensDefault = toPositiveInt(params.max_tokens_default);
    if (maxTokensDefault && !initialMaxTokens) {{
        payload.max_tokens = maxTokensDefault;
    }}

    const finalMaxTokens = toPositiveInt(payload.max_tokens);
    if (finalMaxTokens && payload.thinking && typeof payload.thinking === 'object' && !Array.isArray(payload.thinking)) {{
        const currentBudget = toPositiveInt(payload.thinking.budget_tokens);
        if (currentBudget && currentBudget >= finalMaxTokens) {{
            if (finalMaxTokens <= 1) {{
                delete payload.thinking.budget_tokens;
            }} else {{
                payload.thinking.budget_tokens = finalMaxTokens - 1;
            }}
        }}
    }}

    return payload;
}}

function cloneRequestPayloadForTarget(req, target) {{
    if (!req._requestBody || typeof req._requestBody !== 'object') {{
        if (Buffer.isBuffer(req._rawBodyBuffer)) return req._rawBodyBuffer;
        return Buffer.from('');
    }}
    const payload = JSON.parse(JSON.stringify(req._requestBody));
    if (typeof req._model === 'string' && req._model.length > 0) {{
        payload.model = (target?.rewrite && target.rewrite !== req._model)
            ? target.rewrite
            : req._model;
    }}
    applyTargetParamsToPayload(payload, target);
    return Buffer.from(JSON.stringify(payload));
}}

function applySelectedTarget(req, target, decision) {{
    req._selectedTarget = target;
    req._targetInstance = target.instance;
    req._targetUrl = target.target;
    req._rewrittenModel = target.rewrite;
    req._targetProvider = target.provider || null;
    req._targetParamSummary = summarizeTargetParams(target?.params);
    if (decision) {{
        req._routingDecision = decision;
    }}
}}

function forwardRequestToTarget(req, res, target, decision) {{
    applySelectedTarget(req, target, decision);
    markTriedTarget(req, target);
    req._attemptStartedAt = Date.now();
    applyTargetHeaders(req, target);
    const forwardBody = cloneRequestPayloadForTarget(req, target);
    req.headers['content-length'] = Buffer.byteLength(forwardBody);
    proxy.web(req, res, {{
        target: target.target,
        buffer: require('stream').Readable.from([forwardBody])
    }});
}}

function pickRetryTarget(req) {{
    if (!req._model) return null;
    const route = ROUTES[req._model];
    if (!route) return null;
    const current = req._selectedTarget || null;
    const candidates = getRouteCandidates(route, req._model);
    const nextTargets = [];
    const nextWeights = [];
    for (let i = 0; i < candidates.targets.length; i++) {{
        const candidate = candidates.targets[i];
        if (current && targetIdentity(candidate) === targetIdentity(current)) {{
            continue;
        }}
        if (hasTriedTarget(req, candidate)) {{
            continue;
        }}
        nextTargets.push(candidate);
        nextWeights.push(candidates.weights[i] || 1);
    }}
    if (!nextTargets.length) return null;
    return weightedRandom(nextTargets, nextWeights);
}}

const proxy = httpProxy.createProxyServer({{
    xfwd: true,
    ws: true,
    proxyTimeout: 300000,
    selfHandleResponse: true
}});

proxy.on('error', (err, req, res) => {{
    console.error('Proxy error:', err.message);
    if (res && res.writeHead && !res.headersSent) {{
        res.writeHead(502, {{ 'Content-Type': 'application/json' }});
        res.end(JSON.stringify({{ error: 'Proxy Error', details: err.message }}));
    }}
}});

proxy.on('proxyRes', (proxyRes, req, res) => {{
    const startTime = req._startTime || Date.now();
    const chunks = [];
    const contentType = proxyRes.headers['content-type'] || '';
    const isSSE = contentType.includes('text/event-stream');
    if (isSSE) {{
        // SSE 需要尽快透传，避免客户端超时
        res.writeHead(proxyRes.statusCode, proxyRes.headers);
    }}

    proxyRes.on('data', (chunk) => {{
        chunks.push(chunk);
        if (isSSE) {{
            res.write(chunk);
        }}
    }});

    proxyRes.on('end', () => {{
        const duration = Date.now() - startTime;
        const rawResponseBodyBuffer = Buffer.concat(chunks);
        const decodedResponse = decodeResponseBody(rawResponseBodyBuffer, proxyRes.headers);
        const responseBody = decodedResponse.bodyText;
        const responsePreview = responseBody.length > RESPONSE_PREVIEW_LIMIT
            ? responseBody.slice(0, RESPONSE_PREVIEW_LIMIT) + '...[truncated]'
            : responseBody;
        const attemptStartedAt = req._attemptStartedAt || startTime;
        const attemptDuration = Date.now() - attemptStartedAt;

        // 提取 token 使用信息
        let usage = null;
        const routeAction = classifyResponse(
            proxyRes.statusCode || 0,
            contentType,
            responseBody,
            req._hasThinkingSignature || false
        );
        req._routeAction = routeAction.kind;
        req._cooldownMsApplied = 0;
        req._stickyAction = 'none';

        if (req._stickyKey) {{
            if (routeAction.kind === 'success') {{
                if (req._selectedTarget) {{
                    setStickyTarget(req._stickyKey, req._selectedTarget);
                    req._stickyAction = 'set_on_success';
                }}
            }} else if (routeAction.clearSticky) {{
                clearStickyTarget(req._stickyKey);
                req._stickyAction = 'clear_on_error';
            }}
        }}
        if (routeAction.cooldownMs > 0 && req._selectedTarget && req._model) {{
            setTargetCooldown(req._model, req._selectedTarget, routeAction.cooldownMs);
            req._cooldownMsApplied = routeAction.cooldownMs;
        }}

        const canRetry = !isSSE &&
            req.method === 'POST' &&
            (req._retryCount || 0) < MAX_TARGET_RETRIES &&
            RETRYABLE_ROUTE_ACTIONS.has(routeAction.kind) &&
            (routeAction.kind !== 'auth' ||
                proxyRes.statusCode === 401 ||
                proxyRes.statusCode === 403 ||
                (RETRY_AUTH_ON_5XX && (proxyRes.statusCode || 0) >= 500)) &&
            !res.headersSent;
        if (canRetry) {{
            const retryTarget = pickRetryTarget(req);
            if (retryTarget) {{
                if (!Array.isArray(req._retryTrace)) {{
                    req._retryTrace = [];
                }}
                req._retryTrace.push({{
                    from_instance: req._targetInstance || null,
                    from_url: req._targetUrl || null,
                    from_model: req._rewrittenModel || req._model || null,
                    status_code: proxyRes.statusCode || 0,
                    route_action: routeAction.kind,
                    attempt_duration_ms: attemptDuration,
                    body_preview: responsePreview
                }});
                req._retryCount = (req._retryCount || 0) + 1;
                req._routingDecision = `retry_on_${{routeAction.kind}}`;
                forwardRequestToTarget(req, res, retryTarget, req._routingDecision);
                return;
            }}
        }}

        req._modelHealth = updateModelHealth(
            req._modelHealthKey || null,
            routeAction.kind === 'success'
        );

        if (contentType.includes('text/event-stream')) {{
            usage = extractUsageFromSSE(responseBody);
        }} else if (contentType.includes('application/json')) {{
            usage = extractUsageFromJSON(responseBody);
        }}

        if (isSSE) {{
            res.end();
        }} else {{
            const clientBody = maybeNormalizeJsonErrorBody(contentType, responseBody);
            const responseHeaders = {{ ...proxyRes.headers }};
            delete responseHeaders['content-length'];
            delete responseHeaders['Content-Length'];
            if (decodedResponse.decodedFromEncoding) {{
                delete responseHeaders['content-encoding'];
                delete responseHeaders['Content-Encoding'];
            }}
            if (!res.headersSent) {{
                res.writeHead(proxyRes.statusCode, responseHeaders);
            }}
            res.end(clientBody);
        }}

        // 构建日志条目
        const logEntry = {{
            timestamp: new Date().toISOString(),
            duration_ms: duration,
            request: {{
                method: req.method,
                url: req.url,
                headers: sanitizeHeaders(req.headers),
                body: LOG_VERBOSE ? (req._requestBody || null) : summarizeRequestBody(req._requestBody),
                model: req._requestedModel || req._model || null
            }},
            routing: {{
                requested_model: req._requestedModel || null,
                source_model: req._sourceModel || null,
                target_instance: req._targetInstance || null,
                target_url: req._targetUrl || null,
                rewritten_model: req._rewrittenModel || null,
                provider: req._targetProvider || null,
                target_params: req._targetParamSummary || null,
                auto_upgrade: req._autoUpgrade || null,
                model_health: req._modelHealth || null,
                decision: req._routingDecision || null,
                session_key_hash: req._sessionKeyHash || null,
                has_thinking_signature: req._hasThinkingSignature || false,
                sticky_action: req._stickyAction || 'none',
                retry_count: req._retryCount || 0,
                tried_targets: req._triedTargets ? [...req._triedTargets] : [],
                retry_attempts: Array.isArray(req._retryTrace) ? req._retryTrace.length : 0,
                retry_trace: Array.isArray(req._retryTrace) ? req._retryTrace : []
            }},
            response: {{
                status_code: proxyRes.statusCode,
                headers: proxyRes.headers,
                body_length: rawResponseBodyBuffer.length,
                body_preview: responsePreview,
                route_action: req._routeAction || null,
                cooldown_ms_applied: req._cooldownMsApplied || 0,
                decoded_content_encoding: decodedResponse.decodedFromEncoding || null,
                decode_error: decodedResponse.decodeError || null
            }},
            usage: usage
        }};

        writeLog(logEntry);
    }});
}});

function weightedRandom(targets, weights) {{
    let totalWeight = 0;
    for (let i = 0; i < weights.length; i++) totalWeight += weights[i];
    let random = Math.random() * totalWeight;
    for (let i = 0; i < weights.length; i++) {{
        if (random < weights[i]) return targets[i];
        random -= weights[i];
    }}
    return targets[0];
}}

function selectHighestWeightTarget(targets, weights) {{
    if (!targets.length) return null;
    let bestIdx = 0;
    for (let i = 1; i < weights.length; i++) {{
        if (weights[i] > weights[bestIdx]) bestIdx = i;
    }}
    return targets[bestIdx];
}}

function hashSessionKey(sessionKey) {{
    if (!sessionKey) return null;
    return crypto.createHash('sha1').update(sessionKey).digest('hex').slice(0, 12);
}}

function getSessionKey(req, body) {{
    if (body?.metadata?.user_id && typeof body.metadata.user_id === 'string') {{
        const userId = body.metadata.user_id.trim();
        if (userId) return `metadata:${{userId}}`;
    }}
    const headerCandidates = ['x-session-id', 'x-conversation-id', 'anthropic-conversation-id'];
    for (const header of headerCandidates) {{
        const value = req.headers[header];
        if (typeof value === 'string' && value.trim()) {{
            return `${{header}}:${{value.trim()}}`;
        }}
    }}
    return null;
}}

function hasThinkingSignature(body) {{
    if (!body || !Array.isArray(body.messages)) return false;
    for (const message of body.messages) {{
        if (!Array.isArray(message.content)) continue;
        for (const block of message.content) {{
            if (block?.type === 'thinking' && typeof block.signature === 'string' && block.signature.length > 0) {{
                return true;
            }}
        }}
    }}
    return false;
}}

function modelHealthKey(sessionKeyHash, sourceModel) {{
    if (!sourceModel) return null;
    return `${{sessionKeyHash || 'anon'}}::${{sourceModel}}`;
}}

function getModelHealth(key) {{
    if (!key) return {{ failureStreak: 0, successStreak: 0, updatedAt: Date.now() }};
    const current = modelHealth.get(key);
    if (!current) return {{ failureStreak: 0, successStreak: 0, updatedAt: Date.now() }};
    return current;
}}

function updateModelHealth(key, isSuccess) {{
    if (!key) return null;
    const current = getModelHealth(key);
    const next = {{
        failureStreak: isSuccess ? 0 : (current.failureStreak || 0) + 1,
        successStreak: isSuccess ? (current.successStreak || 0) + 1 : 0,
        updatedAt: Date.now()
    }};
    modelHealth.set(key, next);
    return next;
}}

function resolveAutoUpgradeModel(requestModel, body, sessionKeyHash) {{
    if (!AUTO_UPGRADE_ENABLED) return null;
    const targetModel = AUTO_UPGRADE_MODEL_MAP[requestModel];
    if (typeof targetModel !== 'string' || targetModel.length === 0) return null;
    if (!ROUTES[targetModel]) return null;

    const messagesCount = Array.isArray(body?.messages) ? body.messages.length : 0;
    const toolsCount = Array.isArray(body?.tools) ? body.tools.length : 0;
    const hasSignature = hasThinkingSignature(body);
    const health = getModelHealth(modelHealthKey(sessionKeyHash, requestModel));
    const failureStreak = health.failureStreak || 0;
    const reasons = [];

    if (messagesCount >= AUTO_UPGRADE_MESSAGES_THRESHOLD) reasons.push('messages_threshold');
    if (toolsCount >= AUTO_UPGRADE_TOOLS_THRESHOLD) reasons.push('tools_threshold');
    if (failureStreak >= AUTO_UPGRADE_FAILURE_STREAK_THRESHOLD) reasons.push('failure_streak');
    if (AUTO_UPGRADE_SIGNATURE_ENABLED && hasSignature) reasons.push('thinking_signature');

    if (!reasons.length) return null;
    return {{
        sourceModel: requestModel,
        targetModel,
        reasons,
        messagesCount,
        toolsCount,
        failureStreak
    }};
}}

function stickyRouteKey(sessionKey, model) {{
    return `${{sessionKey}}::${{model}}`;
}}

function targetCooldownKey(model, target) {{
    return `${{model}}::${{target.instance}}::${{target.target}}::${{target.rewrite}}`;
}}

function clearStickyTarget(key) {{
    if (!key) return;
    stickyRoutes.delete(key);
}}

function setTargetCooldown(model, target, cooldownMs) {{
    if (!model || !target || cooldownMs <= 0) return;
    targetCooldowns.set(targetCooldownKey(model, target), {{
        expiresAt: Date.now() + cooldownMs
    }});
}}

function isTargetCooling(model, target) {{
    const key = targetCooldownKey(model, target);
    const entry = targetCooldowns.get(key);
    if (!entry) return false;
    if (entry.expiresAt <= Date.now()) {{
        targetCooldowns.delete(key);
        return false;
    }}
    return true;
}}

function getRouteCandidates(route, model) {{
    const routeWeights = Array.isArray(route.weights) ? route.weights : [];
    const targets = [];
    const weights = [];
    for (let i = 0; i < route.targets.length; i++) {{
        const target = route.targets[i];
        if (isTargetCooling(model, target)) continue;
        targets.push(target);
        weights.push(routeWeights[i] || 1);
    }}
    if (targets.length === 0) {{
        return {{
            targets: route.targets,
            weights: route.targets.map((_, idx) => routeWeights[idx] || 1),
            cooledOut: true
        }};
    }}
    return {{ targets, weights, cooledOut: false }};
}}

function getStickyTarget(route, key, model, options = {{}}) {{
    const ignoreCooldown = options.ignoreCooldown === true;
    const entry = stickyRoutes.get(key);
    if (!entry) return null;
    if (entry.expiresAt <= Date.now()) {{
        stickyRoutes.delete(key);
        return null;
    }}
    const matched = route.targets.find((target) =>
        target.instance === entry.instance &&
        target.target === entry.target &&
        target.rewrite === entry.rewrite
    );
    if (!matched) {{
        stickyRoutes.delete(key);
        return null;
    }}
    if (!ignoreCooldown && isTargetCooling(model, matched)) {{
        stickyRoutes.delete(key);
        return null;
    }}
    entry.expiresAt = Date.now() + STICKY_ROUTE_TTL_MS;
    stickyRoutes.set(key, entry);
    return matched;
}}

function setStickyTarget(key, target) {{
    if (stickyRoutes.size >= MAX_STICKY_KEYS && !stickyRoutes.has(key)) {{
        const entries = [...stickyRoutes.entries()].sort((a, b) => a[1].expiresAt - b[1].expiresAt);
        const evictCount = Math.ceil(MAX_STICKY_KEYS * 0.2);
        for (let i = 0; i < evictCount && i < entries.length; i++) {{
            stickyRoutes.delete(entries[i][0]);
        }}
    }}
    stickyRoutes.set(key, {{
        instance: target.instance,
        target: target.target,
        rewrite: target.rewrite,
        expiresAt: Date.now() + STICKY_ROUTE_TTL_MS
    }});
}}

function normalizeProxyPath(urlPath) {{
    if (!urlPath) return urlPath;
    if (urlPath === '/v1/v1') return '/v1';
    if (urlPath.startsWith('/v1/v1/')) return '/v1' + urlPath.slice('/v1/v1'.length);
    return urlPath;
}}

const server = http.createServer((req, res) => {{
    req._startTime = Date.now();
    req.url = normalizeProxyPath(req.url);

    if (req.method === 'POST') {{
        let body = [];
        req.on('data', chunk => body.push(chunk)).on('end', () => {{
            const rawBody = Buffer.concat(body);
            const bodyStr = rawBody.toString();
            let jsonBody, model;

            try {{
                jsonBody = JSON.parse(bodyStr);
                model = jsonBody.model;
            }} catch (e) {{
                req._requestBody = bodyStr;
                proxy.web(req, res, {{ target: DEFAULT_TARGET, buffer: require('stream').Readable.from([rawBody]) }});
                return;
            }}

            req._requestBody = jsonBody;
            req._rawBodyBuffer = rawBody;
            req._baseHeaders = {{ ...req.headers }};
            req._requestedModel = model;
            req._sourceModel = model;
            req._model = model;
            req._hasThinkingSignature = hasThinkingSignature(jsonBody);
            req._retryCount = 0;
            req._triedTargets = new Set();
            req._retryTrace = [];
            req._targetParamSummary = null;
            req._autoUpgrade = null;
            const sessionKey = getSessionKey(req, jsonBody);
            req._sessionKeyHash = hashSessionKey(sessionKey);
            req._modelHealthKey = modelHealthKey(req._sessionKeyHash, req._sourceModel);

            const autoUpgrade = resolveAutoUpgradeModel(model, jsonBody, req._sessionKeyHash);
            if (autoUpgrade) {{
                model = autoUpgrade.targetModel;
                req._model = model;
                req._autoUpgrade = autoUpgrade;
            }}

            const route = ROUTES[model];
            if (!route) {{
                req._targetUrl = DEFAULT_TARGET;
                req._routingDecision = 'default_target';
                proxy.web(req, res, {{ target: DEFAULT_TARGET, buffer: require('stream').Readable.from([rawBody]) }});
                return;
            }}

            let selected = null;
            let routingDecision = 'weighted_random';
            if (req._autoUpgrade) {{
                routingDecision = `auto_upgrade_${{req._autoUpgrade.sourceModel}}_to_${{req._autoUpgrade.targetModel}}`;
            }}

            if (req._hasThinkingSignature) {{
                if (sessionKey) {{
                    const key = stickyRouteKey(sessionKey, model);
                    req._stickyKey = key;
                    // Thinking 会话必须保持同链路，避免 signature 跨后端失效。
                    selected = getStickyTarget(route, key, model, {{ ignoreCooldown: true }});
                    if (selected) {{
                        routingDecision = 'sticky_session_model_thinking_locked';
                    }} else {{
                        const candidates = getRouteCandidates(route, model);
                        selected = selectHighestWeightTarget(candidates.targets, candidates.weights);
                        routingDecision = candidates.cooledOut
                            ? 'thinking_primary_locked_all_targets_in_cooldown'
                            : 'thinking_primary_locked';
                    }}
                }} else {{
                    const candidates = getRouteCandidates(route, model);
                    selected = selectHighestWeightTarget(candidates.targets, candidates.weights);
                    routingDecision = candidates.cooledOut
                        ? 'thinking_primary_locked_no_session_all_targets_in_cooldown'
                        : 'thinking_primary_locked_no_session';
                }}
            }} else {{
                if (sessionKey) {{
                    const key = stickyRouteKey(sessionKey, model);
                    req._stickyKey = key;
                    selected = getStickyTarget(route, key, model);
                    if (selected) {{
                        routingDecision = 'sticky_session_model';
                    }} else {{
                        const candidates = getRouteCandidates(route, model);
                        selected = weightedRandom(candidates.targets, candidates.weights);
                        routingDecision = candidates.cooledOut ? 'weighted_random_all_targets_in_cooldown' : 'weighted_random';
                    }}
                }} else {{
                    const candidates = getRouteCandidates(route, model);
                    selected = weightedRandom(candidates.targets, candidates.weights);
                    routingDecision = candidates.cooledOut ? 'weighted_random_no_session_all_targets_in_cooldown' : 'weighted_random';
                }}
            }}

            if (!selected) {{
                req._targetUrl = DEFAULT_TARGET;
                req._routingDecision = 'default_target_no_selected';
                proxy.web(req, res, {{ target: DEFAULT_TARGET, buffer: require('stream').Readable.from([rawBody]) }});
                return;
            }}

            forwardRequestToTarget(req, res, selected, routingDecision);
        }});
    }} else {{
        proxy.web(req, res, {{ target: DEFAULT_TARGET }});
    }}
}});

server.on('upgrade', (req, socket, head) => {{
    req.url = normalizeProxyPath(req.url);
    proxy.ws(req, socket, head, {{ target: DEFAULT_TARGET }});
}});

console.log(`🚀 Node.js Smart LB running on port ${{PORT}}`);
console.log(`📁 Request logs: ${{LOG_DIR}}`);
console.log(`🗑️ Log retention: ${{LOG_RETENTION_DAYS}} days`);
server.listen(PORT);
"""
    with open(path, "w") as f:
        f.write(script_content)
    print(f"✅ 生成 Node.js LB 脚本 (含日志): {path}")

def validate_config(instances, routing):
    """校验配置有效性"""
    errors = []
    warnings = []

    # 1. 收集所有定义的 Instance 和 Provider Types
    # 结构: { "zenmux": ["openai", "anthropic", "gemini"] }
    defined_providers = {}
    for inst_name, inst_conf in instances.items():
        types = set()
        for p in inst_conf.get("providers", []):
            types.add(p["type"])
        defined_providers[inst_name] = types

    # 2. 检查 Routing 规则
    for expose_id, targets in routing.items():
        for target in targets:
            inst = target.get("instance")
            prov_type = target.get("provider")

            # 检查 Instance 是否存在
            if inst not in defined_providers:
                errors.append(f"❌ 路由错误: 模型 '{expose_id}' 引用了不存在的实例 '{inst}'")
                continue

            # 检查 Provider 类型是否匹配
            # 注意: 如果 routing 里没写 provider (旧写法)，我们只能跳过检查或给警告
            if prov_type:
                if prov_type not in defined_providers[inst]:
                    # 尝试模糊匹配建议 (比如 openai vs codex)
                    avail = list(defined_providers[inst])
                    errors.append(f"❌ 路由错误: 实例 '{inst}' 中没有类型为 '{prov_type}' 的 Provider (可用: {avail})")
            else:
                warnings.append(f"⚠️  建议: 模型 '{expose_id}' -> '{inst}' 未指定 provider 类型，可能导致挂载失败")

    # 3. 输出结果
    for w in warnings: print(w)

    if errors:
        print("\n🚫 配置校验失败，请修复以下错误:")
        for e in errors: print(e)
        sys.exit(1)

    print("✅ 配置校验通过")

def main():
    config = load_toml()
    global_conf = config["global"]
    instances = config["instances"]
    routing = config["routing"]

    # 0. 执行校验
    validate_config(instances, routing)

    ensure_dir(OUTPUT_DIR)

    pm2_apps = []
    log_dir = os.path.join(BASE_DIR, "logs")
    ensure_dir(log_dir)

    # 1. 生成物理实例配置
    for name, conf in instances.items():
        yaml_content = generate_instance_config(name, conf, routing, global_conf)
        yaml_file = os.path.join(OUTPUT_DIR, f"{name}.yaml")

        with open(yaml_file, "w") as f:
            yaml.dump(yaml_content, f, sort_keys=False)
        print(f"✅ 生成实例配置 ({name}): {yaml_file}")

        pm2_apps.append({
            "name": f"cliproxy-{name}",
            "script": os.path.join(BASE_DIR, "cliproxy"),
            "args": f"-config {yaml_file}",
            "autorestart": True,
            "out_file": os.path.join(log_dir, f"{name}-out.log"),
            "error_file": os.path.join(log_dir, f"{name}-error.log"),
            "merge_logs": True,
            "log_date_format": "YYYY-MM-DD HH:mm:ss"
        })

    # 2. 生成主入口 LB (Node.js)
    lb_script = os.path.join(BASE_DIR, "lb.js")
    create_node_lb_script(lb_script, routing, instances, global_conf["main_port"], global_conf)

    # 确保 Node 依赖已安装
    ensure_node_deps(BASE_DIR)

    pm2_apps.append({
        "name": "cliproxy-main",
        "script": "lb.js",
        "cwd": BASE_DIR,
        "autorestart": True,
        "out_file": os.path.join(log_dir, "lb-access.log"),
        "error_file": os.path.join(log_dir, "lb-error.log"),
        "merge_logs": True
    })

    # 3. 写入 PM2 配置
    # 注意: Python 的 True/False 需要转换为 JS 的 true/false
    pm2_config_str = json.dumps(pm2_apps, indent=2)

    with open(PM2_FILE, "w") as f:
        f.write(f"module.exports = {{\n  apps: {pm2_config_str}\n}};\n")
    print(f"✅ 生成 PM2 配置: {PM2_FILE}")

if __name__ == "__main__":
    main()
