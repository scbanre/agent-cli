#!/usr/bin/env python3
"""cliproxyapi usage statistics engine.

Reads JSONL request logs and produces per-model / per-instance token usage
and success-rate summaries.  Results for completed days are cached so
repeated queries are fast.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

AUTO_MODELS = {"auto", "auto-canary"}
CACHE_SCHEMA_VERSION = 2

# Pricing data from Zenmux (per 1M tokens)
# Format: {provider: {model_pattern: {"input": $, "output": $, "cache_read": $}}}
# Prices are for <200K prompt tokens
PRICING = {
    # Zenmux Anthropic
    "anthropic": {
        "claude-opus-4.6": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write_5m": 6.25},
        "claude-sonnet-4.6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write_5m": 3.75},
        "claude-opus-4.5": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write_5m": 6.25},
        "claude-sonnet-4.5": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write_5m": 3.75},
    },
    # Zenmux OpenAI
    "openai": {
        "gpt-5.2": {"input": 1.75, "output": 14.0, "cache_read": 0.175},
        "gpt-5": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    },
    # Zenmux Gemini (via OpenAI compatible endpoint)
    "gemini": {
        "gemini-3-flash-preview": {"input": 0.5, "output": 3.0, "cache_read": 0.05},
        "gemini-3-pro-preview": {"input": 2.0, "output": 12.0, "cache_read": 0.2},
    },
    # Official/Minimax
    "minimax": {
        "minimax-m2.5": {"input": 0.3, "output": 1.2, "cache_read": 0.03},
    },
}

# Tier to upstream model mapping (from providers.toml routing)
TIER_MODEL_MAP = {
    "opus4.6": "claude-opus-4.6",
    "sonnet4.6": "claude-sonnet-4.6",
    "M2.5": "minimax-m2.5",
    "g3f": "gemini-3-flash-preview",
    "g3f.auto": "gemini-3-flash-preview",
    "g3p": "gemini-3-pro-preview",
    "g3p.auto": "gemini-3-pro-preview",
    "gpt5.2": "gpt-5.2",
}


def get_price(provider: str, model: str) -> dict:
    """Get pricing for a model based on provider and model name."""
    provider_pricing = PRICING.get(provider, {})
    # Try exact match first
    if model in provider_pricing:
        return provider_pricing[model]
    # Try partial match
    for pattern, price in provider_pricing.items():
        if pattern in model.lower():
            return price
    return {"input": 0, "output": 0, "cache_read": 0}


def calculate_cost(model: str, provider: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> dict:
    """Calculate cost for a request."""
    price = get_price(provider, model)
    input_cost = (input_tokens / 1_000_000) * price.get("input", 0)
    output_cost = (output_tokens / 1_000_000) * price.get("output", 0)
    cache_savings = (cached_tokens / 1_000_000) * price.get("cache_read", 0)
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "cache_savings": cache_savings,
        "total_cost": input_cost + output_cost - cache_savings,
    }


def _score_base_dir(path: Path) -> int:
    """Heuristic score for choosing runtime base directory."""
    score = 0
    if (path / "providers.toml").exists():
        score += 3
    req_dir = path / "logs" / "requests"
    if req_dir.exists():
        score += 2
        if any(req_dir.glob("*.jsonl")):
            score += 3
    if (path / "instances").exists():
        score += 1
    return score


def _resolve_base_dir(cli_base_dir: str = "") -> Path:
    script_base = Path(__file__).resolve().parent.parent
    candidates = []

    if cli_base_dir:
        candidates.append(Path(cli_base_dir).expanduser())

    env_base = os.environ.get("CLIPROXY_BASE_DIR")
    if env_base:
        candidates.append(Path(env_base).expanduser())

    candidates.append(Path.cwd())
    candidates.append(script_base)
    candidates.append(script_base.parent)

    # Pick the highest-scoring existing directory.
    best = script_base
    best_score = -1
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved in seen or not resolved.exists() or not resolved.is_dir():
            continue
        seen.add(resolved)
        score = _score_base_dir(resolved)
        if score > best_score:
            best_score = score
            best = resolved
    return best


def _apply_base_dir(base_dir: Path) -> None:
    global BASE_DIR, LOGS_DIR, CACHE_DIR, TOML_FILE, _PROVIDER_MAP
    BASE_DIR = base_dir
    LOGS_DIR = BASE_DIR / "logs" / "requests"
    CACHE_DIR = BASE_DIR / "logs" / "stats_cache"
    TOML_FILE = BASE_DIR / "providers.toml"
    _PROVIDER_MAP = None


_apply_base_dir(_resolve_base_dir())


# ── helpers ──────────────────────────────────────────────────────────────

def fmt_num(n: int) -> str:
    """Format an integer with thousands separators."""
    return f"{n:,}"


def fmt_tokens_short(n: int) -> str:
    """Compact token count: 12.3M / 456.7K / 1,234."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    return fmt_num(n)


