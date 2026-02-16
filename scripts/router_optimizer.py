#!/usr/bin/env python3
"""Auto Model Router analysis and threshold optimization.

Reads JSONL request logs to analyze model_router behavior:
- Factor distributions (percentiles) for auto-routed requests
- Rule hit rates
- Per-model performance (success rate, latency, tokens)
- Threshold adjustment suggestions

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


# ── analysis ─────────────────────────────────────────────────────────────

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


def analyze(records: list[dict]) -> dict:
    """Produce analysis from collected records."""
    # Separate: only auto-activated requests for factor analysis
    auto_records = []
    all_with_router = []

    for rec in records:
        mr = (rec.get("routing") or {}).get("model_router", {})
        all_with_router.append(rec)
        requested = mr.get("requested_model", "")
        if requested in ("auto", "auto-canary"):
            auto_records.append(rec)

    # 1. Factor distributions (auto requests only)
    factor_values: dict[str, list[float]] = defaultdict(list)
    factor_fields = ("messages_count", "tools_count", "prompt_chars",
                     "failure_streak", "success_streak")
    for rec in auto_records:
        factors = (rec.get("routing") or {}).get("model_router", {}).get("factors", {})
        for f in factor_fields:
            v = factors.get(f)
            if v is not None:
                factor_values[f].append(float(v))

    factor_pcts = {f: percentiles(vals) for f, vals in factor_values.items()}

    # 2. Rule hit rates (auto requests only)
    rule_hits: dict[str, int] = defaultdict(int)
    rule_targets: dict[str, str] = {}
    for rec in auto_records:
        mr = (rec.get("routing") or {}).get("model_router", {})
        hit = mr.get("hit_rule")
        if hit:
            rule_hits[hit] += 1
            # Try to capture target from suggested_model
            suggested = mr.get("suggested_model", "")
            if suggested:
                rule_targets[hit] = suggested
        else:
            rule_hits["(default)"] += 1
            rule_targets["(default)"] = mr.get("suggested_model", "g3f")

    total_auto = len(auto_records)

    # 3. Model performance (all router-enabled requests, grouped by final model)
    model_perf: dict[str, dict] = defaultdict(lambda: {
        "requests": 0, "success": 0, "total_latency_ms": 0,
        "input_tokens": 0, "output_tokens": 0,
    })
    for rec in all_with_router:
        mr = (rec.get("routing") or {}).get("model_router", {})
        # Use resolved_model as the final routed model; fall back to request model
        model = mr.get("resolved_model") or rec.get("request", {}).get("model", "(unknown)")
        mp = model_perf[model]
        mp["requests"] += 1
        status = (rec.get("response") or {}).get("status_code", 0)
        if 200 <= status < 300:
            mp["success"] += 1
        mp["total_latency_ms"] += rec.get("duration_ms") or 0
        usage = rec.get("usage") or {}
        mp["input_tokens"] += (usage.get("input_tokens") or 0) + \
                              (usage.get("cache_creation_input_tokens") or 0) + \
                              (usage.get("cache_read_input_tokens") or 0) + \
                              (usage.get("prompt_tokens") or 0)
        mp["output_tokens"] += (usage.get("output_tokens") or 0) + \
                               (usage.get("completion_tokens") or 0)

    return {
        "total_router_records": len(all_with_router),
        "total_auto_records": total_auto,
        "factor_percentiles": factor_pcts,
        "rule_hits": dict(rule_hits),
        "rule_targets": rule_targets,
        "model_perf": dict(model_perf),
    }


# ── suggestions ──────────────────────────────────────────────────────────

# Current rule thresholds for comparison
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
    fp = analysis["factor_percentiles"]

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
            p50 = pcts.get(50, 0)
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


# ── output ───────────────────────────────────────────────────────────────

def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def print_report(analysis: dict, show_suggest: bool) -> None:
    total = analysis["total_auto_records"]
    print(f"=== Auto Model Router Analysis ===")
    print(f"  Router-enabled requests: {analysis['total_router_records']}")
    print(f"  Auto-activated requests: {total}")
    print()

    # Factor distributions
    fp = analysis["factor_percentiles"]
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

    # Rule hit rates
    rule_hits = analysis["rule_hits"]
    rule_targets = analysis["rule_targets"]
    if rule_hits and total > 0:
        print("Rule Hit Rate:")
        for rule, count in sorted(rule_hits.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            target = rule_targets.get(rule, "?")
            print(f"  {rule:<24s} {pct:5.1f}%  ({count:>4d} hits)  -> {target}")
        print()

    # Model performance
    mp = analysis["model_perf"]
    if mp:
        print("Model Performance:")
        hdr = f"  {'Model':<14s} {'Reqs':>6s}  {'OK%':>6s}  {'AvgLat':>8s}  {'AvgTok':>8s}"
        print(hdr)
        for model in sorted(mp, key=lambda m: mp[m]["requests"], reverse=True):
            p = mp[model]
            reqs = p["requests"]
            ok_pct = f"{p['success'] / reqs * 100:.1f}%" if reqs else "-"
            avg_lat = f"{p['total_latency_ms'] / reqs:,.0f}ms" if reqs else "-"
            avg_tok = _fmt_tokens((p["input_tokens"] + p["output_tokens"]) // max(reqs, 1))
            print(f"  {model:<14s} {reqs:>6d}  {ok_pct:>6s}  {avg_lat:>8s}  {avg_tok:>8s}")
        print()

    # Suggestions
    if show_suggest:
        suggestions = suggest_thresholds(analysis)
        if suggestions:
            print("Suggestions:")
            for s in suggestions:
                print(s)
        else:
            print("Suggestions: (no adjustments needed based on current data)")
        print()


def print_json_report(analysis: dict, show_suggest: bool) -> None:
    out = dict(analysis)
    if show_suggest:
        out["suggestions"] = suggest_thresholds(analysis)
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


# ── main ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze auto model router logs and suggest threshold adjustments"
    )
    parser.add_argument(
        "--base-dir", default="",
        help="Runtime base dir (default: auto-detect)",
    )
    parser.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    parser.add_argument("--suggest", action="store_true", help="Show threshold suggestions")
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
        print_json_report(result, args.suggest)
    else:
        print_report(result, args.suggest)


if __name__ == "__main__":
    main()
