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
        # Anthropic style
        bucket["input_tokens"] += (usage.get("input_tokens") or 0)
        bucket["input_tokens"] += (usage.get("cache_creation_input_tokens") or 0)
        bucket["input_tokens"] += (usage.get("cache_read_input_tokens") or 0)
        # OpenAI / Gemini style
        bucket["input_tokens"] += (usage.get("prompt_tokens") or 0)

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


def _infer_provider(rewritten_model: str) -> str:
    """Resolve provider from rewritten model, using TOML mapping then heuristics."""
    rm = rewritten_model or ""
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
    return "(unknown)"


def process_day(day: date) -> dict:
    """Parse a single day's JSONL and return aggregated stats."""
    path = LOGS_DIR / f"{day.isoformat()}.jsonl"
    by_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_instance: dict[str, dict] = defaultdict(_empty_bucket)
    by_provider: dict[str, dict] = defaultdict(_empty_bucket)
    total = _empty_bucket()
    decisions: dict[str, int] = defaultdict(int)
    sticky_keys: set[str] = set()

    if not path.exists():
        return {"date": day.isoformat(), "by_model": {}, "by_instance": {}, "by_provider": {},
                "total": total, "routing": {"decisions": {}, "sticky_keys": 0}}

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)

            model = rec["request"].get("model") or "(unknown)"
            routing = rec.get("routing") or {}
            instance = routing.get("target_instance") or "(unknown)"
            provider = routing.get("provider") or _infer_provider(routing.get("rewritten_model"))

            decision = routing.get("decision") or "(none)"
            decisions[decision] += 1
            sk = routing.get("session_key_hash")
            if sk:
                sticky_keys.add(sk)

            _add(by_model[model], rec)
            _add(by_instance[instance], rec)
            _add(by_provider[f"{model} @ {provider} / {instance}"], rec)
            _add(total, rec)

    return {
        "date": day.isoformat(),
        "by_model": dict(by_model),
        "by_instance": dict(by_instance),
        "by_provider": dict(by_provider),
        "total": total,
        "routing": {"decisions": dict(decisions), "sticky_keys": len(sticky_keys)},
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
            return json.load(f)

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
                "sticky", "random", "thinking"):
        target[key] += source.get(key, 0)


def aggregate(day_stats_list: list[dict]) -> dict:
    by_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_instance: dict[str, dict] = defaultdict(_empty_bucket)
    by_provider: dict[str, dict] = defaultdict(_empty_bucket)
    total = _empty_bucket()
    decisions: dict[str, int] = defaultdict(int)
    sticky_keys = 0

    for ds in day_stats_list:
        _merge(total, ds["total"])
        for name, bucket in ds["by_model"].items():
            _merge(by_model[name], bucket)
        for name, bucket in ds["by_instance"].items():
            _merge(by_instance[name], bucket)
        for name, bucket in ds.get("by_provider", {}).items():
            _merge(by_provider[name], bucket)
        rt = ds.get("routing") or {}
        for d, cnt in rt.get("decisions", {}).items():
            decisions[d] += cnt
        sticky_keys += rt.get("sticky_keys", 0)

    return {
        "by_model": dict(by_model),
        "by_instance": dict(by_instance),
        "by_provider": dict(by_provider),
        "total": total,
        "routing": {"decisions": dict(decisions), "sticky_keys": sticky_keys},
    }


# ── output formatting ────────────────────────────────────────────────────

def print_table(data: dict[str, dict], name_header: str) -> None:
    if not data:
        return

    name_w = max(len(name_header), max(len(n) for n in data))
    hdr = (f"  {name_header:<{name_w}}  {'Reqs':>6}  {'OK%':>5}  "
           f"{'In Tokens':>11}  {'Out Tokens':>11}  "
           f"{'Sticky':>6}  {'Rand':>5}  {'Think':>5}")
    print(hdr)

    for name in sorted(data, key=lambda n: data[n]["requests"], reverse=True):
        b = data[name]
        print(
            f"  {name:<{name_w}}  {fmt_num(b['requests']):>6}  "
            f"{pct(b['success'], b['requests']):>5}  "
            f"{fmt_tokens_short(b['input_tokens']):>11}  "
            f"{fmt_tokens_short(b['output_tokens']):>11}  "
            f"{fmt_num(b['sticky']):>6}  "
            f"{fmt_num(b['random']):>5}  "
            f"{fmt_num(b['thinking']):>5}"
        )


def print_human(agg: dict, start: date, end: date) -> None:
    t = agg["total"]
    rt = agg.get("routing") or {}
    print(f"=== Usage Stats: {start.isoformat()} ~ {end.isoformat()} ===")
    print()
    avg_ms = t["duration_ms"] / t["requests"] if t["requests"] else 0
    print(
        f"  Requests: {fmt_num(t['requests'])}  "
        f"Success: {pct(t['success'], t['requests'])}  "
        f"Tokens: {fmt_tokens_short(t['input_tokens'])} in / {fmt_tokens_short(t['output_tokens'])} out  "
        f"Avg latency: {avg_ms:,.0f} ms  "
        f"Sticky keys: {fmt_num(rt.get('sticky_keys', 0))}"
    )
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


def main() -> None:
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

    if args.json_output:
        print_json(agg, days[0], days[-1])
    else:
        print_human(agg, days[0], days[-1])


if __name__ == "__main__":
    main()
