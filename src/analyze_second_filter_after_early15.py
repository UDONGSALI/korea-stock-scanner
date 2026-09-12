from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

root_dir = Path(__file__).resolve().parents[1]
data_dir = root_dir / "data"
result_dir = data_dir / "results"
trade_path = data_dir / "buy_vs_benchmark_trades.csv"
output_path = data_dir / "second_filter_after_early15.json"

large_winner_threshold = 8.0


def to_float(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def load_signal_map():
    signal_map = {}
    for path in sorted(result_dir.glob("*.json")):
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
        capture_date = str(data.get("scan_date") or path.stem)
        for signal in data.get("signals", []):
            ticker = str(signal.get("ticker", "")).zfill(6)
            signal_map[(capture_date, ticker)] = signal
    return signal_map


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
        str(key): {"count": int(value), "pct": round(float(value / total * 100), 2) if total else 0.0}
        for key, value in counts.items()
    }


def apply_numeric_filter(frame: pd.DataFrame, column: str, operator: str, threshold: float):
    values = pd.to_numeric(frame[column], errors="coerce")
    if operator == ">=":
        return frame[values.notna() & (values >= threshold)]
    if operator == "<=":
        return frame[values.notna() & (values <= threshold)]
    raise ValueError(operator)


def evaluate_filter(name: str, kept: pd.DataFrame, baseline: dict, large_winners: set[tuple[str, str]]) -> dict:
    kept_keys = set(zip(kept["capture_date"].astype(str), kept["ticker"].astype(str)))
    missed = sorted(large_winners - kept_keys)
    current = metrics(kept)
    return {
        "name": name,
        **current,
        "kept_pct": round(len(kept) / baseline["count"] * 100, 2) if baseline["count"] else 0.0,
        "delta_return": round(current["avg_return"] - baseline["avg_return"], 4) if current["avg_return"] is not None else None,
        "delta_excess": round(current["avg_excess"] - baseline["avg_excess"], 4) if current["avg_excess"] is not None else None,
        "delta_stop_rate": round(current["stop_rate"] - baseline["stop_rate"], 2) if current["stop_rate"] is not None else None,
        "missed_large_winner_count": len(missed),
        "missed_large_winners": [f"{date}:{ticker}" for date, ticker in missed],
    }


def build_bin_summary(frame: pd.DataFrame, column: str, bins: list[float], labels: list[str]) -> list[dict]:
    values = pd.to_numeric(frame[column], errors="coerce")
    bucket = pd.cut(values, bins=bins, labels=labels, include_lowest=True, right=False)
    output = []
    for label in labels:
        group = frame[bucket == label]
        if group.empty:
            continue
        output.append({"bucket": label, **metrics(group)})
    return output


