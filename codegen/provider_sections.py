"""Provider section builders for instance YAML generation."""

from typing import Callable, Dict, List, Any


CLAUDE_COMPAT_PROVIDER_TYPES = {"anthropic", "minimax"}


def _collect_models_for_provider(
    instance_name: str,
    provider_type: str,
    routing: Dict[str, Any],
    warn_fn: Callable[[str], None],
) -> List[str]:
    models: List[str] = []
    for expose_id, targets in routing.items():
        for target in targets:
            if target.get("instance") != instance_name:
                continue

            target_provider = target.get("provider")
            if not target_provider:
                warn_fn(f"⚠️  警告: 路由 '{expose_id}' -> '{instance_name}' 未指定 provider 类型，跳过")
                continue

            if target_provider != provider_type:
                continue

            internal_model = target["model"]
            if internal_model not in models:
                models.append(internal_model)
    return models


def build_provider_sections(
    instance_name: str,
    providers: List[Dict[str, Any]],
    routing: Dict[str, Any],
    warn_fn: Callable[[str], None] = print,
) -> Dict[str, Any]:
    claude_keys: List[Dict[str, Any]] = []
    openai_compat: List[Dict[str, Any]] = []
    vertex_keys: List[Dict[str, Any]] = []

    for idx, provider_raw in enumerate(providers):
        provider_type = provider_raw["type"]
        provider_name = f"{instance_name}-{provider_type}-{idx}"
        base_url = provider_raw.get("base_url", "")
        api_keys = provider_raw.get("api_keys", [])

        models = _collect_models_for_provider(instance_name, provider_type, routing, warn_fn)

        # anthropic/minimax(anthropic-compatible) -> claude-api-key
        if provider_type in CLAUDE_COMPAT_PROVIDER_TYPES:
            for key in api_keys:
                entry: Dict[str, Any] = {
                    "api-key": key,
                    "base-url": base_url,
                }
                if models:
                    entry["models"] = [{"name": model, "alias": model} for model in models]
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
                entry["models"] = [{"name": model, "alias": model} for model in models]
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
                    entry["models"] = [{"name": model, "alias": model} for model in models]
                vertex_keys.append(entry)
            continue

        # antigravity/codex 等 OAuth 类型由 auth-dir 自动加载，无需额外配置

    sections: Dict[str, Any] = {}
    if claude_keys:
        sections["claude-api-key"] = claude_keys
    if openai_compat:
        sections["openai-compatibility"] = openai_compat
    if vertex_keys:
        sections["vertex-api-key"] = vertex_keys
    return sections