def pct(num: int, den: int) -> str:
    if den == 0:
        return "  -  "
    return f"{num / den * 100:.1f}%"


# ── per-day processing ───────────────────────────────────────────────────

def _empty_bucket() -> dict:
    return {
        "requests": 0, "success": 0, "input_tokens": 0, "output_tokens": 0,
        "duration_ms": 0, "sticky": 0, "random": 0, "thinking": 0,
        "cache_read_tokens": 0,  # Tokens read from cache
    }


def _add(bucket: dict, rec: dict) -> None:
    bucket["requests"] += 1
    status = rec["response"]["status_code"]
    if 200 <= status < 300:
        bucket["success"] += 1
    bucket["duration_ms"] += rec.get("duration_ms") or 0

    decision = ((rec.get("routing") or {}).get("decision") or "")
    if decision.startswith("sticky"):
        bucket["sticky"] += 1
    elif decision.startswith("thinking"):
        bucket["thinking"] += 1
    elif decision == "weighted_random":
        bucket["random"] += 1

    usage = rec.get("usage")
    if usage:
        # Track new input tokens separately from cache read tokens
        # Anthropic style
        new_tokens = (usage.get("input_tokens") or 0)
        new_tokens += (usage.get("cache_creation_input_tokens") or 0)
        cache_read = (usage.get("cache_read_input_tokens") or 0)
        # OpenAI / Gemini style
        new_tokens += (usage.get("prompt_tokens") or 0)

        # Total input tokens (for display)
        bucket["input_tokens"] += new_tokens + cache_read
        # Cache read tokens (for cost calculation)
        bucket["cache_read_tokens"] += cache_read

        bucket["output_tokens"] += (usage.get("output_tokens") or 0)
        # OpenAI / Gemini style
        bucket["output_tokens"] += (usage.get("completion_tokens") or 0)


_PROVIDER_MAP = None


def _load_provider_map() -> dict[str, str]:
    """Build rewritten_model → provider mapping from providers.toml routing."""
    global _PROVIDER_MAP
    if _PROVIDER_MAP is not None:
        return _PROVIDER_MAP
    _PROVIDER_MAP = {}
    if TOML_FILE.exists():
        try:
            import toml
            cfg = toml.load(TOML_FILE)
            for targets in cfg.get("routing", {}).values():
                for t in targets:
                    model = t.get("model", "")
                    provider = t.get("provider", "")
                    if model and provider:
                        _PROVIDER_MAP[model] = provider
        except Exception:
            pass
    return _PROVIDER_MAP


def _infer_provider(rewritten_model: str, requested_model: str = "") -> str:
    """Resolve provider from rewritten model, using TOML mapping then heuristics."""
    # Try rewritten_model first, then fall back to requested_model
    rm = rewritten_model or requested_model or ""
    mapping = _load_provider_map()
    if rm in mapping:
        return mapping[rm]
    m = rm.lower()
    if m.startswith(("gemini", "g3")):
        return "gemini"
    if m.startswith(("claude-", "anthropic/")):
        return "anthropic"
    if m.startswith(("gpt", "openai/")):
        return "openai"
    if "codex" in m or m.startswith("gpt-5"):
        return "codex"
    if "minimax" in m or m.startswith("minimax"):
        return "minimax"
    if "antigravity" in m:
        return "antigravity"
    return "(unknown)"


def _pick_model(rec: dict) -> str:
    """Best-effort final routed model for statistics."""
    request_model = ((rec.get("request") or {}).get("model") or "").strip()
    routing = rec.get("routing") or {}
    model_router = routing.get("model_router") or {}
    for candidate in (
        routing.get("resolved_model"),
        model_router.get("resolved_model"),
        model_router.get("suggested_model"),
        request_model,
    ):
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if candidate:
                return candidate
    return "(unknown)"


