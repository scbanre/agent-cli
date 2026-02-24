#!/usr/bin/env python3
import toml
import yaml
import os
import sys
import json
import re

from codegen.provider_sections import build_provider_sections
from codegen.lb_codegen import create_node_lb_script

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

    request_log = pick_conf(instance_conf, global_conf, "request_log")
    if request_log is not None:
        yaml_conf["request-log"] = coerce_bool(request_log)

    logs_max_total_size_mb = pick_conf(instance_conf, global_conf, "logs_max_total_size_mb")
    if logs_max_total_size_mb is not None:
        yaml_conf["logs-max-total-size-mb"] = max(0, coerce_int(logs_max_total_size_mb))

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

    provider_sections = build_provider_sections(
        instance_name=name,
        providers=instance_conf.get("providers", []),
        routing=routing,
        warn_fn=print,
    )
    yaml_conf.update(provider_sections)

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
