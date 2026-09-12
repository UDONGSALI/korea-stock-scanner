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
OUTPUT_PATH = DATA_DIR / "sector_leader_component_analysis_v2.json"


def num(value):
    try:
        if value is None or value == "" or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def performance(frame: pd.DataFrame) -> dict:
    returns = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    excess = pd.to_numeric(frame["strategy_excess_pct_point"], errors="coerce")
    exits = frame["exit_reason"].fillna("").astype(str)
    return {
        "count": int(len(frame)),
        "avg_return": round(float(returns.mean()), 4),
        "median_return": round(float(returns.median()), 4),
        "avg_excess": round(float(excess.mean()), 4),
        "win_rate": round(float((returns > 0).mean() * 100), 2),
        "stop_rate": round(float(exits.str.startswith("STOP_LOSS").mean() * 100), 2),
        "large_winners": int((returns >= 8).sum()),
    }


def stats(frame: pd.DataFrame, columns: list[str]) -> dict:
    out = {}
    for column in columns:
        s = pd.to_numeric(frame[column], errors="coerce").dropna()
        out[column] = {
            "mean": round(float(s.mean()), 4) if len(s) else None,
            "median": round(float(s.median()), 4) if len(s) else None,
        }
    return out


def spearman(frame: pd.DataFrame, columns: list[str]) -> dict:
    target = pd.to_numeric(frame["strategy_return_pct"], errors="coerce")
    out = {}
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        valid = values.notna() & target.notna()
        if valid.sum() < 5 or values[valid].nunique() < 2:
            out[column] = None
            continue
        corr = values[valid].rank().corr(target[valid].rank())
        out[column] = round(float(corr), 4) if pd.notna(corr) else None
    return out


def row_view(row: pd.Series) -> dict:
    return {
        "date": str(row["capture_date"]),
        "name": str(row["name"]),
        "ticker": str(row["ticker"]),
        "sector": str(row["sector"]),
        "rank": num(row["sector_leader_rank"]),
        "future_return": num(row["strategy_return_pct"]),
        "exit_reason": None if pd.isna(row["exit_reason"]) else str(row["exit_reason"]),
        "ret5": num(row["return5"]),
        "ret10": num(row["return10"]),
        "ret20": num(row["return20"]),
        "ret60": num(row["return60"]),
        "ret20_pctile": num(row["ret20_pctile"]),
        "ret60_pctile": num(row["ret60_pctile"]),
        "high52_distance": num(row["high52_distance"]),
        "high_proximity": num(row["high_proximity"]),
        "contrib_20": num(row["contrib_20"]),
        "contrib_60": num(row["contrib_60"]),
        "contrib_high": num(row["contrib_high"]),
        "leader_raw": num(row["leader_raw"]),
        "gain_since_early": num(row["gain_since_early_pct"]),
        "buy_score": num(row["buy_score"]),
    }


def main() -> None:
    config = load_json(CONFIG_PATH, {}) or {}
    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    trades = pd.read_csv(TRADE_PATH, dtype={"ticker": str})
    captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
    trades["ticker"] = trades["ticker"].astype(str).str.zfill(6)

    capture_cols = [
        "capture_date", "ticker", "name", "gain_since_early_pct", "buy_score",
        "sector", "sector_leader_rank",
    ]
    base = trades.drop(columns=["buy_grade"], errors="ignore").merge(
        captures[capture_cols], on=["capture_date", "ticker", "name"], how="left"
    )
    gain = pd.to_numeric(base["gain_since_early_pct"], errors="coerce")
    base = base[gain.isna() | (gain < 15)].copy()

    history = add_ranking_indicators(load_history())
    history["return5"] = history.groupby("ticker")["close"].pct_change(5) * 100
    history["return10"] = history.groupby("ticker")["close"].pct_change(10) * 100

    records = []
    for date_text, date_group in base.groupby("capture_date"):
        capture_date = pd.Timestamp(date_text)
        sectors = fetch_sector_classifications(capture_date, config["markets"])
        day = build_day_universe(history, capture_date, sectors, config)
        eligible = day[day["return20_rank"].notna() & day["return60_rank"].notna()].copy()
        eligible["ret20_pctile_recalc"] = eligible.groupby("sector")["return20_rank"].rank(pct=True) * 100
        eligible["ret60_pctile_recalc"] = eligible.groupby("sector")["return60_rank"].rank(pct=True) * 100
        day_map = {str(r["ticker"]).zfill(6): r for _, r in eligible.iterrows()}

        for _, src in date_group.iterrows():
            ticker = str(src["ticker"]).zfill(6)
            r = day_map.get(ticker)
            record = src.to_dict()
            record["ticker"] = ticker
            if r is None:
                for key in ["return5", "return10", "return20", "return60", "ret20_pctile", "ret60_pctile", "high52_distance", "high_proximity", "leader_raw"]:
                    record[key] = None
            else:
                record.update({
                    "return5": num(r.get("return5")),
                    "return10": num(r.get("return10")),
                    "return20": num(r.get("return20_rank")),
                    "return60": num(r.get("return60_rank")),
                    "ret20_pctile": num(r.get("ret20_pctile_recalc")),
                    "ret60_pctile": num(r.get("ret60_pctile_recalc")),
                    "high52_distance": num(r.get("distance_high52_rank")),
                    "high_proximity": num(r.get("high_proximity_score")),
                    "leader_raw": num(r.get("sector_leader_score_raw")),
                })
            record["contrib_20"] = None if record.get("ret20_pctile") is None else record["ret20_pctile"] * 0.45
            record["contrib_60"] = None if record.get("ret60_pctile") is None else record["ret60_pctile"] * 0.35
            record["contrib_high"] = None if record.get("high_proximity") is None else record["high_proximity"] * 0.20
            records.append(record)

    frame = pd.DataFrame(records)
    rank = pd.to_numeric(frame["sector_leader_rank"], errors="coerce")
    groups = {
        "1-5": frame[rank <= 5].copy(),
        "6-20": frame[(rank >= 6) & (rank <= 20)].copy(),
        ">20": frame[rank > 20].copy(),
    }
    columns = [
        "return5", "return10", "return20", "return60", "ret20_pctile", "ret60_pctile",
        "high52_distance", "high_proximity", "contrib_20", "contrib_60", "contrib_high",
        "leader_raw", "gain_since_early_pct", "buy_score",
    ]

    group_out = {
        name: {"performance": performance(group), "components": stats(group, columns)}
        for name, group in groups.items()
    }

    top_1_5 = groups["1-5"].sort_values(["capture_date", "sector_leader_rank"])
    large = frame[pd.to_numeric(frame["strategy_return_pct"], errors="coerce") >= 8].sort_values("strategy_return_pct", ascending=False)

    output = {
        "formula": "leader_raw = 45% sector 20d percentile + 35% sector 60d percentile + 20% 52w-high proximity",
        "cohort_count": int(len(frame)),
        "groups": group_out,
        "spearman_vs_future_return": spearman(frame, columns + ["sector_leader_rank"]),
        "top_1_5": [row_view(row) for _, row in top_1_5.iterrows()],
        "large_winners": [row_view(row) for _, row in large.iterrows()],
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"groups": group_out, "spearman": output["spearman_vs_future_return"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
