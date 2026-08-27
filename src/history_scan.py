from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from pykrx import stock

import scanner


def getSnapshotDays() -> list[pd.Timestamp]:
    days = []
    for path in sorted(scanner.SNAPSHOT_DIR.glob("*.csv.gz")):
        days.append(pd.Timestamp(path.name.removesuffix(".csv.gz")))
    return days


def buildRecords(selected: pd.DataFrame, config: dict, signal_type: str) -> list[dict]:
    if selected.empty:
        return []

    selected = scanner.calculateRiskFields(selected.copy(), config)
    ticker_names = {ticker: stock.get_market_ticker_name(ticker) for ticker in selected["ticker"].unique()}
    selected["name"] = selected["ticker"].map(ticker_names)
    selected["signal_type"] = signal_type
    columns = ["date", "ticker", "name", "market", "signal_type", "market_ok", "close", "market_cap", "rs_score", "sma50", "sma150", "sma200", "high52", "base_high", "base_low", "base_depth_pct", "volume", "avg_volume20", "volume_ratio", "stop_price", "stop_distance_pct", "three_r_target", "max_position_pct_for_risk"]
    return [scanner.cleanRecord(row) for row in selected[columns].sort_values(["date", "market", "ticker"]).to_dict(orient="records")]


def groupByDate(records: list[dict]) -> dict:
    by_date = {}
    for record in records:
        by_date.setdefault(record["date"], []).append(record)
    return by_date


def buildHistory(from_date: str, to_date: str | None = None) -> dict:
    config = scanner.loadConfig()
    business_days = getSnapshotDays()
    if not business_days:
        raise RuntimeError("저장된 스냅샷이 없습니다.")

    history = scanner.loadHistory(business_days)
    market_indices = scanner.fetchMarketIndices(config, business_days)
    indicators = scanner.addIndicators(history, market_indices, config)

    start_date = pd.Timestamp(from_date)
    end_date = pd.Timestamp(to_date) if to_date else indicators["date"].max()
    available_dates = sorted(indicators["date"].drop_duplicates())
    previous_dates = [date for date in available_dates if date < start_date]
    signal_dates = ([previous_dates[-1]] if previous_dates else []) + [date for date in available_dates if start_date <= date <= end_date]
    finalized = scanner.finalizeSignals(indicators, config, signal_dates)
    in_range = (finalized["date"] >= start_date) & (finalized["date"] <= end_date)

    buy_selected = finalized[in_range & finalized["new_signal"]].copy()
    countertrend_event = finalized["new_countertrend_candidate"] | ((finalized["date"] == start_date) & finalized["countertrend_candidate"])
    countertrend_selected = finalized[in_range & countertrend_event].copy()
    current_countertrend = finalized[(finalized["date"] == end_date) & finalized["countertrend_candidate"]].copy()

    buy_records = buildRecords(buy_selected, config, "BUY")
    countertrend_records = buildRecords(countertrend_selected, config, "COUNTERTREND")
    current_countertrend_records = buildRecords(current_countertrend, config, "COUNTERTREND")

    market_status = {}
    end_rows = finalized[finalized["date"] == end_date]
    for market in config["markets"]:
        market_rows = end_rows[end_rows["market"] == market]
        market_status[market] = bool(market_rows["market_ok"].iloc[0]) if not market_rows.empty else None

    return {
        "rule_version": config["rule_version"],
        "from_date": start_date.strftime("%Y-%m-%d"),
        "to_date": end_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "market_status": market_status,
        "signal_count": len(buy_records),
        "by_date": groupByDate(buy_records),
        "signals": buy_records,
        "countertrend_count": len(countertrend_records),
        "countertrend_by_date": groupByDate(countertrend_records),
        "countertrend_candidates": countertrend_records,
        "current_countertrend_count": len(current_countertrend_records),
        "current_countertrend_candidates": current_countertrend_records,
        "manual_checks": ["주도 섹터/섹터 RS", "인더스트리 액션", "기관 수급", "EPS 성장 및 실적"]
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date")
    parser.add_argument("--output", default="data/history_signals.json")
    args = parser.parse_args()

    result = buildHistory(args.from_date, args.to_date)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)

    print(json.dumps({"rule_version": result["rule_version"], "from_date": result["from_date"], "to_date": result["to_date"], "signal_count": result["signal_count"], "countertrend_count": result["countertrend_count"], "current_countertrend_count": result["current_countertrend_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