def _requested_model(rec: dict) -> str:
    request_model = ((rec.get("request") or {}).get("model") or "").strip()
    routing = rec.get("routing") or {}
    requested = routing.get("requested_model")
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    if request_model:
        return request_model
    return "(unknown)"


def _hit_rule_name(hit_rule: object) -> str:
    if isinstance(hit_rule, dict):
        name = hit_rule.get("name")
        if isinstance(name, str):
            return name.strip()
        return ""
    if isinstance(hit_rule, str):
        return hit_rule.strip()
    return ""


def _auto_category(rec: dict) -> str:
    """Extract category for auto-routed requests."""
    requested = _requested_model(rec)
    if requested not in AUTO_MODELS:
        return ""

    routing = rec.get("routing") or {}
    model_router = routing.get("model_router") or {}
    hit_rule = model_router.get("hit_rule")
    if hit_rule is None:
        hit_rule = routing.get("hit_rule")
    name = _hit_rule_name(hit_rule)
    if name.startswith("cat_"):
        return name[4:] or "(unknown)"

    decision = (model_router.get("decision") or "").strip()
    if decision.startswith("category_hit_"):
        return decision[len("category_hit_"):] or "(unknown)"
    if decision == "not_activated":
        return "(not_activated)"

    if isinstance(hit_rule, dict) and hit_rule.get("match") == "category":
        return name or "(category)"
    return "(non_category)"


def _request_method_path(rec: dict) -> tuple[str, str]:
    req = rec.get("request") or {}
    method = (req.get("method") or "").strip().upper()
    url = (req.get("url") or "").strip()
    path = url.split("?", 1)[0]
    return method, path


def _is_meta_request(rec: dict) -> bool:
    """Requests that are not model inference and should be excluded from by_model."""
    method, path = _request_method_path(rec)
    if method == "GET" and path in ("/v1/models", "/models"):
        return True
    return False


def process_day(day: date) -> dict:
    """Parse a single day's JSONL and return aggregated stats."""
    path = LOGS_DIR / f"{day.isoformat()}.jsonl"
    by_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_requested_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_instance: dict[str, dict] = defaultdict(_empty_bucket)
    by_provider: dict[str, dict] = defaultdict(_empty_bucket)
    by_client_ip: dict[str, dict] = defaultdict(_empty_bucket)
    by_auto_model_category: dict[str, dict] = defaultdict(_empty_bucket)
    by_auto_category: dict[str, dict] = defaultdict(_empty_bucket)
    by_auto_model: dict[str, dict] = defaultdict(_empty_bucket)
    total = _empty_bucket()
    decisions: dict[str, int] = defaultdict(int)
    sticky_keys: set[str] = set()
    meta_requests = 0

    if not path.exists():
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "date": day.isoformat(),
            "by_model": {},
            "by_requested_model": {},
            "by_instance": {},
            "by_provider": {},
            "by_client_ip": {},
            "by_auto_model_category": {},
            "by_auto_category": {},
            "by_auto_model": {},
            "total": total,
            "routing": {"decisions": {}, "sticky_keys": 0},
        }

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)

            requested_model = _requested_model(rec)
            model = _pick_model(rec)
            routing = rec.get("routing") or {}
            instance = routing.get("target_instance")
            # Fallback: infer instance from target_url
            if not instance:
                target_url = routing.get("target_url") or ""
                if ":8146" in target_url:
                    instance = "official"
                elif ":8147" in target_url:
                    instance = "zenmux"
            instance = instance or "(unknown)"
            # Infer provider: try routing fields first, then fall back to request model
            provider = routing.get("provider") or _infer_provider(
                routing.get("rewritten_model"), requested_model
            )

            # Get client IP
            client_ip = rec["request"].get("client_ip") or "unknown"

            decision = routing.get("decision") or "(none)"
            decisions[decision] += 1
            sk = routing.get("session_key_hash")
            if sk:
                sticky_keys.add(sk)

            _add(by_client_ip[client_ip], rec)
            if _is_meta_request(rec):
                meta_requests += 1
            else:
                _add(by_model[model], rec)
                _add(by_requested_model[requested_model], rec)
                _add(by_instance[instance], rec)
                _add(by_provider[f"{model} @ {provider} / {instance}"], rec)
                if requested_model in AUTO_MODELS:
                    category = _auto_category(rec)
                    _add(by_auto_model_category[f"{model} × {category}"], rec)
                    _add(by_auto_category[category], rec)
                    _add(by_auto_model[model], rec)

            _add(total, rec)

    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "date": day.isoformat(),
        "by_model": dict(by_model),
        "by_requested_model": dict(by_requested_model),
        "by_instance": dict(by_instance),
        "by_provider": dict(by_provider),
        "by_client_ip": dict(by_client_ip),
        "by_auto_model_category": dict(by_auto_model_category),
        "by_auto_category": dict(by_auto_category),
        "by_auto_model": dict(by_auto_model),
        "total": total,
        "routing": {
            "decisions": dict(decisions),
            "sticky_keys": len(sticky_keys),
            "meta_requests": meta_requests,
        },
    }


