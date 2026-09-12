from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apply_buy_ranking import add_ranking_indicators, build_day_universe, fetch_sector_classifications, load_history, load_json

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CAPTURE_PATH = DATA_DIR / "captures.csv"
TRADE_PATH = DATA_DIR / "buy_vs_benchmark_trades.csv"
CONFIG_PATH = ROOT_DIR / "config.json"
OUTPUT_PATH = DATA_DIR / "sector_leader_component_analysis.json"


def safe_float(value):
    try:
        if value is None or value == "" or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"count": 0}
    returns = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    excess = pd.to_numeric(frame["strategy_excess_pct_point"], errors="coerce")
    exits = frame["exit_reason"].fillna("").astype(str)
    return {
        "count": int(len(frame)),
        "avg_return": round(float(returns.mean()), 4),
        "median_return": round(float(returns.median()), 4),
        "avg_excess": round(float(excess.mean()), 4),
        "win_rate": round(float((returns > 0).mean() * 100), 2),
        "beat_rate": round(float((excess > 0).mean() * 100), 2),
        "stop_rate": round(float(exits.str.startswith("STOP_LOSS").mean() * 100), 2),
        "large_winner_count": int((returns >= 8).sum()),
    }


def numeric_summary(frame: pd.DataFrame, columns: list[str]) -> dict:
    output = {}
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        output[column] = {
            "count": int(len(values)),
            "mean": round(float(values.mean()), 4) if len(values) else None,
            "median": round(float(values.median()), 4) if len(values) else None,
            "min": round(float(values.min()), 4) if len(values) else None,
            "max": round(float(values.max()), 4) if len(values) else None,
        }
    return output


def corr_summary(frame: pd.DataFrame, columns: list[str]) -> dict:
    output = {}
    target = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        valid = values.notna() & target.notna()
        if valid.sum() < 5 or values[valid].nunique() < 2:
            output[column] = None
            continue
        corr = values[valid].corr(target[valid], method="spearman")
        output[column] = round(float(corr), 4) if pd.notna(corr) else None
    return output


