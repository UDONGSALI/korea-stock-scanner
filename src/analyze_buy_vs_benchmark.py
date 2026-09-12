from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from pykrx import stock

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CONFIG_PATH = ROOT_DIR / "config.json"
TRACKING_PATH = DATA_DIR / "tracking.json"
OUTPUT_JSON = DATA_DIR / "buy_vs_benchmark_analysis.json"
OUTPUT_DAILY = DATA_DIR / "buy_vs_benchmark_daily.csv"
OUTPUT_TRADES = DATA_DIR / "buy_vs_benchmark_trades.csv"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def safe_float(value, default=np.nan):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_snapshots(start_date: str, end_date: str, tickers: set[str]) -> pd.DataFrame:
    frames = []
    for path in sorted(SNAPSHOT_DIR.glob("*.csv.gz")):
        date_text = path.stem.replace(".csv", "")
        if date_text < start_date or date_text > end_date:
            continue
        frame = pd.read_csv(path, dtype={"ticker": str})
        frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
        frame = frame[frame["ticker"].isin(tickers)].copy()
        if not frame.empty:
            frame["date"] = pd.to_datetime(frame["date"])
            frames.append(frame[["date", "market", "ticker", "close"]])
    if not frames:
        raise RuntimeError("분석 기간의 스냅샷 데이터가 없습니다.")
    return pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)


