from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

root_dir = Path(__file__).resolve().parents[1]
data_dir = root_dir / "data"
capture_path = data_dir / "captures.csv"
trade_path = data_dir / "buy_vs_benchmark_trades.csv"
output_path = data_dir / "second_filter_after_early15.json"

large_winner_threshold = 8.0


def metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "count": 0,
            "avg_return": None,
            "median_return": None,
            "avg_excess": None,
            "win_rate": None,
            "beat_rate": None,
            "stop_rate": None,
            "large_winner_count": 0,
        }

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
        "large_winner_count": int((returns >= large_winner_threshold).sum()),
    }


def numeric_summary(frame: pd.DataFrame, columns: list[str]) -> dict:
    output = {}
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        output[column] = {
            "count": int(len(values)),
            "mean": round(float(values.mean()), 4) if len(values) else None,
            "median": round(float(values.median()), 4) if len(values) else None,
        }
    return output


def categorical_summary(frame: pd.DataFrame, column: str) -> dict:
    counts = frame[column].fillna("UNKNOWN").astype(str).value_counts()
    total = len(frame)
    return {
        str(key): {
            "count": int(value),
            "pct": round(float(value / total * 100), 2) if total else 0.0,
        }
        for key, value in counts.items()
    }


def evaluate_filter(name: str, kept: pd.DataFrame, baseline_frame: pd.DataFrame, baseline: dict, large_winner_keys: set[tuple[str, str]]) -> dict:
    kept_keys = set(zip(kept["capture_date"].astype(str), kept["ticker"].astype(str)))
    missed_keys = sorted(large_winner_keys - kept_keys)
    current = metrics(kept)

    missed_rows = baseline_frame[
        baseline_frame.apply(
            lambda row: (str(row["capture_date"]), str(row["ticker"])) in missed_keys,
            axis=1,
        )
    ].sort_values("strategy_return_pct", ascending=False)

    return {
        "name": name,
        **current,
        "kept_pct": round(len(kept) / len(baseline_frame) * 100, 2) if len(baseline_frame) else 0.0,
        "delta_return": round(current["avg_return"] - baseline["avg_return"], 4),
        "delta_excess": round(current["avg_excess"] - baseline["avg_excess"], 4),
        "delta_stop_rate": round(current["stop_rate"] - baseline["stop_rate"], 2),
        "missed_large_winner_count": len(missed_keys),
        "missed_large_winners": [
            f"{row['name']} {float(row['strategy_return_pct']):+.2f}%"
            for _, row in missed_rows.iterrows()
        ],
    }


def numeric_filter(frame: pd.DataFrame, column: str, operator: str, threshold: float) -> pd.DataFrame:
    values = pd.to_numeric(frame[column], errors="coerce")
    if operator == ">=":
        return frame[values.notna() & (values >= threshold)]
    if operator == "<=":
        return frame[values.notna() & (values <= threshold)]
    raise ValueError(operator)


def build_bin_summary(frame: pd.DataFrame, column: str, bins: list[float], labels: list[str]) -> list[dict]:
    values = pd.to_numeric(frame[column], errors="coerce")
    buckets = pd.cut(values, bins=bins, labels=labels, include_lowest=True, right=False)
    output = []
    for label in labels:
        group = frame[buckets == label]
        if len(group):
            output.append({"bucket": label, **metrics(group)})
    missing = frame[values.isna()]
    if len(missing):
        output.append({"bucket": "MISSING", **metrics(missing)})
    return output