def main() -> None:
    config = load_json(CONFIG_PATH, {}) or {}
    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    trades = pd.read_csv(TRADE_PATH, dtype={"ticker": str})
    captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
    trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)

    capture_columns = [
        "capture_date", "ticker", "name", "gain_since_early_pct", "trading_days_since_early",
        "early_state", "buy_grade", "buy_score", "sector", "sector_score", "sector_leader_rank",
        "weekly_state", "atr20_pct", "institutional_fit", "avg_trading_value20", "capture_rs_score",
    ]
    base = trades.drop(columns=["buy_grade"], errors="ignore").merge(
        captures[capture_columns], on=["capture_date", "ticker", "name"], how="left"
    )
    gain = pd.to_numeric(base["gain_since_early_pct"], errors="coerce")
    base = base[gain.isna() | (gain < 15)].copy()

    history = add_ranking_indicators(load_history())
    history["return5_rank"] = history.groupby("ticker")["close"].pct_change(5) * 100
    history["return10_rank"] = history.groupby("ticker")["close"].pct_change(10) * 100

    rows = []
    for date_text, date_group in base.groupby("capture_date"):
        capture_date = pd.Timestamp(date_text)
        sector_frame = fetch_sector_classifications(capture_date, config["markets"])
        day = build_day_universe(history, capture_date, sector_frame, config)
        day_map = {str(row["ticker"]).zfill(6): row for _, row in day.iterrows()}

        for _, source in date_group.iterrows():
            ticker = str(source["ticker"]).zfill(6)
            row = day_map.get(ticker)
            record = source.to_dict()
            if row is None:
                for key in [
                    "return5_rank", "return10_rank", "return20_rank", "return60_rank",
                    "sector_ret20_rank", "sector_ret60_rank", "distance_high52_rank",
                    "high_proximity_score", "sector_leader_score_raw",
                ]:
                    record[key] = None
            else:
                for key in [
                    "return5_rank", "return10_rank", "return20_rank", "return60_rank",
                    "sector_ret20_rank", "sector_ret60_rank", "distance_high52_rank",
                    "high_proximity_score", "sector_leader_score_raw",
                ]:
                    record[key] = safe_float(row.get(key))
            rows.append(record)

    frame = pd.DataFrame(rows)
    leader_rank = pd.to_numeric(frame["sector_leader_rank"], errors="coerce")
    frame["rank_group"] = pd.cut(
        leader_rank,
        bins=[0, 5, 20, float("inf")],
        labels=["1-5", "6-20", ">20"],
        include_lowest=True,
        right=True,
    )

    component_columns = [
        "return5_rank", "return10_rank", "return20_rank", "return60_rank",
        "sector_ret20_rank", "sector_ret60_rank", "distance_high52_rank",
        "high_proximity_score", "sector_leader_score_raw", "gain_since_early_pct",
        "buy_score", "capture_rs_score", "sector_score", "atr20_pct",
    ]

    group_summary = {}
    for group_name in ["1-5", "6-20", ">20"]:
        group = frame[frame["rank_group"].astype(str) == group_name].copy()
        group_summary[group_name] = {
            "performance": metrics(group),
            "components": numeric_summary(group, component_columns),
        }

    corr = corr_summary(frame, component_columns + ["sector_leader_rank"])

    top_1_5 = frame[leader_rank <= 5].copy().sort_values(["capture_date", "sector_leader_rank"])
    top_rows = []
    for _, row in top_1_5.iterrows():
        top_rows.append({
            "capture_date": str(row["capture_date"]),
            "ticker": str(row["ticker"]),
            "name": str(row["name"]),
            "sector": str(row.get("sector")),
            "leader_rank": safe_float(row.get("sector_leader_rank")),
            "strategy_return_pct": safe_float(row.get("strategy_return_pct")),
            "excess_pct_point": safe_float(row.get("strategy_excess_pct_point")),
            "exit_reason": None if pd.isna(row.get("exit_reason")) else str(row.get("exit_reason")),
            "return5_pct": safe_float(row.get("return5_rank")),
            "return10_pct": safe_float(row.get("return10_rank")),
            "return20_pct": safe_float(row.get("return20_rank")),
            "return60_pct": safe_float(row.get("return60_rank")),
            "sector_ret20_percentile": safe_float(row.get("sector_ret20_rank")),
            "sector_ret60_percentile": safe_float(row.get("sector_ret60_rank")),
            "distance_52w_high_pct": safe_float(row.get("distance_high52_rank")),
            "high_proximity_score": safe_float(row.get("high_proximity_score")),
            "leader_raw_score": safe_float(row.get("sector_leader_score_raw")),
            "gain_since_early_pct": safe_float(row.get("gain_since_early_pct")),
            "buy_score": safe_float(row.get("buy_score")),
        })

    large = frame[pd.to_numeric(frame["strategy_return_pct"], errors="coerce") >= 8].copy().sort_values("strategy_return_pct", ascending=False)
    large_rows = []
    for _, row in large.iterrows():
        large_rows.append({
            "capture_date": str(row["capture_date"]),
            "ticker": str(row["ticker"]),
            "name": str(row["name"]),
            "sector": str(row.get("sector")),
            "leader_rank": safe_float(row.get("sector_leader_rank")),
            "strategy_return_pct": safe_float(row.get("strategy_return_pct")),
            "return5_pct": safe_float(row.get("return5_rank")),
            "return10_pct": safe_float(row.get("return10_rank")),
            "return20_pct": safe_float(row.get("return20_rank")),
            "return60_pct": safe_float(row.get("return60_rank")),
            "sector_ret20_percentile": safe_float(row.get("sector_ret20_rank")),
            "sector_ret60_percentile": safe_float(row.get("sector_ret60_rank")),
            "distance_52w_high_pct": safe_float(row.get("distance_high52_rank")),
            "high_proximity_score": safe_float(row.get("high_proximity_score")),
            "leader_raw_score": safe_float(row.get("sector_leader_score_raw")),
            "gain_since_early_pct": safe_float(row.get("gain_since_early_pct")),
            "buy_score": safe_float(row.get("buy_score")),
        })

    output = {
        "formula": {
            "sector_leader_score_raw": "0.45 * sector_ret20_percentile + 0.35 * sector_ret60_percentile + 0.20 * high_proximity_score",
            "high_proximity_score": "clip(100 + distance_from_52w_high_pct * 4, 0, 100)",
        },
        "cohort": {
            "rule": "captures.csv gain_since_early_pct is null OR < 15%",
            "count": int(len(frame)),
        },
        "rank_groups": group_summary,
        "spearman_vs_future_strategy_return": corr,
        "top_1_5_records": top_rows,
        "large_winner_records": large_rows,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "cohort_count": len(frame),
        "rank_groups": group_summary,
        "spearman": corr,
        "top_1_5_count": len(top_rows),
        "large_winner_count": len(large_rows),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