# ── caching ──────────────────────────────────────────────────────────────

def _cache_path(day: date) -> Path:
    return CACHE_DIR / f"{day.isoformat()}.json"


def get_day_stats(day: date, *, force: bool = False) -> dict:
    today = date.today()
    cache = _cache_path(day)

    # Today is always recomputed; past days use cache when available.
    if not force and day != today and cache.exists():
        with open(cache) as f:
            cached = json.load(f)
        if isinstance(cached, dict) and cached.get("schema_version") == CACHE_SCHEMA_VERSION:
            return cached

    stats = process_day(day)

    # Cache only non-today results (today's log is still being written).
    if day != today:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache, "w") as f:
            json.dump(stats, f)

    return stats


# ── aggregation across days ──────────────────────────────────────────────

def _merge(target: dict, source: dict) -> None:
    for key in ("requests", "success", "input_tokens", "output_tokens", "duration_ms",
                "sticky", "random", "thinking", "cache_read_tokens"):
        target[key] += source.get(key, 0)


def aggregate(day_stats_list: list[dict]) -> dict:
    by_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_requested_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_instance: dict[str, dict] = defaultdict(_empty_bucket)
    by_provider: dict[str, dict] = defaultdict(_empty_bucket)
    by_client_ip: dict[str, dict] = defaultdict(_empty_bucket)
    by_auto_model_category: dict[str, dict] = defaultdict(_empty_bucket)
    by_auto_category: dict[str, dict] = defaultdict(_empty_bucket)
    by_auto_model: dict[str, dict] = defaultdict(_empty_bucket)
    total = _empty_bucket()
    decisions: dict[str, int] = defaultdict(int)
    sticky_keys = 0
    meta_requests = 0

    for ds in day_stats_list:
        _merge(total, ds["total"])
        for name, bucket in ds["by_model"].items():
            _merge(by_model[name], bucket)
        for name, bucket in ds.get("by_requested_model", {}).items():
            _merge(by_requested_model[name], bucket)
        for name, bucket in ds["by_instance"].items():
            _merge(by_instance[name], bucket)
        for name, bucket in ds.get("by_provider", {}).items():
            _merge(by_provider[name], bucket)
        for name, bucket in ds.get("by_client_ip", {}).items():
            _merge(by_client_ip[name], bucket)
        for name, bucket in ds.get("by_auto_model_category", {}).items():
            _merge(by_auto_model_category[name], bucket)
        for name, bucket in ds.get("by_auto_category", {}).items():
            _merge(by_auto_category[name], bucket)
        for name, bucket in ds.get("by_auto_model", {}).items():
            _merge(by_auto_model[name], bucket)
        rt = ds.get("routing") or {}
        for d, cnt in rt.get("decisions", {}).items():
            decisions[d] += cnt
        sticky_keys += rt.get("sticky_keys", 0)
        meta_requests += rt.get("meta_requests", 0)

    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "by_model": dict(by_model),
        "by_requested_model": dict(by_requested_model),
        "by_instance": dict(by_instance),
        "by_provider": dict(by_provider),
        "by_client_ip": dict(by_client_ip),
        "by_auto_model_category": dict(by_auto_model_category),
        "by_auto_category": dict(by_auto_category),
        "by_auto_model": dict(by_auto_model),
        "total": total,
        "routing": {
            "decisions": dict(decisions),
            "sticky_keys": sticky_keys,
            "meta_requests": meta_requests,
        },
    }


