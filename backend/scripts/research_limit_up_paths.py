"""Read-only, historical exploratory comparison. No signals enter the application.

Run from the repository root with a Python environment containing numpy:
  python backend/scripts/research_limit_up_paths.py
Definitions are fixed in RULES and classify(); there is no parameter search.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

import numpy as np

RULES = {
    "first_board": "1进2参照：T日收盘涨停、板高1、前一交易日未收盘涨停。",
    "second_board": "2进3：T日收盘涨停、板高2、前一交易日收盘涨停且板高1。",
    "consolidation": "横盘缩量：距最近涨停2-4日；涨停后全部日K最高/最低-1<=8%；T收盘较涨停收盘-5%至+8%；涨停后平均量/涨停日量<=0.75；该量价窗口来源标签一致。",
    "mild_raw": "温和回撤对照：距最近涨停1-4日；T收盘较涨停收盘回落3%-8%。",
    "mild_stable": "温和回撤企稳：满足温和回撤；距涨停至少2日；T收盘高于前收且收盘在当日振幅上方40%区域。",
    "strong_nonconsecutive": "强势不连板：距最近涨停2-4日；T收盘高于涨停收盘；连续两个交易日收盘抬升；涨停后最低价不低于涨停收盘的95%。",
    "broken_repair": "断板修复：最近涨停板高>=2；距涨停2-4日；T收盘高于前日最高价且不低于涨停收盘的88%。",
    "deep_raw": "深回撤对照：距最近涨停1-4日；T收盘较涨停收盘回落10%-25%。",
    "deep_repair": "深回撤修复：满足深回撤；距涨停至少2日；T收盘高于前收且收盘在当日振幅上方40%区域。",
}
LABELS = {
    "first_board": "1进2参照", "second_board": "2进3", "consolidation": "横盘缩量",
    "mild_raw": "温和回撤对照", "mild_stable": "温和回撤企稳",
    "strong_nonconsecutive": "强势不连板", "broken_repair": "断板修复",
    "deep_raw": "深回撤对照", "deep_repair": "深回撤修复",
}


def supported(event):
    return (event["symbol"].startswith(("600", "601", "603", "605", "000", "001", "002", "003"))
            and "ST" not in event["name"].upper() and "退" not in event["name"])


def valid_bar(b):
    return bool(b and all(b.get(k) is not None and math.isfinite(b[k]) and b[k] > 0
                         for k in ("open", "high", "low", "close"))
                and b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"])


def price_break(bars):
    """Conservative discontinuity flag, NOT a corporate-action adjustment."""
    return any(abs(b["close"] / a["close"] - 1) > 0.111
               or b["high"] / a["close"] - 1 > 0.112
               or b["low"] / a["close"] - 1 < -0.112
               for a, b in zip(bars, bars[1:]))


def classify(anchor, window, previous_event=None):
    """window ends exactly at signal T; this function never receives future bars."""
    age = len(window) - 1
    last = window[-1]
    if age == 0:
        prev_closed = bool(previous_event and previous_event["closed_limit"])
        if anchor["board_height"] == 1 and not prev_closed:
            return ["first_board"]
        if anchor["board_height"] == 2 and prev_closed and previous_event["board_height"] == 1:
            return ["second_board"]
        return []
    retreat = last["close"] / window[0]["close"] - 1
    position = ((last["close"] - last["low"]) / (last["high"] - last["low"])
                if last["high"] > last["low"] else 0.5)
    stable = age >= 2 and last["close"] > window[-2]["close"] and position >= 0.6
    tags = []
    if -0.08 <= retreat <= -0.03:
        tags.append("mild_raw")
        if stable:
            tags.append("mild_stable")
    if -0.25 <= retreat <= -0.10:
        tags.append("deep_raw")
        if stable:
            tags.append("deep_repair")
    if age >= 2:
        post = window[1:]
        low, high = min(x["low"] for x in post), max(x["high"] for x in post)
        volume_ok = (all(x.get("volume") is not None and x["volume"] > 0 for x in window)
                     and len({x["source"] for x in window}) == 1)
        if (-0.05 <= retreat <= 0.08 and high / low - 1 <= 0.08 and volume_ok
                and mean(x["volume"] for x in post) / window[0]["volume"] <= 0.75):
            tags.append("consolidation")
        if (retreat > 0 and last["close"] > window[-2]["close"] > window[-3]["close"]
                and low >= window[0]["close"] * 0.95):
            tags.append("strong_nonconsecutive")
        if (anchor["board_height"] >= 2 and last["close"] > window[-2]["high"]
                and retreat >= -0.12):
            tags.append("broken_repair")
    return tags


def attach_outcome(row, bars, calendar, events):
    """Use exact market dates; never substitute next available bar for missing D+1."""
    i = calendar.index(row["signal_date"])
    horizon = calendar[i + 1:i + 6]
    result = {"outcome_status": "immature", "r3": None, "r5": None}
    if len(horizon) < 5:
        return result
    future = [bars.get((row["symbol"], d)) for d in horizon]
    if not all(valid_bar(b) for b in future):
        return {**result, "outcome_status": "missing_bar"}
    signal_bar = bars[(row["symbol"], row["signal_date"])]
    if price_break([signal_bar, *future]):
        return {**result, "outcome_status": "price_discontinuity"}
    base = future[0]["open"]
    e = events.get((row["symbol"], horizon[0]))
    return {
        "outcome_status": "complete", "outcome_start": horizon[0], "outcome_end": horizon[-1],
        "r3": (future[2]["close"] / base - 1) * 100,
        "r5": (future[4]["close"] / base - 1) * 100,
        "r1": (future[0]["close"] / base - 1) * 100,
        "gap": (base / signal_bar["close"] - 1) * 100,
        "mae5": (min(b["low"] for b in future) / base - 1) * 100,
        "mfe5": (max(b["high"] for b in future) / base - 1) * 100,
        "next_closed": int(bool(e and e["closed_limit"])),
        "one_price_up": int(future[0]["high"] - future[0]["low"] <= 0.011
                            and base / signal_bar["close"] > 1.085),
        "same_source": len({b["source"] for b in [signal_bar, *future]}) == 1,
        "all_tencent_history": all(b["source"] == "akshare.stock_zh_a_hist_tx"
                                   for b in [signal_bar, *future]),
    }


def first_triggers(pool):
    """Deduplicate from signal information before inspecting outcome availability."""
    seen, result = set(), []
    for row in sorted(pool, key=lambda x: (x["signal_date"], x["symbol"])):
        for tag in row["tags"]:
            key = row["symbol"], row["anchor_date"], tag
            if key not in seen:
                result.append({**row, "strategy": tag})
                seen.add(key)
    return result


def match_baselines(signals, pool):
    groups = defaultdict(list)
    for r in pool:
        if r["outcome_status"] == "complete":
            groups[(r["signal_date"], r["age"])].append(r)
    for r in signals:
        # Exact date / exact age, excluding the signal stock. Other tags may overlap.
        peers = [p for p in groups[(r["signal_date"], r["age"])] if p["symbol"] != r["symbol"]]
        r["baseline_n"] = len(peers)
        r["baseline_r5"] = mean(p["r5"] for p in peers) if peers else None
        r["excess5"] = (r["r5"] - r["baseline_r5"]
                         if r["outcome_status"] == "complete" and peers else None)


def same_time_control(pool, broad, narrow):
    """Compare the refinement at the SAME date/age, not two shifted triggers.

    Daily observations are intentionally not first-trigger deduplicated here;
    this is a separate descriptive ablation, not another strategy estimate.
    """
    groups = defaultdict(list)
    for r in pool:
        if broad in r["tags"] and r["outcome_status"] == "complete":
            groups[(r["signal_date"], r["age"])].append(r)
    differences = []
    for group in groups.values():
        control = [r for r in group if narrow not in r["tags"]]
        if control:
            baseline = mean(r["r5"] for r in control)
            differences.extend({**r, "excess5": r["r5"] - baseline}
                               for r in group if narrow in r["tags"])
    return metric(differences)


def block_interval(rows, dates, reps=2000):
    """Circular 5-session moving block bootstrap; equal weight to signal days.

    Point estimate is mean daily matched difference. Interval is descriptive,
    not a significance test adjusted for the nine examined definitions.
    """
    by_date = defaultdict(list)
    for r in rows:
        if r.get("excess5") is not None:
            by_date[r["signal_date"]].append(r["excess5"])
    if len(by_date) < 8:
        return None
    values = np.array([mean(by_date[d]) if d in by_date else np.nan for d in dates])
    rng = np.random.default_rng(20260907)
    boot = []
    for _ in range(reps):
        starts = rng.integers(0, len(dates), size=math.ceil(len(dates) / 5))
        indices = np.concatenate([(s + np.arange(5)) % len(dates) for s in starts])[:len(dates)]
        sample = values[indices]
        if np.any(np.isfinite(sample)):
            boot.append(float(np.nanmean(sample)))
    return [float(x) for x in np.quantile(boot, [0.025, 0.975])]


def metric(rows):
    if not rows:
        return {"n": 0, "dates": 0}
    r5 = [r["r5"] for r in rows]
    matched = [r for r in rows if r.get("excess5") is not None]
    daily = defaultdict(list)
    for r in matched:
        daily[r["signal_date"]].append(r["excess5"])
    return {"n": len(rows), "dates": len({r["signal_date"] for r in rows}),
            "symbols": len({r["symbol"] for r in rows}), "mean3": mean(r["r3"] for r in rows),
            "mean5": mean(r5), "median5": median(r5), "positive5_pct": mean(x > 0 for x in r5) * 100,
            "p10_5": float(np.quantile(r5, 0.1)), "below_minus5_pct": mean(x < -5 for x in r5) * 100,
            "mae5_median": median(r["mae5"] for r in rows),
            "mfe5_median": median(r["mfe5"] for r in rows),
            "gap_mean": mean(r["gap"] for r in rows),
            "one_price_up_n": sum(r["one_price_up"] for r in rows),
            "next_closed_pct": mean(r["next_closed"] for r in rows) * 100,
            "matched_n": len(matched), "matched_mean5": mean(r["excess5"] for r in matched) if matched else None,
            "daily_matched_mean5": mean(mean(x) for x in daily.values()) if daily else None}


def nonoverlap(rows, calendar):
    """Greedy symbol-level six-session spacing, decided from ALL signals."""
    last, kept = {}, []
    index = {d: i for i, d in enumerate(calendar)}
    for row in sorted(rows, key=lambda x: (x["signal_date"], x["symbol"])):
        i = index[row["signal_date"]]
        if i > last.get(row["symbol"], -100) + 5:
            kept.append(row)
            last[row["symbol"]] = i
    return kept


def analyze(db, start, output):
    query_events = "SELECT * FROM limit_up_events ORDER BY trade_date,symbol"
    query_bars = "SELECT symbol,trade_date,open,high,low,close,volume,source FROM stock_daily_bars ORDER BY trade_date,symbol"
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        event_rows = [dict(r) for r in con.execute(query_events)]
        bar_rows = [dict(r) for r in con.execute(query_bars)]
    events = {(r["symbol"], r["trade_date"]): r for r in event_rows}
    bars = {(r["symbol"], r["trade_date"]): r for r in bar_rows}
    calendar = sorted({r["trade_date"] for r in bar_rows})
    block = [d for d in calendar if d >= start]
    if len(block) < 10 or any(d not in {r["trade_date"] for r in event_rows} for d in block):
        raise ValueError("Need at least 10 consecutive market dates with event records; choose another start")
    index = {d: i for i, d in enumerate(calendar)}
    eligible_events = [r for r in event_rows if r["closed_limit"] and supported(r)]
    by_date = defaultdict(list)
    for r in eligible_events:
        by_date[r["trade_date"]].append(r)
    pool, missing, daily_coverage = [], Counter(), []
    for d in block[4:]:
        i = index[d]
        latest = {}
        for anchor_day in calendar[i - 4:i + 1]:
            for event in by_date[anchor_day]:
                latest[event["symbol"]] = event
        counts = Counter({"date": 0})
        counts.pop("date")
        counts["raw_pool"] = len(latest)
        for symbol, anchor in sorted(latest.items()):
            a = index[anchor["trade_date"]]
            # 20 observed consecutive market bars through T, no future selection here.
            history = [bars.get((symbol, t)) for t in calendar[i - 19:i + 1]]
            window = [bars.get((symbol, t)) for t in calendar[a:i + 1]]
            reason = None
            if len(history) != 20 or not all(valid_bar(b) for b in history):
                reason = "missing_history20"
            elif price_break(history):
                reason = "historical_discontinuity"
            else:
                prev_anchor = bars.get((symbol, calendar[a - 1]))
                if not valid_bar(prev_anchor) or not (0.085 <= window[0]["close"] / prev_anchor["close"] - 1 <= 0.111):
                    reason = "anchor_price_event_mismatch"
            if reason:
                missing[reason] += 1
                counts[reason] += 1
                continue
            previous_event = events.get((symbol, calendar[a - 1]))
            row = {"symbol": symbol, "name": anchor["name"], "signal_date": d,
                   "anchor_date": anchor["trade_date"], "age": i - a,
                   "board_height": anchor["board_height"], "tags": classify(anchor, window, previous_event),
                   "signal_source": window[-1]["source"],
                   "anchor_to_signal_pct": (window[-1]["close"] / window[0]["close"] - 1) * 100}
            pool.append(row)
            counts["feature_eligible"] += 1
        daily_coverage.append({"date": d, **counts})
    signals = first_triggers(pool)
    for row in pool:
        row.update(attach_outcome(row, bars, calendar, events))
    outcome_by_key = {(r["symbol"], r["signal_date"]): r for r in pool}
    for row in signals:
        row.update(outcome_by_key[(row["symbol"], row["signal_date"])])
    match_baselines(signals, pool)
    mature_dates = block[4:-5]
    for daily in daily_coverage:
        rows = [r for r in pool if r["signal_date"] == daily["date"]]
        daily.update(Counter(r["outcome_status"] for r in rows))
    summaries = {}
    for tag in RULES:
        rows = [r for r in signals if r["strategy"] == tag]
        complete = [r for r in rows if r["outcome_status"] == "complete"]
        mature = [r for r in rows if r["outcome_status"] != "immature"]
        spaced = nonoverlap(rows, calendar)
        summaries[tag] = {"label": LABELS[tag], "total_signals": len(rows), "mature_signals": len(mature),
                          "outcome_status": dict(Counter(r["outcome_status"] for r in rows)),
                          "complete_pct": len(complete) / len(mature) * 100 if mature else None,
                          "main": metric(complete), "daily_matched_ci95": block_interval(complete, mature_dates),
                          "without_oneprice": metric([r for r in complete if not r["one_price_up"]]),
                          "same_source": metric([r for r in complete if r["same_source"]]),
                          "tencent_history": metric([r for r in complete if r["all_tencent_history"]]),
                          "nonoverlap": metric([r for r in spaced if r["outcome_status"] == "complete"]),
                          "early": metric([r for r in complete if r["signal_date"] <= "2026-08-07"]),
                          "late": metric([r for r in complete if r["signal_date"] >= "2026-08-17"])}
        next_known = [r for r in rows if r["signal_date"] != calendar[-1]]
        next_hits = sum(bool(events.get((r["symbol"], calendar[index[r["signal_date"]] + 1]), {}).get("closed_limit"))
                        for r in next_known)
        summaries[tag]["nextday_event_label"] = {"n": len(next_known), "hits": next_hits,
                                                 "pct": next_hits / len(next_known) * 100 if next_known else None}
    overlap = {}
    for x in RULES:
        left = {(r["symbol"], r["anchor_date"]) for r in signals if r["strategy"] == x}
        overlap[x] = {y: len(left & {(r["symbol"], r["anchor_date"]) for r in signals if r["strategy"] == y}) for y in RULES}
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "kind": "historical_exploration_not_live",
               "database_path": str(db.resolve()), "input_sha256": hashlib.sha256(json.dumps([event_rows, bar_rows], sort_keys=True, ensure_ascii=True).encode()).hexdigest(),
               "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "queries": [query_events, query_bars], "rules": RULES,
               "inventory": {"event_rows": len(event_rows), "event_symbols": len({r["symbol"] for r in event_rows}),
                             "bar_rows": len(bar_rows), "bar_symbols": len({r["symbol"] for r in bar_rows}),
                             "bar_sources": dict(Counter(r["source"] for r in bar_rows)),
                             "event_start": event_rows[0]["trade_date"], "bar_start": bar_rows[0]["trade_date"],
                             "data_end": block[-1], "continuous_start": block[0], "continuous_dates": len(block),
                             "signal_start": block[4], "signal_end": block[-1], "mature_end": mature_dates[-1],
                             "mature_dates": len(mature_dates), "raw_pool_rows": sum(d["raw_pool"] for d in daily_coverage),
                             "eligible_pool_rows": len(pool), "exclusions": dict(missing),
                             "pool_outcomes": dict(Counter(r["outcome_status"] for r in pool))},
               "daily_coverage": daily_coverage, "summary": summaries, "anchor_overlap": overlap,
               "same_time_ablation": {"mild": same_time_control(pool, "mild_raw", "mild_stable"),
                                      "deep": same_time_control(pool, "deep_raw", "deep_repair")}}
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for filename, rows in [("signals.csv", signals), ("eligible_pool.csv", pool), ("daily_coverage.csv", daily_coverage)]:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with (output / filename).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k, v in r.items()} for r in rows)
    print(json.dumps({"inventory": payload["inventory"], "summary": summaries}, ensure_ascii=True, indent=2))
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("backend/data/limituplab.sqlite"))
    parser.add_argument("--start", default="2026-07-20")
    parser.add_argument("--output", type=Path, default=Path("output/research/limit-up-paths-20260907"))
    args = parser.parse_args()
    analyze(args.db, args.start, args.output)
