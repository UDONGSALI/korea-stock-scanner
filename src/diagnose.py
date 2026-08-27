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


def buildDiagnostics(tickers: list[str], from_date: str, to_date: str) -> dict:
    config = scanner.loadConfig()
    business_days = getSnapshotDays()
    history = scanner.loadHistory(business_days)
    market_indices = scanner.fetchMarketIndices(config, business_days)
    indicators = scanner.addIndicators(history, market_indices, config)

    start_date = pd.Timestamp(from_date)
    end_date = pd.Timestamp(to_date)
    selected = indicators[(indicators["ticker"].isin(tickers)) & (indicators["date"] >= start_date) & (indicators["date"] <= end_date)].copy()

    cap_cache = {}
    for date in sorted(selected["date"].drop_duplicates()):
        for market in selected[selected["date"] == date]["market"].unique():
            key = (date, market)
            caps = scanner.fetchMarketCaps(date, [market])
            cap_cache[key] = caps.set_index("ticker")["market_cap"].to_dict() if not caps.empty else {}

    selected["market_cap"] = selected.apply(lambda row: cap_cache.get((row["date"], row["market"]), {}).get(str(row["ticker"])), axis=1)
    selected["cap_ok"] = selected["market_cap"] >= config["min_market_cap"]
    selected["rs_ok"] = selected["rs_score"] >= config["rs_min_score"]
    selected["mtt_ok"] = selected["mtt"].astype(bool)
    selected["weekly_trend_ok"] = selected["weekly_trend_proxy"].astype(bool)
    selected["base_depth_ok"] = selected["base_depth_pct"] <= config["base"]["max_depth_pct"]
    selected["base_volume_ok"] = selected["base_volume_contracted"].astype(bool)
    selected["breakout_ok"] = selected["breakout"].astype(bool)
    selected["breakout_volume_ok"] = selected["volume_ratio"] >= config["breakout_volume_ratio_min"]

    stock_condition_columns = ["cap_ok", "rs_ok", "mtt_ok", "weekly_trend_ok", "base_depth_ok", "base_volume_ok", "breakout_ok", "breakout_volume_ok"]
    selected["stock_pass_count"] = selected[stock_condition_columns].sum(axis=1)
    selected["stock_all_ok"] = selected[stock_condition_columns].all(axis=1)

    results = []
    for ticker in tickers:
        frame = selected[selected["ticker"] == ticker].copy().sort_values("date")
        if frame.empty:
            results.append({"ticker": ticker, "name": stock.get_market_ticker_name(ticker), "error": "no_data"})
            continue

        name = stock.get_market_ticker_name(ticker)
        best = frame.sort_values(["stock_pass_count", "breakout_ok", "volume_ratio"], ascending=[False, False, False]).head(5)
        breakout_rows = frame[frame["breakout_ok"]].sort_values("date")
        eligible_rows = frame[frame["stock_all_ok"]].sort_values("date")

        rows = []
        for row in best.to_dict(orient="records"):
            failed = [column.removesuffix("_ok") for column in stock_condition_columns if not bool(row[column])]
            if not bool(row["market_ok"]):
                failed.append("market")
            rows.append({
                "date": row["date"].strftime("%Y-%m-%d"), "close": float(row["close"]), "market": row["market"], "market_ok": bool(row["market_ok"]),
                "market_cap": None if pd.isna(row["market_cap"]) else float(row["market_cap"]), "rs_score": None if pd.isna(row["rs_score"]) else float(row["rs_score"]),
                "high52": None if pd.isna(row["high52"]) else float(row["high52"]), "distance_from_high52_pct": None if pd.isna(row["distance_from_high52_pct"]) else float(row["distance_from_high52_pct"]),
                "sma50": None if pd.isna(row["sma50"]) else float(row["sma50"]), "sma150": None if pd.isna(row["sma150"]) else float(row["sma150"]), "sma200": None if pd.isna(row["sma200"]) else float(row["sma200"]),
                "base_high": None if pd.isna(row["base_high"]) else float(row["base_high"]), "base_low": None if pd.isna(row["base_low"]) else float(row["base_low"]), "base_depth_pct": None if pd.isna(row["base_depth_pct"]) else float(row["base_depth_pct"]),
                "volume_ratio": None if pd.isna(row["volume_ratio"]) else float(row["volume_ratio"]), "stock_pass_count": int(row["stock_pass_count"]), "failed": failed,
                "flags": {column: bool(row[column]) for column in stock_condition_columns}
            })

        results.append({
            "ticker": ticker,
            "name": name,
            "market": str(frame["market"].iloc[-1]),
            "max_rs_score": None if frame["rs_score"].isna().all() else float(frame["rs_score"].max()),
            "breakout_dates": [date.strftime("%Y-%m-%d") for date in breakout_rows["date"].tolist()],
            "stock_all_ok_dates": [date.strftime("%Y-%m-%d") for date in eligible_rows["date"].tolist()],
            "best_rows": rows
        })

    return {
        "rule_version": config["rule_version"],
        "from_date": start_date.strftime("%Y-%m-%d"),
        "to_date": end_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tickers": results
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--output", default="data/diagnostics.json")
    args = parser.parse_args()

    tickers = [ticker.strip() for ticker in args.tickers.split(",") if ticker.strip()]
    result = buildDiagnostics(tickers, args.from_date, args.to_date)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"tickers": len(tickers), "output": str(output_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