# ── output formatting ────────────────────────────────────────────────────

def print_table(data: dict[str, dict], name_header: str) -> None:
    if not data:
        return

    # Check if costs are available
    has_costs = any("total_cost" in b for b in data.values())

    name_w = max(len(name_header), max(len(n) for n in data))
    hdr = (f"  {name_header:<{name_w}}  {'Reqs':>6}  {'OK%':>5}  "
           f"{'In Tokens':>11}  {'Out Tokens':>11}  "
           f"{'Sticky':>6}  {'Rand':>5}  {'Think':>5}")
    if has_costs:
        hdr += f"  {'Est. Cost':>10}"
    print(hdr)

    for name in sorted(data, key=lambda n: data[n]["requests"], reverse=True):
        b = data[name]
        line = (
            f"  {name:<{name_w}}  {fmt_num(b['requests']):>6}  "
            f"{pct(b['success'], b['requests']):>5}  "
            f"{fmt_tokens_short(b['input_tokens']):>11}  "
            f"{fmt_tokens_short(b['output_tokens']):>11}  "
            f"{fmt_num(b['sticky']):>6}  "
            f"{fmt_num(b['random']):>5}  "
            f"{fmt_num(b['thinking']):>5}"
        )
        if has_costs and "total_cost" in b:
            line += f"  ${b['total_cost']:>9.2f}"
        print(line)


def print_human(agg: dict, start: date, end: date) -> None:
    t = agg["total"]
    rt = agg.get("routing") or {}
    print(f"=== Usage Stats: {start.isoformat()} ~ {end.isoformat()} ===")
    print()
    avg_ms = t["duration_ms"] / t["requests"] if t["requests"] else 0
    cost_line = ""
    if "total_cost" in t:
        cost_line = f"  Est. Cost: ${t['total_cost']:.2f}"
    print(
        f"  Requests: {fmt_num(t['requests'])}  "
        f"Success: {pct(t['success'], t['requests'])}  "
        f"Tokens: {fmt_tokens_short(t['input_tokens'])} in / {fmt_tokens_short(t['output_tokens'])} out  "
        f"Avg latency: {avg_ms:,.0f} ms  "
        f"Sticky keys: {fmt_num(rt.get('sticky_keys', 0))}  "
        f"Meta reqs: {fmt_num(rt.get('meta_requests', 0))}"
        + cost_line
    )
    print()

    # 按客户端 IP 统计
    by_client_ip = agg.get("by_client_ip", {})
    if by_client_ip:
        print("--- By Client IP ---")
        print_table(by_client_ip, "Client IP")
        print()

    by_model = agg.get("by_model", {})
    if by_model:
        print("--- By Routed Model ---")
        print_table(by_model, "Model")
        print()

    auto_mix = agg.get("by_auto_model_category", {})
    if auto_mix:
        print("--- Auto Mode: Model x Category ---")
        print_table(auto_mix, "Model x Category")
        print()

    print_table(agg["by_provider"], "Model @ Provider / Instance")


def print_json(agg: dict, start: date, end: date) -> None:
    out = {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        **agg,
    }
    json.dump(out, sys.stdout, indent=2)
    print()


# ── main ─────────────────────────────────────────────────────────────────

def resolve_days(args) -> list[date]:
    today = date.today()

    if args.all:
        if not LOGS_DIR.exists():
            return []
        dates = []
        for p in sorted(LOGS_DIR.glob("*.jsonl")):
            try:
                dates.append(date.fromisoformat(p.stem))
            except ValueError:
                continue
        return dates

    return [today - timedelta(days=i) for i in range(args.days - 1, -1, -1)]


