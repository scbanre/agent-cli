#!/usr/bin/env python3
"""Auto Model Router analysis and threshold/category optimization.

Reads JSONL request logs to analyze model_router behavior:
- Factor distributions (percentiles) for auto-routed requests
- Rule/category hit rates
- Per-model performance (success rate, latency, tokens)
- Category fallback analysis
- Threshold + category-oriented suggestions

Reuses the base-dir resolution from usage_stats.py.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# Reuse base-dir resolution from sibling module
from usage_stats import _resolve_base_dir, _apply_base_dir, LOGS_DIR


# ── data collection ──────────────────────────────────────────────────────

def _collect_records(days: int) -> list[dict]:
    """Read JSONL logs and return raw records that have model_router data."""
    today = date.today()
    records = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        path = LOGS_DIR / f"{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mr = (rec.get("routing") or {}).get("model_router")
                if mr and mr.get("enabled"):
                    records.append(rec)
    return records


# ── helpers ──────────────────────────────────────────────────────────────

def percentiles(values: list[float], ps: tuple = (25, 50, 75, 90)) -> dict[int, float]:
    if not values:
        return {p: 0 for p in ps}
    s = sorted(values)
    n = len(s)
    result = {}
    for p in ps:
        k = (p / 100) * (n - 1)
        lo = int(math.floor(k))
        hi = min(lo + 1, n - 1)
        w = k - lo
        result[p] = s[lo] * (1 - w) + s[hi] * w
    return result


def _extract_hit_rule_name(hit_rule) -> str:
    if isinstance(hit_rule, dict):
        return str(hit_rule.get("name", "")).strip()
    if isinstance(hit_rule, str):
        return hit_rule.strip()
    if hit_rule is None:
        return ""
    return str(hit_rule).strip()


def _extract_matched_signal(hit_rule) -> str:
    if isinstance(hit_rule, dict):
        return str(hit_rule.get("matched_signal", "")).strip()
    return ""


def _is_success(rec: dict) -> bool:
    status = (rec.get("response") or {}).get("status_code", 0)
    return 200 <= status < 300


def _extract_total_tokens(rec: dict) -> int:
    usage = rec.get("usage") or {}
    input_tokens = 0
    input_tokens += usage.get("input_tokens") or 0
    input_tokens += usage.get("cache_creation_input_tokens") or 0
    input_tokens += usage.get("cache_read_input_tokens") or 0
    input_tokens += usage.get("prompt_tokens") or 0
    prompt_details = usage.get("prompt_tokens_details") or {}
    input_tokens += prompt_details.get("cached_tokens") or 0

    output_tokens = 0
    output_tokens += usage.get("output_tokens") or 0
    output_tokens += usage.get("completion_tokens") or 0
    return int(input_tokens + output_tokens)


def _extract_system_prompt_types(factors: dict) -> list[str]:
    if not isinstance(factors, dict):
        return []
    raw = factors.get("system_prompt_type")
    if raw is None:
        raw = factors.get("system_prompt_tags")
    if isinstance(raw, list):
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip().lower()]
    return []


def _empty_category_bucket() -> dict:
    return {
        "requests": 0,
        "success": 0,
        "total_latency_ms": 0,
        "total_tokens": 0,
        "target_model": "",
        "matched_signals": defaultdict(int),
    }


def _finalize_bucket(bucket: dict) -> dict:
    req = bucket.get("requests", 0)
    succ = bucket.get("success", 0)
    return {
        "requests": req,
        "success": succ,
        "success_rate": (succ / req * 100.0) if req else 0.0,
        "avg_latency_ms": (bucket.get("total_latency_ms", 0) / req) if req else 0.0,
        "avg_tokens": (bucket.get("total_tokens", 0) / req) if req else 0.0,
        "target_model": bucket.get("target_model", ""),
    }


def _sorted_counts(raw: dict[str, int]) -> dict[str, int]:
    return dict(sorted(raw.items(), key=lambda kv: (-kv[1], kv[0])))


# ── analysis ─────────────────────────────────────────────────────────────

def analyze(records: list[dict]) -> dict:
    """Produce analysis from collected records."""
    # Separate: only auto-activated requests for factor/category analysis
    auto_records = []
    all_with_router = []
    for rec in records:
        mr = (rec.get("routing") or {}).get("model_router", {})
        all_with_router.append(rec)
        requested = mr.get("requested_model", "")
        if requested in ("auto", "auto-canary"):
            auto_records.append(rec)

    # 1) Factor distributions (auto requests only)
    factor_values: dict[str, list[float]] = defaultdict(list)
    factor_fields = ("messages_count", "tools_count", "prompt_chars",
                     "failure_streak", "success_streak")
    for rec in auto_records:
        factors = (rec.get("routing") or {}).get("model_router", {}).get("factors", {})
        for field in factor_fields:
            val = factors.get(field)
            if val is None:
                continue
            try:
                factor_values[field].append(float(val))
            except (TypeError, ValueError):
                continue
    factor_pcts = {field: percentiles(vals) for field, vals in factor_values.items()}

    # 2) Rule/category hit rates + category detail
    rule_hits: dict[str, int] = defaultdict(int)
    rule_targets: dict[str, str] = {}
    category_hits: dict[str, int] = defaultdict(int)
    category_targets: dict[str, str] = {}

    category_buckets: dict[str, dict] = defaultdict(_empty_category_bucket)
    fallback_bucket: dict = _empty_category_bucket()
    fallback_task_category: dict[str, int] = defaultdict(int)
    fallback_tool_profile: dict[str, int] = defaultdict(int)
    fallback_system_prompt_type: dict[str, int] = defaultdict(int)

    for rec in auto_records:
        routing = rec.get("routing") or {}
        mr = routing.get("model_router", {}) or {}
        factors = mr.get("factors", {}) or {}
        suggested = str(mr.get("suggested_model") or mr.get("resolved_model") or "").strip()
        hit = mr.get("hit_rule")
        hit_name = _extract_hit_rule_name(hit)
        matched_signal = _extract_matched_signal(hit)
        is_ok = _is_success(rec)
        duration_ms = rec.get("duration_ms") or 0
        total_tokens = _extract_total_tokens(rec)

        if hit_name:
            rule_hits[hit_name] += 1
            if suggested:
                rule_targets[hit_name] = suggested
        else:
            rule_hits["(default)"] += 1
            if suggested:
                rule_targets["(default)"] = suggested

        cat_name = ""
        if hit_name.startswith("cat_"):
            cat_name = hit_name[4:] or "unnamed"

        if cat_name:
            category_hits[cat_name] += 1
            if suggested:
                category_targets[cat_name] = suggested
            bucket = category_buckets[cat_name]
            bucket["requests"] += 1
            bucket["success"] += 1 if is_ok else 0
            bucket["total_latency_ms"] += duration_ms
            bucket["total_tokens"] += total_tokens
            if suggested:
                bucket["target_model"] = suggested
            if matched_signal:
                bucket["matched_signals"][matched_signal] += 1
        else:
            fallback_bucket["requests"] += 1
            fallback_bucket["success"] += 1 if is_ok else 0
            fallback_bucket["total_latency_ms"] += duration_ms
            fallback_bucket["total_tokens"] += total_tokens
            fallback_bucket["target_model"] = "(fallback)"

            task_category = str(factors.get("task_category") or "unknown").strip().lower() or "unknown"
            tool_profile = str(factors.get("tool_profile") or "unknown").strip().lower() or "unknown"
            fallback_task_category[task_category] += 1
            fallback_tool_profile[tool_profile] += 1
            for tag in _extract_system_prompt_types(factors):
                fallback_system_prompt_type[tag] += 1

    total_auto = len(auto_records)

    finalized_categories = {}
    for cat_name, bucket in category_buckets.items():
        entry = _finalize_bucket(bucket)
        signals = bucket.get("matched_signals", {}) or {}
        entry["matched_signals"] = _sorted_counts(dict(signals))
        if not entry["target_model"]:
            entry["target_model"] = category_targets.get(cat_name, "")
        finalized_categories[cat_name] = entry

    fallback_summary = _finalize_bucket(fallback_bucket)
    fallback_summary["task_category_hits"] = _sorted_counts(dict(fallback_task_category))
    fallback_summary["tool_profile_hits"] = _sorted_counts(dict(fallback_tool_profile))
    fallback_summary["system_prompt_type_hits"] = _sorted_counts(dict(fallback_system_prompt_type))

    # 3) Model performance (all router-enabled requests, grouped by final model)
    model_perf: dict[str, dict] = defaultdict(lambda: {
        "requests": 0, "success": 0, "total_latency_ms": 0,
        "input_tokens": 0, "output_tokens": 0,
    })
    for rec in all_with_router:
        mr = (rec.get("routing") or {}).get("model_router", {})
        # Use resolved_model as the final routed model; fall back to request model
        model = mr.get("resolved_model") or rec.get("request", {}).get("model", "(unknown)")
        bucket = model_perf[model]
        bucket["requests"] += 1
        status = (rec.get("response") or {}).get("status_code", 0)
        if 200 <= status < 300:
            bucket["success"] += 1
        bucket["total_latency_ms"] += rec.get("duration_ms") or 0
        usage = rec.get("usage") or {}
        bucket["input_tokens"] += (usage.get("input_tokens") or 0) + \
                                  (usage.get("cache_creation_input_tokens") or 0) + \
                                  (usage.get("cache_read_input_tokens") or 0) + \
                                  (usage.get("prompt_tokens") or 0)
        ptd = usage.get("prompt_tokens_details") or {}
        bucket["input_tokens"] += (ptd.get("cached_tokens") or 0)
        bucket["output_tokens"] += (usage.get("output_tokens") or 0) + \
                                   (usage.get("completion_tokens") or 0)

    return {
        "total_router_records": len(all_with_router),
        "total_auto_records": total_auto,
        "factor_percentiles": factor_pcts,
        "rule_hits": dict(rule_hits),
        "rule_targets": rule_targets,
        "category_hits": dict(category_hits),
        "category_targets": category_targets,
        "category_analysis": {
            "categories": finalized_categories,
            "fallback": fallback_summary,
        },
        "model_perf": dict(model_perf),
    }


# ── suggestions ──────────────────────────────────────────────────────────

# Current threshold rules for comparison
CURRENT_THRESHOLDS = {
    "medium": {
        "messages_count": 10,
        "tools_count": 3,
        "prompt_chars": 5000,
    },
    "medium_high": {
        "messages_count": 25,
        "tools_count": 6,
        "prompt_chars": 15000,
    },
    "high_complexity": {
        "messages_count": 50,
        "tools_count": 10,
    },
}

# Target: each tier should capture roughly this percentile range
TIER_TARGETS = {
    "medium": {"pct_above": 50, "field_pcts": {"messages_count": 50, "tools_count": 50, "prompt_chars": 50}},
    "medium_high": {"pct_above": 25, "field_pcts": {"messages_count": 75, "tools_count": 75, "prompt_chars": 75}},
    "high_complexity": {"pct_above": 10, "field_pcts": {"messages_count": 90, "tools_count": 90}},
}


def suggest_thresholds(analysis: dict) -> list[str]:
    """Generate threshold adjustment suggestions based on factor distributions."""
    suggestions = []
    fp = analysis.get("factor_percentiles", {})

    for rule_name, target in TIER_TARGETS.items():
        current = CURRENT_THRESHOLDS.get(rule_name, {})
        for field, target_pct in target["field_pcts"].items():
            if field not in fp:
                continue
            pcts = fp[field]
            observed_at_target = pcts.get(target_pct, 0)
            current_val = current.get(field)
            if current_val is None:
                continue

            # If current threshold captures too many or too few requests
            # compared to the target percentile, suggest adjustment
            p25 = pcts.get(25, 0)
            p75 = pcts.get(75, 0)

            if current_val < p25 and target_pct >= 50:
                suggested = int(round(observed_at_target))
                if suggested != current_val:
                    suggestions.append(
                        f"  {rule_name}.{field}={current_val} 偏低 "
                        f"(P25={p25:.0f}, P{target_pct}={observed_at_target:.0f})"
                        f" -> 建议调整到 {suggested}"
                    )
            elif current_val > p75 and target_pct <= 50:
                suggested = int(round(observed_at_target))
                if suggested != current_val:
                    suggestions.append(
                        f"  {rule_name}.{field}={current_val} 偏高 "
                        f"(P75={p75:.0f}, P{target_pct}={observed_at_target:.0f})"
                        f" -> 建议调整到 {suggested}"
                    )

    return suggestions


def suggest_categories(analysis: dict) -> list[str]:
    """Generate category-oriented suggestions."""
    suggestions = []
    total = analysis.get("total_auto_records", 0)
    if total <= 0:
        return suggestions

    category_analysis = analysis.get("category_analysis", {})
    categories = category_analysis.get("categories", {})
    fallback = category_analysis.get("fallback", {})

    # 1) Categories with low success rate under meaningful volume
    ordered_cats = sorted(
        categories.items(),
        key=lambda kv: kv[1].get("requests", 0),
        reverse=True,
    )
    for cat_name, stats in ordered_cats:
        req = int(stats.get("requests", 0))
        ok = float(stats.get("success_rate", 0.0))
        target = stats.get("target_model") or "?"
        if req >= 15 and ok < 97.0:
            suggestions.append(
                f'  分类 "{cat_name}" 成功率 {ok:.1f}% ({req} req, target={target}) 偏低 '
                f"-> 建议评估升档模型或拆分该 category signals"
            )

    # 2) Category fallback too high
    fallback_req = int(fallback.get("requests", 0))
    fallback_ok = float(fallback.get("success_rate", 0.0))
    if fallback_req > 0:
        fallback_pct = fallback_req / total * 100.0
        if fallback_pct >= 10.0:
            suggestions.append(
                f"  categories fallback 占比 {fallback_pct:.1f}% ({fallback_req}/{total}, OK={fallback_ok:.1f}%) "
                f"-> 建议补充 task_category/tool_profile/system_prompt_type signals"
            )

        task_hits = fallback.get("task_category_hits", {}) or {}
        min_task_hits = max(5, int(math.ceil(fallback_req * 0.15)))
        for task_name, count in sorted(task_hits.items(), key=lambda kv: kv[1], reverse=True)[:3]:
            if task_name in {"", "unknown"}:
                continue
            if count >= min_task_hits:
                suggestions.append(
                    f"  fallback 高频 task_category={task_name} ({count}/{fallback_req}) "
                    f"-> 可新增 signal: task_category:{task_name}"
                )

        tool_hits = fallback.get("tool_profile_hits", {}) or {}
        min_tool_hits = max(5, int(math.ceil(fallback_req * 0.15)))
        for tool_name, count in sorted(tool_hits.items(), key=lambda kv: kv[1], reverse=True)[:2]:
            if tool_name in {"", "unknown", "none"}:
                continue
            if count >= min_tool_hits:
                suggestions.append(
                    f"  fallback 高频 tool_profile={tool_name} ({count}/{fallback_req}) "
                    f"-> 可新增 signal: tool_profile:{tool_name}"
                )

        system_hits = fallback.get("system_prompt_type_hits", {}) or {}
        min_system_hits = max(5, int(math.ceil(fallback_req * 0.2)))
        for sys_type, count in sorted(system_hits.items(), key=lambda kv: kv[1], reverse=True)[:2]:
            if not sys_type:
                continue
            if count >= min_system_hits:
                suggestions.append(
                    f"  fallback 高频 system_prompt_type={sys_type} ({count}/{fallback_req}) "
                    f"-> 可新增 signal: system_prompt_type:{sys_type}"
                )

    return suggestions


def suggest_all(analysis: dict) -> dict:
    threshold = suggest_thresholds(analysis)
    category = suggest_categories(analysis)
    return {
        "threshold": threshold,
        "category": category,
        "all": threshold + category,
    }


# ── output ───────────────────────────────────────────────────────────────

def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _print_category_distribution(analysis: dict) -> None:
    total = analysis.get("total_auto_records", 0)
    category_hits = analysis.get("category_hits", {})
    category_targets = analysis.get("category_targets", {})
    if not category_hits or total <= 0:
        return
    print("Category Distribution:")
    for cat, count in sorted(category_hits.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        target = category_targets.get(cat, "?")
        print(f"  {cat:<24s} {pct:5.1f}%  ({count:>4d} hits)  -> {target}")
    cat_total = sum(category_hits.values())
    cat_pct = cat_total / total * 100 if total else 0
    print(f"  {'(category total)':<24s} {cat_pct:5.1f}%  ({cat_total:>4d} hits)")
    print()


def _print_detailed_category_analysis(analysis: dict) -> None:
    total = analysis.get("total_auto_records", 0)
    details = (analysis.get("category_analysis") or {})
    categories = details.get("categories") or {}
    fallback = details.get("fallback") or {}

    print("=== Category Analysis ===")
    if total <= 0 or (not categories and not fallback.get("requests")):
        print("  No category activity in selected period.")
        print()
        return

    for cat_name, stats in sorted(categories.items(), key=lambda kv: kv[1].get("requests", 0), reverse=True):
        req = int(stats.get("requests", 0))
        pct = req / total * 100 if total else 0
        ok = float(stats.get("success_rate", 0.0))
        avg_lat = float(stats.get("avg_latency_ms", 0.0))
        avg_tok = int(round(float(stats.get("avg_tokens", 0.0))))
        target = stats.get("target_model") or "?"
        print(
            f"  {cat_name:<16s} {req:>5d} ({pct:5.1f}%) "
            f"-> {target:<12s} OK={ok:5.1f}% Lat={avg_lat:7.0f}ms Tok={_fmt_tokens(avg_tok):>6s}"
        )
        signals = stats.get("matched_signals") or {}
        if signals:
            signal_preview = ", ".join(f"{sig}({cnt})" for sig, cnt in list(signals.items())[:3])
            print(f"    top_signals: {signal_preview}")

    fallback_req = int(fallback.get("requests", 0))
    if fallback_req > 0:
        pct = fallback_req / total * 100 if total else 0
        ok = float(fallback.get("success_rate", 0.0))
        avg_lat = float(fallback.get("avg_latency_ms", 0.0))
        avg_tok = int(round(float(fallback.get("avg_tokens", 0.0))))
        print(
            f"  {'(fallback)':<16s} {fallback_req:>5d} ({pct:5.1f}%) "
            f"-> threshold/default OK={ok:5.1f}% Lat={avg_lat:7.0f}ms Tok={_fmt_tokens(avg_tok):>6s}"
        )
        task_hits = fallback.get("task_category_hits", {}) or {}
        if task_hits:
            preview = ", ".join(f"{k}({v})" for k, v in list(task_hits.items())[:4])
            print(f"    fallback.task_category: {preview}")
        tool_hits = fallback.get("tool_profile_hits", {}) or {}
        if tool_hits:
            preview = ", ".join(f"{k}({v})" for k, v in list(tool_hits.items())[:4])
            print(f"    fallback.tool_profile: {preview}")
        system_hits = fallback.get("system_prompt_type_hits", {}) or {}
        if system_hits:
            preview = ", ".join(f"{k}({v})" for k, v in list(system_hits.items())[:4])
            print(f"    fallback.system_prompt_type: {preview}")
    print()


def print_report(analysis: dict, show_suggest: bool, analyze_categories: bool) -> None:
    total = analysis.get("total_auto_records", 0)
    print("=== Auto Model Router Analysis ===")
    print(f"  Router-enabled requests: {analysis.get('total_router_records', 0)}")
    print(f"  Auto-activated requests: {total}")
    print()

    # Factor distributions
    fp = analysis.get("factor_percentiles", {})
    if fp:
        print("Factor Distribution (auto requests only):")
        for field in ("messages_count", "tools_count", "prompt_chars",
                      "failure_streak", "success_streak"):
            if field not in fp:
                continue
            pcts = fp[field]
            parts = "  ".join(f"P{p}={v:.0f}" for p, v in sorted(pcts.items()))
            print(f"  {field:<20s} {parts}")
        print()

    _print_category_distribution(analysis)
    if analyze_categories:
        _print_detailed_category_analysis(analysis)

    # Rule hit rates
    rule_hits = analysis.get("rule_hits", {})
    rule_targets = analysis.get("rule_targets", {})
    if rule_hits and total > 0:
        print("Rule Hit Rate:")
        for rule, count in sorted(rule_hits.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            target = rule_targets.get(rule, "?")
            print(f"  {rule:<24s} {pct:5.1f}%  ({count:>4d} hits)  -> {target}")
        print()

    # Model performance
    mp = analysis.get("model_perf", {})
    if mp:
        print("Model Performance:")
        print(f"  {'Model':<14s} {'Reqs':>6s}  {'OK%':>6s}  {'AvgLat':>8s}  {'AvgTok':>8s}")
        for model in sorted(mp, key=lambda m: mp[m]["requests"], reverse=True):
            perf = mp[model]
            reqs = perf["requests"]
            ok_pct = f"{perf['success'] / reqs * 100:.1f}%" if reqs else "-"
            avg_lat = f"{perf['total_latency_ms'] / reqs:,.0f}ms" if reqs else "-"
            avg_tok = _fmt_tokens((perf["input_tokens"] + perf["output_tokens"]) // max(reqs, 1))
            print(f"  {model:<14s} {reqs:>6d}  {ok_pct:>6s}  {avg_lat:>8s}  {avg_tok:>8s}")
        print()

    # Suggestions
    if show_suggest:
        suggestions = suggest_all(analysis)
        has_threshold = bool(suggestions["threshold"])
        has_category = bool(suggestions["category"])
        if has_threshold or has_category:
            print("Suggestions:")
            if has_threshold:
                print("  [threshold]")
                for item in suggestions["threshold"]:
                    print(item)
            if has_category:
                print("  [category]")
                for item in suggestions["category"]:
                    print(item)
        else:
            print("Suggestions: (no adjustments needed based on current data)")
        print()


def print_json_report(analysis: dict, show_suggest: bool, analyze_categories: bool) -> None:
    out = dict(analysis)
    if not analyze_categories:
        # Keep base payload compact unless explicit category analysis is requested.
        out.pop("category_analysis", None)
    if show_suggest:
        suggestions = suggest_all(analysis)
        out["threshold_suggestions"] = suggestions["threshold"]
        out["category_suggestions"] = suggestions["category"]
        out["suggestions"] = suggestions["all"]
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


# ── main ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze auto model router logs and suggest threshold/category adjustments"
    )
    parser.add_argument(
        "--base-dir", default="",
        help="Runtime base dir (default: auto-detect)",
    )
    parser.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    parser.add_argument("--suggest", action="store_true", help="Show threshold + category suggestions")
    parser.add_argument("--analyze-categories", action="store_true", help="Show detailed category analysis")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    _apply_base_dir(_resolve_base_dir(args.base_dir))
    # Re-import LOGS_DIR after apply
    from usage_stats import LOGS_DIR as logs_dir
    global LOGS_DIR
    LOGS_DIR = logs_dir

    records = _collect_records(args.days)
    if not records:
        print("No model_router records found in the selected period.", file=sys.stderr)
        sys.exit(1)

    result = analyze(records)

    if args.json_output:
        print_json_report(result, args.suggest, args.analyze_categories)
    else:
        print_report(result, args.suggest, args.analyze_categories)


if __name__ == "__main__":
    main()