def main():
    trades = pd.read_csv(trade_path, dtype={"ticker": str})
    trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)
    signal_map = load_signal_map()

    rows = []
    for _, trade in trades.iterrows():
        capture_date = str(trade["capture_date"])
        ticker = str(trade["ticker"]).zfill(6)
        signal = signal_map.get((capture_date, ticker), {})
        gain_since_early = to_float(signal.get("gain_since_early_pct"))

        # EARLY 기록이 없으면 과열로 볼 근거가 없으므로 유지한다.
        if gain_since_early is not None and gain_since_early >= 15.0:
            continue

        row = trade.to_dict()
        row.update({
            "ticker": ticker,
            "gain_since_early_pct": gain_since_early,
            "early_state": signal.get("early_state"),
            "rs_score": to_float(signal.get("rs_score")),
            "rs_20_score": to_float(signal.get("rs_20_score")),
            "rs_60_score": to_float(signal.get("rs_60_score")),
            "rs_acceleration": to_float(signal.get("rs_acceleration")),
            "sector_score": to_float(signal.get("sector_score")),
            "sector_leader_rank": to_float(signal.get("sector_leader_rank")),
            "weekly_state": signal.get("weekly_state"),
            "mtt": bool(signal.get("mtt")),
            "institutional_fit": bool(signal.get("institutional_fit")),
            "distance_from_high52_pct": to_float(signal.get("distance_from_high52_pct")),
            "base_days": to_float(signal.get("base_days")),
            "base_depth_pct": to_float(signal.get("base_depth_pct")),
            "breakout_extension_pct": to_float(signal.get("distance_to_base_high_pct")),
            "volume_ratio": to_float(signal.get("volume_ratio")),
            "atr20_pct": to_float(signal.get("atr20_pct")),
            "market_cap": to_float(signal.get("market_cap")),
            "buy_score_capture": to_float(signal.get("buy_score")),
        })
        rows.append(row)

    frame = pd.DataFrame(rows)
    baseline = metrics(frame)
    returns = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    winner_frame = frame[returns > 0]
    loser_frame = frame[returns < 0]

    large_winner_frame = frame[returns >= large_winner_threshold]
    large_winners = set(zip(large_winner_frame["capture_date"].astype(str), large_winner_frame["ticker"].astype(str)))
    large_winner_names = [
        {
            "capture_date": str(row["capture_date"]),
            "ticker": str(row["ticker"]),
            "name": str(row["name"]),
            "return": round(float(row["strategy_return_pct"]), 4),
        }
        for _, row in large_winner_frame.sort_values("strategy_return_pct", ascending=False).iterrows()
    ]

    numeric_columns = [
        "rs_score", "rs_20_score", "rs_60_score", "rs_acceleration",
        "sector_score", "sector_leader_rank", "distance_from_high52_pct",
        "base_days", "base_depth_pct", "breakout_extension_pct",
        "volume_ratio", "atr20_pct", "market_cap", "buy_score_capture",
    ]

    filters = []
    threshold_map = {
        "rs_score": [40, 50, 60, 70, 80],
        "rs_20_score": [60, 70, 80, 90],
        "rs_60_score": [75, 80, 85, 90, 95],
        "rs_acceleration": [0, 3, 5, 10, 15],
        "sector_score": [40, 50, 60, 70],
        "sector_leader_rank": [5, 10, 15, 20, 30],
        "distance_from_high52_pct": [-20, -15, -10, -5],
        "base_days": [7, 10, 15, 20],
        "base_depth_pct": [10, 12.5, 15, 17.5],
        "breakout_extension_pct": [1, 2, 3, 5],
        "volume_ratio": [1.2, 1.5, 2, 3],
        "atr20_pct": [3, 4, 5, 6],
        "market_cap": [500_000_000_000, 1_000_000_000_000, 5_000_000_000_000],
        "buy_score_capture": [55, 60, 65, 70, 75],
    }

    for column, thresholds in threshold_map.items():
        for threshold in thresholds:
            for operator in [">=", "<="]:
                kept = apply_numeric_filter(frame, column, operator, threshold)
                if len(kept) < 10 or len(kept) == len(frame):
                    continue
                filters.append(evaluate_filter(f"{column} {operator} {threshold}", kept, baseline, large_winners))

    categorical_filters = {
        "KOSPI만": frame[frame["market"] == "KOSPI"],
        "KOSDAQ만": frame[frame["market"] == "KOSDAQ"],
        "주봉_STRONG_GOOD": frame[frame["weekly_state"].isin(["STRONG", "GOOD"])],
        "주봉_RECOVERING제외": frame[frame["weekly_state"] != "RECOVERING"],
        "MTT_true": frame[frame["mtt"] == True],
        "MTT_false": frame[frame["mtt"] == False],
        "기관적합_true": frame[frame["institutional_fit"] == True],
        "기관적합_false": frame[frame["institutional_fit"] == False],
        "EARLY_FRESH": frame[frame["early_state"] == "FRESH"],
        "EARLY_FRESH_NOEARLY": frame[frame["early_state"].isin(["FRESH", "NO_EARLY"])],
    }
    for name, kept in categorical_filters.items():
        if len(kept) >= 10 and len(kept) < len(frame):
            filters.append(evaluate_filter(name, kept, baseline, large_winners))

    # 최소 절반 이상 유지 + 큰 승자 보존 + 수익/초과수익/손절률이 모두 개선되는 후보를 우선한다.
    robust = [
        row for row in filters
        if row["count"] >= len(frame) / 2
        and row["missed_large_winner_count"] == 0
        and row["delta_return"] > 0
        and row["delta_excess"] > 0
        and row["delta_stop_rate"] <= 0
    ]
    robust = sorted(robust, key=lambda row: (row["avg_return"], row["avg_excess"], -row["stop_rate"]), reverse=True)

    all_ranked = sorted(filters, key=lambda row: (row["avg_return"], row["avg_excess"]), reverse=True)

    bin_specs = {
        "rs_score": ([-float("inf"), 40, 60, 80, float("inf")], ["<40", "40-60", "60-80", ">=80"]),
        "rs_20_score": ([-float("inf"), 60, 80, 90, float("inf")], ["<60", "60-80", "80-90", ">=90"]),
        "rs_60_score": ([-float("inf"), 80, 90, 95, float("inf")], ["<80", "80-90", "90-95", ">=95"]),
        "rs_acceleration": ([-float("inf"), 0, 5, 10, float("inf")], ["<0", "0-5", "5-10", ">=10"]),
        "sector_score": ([-float("inf"), 40, 55, 70, float("inf")], ["<40", "40-55", "55-70", ">=70"]),
        "distance_from_high52_pct": ([-float("inf"), -20, -15, -10, -5, float("inf")], ["<-20", "-20~-15", "-15~-10", "-10~-5", ">=-5"]),
        "base_depth_pct": ([-float("inf"), 10, 15, 17.5, float("inf")], ["<10", "10-15", "15-17.5", ">=17.5"]),
        "breakout_extension_pct": ([-float("inf"), 1, 2, 3, 5, float("inf")], ["<1", "1-2", "2-3", "3-5", ">=5"]),
        "volume_ratio": ([-float("inf"), 1.2, 1.5, 2, 3, float("inf")], ["<1.2", "1.2-1.5", "1.5-2", "2-3", ">=3"]),
        "atr20_pct": ([-float("inf"), 3, 4, 5, 6, float("inf")], ["<3", "3-4", "4-5", "5-6", ">=6"]),
    }

    output = {
        "subset_rule": "gain_since_early_pct is null OR < 15%",
        "baseline": baseline,
        "large_winners": large_winner_names,
        "winner_vs_loser": {
            "winner_count": int(len(winner_frame)),
            "loser_count": int(len(loser_frame)),
            "winner_numeric": numeric_summary(winner_frame, numeric_columns),
            "loser_numeric": numeric_summary(loser_frame, numeric_columns),
            "winner_weekly": categorical_summary(winner_frame, "weekly_state"),
            "loser_weekly": categorical_summary(loser_frame, "weekly_state"),
            "winner_market": categorical_summary(winner_frame, "market"),
            "loser_market": categorical_summary(loser_frame, "market"),
            "winner_mtt": categorical_summary(winner_frame, "mtt"),
            "loser_mtt": categorical_summary(loser_frame, "mtt"),
            "winner_institutional": categorical_summary(winner_frame, "institutional_fit"),
            "loser_institutional": categorical_summary(loser_frame, "institutional_fit"),
        },
        "robust_second_filter_candidates": robust[:20],
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
        "large_winners": large_winner_names,
        "robust_top5": robust[:5],
        "all_top5": all_ranked[:5],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
