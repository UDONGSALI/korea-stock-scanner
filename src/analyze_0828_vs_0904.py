from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
RESULT_DIR = DATA_DIR / "results"
TRACKING_ANALYSIS_PATH = DATA_DIR / "buy_vs_benchmark_trades.csv"
OUTPUT_PATH = DATA_DIR / "compare_2026-08-28_vs_2026-09-04.json"

DATES = ["2026-08-28", "2026-09-04"]

NUMERIC_FIELDS = [
    "rs_score",
    "rs_20_score",
    "rs_60_score",
    "rs_acceleration",
    "distance_from_high52_pct",
    "base_days",
    "base_depth_pct",
    "distance_to_base_high_pct",
    "volume_ratio",
    "buy_score",
    "sector_score",
    "sector_leader_rank",
    "atr20_pct",
    "market_cap",
    "gain_since_early_pct",
    "trading_days_since_early",
]


def load_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def to_number(value):
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def clean_json(value):
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def summarize_numeric(rows: list[dict], field: str) -> dict:
    values = [to_number(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    series = pd.Series(values, dtype=float)
    return {
        "count": len(values),
        "mean": round(float(series.mean()), 4),
        "median": round(float(series.median()), 4),
        "min": round(float(series.min()), 4),
        "max": round(float(series.max()), 4),
    }


def pct(count: int, total: int) -> float:
    return round(count / total * 100, 2) if total else 0.0


def summarize_categorical(rows: list[dict], field: str) -> dict:
    counts = Counter(str(row.get(field)) for row in rows)
    total = len(rows)
    return {
        key: {"count": count, "pct": pct(count, total)}
        for key, count in counts.most_common()
    }


def build_rows(date_text: str, trade_map: dict[tuple[str, str], dict]) -> list[dict]:
    result = load_json(RESULT_DIR / f"{date_text}.json")
    rows = []
    for signal in result.get("signals", []):
        ticker = str(signal.get("ticker", "")).zfill(6)
        trade = trade_map.get((date_text, ticker), {})
        row = dict(signal)
        row.update({
            "strategy_return_pct": to_number(trade.get("strategy_return_pct")),
            "matched_benchmark_return_pct": to_number(trade.get("matched_benchmark_return_pct")),
            "strategy_excess_pct_point": to_number(trade.get("strategy_excess_pct_point")),
            "raw_hold_return_pct": to_number(trade.get("raw_hold_return_pct")),
            "raw_hold_excess_pct_point": to_number(trade.get("raw_hold_excess_pct_point")),
            "max_return_pct": to_number(trade.get("max_return_pct")),
            "max_drawdown_pct": to_number(trade.get("max_drawdown_pct")),
            "trade_status": trade.get("trade_status"),
            "exit_reason": trade.get("exit_reason"),
        })
        rows.append(row)
    return rows


def cohort_summary(rows: list[dict]) -> dict:
    total = len(rows)
    strategy_values = [row["strategy_return_pct"] for row in rows if row.get("strategy_return_pct") is not None]
    excess_values = [row["strategy_excess_pct_point"] for row in rows if row.get("strategy_excess_pct_point") is not None]

    winners = [row for row in rows if (row.get("strategy_return_pct") or 0) > 0]
    losers = [row for row in rows if (row.get("strategy_return_pct") or 0) < 0]
    beaters = [row for row in rows if (row.get("strategy_excess_pct_point") or 0) > 0]
    stops = [row for row in rows if str(row.get("exit_reason") or "").startswith("STOP_LOSS")]

    return {
        "count": total,
        "performance": {
            "avg_strategy_return_pct": round(float(pd.Series(strategy_values).mean()), 4),
            "median_strategy_return_pct": round(float(pd.Series(strategy_values).median()), 4),
            "avg_excess_pct_point": round(float(pd.Series(excess_values).mean()), 4),
            "win_rate_pct": pct(len(winners), total),
            "beat_benchmark_rate_pct": pct(len(beaters), total),
            "stop_loss_rate_pct": pct(len(stops), total),
            "winners": len(winners),
            "losers": len(losers),
            "stop_losses": len(stops),
        },
        "numeric": {field: summarize_numeric(rows, field) for field in NUMERIC_FIELDS},
        "categorical": {
            "market": summarize_categorical(rows, "market"),
            "buy_grade": summarize_categorical(rows, "buy_grade"),
            "weekly_state": summarize_categorical(rows, "weekly_state"),
            "early_state": summarize_categorical(rows, "early_state"),
            "mtt": summarize_categorical(rows, "mtt"),
            "institutional_fit": summarize_categorical(rows, "institutional_fit"),
            "sector": summarize_categorical(rows, "sector"),
        },
        "thresholds": {
            "leader_rank_le_5_pct": pct(sum(1 for row in rows if to_number(row.get("sector_leader_rank")) is not None and to_number(row.get("sector_leader_rank")) <= 5), total),
            "leader_rank_le_10_pct": pct(sum(1 for row in rows if to_number(row.get("sector_leader_rank")) is not None and to_number(row.get("sector_leader_rank")) <= 10), total),
            "sector_score_ge_55_pct": pct(sum(1 for row in rows if (to_number(row.get("sector_score")) or 0) >= 55), total),
            "sector_score_ge_70_pct": pct(sum(1 for row in rows if (to_number(row.get("sector_score")) or 0) >= 70), total),
            "weekly_strong_good_pct": pct(sum(1 for row in rows if str(row.get("weekly_state")) in {"STRONG", "GOOD"}), total),
            "mtt_true_pct": pct(sum(1 for row in rows if bool(row.get("mtt"))), total),
            "institutional_fit_pct": pct(sum(1 for row in rows if bool(row.get("institutional_fit"))), total),
            "rs_ge_70_pct": pct(sum(1 for row in rows if (to_number(row.get("rs_score")) or 0) >= 70), total),
            "rs_ge_80_pct": pct(sum(1 for row in rows if (to_number(row.get("rs_score")) or 0) >= 80), total),
            "breakout_extension_le_3_pct": pct(sum(1 for row in rows if to_number(row.get("distance_to_base_high_pct")) is not None and to_number(row.get("distance_to_base_high_pct")) <= 3), total),
            "breakout_extension_gt_5_pct": pct(sum(1 for row in rows if (to_number(row.get("distance_to_base_high_pct")) or 0) > 5), total),
            "base_depth_le_15_pct": pct(sum(1 for row in rows if to_number(row.get("base_depth_pct")) is not None and to_number(row.get("base_depth_pct")) <= 15), total),
            "volume_ratio_ge_2_pct": pct(sum(1 for row in rows if (to_number(row.get("volume_ratio")) or 0) >= 2), total),
            "early_no_early_pct": pct(sum(1 for row in rows if str(row.get("early_state")) == "NO_EARLY"), total),
            "early_late_climax_pct": pct(sum(1 for row in rows if str(row.get("early_state")) in {"LATE", "CLIMAX"}), total),
        },
        "rows": [
            {
                "name": row.get("name"),
                "ticker": str(row.get("ticker", "")).zfill(6),
                "market": row.get("market"),
                "strategy_return_pct": to_number(row.get("strategy_return_pct")),
                "strategy_excess_pct_point": to_number(row.get("strategy_excess_pct_point")),
                "trade_status": row.get("trade_status"),
                "exit_reason": row.get("exit_reason"),
                "buy_grade": row.get("buy_grade"),
                "buy_score": to_number(row.get("buy_score")),
                "sector": row.get("sector"),
                "sector_score": to_number(row.get("sector_score")),
                "sector_leader_rank": to_number(row.get("sector_leader_rank")),
                "weekly_state": row.get("weekly_state"),
                "rs_score": to_number(row.get("rs_score")),
                "rs_20_score": to_number(row.get("rs_20_score")),
                "rs_60_score": to_number(row.get("rs_60_score")),
                "rs_acceleration": to_number(row.get("rs_acceleration")),
                "mtt": bool(row.get("mtt")),
                "distance_from_high52_pct": to_number(row.get("distance_from_high52_pct")),
                "base_days": to_number(row.get("base_days")),
                "base_depth_pct": to_number(row.get("base_depth_pct")),
                "breakout_extension_pct": to_number(row.get("distance_to_base_high_pct")),
                "volume_ratio": to_number(row.get("volume_ratio")),
                "early_state": row.get("early_state"),
                "gain_since_early_pct": to_number(row.get("gain_since_early_pct")),
                "atr20_pct": to_number(row.get("atr20_pct")),
                "institutional_fit": bool(row.get("institutional_fit")),
            }
            for row in sorted(rows, key=lambda value: value.get("strategy_return_pct") if value.get("strategy_return_pct") is not None else -999, reverse=True)
        ],
    }


def winner_loser_feature_summary(rows: list[dict]) -> dict:
    groups = {
        "winner": [row for row in rows if (row.get("strategy_return_pct") or 0) > 0],
        "loser": [row for row in rows if (row.get("strategy_return_pct") or 0) < 0],
    }
    output = {}
    for group_name, group_rows in groups.items():
        output[group_name] = {
            "count": len(group_rows),
            "numeric": {
                field: summarize_numeric(group_rows, field)
                for field in [
                    "rs_score", "rs_20_score", "rs_60_score", "rs_acceleration",
                    "sector_score", "sector_leader_rank", "base_depth_pct",
                    "distance_to_base_high_pct", "volume_ratio", "atr20_pct",
                    "distance_from_high52_pct",
                ]
            },
            "weekly_state": summarize_categorical(group_rows, "weekly_state"),
            "early_state": summarize_categorical(group_rows, "early_state"),
        }
    return output


def main() -> None:
    trades = pd.read_csv(TRACKING_ANALYSIS_PATH, dtype={"ticker": str})
    trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)
    trade_map = {
        (str(row["capture_date"]), str(row["ticker"]).zfill(6)): row.to_dict()
        for _, row in trades.iterrows()
    }

    cohorts = {date_text: build_rows(date_text, trade_map) for date_text in DATES}
    output = {
        "comparison": {date_text: cohort_summary(rows) for date_text, rows in cohorts.items()},
        "combined_winner_loser": winner_loser_feature_summary(cohorts[DATES[0]] + cohorts[DATES[1]]),
    }
    output = clean_json(output)

    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
