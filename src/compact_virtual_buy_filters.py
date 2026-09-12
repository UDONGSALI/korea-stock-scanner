from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CAPTURE_PATH = DATA_DIR / "captures.csv"
TRADE_PATH = DATA_DIR / "buy_vs_benchmark_trades.csv"
OUTPUT_PATH = DATA_DIR / "virtual_buy_filter_summary.json"


def pct(count: int, total: int) -> float:
    return round(count / total * 100, 2) if total else 0.0


def summarize(frame: pd.DataFrame, baseline: pd.DataFrame) -> dict:
    strategy = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    excess = pd.to_numeric(frame["strategy_excess_pct_point"], errors="coerce")
    stops = frame[frame["exit_reason"].fillna("").astype(str).str.startswith("STOP_LOSS")]
    winners = frame[strategy > 0]
    beaters = frame[excess > 0]

    kept_keys = set(zip(frame["capture_date"].astype(str), frame["ticker"].astype(str)))
    excluded = baseline[
        ~baseline.apply(lambda row: (str(row["capture_date"]), str(row["ticker"])) in kept_keys, axis=1)
    ].copy()
    excluded["strategy_return_pct"] = pd.to_numeric(excluded["strategy_return_pct"], errors="coerce")
    large = excluded[excluded["strategy_return_pct"] >= 8].sort_values("strategy_return_pct", ascending=False)

    return {
        "kept": int(len(frame)),
        "excluded": int(len(excluded)),
        "avg_return": round(float(strategy.mean()), 4),
        "median_return": round(float(strategy.median()), 4),
        "avg_excess": round(float(excess.mean()), 4),
        "win_rate": pct(len(winners), len(frame)),
        "beat_rate": pct(len(beaters), len(frame)),
        "stop_rate": pct(len(stops), len(frame)),
        "missed_large_winners": [
            f"{row['name']} {float(row['strategy_return_pct']):+.2f}%"
            for _, row in large.iterrows()
        ],
    }


def main() -> None:
    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    trades = pd.read_csv(TRADE_PATH, dtype={"ticker": str})
    captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
    trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)
    columns = ["capture_date", "ticker", "name", "early_state", "gain_since_early_pct", "sector_leader_rank"]
    base = trades.merge(captures[columns], on=["capture_date", "ticker", "name"], how="left")

    gain = pd.to_numeric(base["gain_since_early_pct"], errors="coerce")
    rank = pd.to_numeric(base["sector_leader_rank"], errors="coerce")
    no_climax = base["early_state"].fillna("").astype(str) != "CLIMAX_RISK"
    early15 = gain.isna() | (gain < 15)
    early20 = gain.isna() | (gain < 20)
    top10 = rank.notna() & (rank <= 10)

    filters = {
        "기본_전체BUY": pd.Series(True, index=base.index),
        "CLIMAX제외": no_climax,
        "EARLY15미만": early15,
        "EARLY20미만": early20,
        "섹터TOP10": top10,
        "CLIMAX제외_EARLY15미만": no_climax & early15,
        "CLIMAX제외_EARLY20미만": no_climax & early20,
        "전체조합_15": no_climax & early15 & top10,
        "전체조합_20": no_climax & early20 & top10,
    }

    output = {name: summarize(base[mask].copy(), base) for name, mask in filters.items()}
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
