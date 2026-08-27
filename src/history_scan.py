from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pykrx import stock

import scanner


def cleanValue(value):
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if np.isnan(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value


def getSnapshotDays() -> list[pd.Timestamp]:
    days = []
    for path in sorted(scanner.SNAPSHOT_DIR.glob("*.csv.gz")):
        date_text = path.name.removesuffix(".csv.gz")
        days.append(pd.Timestamp(date_text))
    return days


def buildHistory(from_date: str, to_date: str | None = None) -> dict:
    config = scanner.loadConfig()
    business_days = getSnapshotDays()
    if not business_days:
        raise RuntimeError("저장된 스냅샷이 없습니다.")

    history = scanner.loadHistory(business_days)
    benchmark = scanner.fetchBenchmark(config, business_days)
    indicators = scanner.addIndicators(history, benchmark, config)

    start_date = pd.Timestamp(from_date)
    end_date = pd.Timestamp(to_date) if to_date else indicators["date"].max()
    selected = indicators[(indicators["date"] >= start_date) & (indicators["date"] <= end_date) & indicators["new_signal"]].copy()

    ticker_names = {ticker: stock.get_market_ticker_name(ticker) for ticker in selected["ticker"].unique()}
    selected["name"] = selected["ticker"].map(ticker_names)
    columns = ["date", "ticker", "name", "market", "close", "volume", "avg_volume20", "volume_ratio", "prev_55_high", "sma20", "sma60", "sma120", "relative_strength60_pp", "atr14", "atr50"]

    signals = []
    for row in selected[columns].sort_values(["date", "market", "ticker"]).to_dict(orient="records"):
        signals.append({key: cleanValue(value) for key, value in row.items()})

    by_date = {}
    for signal in signals:
        by_date.setdefault(signal["date"], []).append(signal)

    return {
        "from_date": start_date.strftime("%Y-%m-%d"),
        "to_date": end_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "signal_count": len(signals),
        "by_date": by_date,
        "signals": signals
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

    print(json.dumps({"from_date": result["from_date"], "to_date": result["to_date"], "signal_count": result["signal_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
