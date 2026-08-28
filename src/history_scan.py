from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

import early_state
import scanner


def getSnapshotDays() -> list[pd.Timestamp]:
    days = []
    for path in sorted(scanner.SNAPSHOT_DIR.glob("*.csv.gz")):
        days.append(pd.Timestamp(path.name.removesuffix(".csv.gz")))
    return days


def groupByDate(records: list[dict]) -> dict:
    grouped = {}
    for record in records:
        grouped.setdefault(record["date"], []).append(record)
    return grouped


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
    target_dates = ([previous_dates[-1]] if previous_dates else []) + [date for date in available_dates if start_date <= date <= end_date]
    finalized = scanner.finalizeSignals(indicators, config, target_dates)
    in_range = finalized[(finalized["date"] >= start_date) & (finalized["date"] <= end_date)].copy()

    buy_rows = scanner.calculateRiskFields(in_range[in_range["new_signal"]].copy(), config)
    buy_records = scanner.buildStageRecords(buy_rows, "BUY", include_risk=True)
    early_events = scanner.buildStageRecords(in_range[in_range["new_early_leader"]].copy(), "EARLY_LEADER")
    leader_events = scanner.buildStageRecords(in_range[in_range["new_leader"]].copy(), "LEADER")
    setup_events = scanner.buildStageRecords(in_range[in_range["new_setup"]].copy(), "SETUP")

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
        "countertrend_buy_count": sum(1 for row in buy_records if row["market_alignment"] == "COUNTERTREND"),
        "by_date": groupByDate(buy_records),
        "signals": buy_records,
        "stage_event_counts": {
            "early_leader": len(early_events),
            "leader": len(leader_events),
            "setup": len(setup_events)
        },
        "early_leader_events": early_events,
        "leader_events": leader_events,
        "setup_events": setup_events,
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

    early_state.enrich_history_file(output_path)
    result = early_state.load_json(output_path, result)

    print(json.dumps({
        "rule_version": result["rule_version"],
        "from_date": result["from_date"],
        "to_date": result["to_date"],
        "signal_count": result["signal_count"],
        "countertrend_buy_count": result["countertrend_buy_count"],
        "stage_event_counts": result["stage_event_counts"]
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
