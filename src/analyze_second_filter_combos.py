from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

root_dir = Path(__file__).resolve().parents[1]
data_dir = root_dir / "data"
captures = pd.read_csv(data_dir / "captures.csv", dtype={"ticker": str})
trades = pd.read_csv(data_dir / "buy_vs_benchmark_trades.csv", dtype={"ticker": str})

captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)

capture_columns = [
    "capture_date", "ticker", "name", "gain_since_early_pct", "trading_days_since_early",
    "buy_score", "sector_score", "sector_leader_rank", "weekly_state", "atr20_pct",
    "institutional_fit", "avg_trading_value20", "early_state",
]
base = trades.merge(captures[capture_columns], on=["capture_date", "ticker", "name"], how="left")
gain = pd.to_numeric(base["gain_since_early_pct"], errors="coerce")
base = base[gain.isna() | (gain < 15)].copy()

returns = pd.to_numeric(base["strategy_return_pct"], errors="coerce")
large = base[returns >= 8]
large_keys = set(zip(large["capture_date"].astype(str), large["ticker"].astype(str)))


def summarize(name, frame):
    ret = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    excess = pd.to_numeric(frame["strategy_excess_pct_point"], errors="coerce")
    stop = frame["exit_reason"].fillna("").astype(str).str.startswith("STOP_LOSS")
    keys = set(zip(frame["capture_date"].astype(str), frame["ticker"].astype(str)))
    missed = large_keys - keys
    missed_rows = large[large.apply(lambda row: (str(row["capture_date"]), str(row["ticker"])) in missed, axis=1)]
    return {
        "name": name,
        "count": int(len(frame)),
        "avg_return": round(float(ret.mean()), 4),
        "median_return": round(float(ret.median()), 4),
        "avg_excess": round(float(excess.mean()), 4),
        "win_rate": round(float((ret > 0).mean() * 100), 2),
        "beat_rate": round(float((excess > 0).mean() * 100), 2),
        "stop_rate": round(float(stop.mean() * 100), 2),
        "large_winners_kept": int((ret >= 8).sum()),
        "missed_large_winners": [f"{row['name']} {float(row['strategy_return_pct']):+.2f}%" for _, row in missed_rows.iterrows()],
    }

rank = pd.to_numeric(base["sector_leader_rank"], errors="coerce")
score = pd.to_numeric(base["buy_score"], errors="coerce")
days = pd.to_numeric(base["trading_days_since_early"], errors="coerce")

filters = {
    "baseline_EARLY15": pd.Series(True, index=base.index),
    "rank_6_20": rank.between(6, 20, inclusive="both"),
    "rank_6_30": rank.between(6, 30, inclusive="both"),
    "rank_le20_score_le70": (rank <= 20) & (score <= 70),
    "rank_le20_score_le65": (rank <= 20) & (score <= 65),
    "rank_6_20_score_le70": rank.between(6, 20, inclusive="both") & (score <= 70),
    "rank_6_20_score_le65": rank.between(6, 20, inclusive="both") & (score <= 65),
    "rank_6_30_score_le70": rank.between(6, 30, inclusive="both") & (score <= 70),
    "rank_6_30_score_le65": rank.between(6, 30, inclusive="both") & (score <= 65),
    "score_le70": score <= 70,
    "score_le65": score <= 65,
    "early_days_le6_or_noearly": days.isna() | (days <= 6),
    "early_days_le6_or_noearly_score_le70": (days.isna() | (days <= 6)) & (score <= 70),
}

result = [summarize(name, base[mask].copy()) for name, mask in filters.items()]
(data_dir / "second_filter_combo_summary.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False))