def main() -> None:
    captures = pd.read_csv(capture_path, dtype={"ticker": str})
    trades = pd.read_csv(trade_path, dtype={"ticker": str})
    captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
    trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)

    capture_columns = [
        "capture_date", "ticker", "name", "early_state", "gain_since_early_pct",
        "trading_days_since_early", "buy_grade", "buy_score", "priority_selected",
        "sector", "sector_score", "sector_leader_rank", "sector_member_count",
        "weekly_state", "atr20_pct", "institutional_fit", "avg_trading_value20",
        "capture_rs_score", "market_alignment",
    ]
    base = trades.merge(captures[capture_columns], on=["capture_date", "ticker", "name"], how="left")

    gain = pd.to_numeric(base["gain_since_early_pct"], errors="coerce")
    frame = base[gain.isna() | (gain < 15.0)].copy()

    baseline = metrics(frame)
    returns = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    winners = frame[returns > 0].copy()
    losers = frame[returns < 0].copy()

    large_winner_frame = frame[returns >= large_winner_threshold].sort_values("strategy_return_pct", ascending=False)
    large_winner_keys = set(zip(large_winner_frame["capture_date"].astype(str), large_winner_frame["ticker"].astype(str)))
    large_winners = [
        {
            "capture_date": str(row["capture_date"]),
            "ticker": str(row["ticker"]),
            "name": str(row["name"]),
            "return": round(float(row["strategy_return_pct"]), 4),
            "sector_leader_rank": None if pd.isna(row["sector_leader_rank"]) else float(row["sector_leader_rank"]),
            "weekly_state": None if pd.isna(row["weekly_state"]) else str(row["weekly_state"]),
        }
        for _, row in large_winner_frame.iterrows()
    ]

    numeric_columns = [
        "gain_since_early_pct", "trading_days_since_early", "buy_score",
        "sector_score", "sector_leader_rank", "sector_member_count", "atr20_pct",
        "avg_trading_value20", "capture_rs_score",
    ]

    filter_results = []
    thresholds = {
        "gain_since_early_pct": [0, 3, 5, 8, 10, 12],
        "trading_days_since_early": [0, 3, 5, 8, 10],
        "buy_score": [50, 55, 60, 65, 70, 75],
        "sector_score": [40, 50, 55, 60, 70, 80],
        "sector_leader_rank": [5, 10, 15, 20, 30],
        "atr20_pct": [3, 4, 5, 6, 8],
        "avg_trading_value20": [1_000_000_000, 5_000_000_000, 10_000_000_000, 30_000_000_000, 50_000_000_000],
        "capture_rs_score": [40, 50, 60, 70, 80],
    }

    for column, values in thresholds.items():
        for threshold in values:
            for operator in [">=", "<="]:
                kept = numeric_filter(frame, column, operator, threshold)
                if len(kept) < 10 or len(kept) == len(frame):
                    continue
                filter_results.append(evaluate_filter(
                    f"{column} {operator} {threshold}", kept, frame, baseline, large_winner_keys
                ))

    categorical_filters = {
        "KOSPI만": frame[frame["market"] == "KOSPI"],
        "KOSDAQ만": frame[frame["market"] == "KOSDAQ"],
        "주봉_STRONG_GOOD": frame[frame["weekly_state"].isin(["STRONG", "GOOD"])],
        "주봉_STRONG": frame[frame["weekly_state"] == "STRONG"],
        "주봉_RECOVERING": frame[frame["weekly_state"] == "RECOVERING"],
        "기관적합_true": frame[frame["institutional_fit"].astype(str).str.lower() == "true"],
        "기관적합_false": frame[frame["institutional_fit"].astype(str).str.lower() != "true"],
        "EARLY_FRESH": frame[frame["early_state"] == "FRESH"],
        "EARLY_FRESH_NOEARLY": frame[frame["early_state"].isin(["FRESH", "NO_EARLY"])],
        "품질_SA": frame[frame["buy_grade"].isin(["S", "A"])],
        "품질_AB": frame[frame["buy_grade"].isin(["A", "B"])],
        "품질_BC": frame[frame["buy_grade"].isin(["B", "C"])],
    }
    for name, kept in categorical_filters.items():
        if 10 <= len(kept) < len(frame):
            filter_results.append(evaluate_filter(name, kept, frame, baseline, large_winner_keys))

    robust = [
        row for row in filter_results
        if row["count"] >= len(frame) / 2
        and row["missed_large_winner_count"] == 0
        and row["delta_return"] > 0
        and row["delta_excess"] > 0
        and row["delta_stop_rate"] <= 0
    ]
    robust.sort(key=lambda row: (row["avg_return"], row["avg_excess"], -row["stop_rate"]), reverse=True)

    relaxed = [
        row for row in filter_results
        if row["count"] >= len(frame) * 0.4
        and row["missed_large_winner_count"] <= 1
        and row["delta_return"] > 0
        and row["delta_excess"] > 0
    ]
    relaxed.sort(key=lambda row: (row["avg_return"], row["avg_excess"], -row["stop_rate"]), reverse=True)

    all_ranked = sorted(
        filter_results,
        key=lambda row: (row["avg_return"], row["avg_excess"], -row["stop_rate"]),
        reverse=True,
    )

    bin_specs = {
        "gain_since_early_pct": ([-float("inf"), 0, 3, 5, 8, 10, 15], ["<0", "0-3", "3-5", "5-8", "8-10", "10-15"]),
        "trading_days_since_early": ([-float("inf"), 1, 4, 7, 11, float("inf")], ["0", "1-3", "4-6", "7-10", ">=11"]),
        "buy_score": ([-float("inf"), 55, 60, 65, 70, float("inf")], ["<55", "55-60", "60-65", "65-70", ">=70"]),
        "sector_score": ([-float("inf"), 40, 55, 70, float("inf")], ["<40", "40-55", "55-70", ">=70"]),
        "sector_leader_rank": ([-float("inf"), 6, 11, 21, 31, float("inf")], ["1-5", "6-10", "11-20", "21-30", ">30"]),
        "atr20_pct": ([-float("inf"), 3, 4, 5, 6, 8, float("inf")], ["<3", "3-4", "4-5", "5-6", "6-8", ">=8"]),
        "avg_trading_value20": ([-float("inf"), 1e9, 5e9, 1e10, 3e10, float("inf")], ["<1B", "1-5B", "5-10B", "10-30B", ">=30B"]),
        "capture_rs_score": ([-float("inf"), 50, 60, 70, 80, float("inf")], ["<50", "50-60", "60-70", "70-80", ">=80"]),
    }

    output = {
        "subset_rule": "captures.csv gain_since_early_pct is null OR < 15%",
        "baseline": baseline,
        "large_winners": large_winners,
        "winner_vs_loser": {
            "winner_count": int(len(winners)),
            "loser_count": int(len(losers)),
            "winner_numeric": numeric_summary(winners, numeric_columns),
            "loser_numeric": numeric_summary(losers, numeric_columns),
            "winner_weekly": categorical_summary(winners, "weekly_state"),
            "loser_weekly": categorical_summary(losers, "weekly_state"),
            "winner_grade": categorical_summary(winners, "buy_grade"),
            "loser_grade": categorical_summary(losers, "buy_grade"),
            "winner_market": categorical_summary(winners, "market"),
            "loser_market": categorical_summary(losers, "market"),
            "winner_institutional": categorical_summary(winners, "institutional_fit"),
            "loser_institutional": categorical_summary(losers, "institutional_fit"),
        },
        "robust_candidates": robust[:20],
        "relaxed_candidates": relaxed[:20],
        "top_all_single_filters": all_ranked[:30],
        "feature_bins": {
            column: build_bin_summary(frame, column, bins, labels)
            for column, (bins, labels) in bin_specs.items()
        },
    }

    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "subset_count": len(frame),
        "baseline": baseline,
        "large_winners": large_winners,
        "robust_top10": robust[:10],
        "relaxed_top10": relaxed[:10],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