def add_costs(agg: dict) -> dict:
    """Add cost calculations to aggregated data."""
    # Add costs to by_provider
    # Provider name format: "model @ provider / instance"
    for name, bucket in agg.get("by_provider", {}).items():
        input_tokens = bucket.get("input_tokens", 0)
        cache_read_tokens = bucket.get("cache_read_tokens", 0)
        output_tokens = bucket.get("output_tokens", 0)

        # Parse provider name: "M2.5 @ minimax / official"
        provider = None
        model = name
        if " @ " in name:
            parts = name.split(" @ ")
            model = parts[0]
            provider_part = parts[1].split(" / ")[0] if " / " in parts[1] else parts[1]
            provider = provider_part.lower()

        # Try to find pricing
        price = {}
        if provider and provider in PRICING:
            upstream = TIER_MODEL_MAP.get(model, model)
            price = PRICING[provider].get(upstream, {})

        if not price:
            # Fallback: search all providers
            upstream = TIER_MODEL_MAP.get(model, model)
            for p, models in PRICING.items():
                if upstream in models:
                    price = models[upstream]
                    break

        if price:
            # New input tokens = total - cache_read
            new_input_tokens = max(0, input_tokens - cache_read_tokens)
            # Cost = (new tokens × input price) + (cache read tokens × cache price)
            input_cost = (new_input_tokens / 1_000_000) * price.get("input", 0)
            input_cost += (cache_read_tokens / 1_000_000) * price.get("cache_read", 0)
            output_cost = (output_tokens / 1_000_000) * price.get("output", 0)
        else:
            input_cost = 0
            output_cost = 0

        bucket["input_cost"] = round(input_cost, 4)
        bucket["output_cost"] = round(output_cost, 4)
        bucket["total_cost"] = round(input_cost + output_cost, 4)

    # Add costs to by_model (more accurate since we can match model to pricing)
    for name, bucket in agg.get("by_model", {}).items():
        input_tokens = bucket.get("input_tokens", 0)
        cache_read_tokens = bucket.get("cache_read_tokens", 0)
        output_tokens = bucket.get("output_tokens", 0)

        # Try to find pricing based on tier mapping
        upstream_model = TIER_MODEL_MAP.get(name, name)
        price = {}
        for provider, models in PRICING.items():
            if upstream_model in models:
                price = models[upstream_model]
                break

        if price:
            # New input tokens = total - cache_read
            new_input_tokens = max(0, input_tokens - cache_read_tokens)
            # Cost = (new tokens × input price) + (cache read tokens × cache price)
            input_cost = (new_input_tokens / 1_000_000) * price.get("input", 0)
            input_cost += (cache_read_tokens / 1_000_000) * price.get("cache_read", 0)
            output_cost = (output_tokens / 1_000_000) * price.get("output", 0)
        else:
            input_cost = 0
            output_cost = 0

        bucket["input_cost"] = round(input_cost, 4)
        bucket["output_cost"] = round(output_cost, 4)
        bucket["total_cost"] = round(input_cost + output_cost, 4)

    # Add costs to total
    total = agg.get("total", {})
    # Sum all model costs
    total_cost = sum(
        bucket.get("total_cost", 0)
        for bucket in agg.get("by_model", {}).values()
    )
    total["total_cost"] = round(total_cost, 4)

    return agg


def main() -> None:
    pass
    parser = argparse.ArgumentParser(description="cliproxyapi usage statistics")
    parser.add_argument(
        "--base-dir",
        default="",
        help="Runtime base dir for logs/providers (default: auto-detect; CLI > CLIPROXY_BASE_DIR > heuristic)",
    )
    parser.add_argument("--days", type=int, default=7, help="Number of recent days (default: 7)")
    parser.add_argument("--all", action="store_true", help="Process all available log files")
    parser.add_argument("--force", action="store_true", help="Ignore cache, recompute everything")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output JSON instead of table")
    parser.add_argument("--costs", action="store_true", default=True, help="Show cost estimates (default: enabled)")
    args = parser.parse_args()

    _apply_base_dir(_resolve_base_dir(args.base_dir))

    days = resolve_days(args)
    if not days:
        print("No log files found.", file=sys.stderr)
        sys.exit(1)

    day_stats = [get_day_stats(d, force=args.force) for d in days]
    agg = aggregate(day_stats)

    if agg["total"]["requests"] == 0:
        print("No requests found in the selected period.", file=sys.stderr)
        sys.exit(0)

    # Add costs if requested
    if args.costs:
        agg = add_costs(agg)

    if args.json_output:
        print_json(agg, days[0], days[-1])
    else:
        print_human(agg, days[0], days[-1])


if __name__ == "__main__":
    main()
