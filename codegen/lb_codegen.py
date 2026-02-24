"""LB script generator for cliproxy routing."""

import json
import os
import re

import toml


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


def _substitute_env(data):
    if isinstance(data, dict):
        return {k: _substitute_env(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_substitute_env(v) for v in data]
    if isinstance(data, str):
        return re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), data)
    return data


def _deep_merge_dict(base, override):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_model_router_config_file(lb_script_path, config_file):
    if not isinstance(config_file, str) or not config_file.strip():
        return {}

    config_path = config_file.strip()
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(os.path.abspath(lb_script_path)), config_path)

    if not os.path.exists(config_path):
        print(f"⚠️  lb_model_router config_file 不存在，已忽略: {config_path}")
        return {}

    loaded = _substitute_env(toml.load(config_path))
    if isinstance(loaded.get("lb_model_router"), dict):
        return loaded["lb_model_router"]
    global_conf = loaded.get("global")
    if isinstance(global_conf, dict) and isinstance(global_conf.get("lb_model_router"), dict):
        return global_conf.get("lb_model_router", {})
    if isinstance(loaded, dict):
        return loaded
    return {}


def _normalize_lb_model_router_config(lb_script_path, global_conf):
    base_conf = global_conf.get("lb_model_router")
    if not isinstance(base_conf, dict):
        base_conf = {}
    else:
        base_conf = dict(base_conf)

    config_file = base_conf.get("config_file")
    if isinstance(config_file, str) and config_file.strip():
        file_conf = _load_model_router_config_file(lb_script_path, config_file)
        if isinstance(file_conf, dict) and file_conf:
            base_conf = _deep_merge_dict(base_conf, file_conf)

    supported_ops = {
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "in",
        "not_in",
        "contains",
        "not_contains",
        "exists",
        "not_exists",
        "regex",
    }

    activation_models = []
    raw_activation_models = base_conf.get("activation_models", ["auto"])
    if isinstance(raw_activation_models, list):
        for raw_model in raw_activation_models:
            model = str(raw_model).strip()
            if model and model not in activation_models:
                activation_models.append(model)

    default_model = base_conf.get("default_model")
    if default_model is not None:
        default_model = str(default_model).strip() or None

    raw_rules = base_conf.get("rules", [])
    normalized_rules = []
    if isinstance(raw_rules, list):
        for idx, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                continue
            target_model = str(raw_rule.get("target_model", "")).strip()
            if not target_model:
                continue

            try:
                priority = coerce_int(raw_rule.get("priority", 0))
            except Exception:
                priority = 0

            match_mode = str(raw_rule.get("match", "all")).strip().lower()
            if match_mode not in {"all", "any"}:
                match_mode = "all"

            name = str(raw_rule.get("name", f"rule_{idx + 1}")).strip() or f"rule_{idx + 1}"

            conditions = []
            raw_conditions = raw_rule.get("when", [])
            if isinstance(raw_conditions, list):
                for cond in raw_conditions:
                    if not isinstance(cond, dict):
                        continue
                    field = str(cond.get("field", "")).strip()
                    if not field:
                        continue
                    op = str(cond.get("op", "==")).strip().lower()
                    if op not in supported_ops:
                        continue
                    conditions.append(
                        {
                            "field": field,
                            "op": op,
                            "value": cond.get("value"),
                        }
                    )

            normalized_rules.append(
                {
                    "name": name,
                    "priority": priority,
                    "target_model": target_model,
                    "match": match_mode,
                    "when": conditions,
                }
            )

    normalized_rules.sort(key=lambda item: item.get("priority", 0), reverse=True)

    enabled = coerce_bool(base_conf.get("enabled", False))
    shadow_only = coerce_bool(base_conf.get("shadow_only", False))
    log_factors = coerce_bool(base_conf.get("log_factors", True))

    raw_categories = base_conf.get("categories", [])
    normalized_categories = []
    if isinstance(raw_categories, list):
        for idx, raw_cat in enumerate(raw_categories):
            if not isinstance(raw_cat, dict):
                continue
            cat_name = str(raw_cat.get("name", f"cat_{idx + 1}")).strip()
            target_model = str(raw_cat.get("target_model", "")).strip()
            if not target_model:
                continue
            try:
                priority = coerce_int(raw_cat.get("priority", 0))
            except Exception:
                priority = 0
            raw_signals = raw_cat.get("signals", [])
            signals = []
            if isinstance(raw_signals, list):
                for sig in raw_signals:
                    s = str(sig).strip()
                    if s:
                        signals.append(s)
            if not signals:
                continue
            normalized_categories.append({
                "name": cat_name,
                "priority": priority,
                "target_model": target_model,
                "signals": signals,
            })
    normalized_categories.sort(key=lambda item: item.get("priority", 0), reverse=True)

    normalized = {
        "enabled": enabled,
        "shadow_only": shadow_only,
        "log_factors": log_factors,
        "activation_models": activation_models,
        "default_model": default_model,
        "categories": normalized_categories,
        "rules": normalized_rules,
    }
    if isinstance(config_file, str) and config_file.strip():
        normalized["config_file"] = config_file.strip()
    return normalized

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
    model_router_conf = _normalize_lb_model_router_config(path, global_conf)

    script_content = f"""
const http = require('http');
const httpProxy = require('http-proxy');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');

const PORT = {port};
const ROUTES = {json.dumps(routes, indent=2)};

// 从 rewrite 字段推断 model_group（与 antigravity GetModelGroup 保持一致）
function getModelGroup(rewrite) {{
    if (!rewrite) return '';
    if (rewrite.includes('gpt')) return 'gpt';
    if (rewrite.includes('claude')) return 'claude';
    if (rewrite.includes('gemini')) return 'gemini';
    return rewrite;
}}
// signature group → 可处理该组 signature 的 route model 列表
const SIGNATURE_GROUP_ROUTES = {{}};
for (const [modelName, route] of Object.entries(ROUTES)) {{
    for (const target of route.targets) {{
        const group = getModelGroup(target.rewrite || '');
        if (!group) continue;
        if (!SIGNATURE_GROUP_ROUTES[group]) SIGNATURE_GROUP_ROUTES[group] = [];
        if (!SIGNATURE_GROUP_ROUTES[group].includes(modelName)) {{
            SIGNATURE_GROUP_ROUTES[group].push(modelName);
        }}
    }}
}}

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
const MODEL_ROUTER_CONFIG = {json.dumps(model_router_conf, indent=2)};
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

function normalizeScalar(value) {{
    if (typeof value === 'string') {{
        const trimmed = value.trim();
        if (trimmed.length === 0) return '';
        const lower = trimmed.toLowerCase();
        if (lower === 'true') return true;
        if (lower === 'false') return false;
        if (/^-?\\d+(\\.\\d+)?$/.test(trimmed)) {{
            const num = Number(trimmed);
            if (Number.isFinite(num)) return num;
        }}
        return trimmed;
    }}
    return value;
}}

function toFiniteNumber(value) {{
    const normalized = normalizeScalar(value);
    if (typeof normalized === 'number' && Number.isFinite(normalized)) return normalized;
    return null;
}}

function scalarEquals(left, right) {{
    return normalizeScalar(left) === normalizeScalar(right);
}}

function extractMessageTextLength(content) {{
    if (typeof content === 'string') return content.length;
    if (!Array.isArray(content)) return 0;
    let total = 0;
    for (const block of content) {{
        if (typeof block === 'string') {{
            total += block.length;
            continue;
        }}
        if (!block || typeof block !== 'object') continue;
        if (typeof block.text === 'string') total += block.text.length;
        if (typeof block.input_text === 'string') total += block.input_text.length;
    }}
    return total;
}}

function estimatePromptChars(body) {{
    if (!body || typeof body !== 'object') return 0;
    let total = 0;
    if (typeof body.system === 'string') total += body.system.length;
    if (Array.isArray(body.system)) {{
        for (const item of body.system) {{
            if (typeof item === 'string') total += item.length;
            if (item && typeof item === 'object' && typeof item.text === 'string') total += item.text.length;
        }}
    }}
    if (Array.isArray(body.messages)) {{
        for (const message of body.messages) {{
            if (!message || typeof message !== 'object') continue;
            total += extractMessageTextLength(message.content);
        }}
    }}
    return total;
}}

function hasSystemPrompt(body) {{
    if (!body || typeof body !== 'object') return false;
    if (typeof body.system === 'string' && body.system.trim().length > 0) return true;
    if (Array.isArray(body.system) && body.system.length > 0) return true;
    if (!Array.isArray(body.messages)) return false;
    for (const message of body.messages) {{
        if (message?.role === 'system') return true;
    }}
    return false;
}}

function extractLastUserMessageText(body) {{
    if (!body || !Array.isArray(body.messages)) return '';
    for (let i = body.messages.length - 1; i >= 0; i--) {{
        const msg = body.messages[i];
        if (!msg || msg.role !== 'user') continue;
        const content = msg.content;
        if (typeof content === 'string') return content.slice(0, 2000);
        if (Array.isArray(content)) {{
            let text = '';
            for (const block of content) {{
                if (typeof block === 'string') {{ text += block; }}
                else if (block && typeof block === 'object') {{
                    if (typeof block.text === 'string') text += block.text;
                    if (typeof block.input_text === 'string') text += block.input_text;
                }}
                if (text.length >= 2000) break;
            }}
            return text.slice(0, 2000);
        }}
        return '';
    }}
    return '';
}}

function classifyToolProfile(body) {{
    if (!body || !Array.isArray(body.tools) || body.tools.length === 0) return 'none';
    const names = new Set();
    for (const tool of body.tools) {{
        const nameCandidates = [
            tool?.function?.name,
            tool?.name,
            tool?.type
        ];
        for (const candidate of nameCandidates) {{
            if (typeof candidate !== 'string') continue;
            const normalized = candidate.trim().toLowerCase();
            if (normalized) names.add(normalized);
        }}
    }}
    if (names.size === 0) return 'none';
    const hasNameLike = (patterns) => {{
        const arr = Array.isArray(patterns) ? patterns : [patterns];
        for (const name of names) {{
            for (const pattern of arr) {{
                if (pattern instanceof RegExp) {{
                    if (pattern.test(name)) return true;
                }} else if (typeof pattern === 'string') {{
                    if (name === pattern) return true;
                }}
            }}
        }}
        return false;
    }};
    const hasCoding = hasNameLike([
        /^edit$/, /^write$/, /^notebookedit$/, /^apply_patch$/, /update/, /create/, /insert/, /replace/, /code/
    ]);
    const hasRead = hasNameLike([
        /^read$/, /^glob$/, /^grep$/, /^find$/, /^search/, /list/, /query/, /fetch/
    ]);
    const hasExplore = hasNameLike([
        /^task$/, /^websearch$/, /^webfetch$/, /browse/, /crawl/, /research/
    ]);
    const hasOps = hasNameLike([
        /^bash$/, /^shell$/, /^terminal$/, /^exec_command$/, /^write_stdin$/, /git/, /deploy/, /pm2/
    ]);
    const categories = [];
    if (hasCoding) categories.push('coding');
    if (hasRead && !hasCoding) categories.push('read');
    if (hasExplore) categories.push('explore');
    if (hasOps) categories.push('ops');
    if (categories.length > 1) return 'multi';
    if (categories.length === 1) return categories[0];
    return 'none';
}}

function classifyTaskCategory(body) {{
    const text = extractLastUserMessageText(body);
    if (typeof text !== 'string' || !text.trim()) return 'unknown';
    const normalized = text.toLowerCase();
    const patterns = [
        ['architecture', /(architect|architecture|system\\s*design|scalability|technical\\s*design|架构|系统设计|可扩展)/i],
        ['code-review', /(review|audit|refactor|rewrite|debug|root\\s*cause|排查|根因|代码审查|重构)/i],
        ['visual-coding', /(frontend|ui|css|tailwind|responsive|animation|visual|前端|界面|样式|动画|视觉)/i],
        ['coding', /(implement|write|fix|add|create|modify|code|bug|patch|script|函数|代码|修复|实现)/i],
        ['explore', /(find|search|where|explain|what|how|lookup|research|trace|inspect|查找|搜索|解释|什么|如何)/i],
        ['ops', /(deploy|restart|build|test|run|release|ci\\/?cd|运维|部署|发布|重启|构建)/i]
    ];
    for (const [category, regex] of patterns) {{
        if (regex.test(normalized)) return category;
    }}
    const quick = normalized.trim();
    if (/^(hi|hello|thanks|ok|hey|你好|谢谢|收到)$/.test(quick)) return 'quick';
    return 'unknown';
}}

function detectCodeContext(body) {{
    if (!body || !Array.isArray(body.messages)) return false;
    const codePattern = /```|import\\s+|require\\s*\\(|from\\s+\\S+\\s+import|class\\s+\\w+|function\\s+\\w+|def\\s+\\w+/;
    const start = Math.max(0, body.messages.length - 5);
    for (let i = start; i < body.messages.length; i++) {{
        const msg = body.messages[i];
        if (!msg) continue;
        const content = msg.content;
        let text = '';
        if (typeof content === 'string') {{ text = content; }}
        else if (Array.isArray(content)) {{
            for (const block of content) {{
                if (typeof block === 'string') text += block;
                else if (block && typeof block === 'object') {{
                    if (typeof block.text === 'string') text += block.text;
                    if (typeof block.input_text === 'string') text += block.input_text;
                }}
            }}
        }}
        if (text && codePattern.test(text)) return true;
    }}
    return false;
}}

function classifySystemPromptType(body) {{
    if (!body || typeof body !== 'object') return [];
    const tags = [];
    let systemText = '';
    if (typeof body.system === 'string') {{ systemText = body.system; }}
    else if (Array.isArray(body.system)) {{
        for (const item of body.system) {{
            if (typeof item === 'string') systemText += item;
            else if (item && typeof item.text === 'string') systemText += item.text;
        }}
    }}
    if (Array.isArray(body.messages)) {{
        for (const msg of body.messages) {{
            if (msg?.role === 'system') {{
                const c = msg.content;
                if (typeof c === 'string') systemText += c;
            }}
        }}
    }}
    if (!systemText) return tags;
    const lower = systemText.toLowerCase();
    if (lower.includes('plan mode') || lower.includes('plan_mode') || lower.includes('enterplanmode')) tags.push('plan_mode');
    if (lower.includes('review') || lower.includes('audit') || lower.includes('code review')) tags.push('review');
    if (systemText.length > 5000) tags.push('long');
    if (systemText.length <= 500) tags.push('short');
    return tags;
}}

function buildModelRouterFactors(requestedModel, body, sessionKeyHash) {{
    const messagesCount = Array.isArray(body?.messages) ? body.messages.length : 0;
    const toolsCount = Array.isArray(body?.tools) ? body.tools.length : 0;
    const requested = typeof requestedModel === 'string' ? requestedModel : '';
    const health = getModelHealth(modelHealthKey(sessionKeyHash, requested));
    const systemPromptType = classifySystemPromptType(body);
    return {{
        requested_model: requested || null,
        messages_count: messagesCount,
        conversation_depth: messagesCount,
        tools_count: toolsCount,
        has_thinking_signature: hasThinkingSignature(body),
        has_system_prompt: hasSystemPrompt(body),
        prompt_chars: estimatePromptChars(body),
        failure_streak: health.failureStreak || 0,
        success_streak: health.successStreak || 0,
        last_user_text: extractLastUserMessageText(body),
        task_category: classifyTaskCategory(body),
        tool_profile: classifyToolProfile(body),
        has_code_context: detectCodeContext(body),
        system_prompt_type: systemPromptType,
        system_prompt_tags: systemPromptType
    }};
}}

function evaluateModelRouterCondition(condition, factors) {{
    const field = String(condition?.field || '').trim();
    const op = String(condition?.op || '==').trim().toLowerCase();
    const expected = condition?.value;
    const actual = factors[field];
    const hasField = Object.prototype.hasOwnProperty.call(factors, field);
    let matched = false;
    let reason = null;

    if (!field) {{
        return {{ field, op, expected, actual: null, matched: false, reason: 'missing_field_name' }};
    }}

    switch (op) {{
        case 'exists':
            matched = hasField && actual != null;
            break;
        case 'not_exists':
            matched = !hasField || actual == null;
            break;
        case '==':
            matched = hasField && scalarEquals(actual, expected);
            break;
        case '!=':
            matched = !hasField || !scalarEquals(actual, expected);
            break;
        case '>':
        case '>=':
        case '<':
        case '<=': {{
            const left = toFiniteNumber(actual);
            const right = toFiniteNumber(expected);
            if (left == null || right == null) {{
                matched = false;
                reason = 'non_numeric_compare';
                break;
            }}
            if (op === '>') matched = left > right;
            if (op === '>=') matched = left >= right;
            if (op === '<') matched = left < right;
            if (op === '<=') matched = left <= right;
            break;
        }}
        case 'in':
        case 'not_in': {{
            const values = Array.isArray(expected) ? expected : [expected];
            const exists = values.some((item) => scalarEquals(actual, item));
            matched = op === 'in' ? exists : !exists;
            break;
        }}
        case 'contains':
        case 'not_contains': {{
            let exists = false;
            if (Array.isArray(actual)) {{
                exists = actual.some((item) => scalarEquals(item, expected));
            }} else if (typeof actual === 'string') {{
                exists = actual.includes(String(expected ?? ''));
            }}
            matched = op === 'contains' ? exists : !exists;
            break;
        }}
        case 'regex': {{
            if (typeof expected !== 'string') {{
                matched = false;
                reason = 'regex_pattern_not_string';
                break;
            }}
            try {{
                const regex = new RegExp(expected);
                matched = regex.test(String(actual ?? ''));
            }} catch (error) {{
                matched = false;
                reason = 'invalid_regex';
            }}
            break;
        }}
        default:
            matched = false;
            reason = 'unsupported_op';
            break;
    }}

    return {{ field, op, expected, actual, matched, reason }};
}}

function evaluateModelRouterRule(rule, factors) {{
    const conditions = Array.isArray(rule?.when) ? rule.when : [];
    const mode = String(rule?.match || 'all').toLowerCase() === 'any' ? 'any' : 'all';

    if (conditions.length === 0) {{
        return {{
            matched: true,
            mode,
            conditions: []
        }};
    }}

    const conditionResults = conditions.map((condition) => evaluateModelRouterCondition(condition, factors));
    const matched = mode === 'any'
        ? conditionResults.some((item) => item.matched)
        : conditionResults.every((item) => item.matched);

    return {{
        matched,
        mode,
        conditions: conditionResults
    }};
}}

function evaluateCategorySignal(signal, factors) {{
    if (typeof signal !== 'string') return false;
    const colonIdx = signal.indexOf(':');
    if (colonIdx < 0) return false;
    const type = signal.slice(0, colonIdx).trim().toLowerCase();
    const value = signal.slice(colonIdx + 1).trim();
    switch (type) {{
        case 'keyword': {{
            const text = factors.last_user_text;
            if (typeof text !== 'string' || !text) return false;
            try {{
                return new RegExp(value, 'i').test(text);
            }} catch (e) {{
                return false;
            }}
        }}
        case 'task_category':
            return String(factors.task_category || '').toLowerCase() === value.toLowerCase();
        case 'tool_profile':
            return String(factors.tool_profile || '').toLowerCase() === value.toLowerCase();
        case 'has_code_context':
            return String(!!factors.has_code_context) === value.toLowerCase();
        case 'system_prompt_type':
        case 'system_tag':
            return Array.isArray(factors.system_prompt_type)
                ? factors.system_prompt_type.includes(value)
                : Array.isArray(factors.system_prompt_tags) && factors.system_prompt_tags.includes(value);
        case 'conversation_depth': {{
            const factorVal = toFiniteNumber(factors.messages_count);
            if (factorVal == null) return false;
            const opMatch = value.match(/^(<=|>=|<|>|==|!=)(\\d+(?:\\.\\d+)?)$/);
            if (!opMatch) return false;
            const op = opMatch[1];
            const threshold = Number(opMatch[2]);
            if (!Number.isFinite(threshold)) return false;
            if (op === '<=') return factorVal <= threshold;
            if (op === '>=') return factorVal >= threshold;
            if (op === '<') return factorVal < threshold;
            if (op === '>') return factorVal > threshold;
            if (op === '==') return factorVal === threshold;
            if (op === '!=') return factorVal !== threshold;
            return false;
        }}
        case 'messages_count':
        case 'prompt_chars': {{
            const factorVal = toFiniteNumber(factors[type]);
            if (factorVal == null) return false;
            const opMatch = value.match(/^(<=|>=|<|>|==|!=)(\\d+(?:\\.\\d+)?)$/);
            if (!opMatch) return false;
            const op = opMatch[1];
            const threshold = Number(opMatch[2]);
            if (!Number.isFinite(threshold)) return false;
            if (op === '<=') return factorVal <= threshold;
            if (op === '>=') return factorVal >= threshold;
            if (op === '<') return factorVal < threshold;
            if (op === '>') return factorVal > threshold;
            if (op === '==') return factorVal === threshold;
            if (op === '!=') return factorVal !== threshold;
            return false;
        }}
        default:
            return false;
    }}
}}

function resolveModelViaCategories(factors, categories) {{
    for (const cat of categories) {{
        const signals = Array.isArray(cat.signals) ? cat.signals : [];
        for (const signal of signals) {{
            if (evaluateCategorySignal(signal, factors)) {{
                return {{
                    matched: true,
                    category_name: cat.name || 'unnamed',
                    target_model: cat.target_model || '',
                    matched_signal: signal
                }};
            }}
        }}
    }}
    return {{ matched: false, category_name: null, target_model: null, matched_signal: null }};
}}

function resolveModelViaRouter(requestedModel, body, sessionKeyHash) {{
    const requested = typeof requestedModel === 'string' ? requestedModel : '';
    const config = MODEL_ROUTER_CONFIG && typeof MODEL_ROUTER_CONFIG === 'object'
        ? MODEL_ROUTER_CONFIG
        : null;

    if (!config?.enabled) {{
        return {{
            enabled: false,
            activated: false,
            shadow_only: false,
            decision: 'disabled',
            requested_model: requested || null,
            suggested_model: requested || null,
            resolved_model: requested || null,
            applied: false,
            hit_rule: null,
            factors: null,
            eval_trace: null
        }};
    }}

    const activationModels = Array.isArray(config.activation_models) ? config.activation_models : [];
    if (activationModels.length > 0 && !activationModels.includes(requested)) {{
        return {{
            enabled: true,
            activated: false,
            shadow_only: config.shadow_only === true,
            decision: 'not_activated',
            requested_model: requested || null,
            suggested_model: requested || null,
            resolved_model: requested || null,
            applied: false,
            hit_rule: null,
            factors: config.log_factors ? buildModelRouterFactors(requested, body, sessionKeyHash) : null,
            eval_trace: null
        }};
    }}

    const factors = buildModelRouterFactors(requested, body, sessionKeyHash);
    const trace = [];
    const rules = Array.isArray(config.rules) ? config.rules : [];
    let hitRule = null;
    let suggestedModel = requested;
    let decision = 'no_rule';

    // Categories routing (priority over threshold rules)
    const categories = Array.isArray(config.categories) ? config.categories : [];
    if (categories.length > 0) {{
        const catResult = resolveModelViaCategories(factors, categories);
        if (catResult.matched && ROUTES[catResult.target_model]) {{
            hitRule = {{
                name: `cat_${{catResult.category_name}}`,
                priority: 0,
                target_model: catResult.target_model,
                match: 'category',
                matched_signal: catResult.matched_signal
            }};
            suggestedModel = catResult.target_model;
            decision = `category_hit_${{catResult.category_name}}`;
        }}
    }}

    // Threshold rules (fallback when no category matched)
    if (!hitRule) {{ for (const rule of rules) {{
        const targetModel = String(rule?.target_model || '').trim();
        const ruleName = String(rule?.name || 'unnamed_rule');
        const priority = Number(rule?.priority || 0);

        if (!targetModel) {{
            trace.push({{
                rule: ruleName,
                priority,
                matched: false,
                skipped: 'missing_target_model'
            }});
            continue;
        }}
        if (!ROUTES[targetModel]) {{
            trace.push({{
                rule: ruleName,
                priority,
                matched: false,
                skipped: 'target_model_not_found',
                target_model: targetModel
            }});
            continue;
        }}

        const result = evaluateModelRouterRule(rule, factors);
        trace.push({{
            rule: ruleName,
            priority,
            target_model: targetModel,
            match_mode: result.mode,
            matched: result.matched,
            conditions: result.conditions
        }});

        if (result.matched) {{
            hitRule = {{
                name: ruleName,
                priority,
                target_model: targetModel,
                match: String(rule?.match || 'all')
            }};
            suggestedModel = targetModel;
            decision = `rule_hit_${{ruleName}}`;
            break;
        }}
    }}

    if (!hitRule) {{
        const defaultModel = String(config.default_model || '').trim();
        if (defaultModel) {{
            if (ROUTES[defaultModel]) {{
                suggestedModel = defaultModel;
                decision = 'default_model';
            }} else {{
                decision = 'default_model_not_found';
                trace.push({{
                    rule: '__default_model__',
                    matched: false,
                    skipped: 'default_model_not_found',
                    target_model: defaultModel
                }});
            }}
        }}
    }}
    }} // end if (!hitRule) — categories guard

    const shadowOnly = config.shadow_only === true;
    const applied = !shadowOnly && suggestedModel !== requested;
    const resolvedModel = applied ? suggestedModel : requested;

    return {{
        enabled: true,
        activated: true,
        shadow_only: shadowOnly,
        decision,
        requested_model: requested || null,
        suggested_model: suggestedModel || null,
        resolved_model: resolvedModel || null,
        applied,
        hit_rule: hitRule,
        factors: config.log_factors ? factors : null,
        eval_trace: config.log_factors ? trace : null
    }};
}}

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
    if (typeof params.thinking_level === 'string' && params.thinking_level.trim()) {{
        summary.thinking_level = params.thinking_level.trim();
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
        let rewrittenModel = (target?.rewrite && target.rewrite !== req._model)
            ? target.rewrite
            : req._model;
        // Append thinking_level suffix so cliproxy uses this level instead of
        // deriving one from the client's budget_tokens (which may map to an
        // unsupported level like "xhigh" for models that only accept "low"/"high").
        const thinkingLevel = target?.params?.thinking_level;
        if (typeof thinkingLevel === 'string' && thinkingLevel.trim()) {{
            // Only append if the model name doesn't already have a suffix
            if (!rewrittenModel.includes('(')) {{
                rewrittenModel = rewrittenModel + '(' + thinkingLevel.trim() + ')';
            }}
        }}
        payload.model = rewrittenModel;
    }}
    applyTargetParamsToPayload(payload, target);
    // Strip unsupported fields for MiniMax (Anthropic-specific params)
    if (target?.provider === 'minimax') {{
        delete payload.metadata;
    }}
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

        // Signature 透明恢复：400 signature error → 找到正确 provider 重试，对客户端不可见
        if (routeAction.kind === 'signature' && !res.headersSent && !req._signatureRetried) {{
            const sigGroup = extractThinkingSignatureGroup(req._requestBody);
            if (sigGroup) {{
                const recoveryModels = SIGNATURE_GROUP_ROUTES[sigGroup] || [];
                for (const recoveryModel of recoveryModels) {{
                    const recoveryRoute = ROUTES[recoveryModel];
                    if (!recoveryRoute) continue;
                    const candidates = getRouteCandidates(recoveryRoute, recoveryModel);
                    const recoveryTarget = selectHighestWeightTarget(candidates.targets, candidates.weights);
                    if (recoveryTarget) {{
                        req._signatureRetried = true;
                        req._model = recoveryModel;
                        req._resolvedModel = recoveryModel;
                        if (!Array.isArray(req._retryTrace)) req._retryTrace = [];
                        req._retryTrace.push({{
                            attempt: req._retryCount || 0,
                            target: req._selectedTarget,
                            status: proxyRes.statusCode,
                            route_action: routeAction.kind,
                            sig_group: sigGroup,
                            attempt_duration_ms: Date.now() - (req._attemptStartedAt || req._startTime || Date.now())
                        }});
                        forwardRequestToTarget(req, res, recoveryTarget, `retry_on_signature_group_${{sigGroup}}`);
                        return;
                    }}
                }}
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
                model: req._requestedModel || req._model || null,
                client_ip: req._clientIp
            }},
            routing: {{
                requested_model: req._requestedModel || null,
                resolved_model: req._resolvedModel || req._model || null,
                source_model: req._sourceModel || null,
                target_instance: req._targetInstance || null,
                target_url: req._targetUrl || null,
                rewritten_model: req._rewrittenModel || null,
                provider: req._targetProvider || null,
                target_params: req._targetParamSummary || null,
                hit_rule: req._modelRouter?.hit_rule || null,
                factors: req._modelRouter?.factors || null,
                eval_trace: req._modelRouter?.eval_trace || null,
                model_router: req._modelRouter || null,
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

// 从请求 messages 中提取 thinking signature 的 model_group 前缀
function extractThinkingSignatureGroup(body) {{
    if (!body || !Array.isArray(body.messages)) return null;
    for (const msg of body.messages) {{
        if (!Array.isArray(msg.content)) continue;
        for (const block of msg.content) {{
            if (block?.type === 'thinking' && typeof block.signature === 'string') {{
                const hashIdx = block.signature.indexOf('#');
                if (hashIdx > 0) return block.signature.slice(0, hashIdx);
            }}
        }}
    }}
    return null;
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
    req._clientIp = req.socket?.remoteAddress || req.ip || req.headers['x-forwarded-for'] || 'unknown';
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
            req._modelRouter = null;
            req._resolvedModel = model;
            const sessionKey = getSessionKey(req, jsonBody);
            req._sessionKeyHash = hashSessionKey(sessionKey);
            req._modelHealthKey = modelHealthKey(req._sessionKeyHash, req._sourceModel);

            const modelRouter = resolveModelViaRouter(model, jsonBody, req._sessionKeyHash);
            req._modelRouter = modelRouter;
            if (modelRouter?.enabled && modelRouter?.activated && modelRouter?.applied && modelRouter?.resolved_model) {{
                model = modelRouter.resolved_model;
                req._model = model;
            }}

            const autoUpgrade = resolveAutoUpgradeModel(model, jsonBody, req._sessionKeyHash);
            if (autoUpgrade) {{
                model = autoUpgrade.targetModel;
                req._model = model;
                req._autoUpgrade = autoUpgrade;
            }}
            req._resolvedModel = model;

            let route = ROUTES[model];
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
            }} else if (req._modelRouter?.enabled && req._modelRouter?.activated) {{
                routingDecision = req._modelRouter.applied
                    ? `model_router_${{req._modelRouter.decision || 'resolved'}}`
                    : `model_router_${{req._modelRouter.decision || 'passthrough'}}`;
            }}

            // thinking cross-model sticky: 当 session 已在某 model 上有 sticky 时，锁定回去
            // 避免 model router 切换 model 导致 thinking signature 跨 provider 失效
            if (req._hasThinkingSignature && sessionKey) {{
                const modelList = Object.keys(ROUTES);
                for (const candidateModel of modelList) {{
                    const candidateRoute = ROUTES[candidateModel];
                    if (!candidateRoute) continue;
                    const candidateStickyKey = stickyRouteKey(sessionKey, candidateModel);
                    const candidateSticky = getStickyTarget(candidateRoute, candidateStickyKey, candidateModel, {{ ignoreCooldown: true }});
                    if (candidateSticky) {{
                        model = candidateModel;
                        req._model = model;
                        req._resolvedModel = model;
                        route = candidateRoute;
                        selected = candidateSticky;
                        req._stickyKey = candidateStickyKey;
                        routingDecision = 'thinking_sticky_cross_model_locked';
                        break;
                    }}
                }}
            }}

            if (req._hasThinkingSignature && !selected) {{
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
            }} else if (!req._hasThinkingSignature) {{
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
        // 非 POST 请求（如 GET），直接转发
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
