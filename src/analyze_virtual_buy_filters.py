from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CAPTURE_PATH = DATA_DIR / "captures.csv"
TRADE_PATH = DATA_DIR / "buy_vs_benchmark_trades.csv"
OUTPUT_PATH = DATA_DIR / "virtual_buy_filter_analysis.json"

LARGE_WINNER_PCT = 8.0


def to_float(value):
    try:
        if value is None or value == "" or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def summarize(frame: pd.DataFrame, baseline: pd.DataFrame) -> dict:
    total = len(frame)
    strategy = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    benchmark = pd.to_numeric(frame["matched_benchmark_return_pct"], errors="coerce")
    excess = pd.to_numeric(frame["strategy_excess_pct_point"], errors="coerce")
    winners = frame[strategy > 0]
    losers = frame[strategy < 0]
    beaters = frame[excess > 0]
    stops = frame[frame["exit_reason"].fillna("").astype(str).str.startswith("STOP_LOSS")]

    kept_keys = set(zip(frame["capture_date"].astype(str), frame["ticker"].astype(str)))
    excluded = baseline[
        ~baseline.apply(lambda row: (str(row["capture_date"]), str(row["ticker"])) in kept_keys, axis=1)
    ].copy()
    excluded["strategy_return_pct"] = pd.to_numeric(excluded["strategy_return_pct"], errors="coerce")
    excluded_positive = excluded[excluded["strategy_return_pct"] > 0].sort_values("strategy_return_pct", ascending=False)
    excluded_large = excluded[excluded["strategy_return_pct"] >= LARGE_WINNER_PCT].sort_values("strategy_return_pct", ascending=False)

    def pct(count: int, denominator: int) -> float:
        return round(count / denominator * 100, 2) if denominator else 0.0

    return {
        "kept_count": int(total),
        "excluded_count": int(len(excluded)),
        "kept_pct": pct(total, len(baseline)),
        "avg_strategy_return_pct": round(float(strategy.mean()), 4) if total else None,
        "median_strategy_return_pct": round(float(strategy.median()), 4) if total else None,
        "avg_benchmark_return_pct": round(float(benchmark.mean()), 4) if total else None,
        "avg_excess_pct_point": round(float(excess.mean()), 4) if total else None,
        "win_rate_pct": pct(len(winners), total),
        "beat_benchmark_rate_pct": pct(len(beaters), total),
        "stop_loss_rate_pct": pct(len(stops), total),
        "avg_winner_pct": round(float(pd.to_numeric(winners["strategy_return_pct"], errors="coerce").mean()), 4) if len(winners) else None,
        "avg_loser_pct": round(float(pd.to_numeric(losers["strategy_return_pct"], errors="coerce").mean()), 4) if len(losers) else None,
        "excluded_positive_count": int(len(excluded_positive)),
        "excluded_large_winner_count": int(len(excluded_large)),
        "excluded_large_winners": [
            {
                "capture_date": str(row["capture_date"]),
                "name": str(row["name"]),
                "ticker": str(row["ticker"]).zfill(6),
                "strategy_return_pct": round(float(row["strategy_return_pct"]), 4),
                "early_state": clean_value(row.get("early_state")),
                "gain_since_early_pct": clean_value(to_float(row.get("gain_since_early_pct"))),
                "sector_leader_rank": clean_value(to_float(row.get("sector_leader_rank"))),
            }
            for _, row in excluded_large.iterrows()
        ],
        "top_excluded_positive": [
            {
                "capture_date": str(row["capture_date"]),
                "name": str(row["name"]),
                "ticker": str(row["ticker"]).zfill(6),
                "strategy_return_pct": round(float(row["strategy_return_pct"]), 4),
                "early_state": clean_value(row.get("early_state")),
                "gain_since_early_pct": clean_value(to_float(row.get("gain_since_early_pct"))),
                "sector_leader_rank": clean_value(to_float(row.get("sector_leader_rank"))),
            }
            for _, row in excluded_positive.head(10).iterrows()
        ],
    }


def main() -> None:
    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    trades = pd.read_csv(TRADE_PATH, dtype={"ticker": str})
    captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
    trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)

    capture_columns = [
        "capture_date", "ticker", "name", "early_state", "gain_since_early_pct",
        "sector_leader_rank", "sector_score", "buy_grade", "buy_score",
    ]
    base = trades.merge(captures[capture_columns], on=["capture_date", "ticker", "name"], how="left")

    gain = pd.to_numeric(base["gain_since_early_pct"], errors="coerce")
    leader_rank = pd.to_numeric(base["sector_leader_rank"], errors="coerce")
    not_climax = base["early_state"].fillna("").astype(str) != "CLIMAX_RISK"
    early_15 = gain.isna() | (gain < 15.0)
    early_20 = gain.isna() | (gain < 20.0)
    leader_10 = leader_rank.notna() & (leader_rank <= 10.0)

    filters = {
        "baseline_all_buy": pd.Series(True, index=base.index),
        "no_climax_risk": not_climax,
        "early_gain_under_15": early_15,
        "early_gain_under_20": early_20,
        "sector_leader_top10": leader_10,
        "combo_climax_early15_top10": not_climax & early_15 & leader_10,
        "combo_climax_early20_top10": not_climax & early_20 & leader_10,
    }

    results = {name: summarize(base[mask].copy(), base) for name, mask in filters.items()}
    baseline_avg = results["baseline_all_buy"]["avg_strategy_return_pct"]
    baseline_excess = results["baseline_all_buy"]["avg_excess_pct_point"]
    baseline_stop = results["baseline_all_buy"]["stop_loss_rate_pct"]

    for result in results.values():
        result["delta_vs_baseline_avg_return_pct_point"] = round(result["avg_strategy_return_pct"] - baseline_avg, 4)
        result["delta_vs_baseline_excess_pct_point"] = round(result["avg_excess_pct_point"] - baseline_excess, 4)
        result["delta_vs_baseline_stop_rate_pct_point"] = round(result["stop_loss_rate_pct"] - baseline_stop, 2)

    output = {
        "analysis_period": {
            "start": str(base["capture_date"].min()),
            "end": str(base["capture_date"].max()),
            "trade_outcome_through": str(pd.to_datetime(base["capture_date"]).max().date()),
            "total_buy_signals": int(len(base)),
        },
        "large_winner_definition": f"strategy_return_pct >= {LARGE_WINNER_PCT}%",
        "filters": results,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
