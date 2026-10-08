from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_buy_vs_benchmark import (
    CONFIG_PATH,
    DATA_DIR,
    TRACKING_PATH,
    build_trade_rows,
    fetch_indices,
    load_json,
    load_snapshots,
    safe_float,
    summarize_rows,
)

OUT_JSON = DATA_DIR / "all_buy_vs_index_20261007.json"
OUT_CSV = DATA_DIR / "all_buy_vs_index_20261007.csv"
UNIT_KRW = 6_250_000
PERIOD_END = "2026-10-07"


def overview(rows: list[dict]) -> dict:
    result = summarize_rows(rows, "strategy_return_pct", "matched_benchmark_return_pct")
    if not rows:
        return result

    raw = summarize_rows(rows, "raw_hold_return_pct", "current_benchmark_return_pct")
    values = np.array([safe_float(r["strategy_return_pct"]) for r in rows], dtype=float)
    refs = np.array([safe_float(r["matched_benchmark_return_pct"]) for r in rows], dtype=float)
    valid = np.isfinite(values) & np.isfinite(refs)
    if not bool(valid.all()):
        raise RuntimeError(f"지수 값 누락: {len(rows) - int(valid.sum())}건")

    result["raw_hold"] = raw
    result["total_gross_entry_krw"] = len(rows) * UNIT_KRW
    result["strategy_pnl_krw"] = round(float(np.sum(values * UNIT_KRW / 100.0)))
    result["index_matched_pnl_krw"] = round(float(np.sum(refs * UNIT_KRW / 100.0)))
    result["excess_pnl_krw"] = result["strategy_pnl_krw"] - result["index_matched_pnl_krw"]
    result["strategy_open_and_closed_count"] = dict(Counter(r["trade_status"] for r in rows))
    result["up_vs_index_count"] = int(np.sum(values > refs))
    return result


def main() -> None:
    cfg = load_json(CONFIG_PATH, {}) or {}
    tracking = load_json(TRACKING_PATH, []) or []
    if not tracking:
        raise RuntimeError("tracking.json이 비어 있습니다.")

    start_date = min(str(r.get("capture_date")) for r in tracking)
    last_date = max(str(r.get("latest_date")) for r in tracking)
    if last_date != PERIOD_END:
        raise RuntimeError(f"분석 최신일 불일치: {last_date} != {PERIOD_END}")

    tickers = {str(r.get("ticker", "")).zfill(6) for r in tracking}
    snap = load_snapshots(start_date, last_date, tickers)
    indices = fetch_indices(cfg, start_date, last_date)
    rows = build_trade_rows(tracking, snap, indices, last_date)
    if len(rows) != len(tracking):
        raise RuntimeError(f"지수 매칭 누락: {len(rows)} / {len(tracking)}")

    groups = defaultdict(list)
    for r in rows:
        groups[r["market"]].append(r)

    index_period = {}
    for market, frame in indices.items():
        start_close = float(frame.iloc[0]["close"])
        end_close = float(frame.iloc[-1]["close"])
        index_period[market] = {
            "start_close": start_close,
            "end_close": end_close,
            "full_period_return_pct": round((end_close / start_close - 1) * 100, 4),
            "first_date": frame.index[0].strftime("%Y-%m-%d"),
            "last_date": frame.index[-1].strftime("%Y-%m-%d"),
        }

    result = {
        "period": {"start": start_date, "end": last_date},
        "assumptions": {
            "buy": "all 137 BUY captures at capture-day close, including repeat captures",
            "allocation": "equal amount KRW 6,250,000 per capture, no cash limit, no fees",
            "return": "kangto_exit_v2 marked strategy return, partial exits included",
            "benchmark": "same-market KOSPI or KOSDAQ close at each BUY to same trade exit date, or evaluation date if still open; gap exits use index open",
            "note": "Equal-weight trades, not a feasible 100m KRW fixed-capital portfolio due to unlimited simultaneous buys",
        },
        "index_full_period": index_period,
        "all": overview(rows),
        "by_market": {name: overview(group) for name, group in sorted(groups.items())},
        "by_status": {name: overview([r for r in rows if r["trade_status"] == name]) for name in ["OPEN", "PARTIAL", "CLOSED"]},
        "capture_dates_count": len(set(r["capture_date"] for r in rows)),
    }

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