def fetch_indices(config: dict, start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
    output = {}
    from_date = start_date.replace("-", "")
    to_date = end_date.replace("-", "")
    for market, index_code in config["market_indices"].items():
        frame = stock.get_index_ohlcv(from_date, to_date, index_code)
        if frame is None or frame.empty:
            raise RuntimeError(f"{market} 지수 데이터를 가져오지 못했습니다.")
        frame = frame.reset_index()
        date_column = frame.columns[0]
        frame = frame.rename(columns={date_column: "date", "시가": "open", "종가": "close"})
        frame["date"] = pd.to_datetime(frame["date"])
        output[market] = frame.set_index("date")[["open", "close"]].astype(float).sort_index()
    return output


def resolve_trade_market(snapshot: pd.DataFrame, capture_date: str, ticker: str) -> str:
    date_value = pd.Timestamp(capture_date)
    rows = snapshot[(snapshot["date"] == date_value) & (snapshot["ticker"] == ticker)]
    if rows.empty:
        rows = snapshot[snapshot["ticker"] == ticker]
    if rows.empty:
        return "UNKNOWN"
    return str(rows.iloc[0]["market"])


def index_return(index_frame: pd.DataFrame, start_date: str, end_date: str, end_at_open: bool = False) -> float:
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    if start_ts not in index_frame.index:
        valid = index_frame.index[index_frame.index >= start_ts]
        if len(valid) == 0:
            return np.nan
        start_ts = valid[0]
    if end_ts not in index_frame.index:
        valid = index_frame.index[index_frame.index <= end_ts]
        if len(valid) == 0:
            return np.nan
        end_ts = valid[-1]
    start_price = float(index_frame.loc[start_ts, "close"])
    end_field = "open" if end_at_open else "close"
    end_price = float(index_frame.loc[end_ts, end_field])
    return (end_price / start_price - 1.0) * 100.0


def summarize_rows(rows: list[dict], strategy_key: str, benchmark_key: str) -> dict:
    if not rows:
        return {"count": 0}
    strategy = np.array([safe_float(row.get(strategy_key)) for row in rows], dtype=float)
    benchmark = np.array([safe_float(row.get(benchmark_key)) for row in rows], dtype=float)
    valid = np.isfinite(strategy) & np.isfinite(benchmark)
    strategy = strategy[valid]
    benchmark = benchmark[valid]
    if len(strategy) == 0:
        return {"count": 0}
    excess = strategy - benchmark
    return {
        "count": int(len(strategy)),
        "avg_strategy_return_pct": round(float(strategy.mean()), 4),
        "median_strategy_return_pct": round(float(np.median(strategy)), 4),
        "avg_benchmark_return_pct": round(float(benchmark.mean()), 4),
        "avg_excess_return_pct_point": round(float(excess.mean()), 4),
        "median_excess_return_pct_point": round(float(np.median(excess)), 4),
        "win_rate_pct": round(float((strategy > 0).mean() * 100), 2),
        "beat_rate_pct": round(float((excess > 0).mean() * 100), 2),
        "avg_winner_pct": round(float(strategy[strategy > 0].mean()), 4) if np.any(strategy > 0) else None,
        "avg_loser_pct": round(float(strategy[strategy < 0].mean()), 4) if np.any(strategy < 0) else None,
    }


def group_summary(rows: list[dict], key: str, strategy_key: str, benchmark_key: str) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    return {group: summarize_rows(group_rows, strategy_key, benchmark_key) for group, group_rows in sorted(groups.items())}


def build_trade_rows(tracking: list[dict], snapshot: pd.DataFrame, indices: dict[str, pd.DataFrame], latest_date: str) -> list[dict]:
    rows = []
    for trade in tracking:
        capture_date = str(trade.get("capture_date"))
        ticker = str(trade.get("ticker", "")).zfill(6)
        market = resolve_trade_market(snapshot, capture_date, ticker)
        if market not in indices:
            continue

        trade_status = str(trade.get("trade_status") or "OPEN")
        exit_date = trade.get("exit_date")
        strategy_end_date = str(exit_date) if trade_status == "CLOSED" and exit_date else latest_date
        exit_reason = str(trade.get("exit_reason") or "")
        end_at_open = trade_status == "CLOSED" and "GAP" in exit_reason
        matched_benchmark = index_return(indices[market], capture_date, strategy_end_date, end_at_open=end_at_open)
        current_benchmark = index_return(indices[market], capture_date, latest_date)

        strategy_return = safe_float(trade.get("strategy_return_pct"), 0.0)
        raw_return = safe_float(trade.get("return_pct"), 0.0)
        current_tracking_benchmark = safe_float(trade.get("benchmark_return_pct"), current_benchmark)
        if np.isfinite(current_tracking_benchmark):
            current_benchmark = current_tracking_benchmark

        row = {
            "capture_date": capture_date,
            "ticker": ticker,
            "name": str(trade.get("name") or ""),
            "market": market,
            "buy_grade": str(trade.get("buy_grade") or "UNKNOWN"),
            "priority_selected": bool(trade.get("priority_selected")),
            "trade_status": trade_status,
            "exit_date": exit_date,
            "exit_reason": exit_reason or None,
            "strategy_return_pct": strategy_return,
            "matched_benchmark_return_pct": matched_benchmark,
            "strategy_excess_pct_point": strategy_return - matched_benchmark if np.isfinite(matched_benchmark) else np.nan,
            "raw_hold_return_pct": raw_return,
            "current_benchmark_return_pct": current_benchmark,
            "raw_hold_excess_pct_point": raw_return - current_benchmark if np.isfinite(current_benchmark) else np.nan,
            "max_return_pct": safe_float(trade.get("max_return_pct")),
            "max_drawdown_pct": safe_float(trade.get("max_drawdown_pct")),
        }
        rows.append(row)
    return rows


def daily_strategy_return(trade: dict, date_value: pd.Timestamp, close_lookup: dict[tuple[pd.Timestamp, str], float]) -> float | None:
    capture_ts = pd.Timestamp(trade["capture_date"])
    if date_value < capture_ts:
        return None
    exit_date = trade.get("exit_date")
    if exit_date and date_value >= pd.Timestamp(exit_date):
        return safe_float(trade.get("strategy_return_pct"), 0.0)

    close = close_lookup.get((date_value, trade["ticker"]))
    if close is None or not np.isfinite(close):
        return None
    entry = safe_float(trade.get("capture_close"))
    if not np.isfinite(entry) or entry <= 0:
        return None

    current_return = (float(close) / entry - 1.0) * 100.0
    partial_date = trade.get("partial_exit_date")
    partial_price = safe_float(trade.get("partial_exit_price"))
    remaining_pct = safe_float(trade.get("remaining_position_pct"), 100.0)
    if partial_date and date_value >= pd.Timestamp(partial_date) and np.isfinite(partial_price) and remaining_pct < 100:
        realized_weight = max(0.0, min(1.0, 1.0 - remaining_pct / 100.0))
        remaining_weight = 1.0 - realized_weight
        partial_return = (partial_price / entry - 1.0) * 100.0
        return partial_return * realized_weight + current_return * remaining_weight
    return current_return


def build_daily_curve(tracking: list[dict], trade_rows: list[dict], snapshot: pd.DataFrame, indices: dict[str, pd.DataFrame]) -> list[dict]:
    trade_meta = {(row["capture_date"], row["ticker"]): row for row in trade_rows}
    normalized = []
    for trade in tracking:
        key = (str(trade.get("capture_date")), str(trade.get("ticker", "")).zfill(6))
        meta = trade_meta.get(key)
        if not meta:
            continue
        merged = dict(trade)
        merged.update(meta)
        normalized.append(merged)

    close_lookup = {
        (row.date, str(row.ticker).zfill(6)): float(row.close)
        for row in snapshot.itertuples(index=False)
    }
    dates = sorted(snapshot["date"].drop_duplicates().tolist())
    output = []

    for date_value in dates:
        strategy_returns = []
        benchmark_returns = []
        raw_returns = []
        raw_benchmarks = []
        active_count = 0

        for trade in normalized:
            capture_ts = pd.Timestamp(trade["capture_date"])
            if capture_ts > date_value:
                continue
            active_count += 1
            strategy_return = daily_strategy_return(trade, date_value, close_lookup)
            if strategy_return is not None:
                strategy_returns.append(strategy_return)

                exit_date = trade.get("exit_date")
                ended = exit_date and date_value >= pd.Timestamp(exit_date)
                benchmark_end_date = str(exit_date) if ended else date_value.strftime("%Y-%m-%d")
                end_at_open = bool(ended and "GAP" in str(trade.get("exit_reason") or ""))
                benchmark_return = index_return(indices[trade["market"]], trade["capture_date"], benchmark_end_date, end_at_open=end_at_open)
                if np.isfinite(benchmark_return):
                    benchmark_returns.append(benchmark_return)

            close = close_lookup.get((date_value, trade["ticker"]))
            entry = safe_float(trade.get("capture_close"))
            if close is not None and np.isfinite(entry) and entry > 0:
                raw_returns.append((close / entry - 1.0) * 100.0)
                raw_benchmark = index_return(indices[trade["market"]], trade["capture_date"], date_value.strftime("%Y-%m-%d"))
                if np.isfinite(raw_benchmark):
                    raw_benchmarks.append(raw_benchmark)

        if not strategy_returns:
            continue
        output.append({
            "date": date_value.strftime("%Y-%m-%d"),
            "signals_in_cohort": active_count,
            "strategy_avg_return_pct": round(float(np.mean(strategy_returns)), 4),
            "strategy_matched_benchmark_avg_pct": round(float(np.mean(benchmark_returns)), 4) if benchmark_returns else None,
            "strategy_excess_pct_point": round(float(np.mean(strategy_returns) - np.mean(benchmark_returns)), 4) if benchmark_returns else None,
            "raw_hold_avg_return_pct": round(float(np.mean(raw_returns)), 4) if raw_returns else None,
            "raw_hold_benchmark_avg_pct": round(float(np.mean(raw_benchmarks)), 4) if raw_benchmarks else None,
            "raw_hold_excess_pct_point": round(float(np.mean(raw_returns) - np.mean(raw_benchmarks)), 4) if raw_returns and raw_benchmarks else None,
        })
    return output


def main() -> None:
    config = load_json(CONFIG_PATH, {}) or {}
    tracking = load_json(TRACKING_PATH, []) or []
    if not tracking:
        raise RuntimeError("tracking.json이 비어 있습니다.")

    start_date = min(str(row.get("capture_date")) for row in tracking)
    latest_date = max(str(row.get("latest_date")) for row in tracking)
    tickers = {str(row.get("ticker", "")).zfill(6) for row in tracking}
    snapshot = load_snapshots(start_date, latest_date, tickers)
    indices = fetch_indices(config, start_date, latest_date)
    trade_rows = build_trade_rows(tracking, snapshot, indices, latest_date)
    daily_rows = build_daily_curve(tracking, trade_rows, snapshot, indices)

    stop_rows = [row for row in trade_rows if row.get("exit_reason") in {"STOP_LOSS", "STOP_LOSS_GAP"}]
    closed_rows = [row for row in trade_rows if row.get("trade_status") == "CLOSED"]
    open_rows = [row for row in trade_rows if row.get("trade_status") != "CLOSED"]

    index_period = {}
    for market, index_frame in indices.items():
        index_period[market] = {
            "start_close": round(float(index_frame.iloc[0]["close"]), 4),
            "end_close": round(float(index_frame.iloc[-1]["close"]), 4),
            "return_pct": round((float(index_frame.iloc[-1]["close"]) / float(index_frame.iloc[0]["close"]) - 1.0) * 100.0, 4),
        }

    raw_summary = summarize_rows(trade_rows, "raw_hold_return_pct", "current_benchmark_return_pct")
    strategy_summary = summarize_rows(trade_rows, "strategy_return_pct", "matched_benchmark_return_pct")

    result = {
        "analysis_period": {"start": start_date, "end": latest_date},
        "method": {
            "entry": "BUY capture-day close",
            "raw_hold": "capture close to latest close, no exits",
            "strategy": "current kangto_exit_v2 realized/marked strategy return",
            "strategy_benchmark": "same market index from capture close to actual exit date (gap exit uses index open) or latest close",
            "daily_curve": "equal-weight average return of all BUY signals captured up to each date; not a fixed-capital portfolio",
        },
        "index_period": index_period,
        "counts": {
            "total": len(trade_rows),
            "open": len(open_rows),
            "closed": len(closed_rows),
            "stop_loss": len(stop_rows),
            "partial_exit": sum(1 for row in tracking if row.get("partial_exit_date")),
        },
        "raw_hold_all_buy": raw_summary,
        "strategy_all_buy": strategy_summary,
        "strategy_by_capture_date": group_summary(trade_rows, "capture_date", "strategy_return_pct", "matched_benchmark_return_pct"),
        "raw_hold_by_capture_date": group_summary(trade_rows, "capture_date", "raw_hold_return_pct", "current_benchmark_return_pct"),
        "strategy_by_grade": group_summary(trade_rows, "buy_grade", "strategy_return_pct", "matched_benchmark_return_pct"),
        "strategy_by_market": group_summary(trade_rows, "market", "strategy_return_pct", "matched_benchmark_return_pct"),
        "strategy_by_priority": {
            "priority_true": summarize_rows([row for row in trade_rows if row.get("priority_selected")], "strategy_return_pct", "matched_benchmark_return_pct"),
            "priority_false": summarize_rows([row for row in trade_rows if not row.get("priority_selected")], "strategy_return_pct", "matched_benchmark_return_pct"),
        },
        "stop_loss_summary": summarize_rows(stop_rows, "strategy_return_pct", "matched_benchmark_return_pct"),
        "exit_vs_hold": {
            "closed_count": len(closed_rows),
            "avg_strategy_minus_raw_hold_pct_point": round(float(np.mean([row["strategy_return_pct"] - row["raw_hold_return_pct"] for row in closed_rows])), 4) if closed_rows else None,
            "closed_better_than_hold_count": sum(1 for row in closed_rows if row["strategy_return_pct"] > row["raw_hold_return_pct"]),
            "closed_worse_than_hold_count": sum(1 for row in closed_rows if row["strategy_return_pct"] < row["raw_hold_return_pct"]),
        },
        "top_strategy": sorted(trade_rows, key=lambda row: row["strategy_return_pct"], reverse=True)[:10],
        "bottom_strategy": sorted(trade_rows, key=lambda row: row["strategy_return_pct"])[:10],
        "latest_daily_curve": daily_rows[-1] if daily_rows else None,
    }

    with OUTPUT_JSON.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)

    if daily_rows:
        with OUTPUT_DAILY.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(daily_rows[0].keys()))
            writer.writeheader()
            writer.writerows(daily_rows)

    if trade_rows:
        with OUTPUT_TRADES.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(trade_rows[0].keys()))
            writer.writeheader()
            writer.writerows(trade_rows)

    print(json.dumps({
        "period": result["analysis_period"],
        "counts": result["counts"],
        "raw_hold": result["raw_hold_all_buy"],
        "strategy": result["strategy_all_buy"],
        "exit_vs_hold": result["exit_vs_hold"],
        "latest_daily_curve": result["latest_daily_curve"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
