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


def getStage(row: dict) -> str:
    if bool(row.get("signal")):
        return "BUY"
    if bool(row.get("setup")):
        return "SETUP"
    if bool(row.get("leader")):
        return "LEADER"
    if bool(row.get("early_leader")):
        return "EARLY_LEADER"
    return "NONE"


def buildDiagnostics(tickers: list[str], from_date: str, to_date: str) -> dict:
    config = scanner.loadConfig()
    business_days = getSnapshotDays()
    history = scanner.loadHistory(business_days)
    market_indices = scanner.fetchMarketIndices(config, business_days)
    indicators = scanner.addIndicators(history, market_indices, config)

    start_date = pd.Timestamp(from_date)
    end_date = pd.Timestamp(to_date)
    available_dates = sorted(indicators["date"].drop_duplicates())
    target_dates = [date for date in available_dates if start_date <= date <= end_date]
    finalized = scanner.finalizeSignals(indicators, config, target_dates)
    selected = finalized[finalized["ticker"].isin(tickers)].copy()

    results = []
    for ticker in tickers:
        frame = selected[selected["ticker"] == ticker].copy().sort_values("date")
        if frame.empty:
            results.append({"ticker": ticker, "name": stock.get_market_ticker_name(ticker), "error": "no_data"})
            continue

        name = stock.get_market_ticker_name(ticker)
        frame["stage_rank"] = (
            frame["early_leader"].astype(int)
            + frame["leader"].astype(int) * 2
            + frame["setup"].astype(int) * 3
            + frame["signal"].astype(int) * 4
        )
        best = frame.sort_values(["stage_rank", "rs_score", "volume_ratio"], ascending=[False, False, False]).head(8)

        rows = []
        for row in best.to_dict(orient="records"):
            failed = []
            if not bool(row["trend_recovery"]):
                failed.append("close_below_sma50")
            if not bool(row["early_near_52w_high"]):
                failed.append("far_from_52w_high")
            if not bool(row["short_rs_strong"]) and not bool(row["leader_pre_cap"]):
                failed.append("rs_strength")
            if pd.isna(row["market_cap"]) or float(row["market_cap"]) < config["min_market_cap"]:
                failed.append("market_cap")
            if not bool(row["base_valid"]):
                failed.append("base")
            if bool(row["base_valid"]) and not bool(row["base_volume_contracted"]):
                failed.append("base_volume")
            if not bool(row["breakout"]) and not bool(row["near_breakout"]):
                failed.append("not_near_breakout")
            if bool(row["breakout"]) and float(row["volume_ratio"]) < config["breakout"]["volume_ratio_min"]:
                failed.append("breakout_volume")
            if bool(row["breakout"]) and pd.notna(row["breakout_extension_pct"]) and float(row["breakout_extension_pct"]) > config["breakout"]["max_extension_pct"]:
                failed.append("overextended")

            rows.append({
                "date": row["date"].strftime("%Y-%m-%d"),
                "stage": getStage(row),
                "market_alignment": row["market_alignment"],
                "close": float(row["close"]),
                "market_cap": None if pd.isna(row["market_cap"]) else float(row["market_cap"]),
                "rs_score": None if pd.isna(row["rs_score"]) else float(row["rs_score"]),
                "rs_20_score": None if pd.isna(row["rs_20_score"]) else float(row["rs_20_score"]),
                "rs_60_score": None if pd.isna(row["rs_60_score"]) else float(row["rs_60_score"]),
                "rs_acceleration": None if pd.isna(row["rs_acceleration"]) else float(row["rs_acceleration"]),
                "mtt": bool(row["mtt"]),
                "distance_from_high52_pct": None if pd.isna(row["distance_from_high52_pct"]) else float(row["distance_from_high52_pct"]),
                "base_days": None if pd.isna(row["base_days"]) else int(row["base_days"]),
                "base_high": None if pd.isna(row["base_high"]) else float(row["base_high"]),
                "base_low": None if pd.isna(row["base_low"]) else float(row["base_low"]),
                "base_depth_pct": None if pd.isna(row["base_depth_pct"]) else float(row["base_depth_pct"]),
                "distance_to_base_high_pct": None if pd.isna(row["distance_to_base_high_pct"]) else float(row["distance_to_base_high_pct"]),
                "volume_ratio": None if pd.isna(row["volume_ratio"]) else float(row["volume_ratio"]),
                "failed": failed
            })

        results.append({
            "ticker": ticker,
            "name": name,
            "market": str(frame["market"].iloc[-1]),
            "max_rs_score": None if frame["rs_score"].isna().all() else float(frame["rs_score"].max()),
            "early_leader_dates": [date.strftime("%Y-%m-%d") for date in frame[frame["new_early_leader"]]["date"].tolist()],
            "leader_dates": [date.strftime("%Y-%m-%d") for date in frame[frame["new_leader"]]["date"].tolist()],
            "setup_dates": [date.strftime("%Y-%m-%d") for date in frame[frame["new_setup"]]["date"].tolist()],
            "buy_dates": [date.strftime("%Y-%m-%d") for date in frame[frame["new_signal"]]["date"].tolist()],
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
