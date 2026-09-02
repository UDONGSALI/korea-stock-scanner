from __future__ import annotations

import csv
import gzip
import json
import math
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CONFIG_PATH = ROOT_DIR / "config.json"
CAPTURE_PATH = DATA_DIR / "captures.csv"
TRACKING_PATH = DATA_DIR / "tracking.json"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)


def load_captures() -> list[dict]:
    if not CAPTURE_PATH.exists():
        return []
    with CAPTURE_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_price_history(tickers: set[str]) -> pd.DataFrame:
    frames = []
    for path in sorted(SNAPSHOT_DIR.glob("*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as file:
            frame = pd.read_csv(file, dtype={"ticker": str})
        frame = frame[frame["ticker"].isin(tickers)].copy()
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()

    history = pd.concat(frames, ignore_index=True)
    history["date"] = pd.to_datetime(history["date"])
    history = history.sort_values(["ticker", "date"]).reset_index(drop=True)
    return history


def add_exit_indicators(history: pd.DataFrame, config: dict) -> pd.DataFrame:
    if history.empty:
        return history

    output = []
    base_config = config["base"]
    for _, frame in history.groupby("ticker", sort=False):
        frame = frame.copy().sort_values("date")
        open_price = frame["open"].astype(float)
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        close = frame["close"].astype(float)
        volume = frame["volume"].astype(float)

        frame["sma5_exit"] = close.rolling(5).mean()
        frame["sma20_exit"] = close.rolling(20).mean()
        frame["sma50_exit"] = close.rolling(50).mean()
        frame["avg_volume20_exit"] = volume.shift(1).rolling(20).mean()
        frame["volume_ratio_exit"] = volume / frame["avg_volume20_exit"]
        frame["prior_high20_exit"] = high.shift(1).rolling(20, min_periods=5).max()

        candle_range = (high - low).replace(0, float("nan"))
        frame["upper_wick_ratio_exit"] = (high - pd.concat([open_price, close], axis=1).max(axis=1)) / candle_range
        frame["upper_wick_ratio_exit"] = frame["upper_wick_ratio_exit"].fillna(0.0)

        frame["base_low_exit"] = float("nan")
        for base_days in sorted(base_config["candidate_days"]):
            base_high = high.shift(1).rolling(base_days).max()
            base_low = low.shift(1).rolling(base_days).min()
            base_depth_pct = (base_high - base_low) / base_high * 100
            valid_base = base_depth_pct <= base_config["max_depth_pct"]
            frame.loc[valid_base, "base_low_exit"] = base_low[valid_base]

        output.append(frame)

    return pd.concat(output, ignore_index=True).sort_values(["ticker", "date"]).reset_index(drop=True)


def is_number(value) -> bool:
    try:
        return value is not None and value != "" and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def get_initial_stop(capture: dict, frame: pd.DataFrame, exit_config: dict) -> float:
    if is_number(capture.get("stop_price")):
        return float(capture["stop_price"])

    capture_date = pd.Timestamp(capture["capture_date"])
    day = frame[frame["date"] == capture_date]
    if not day.empty and is_number(day.iloc[-1].get("base_low_exit")):
        return float(day.iloc[-1]["base_low_exit"])

    capture_price = float(capture["capture_close"])
    fallback_stop_pct = float(exit_config.get("fallback_stop_pct", 8.0))
    return capture_price * (1 - fallback_stop_pct / 100)


def get_trend_pace(highest_close: float, capture_price: float, risk_per_share: float, exit_config: dict) -> tuple[str, int]:
    peak_r = (highest_close - capture_price) / risk_per_share if risk_per_share > 0 else 0.0
    peak_gain_pct = (highest_close / capture_price - 1) * 100

    if peak_r >= float(exit_config["fast_trend_r_multiple"]) or peak_gain_pct >= float(exit_config["fast_trend_gain_pct"]):
        return "FAST", int(exit_config["trailing_ma_days"]["fast"])
    if peak_r >= float(exit_config["normal_trend_r_multiple"]):
        return "NORMAL", int(exit_config["trailing_ma_days"]["normal"])
    return "SLOW", int(exit_config["trailing_ma_days"]["slow"])


def is_climax_exit(row: pd.Series, peak_r: float, exit_config: dict) -> bool:
    if peak_r < float(exit_config["climax_min_r_multiple"]):
        return False

    volume_ratio = row.get("volume_ratio_exit")
    prior_high = row.get("prior_high20_exit")
    if not is_number(volume_ratio) or not is_number(prior_high):
        return False

    near_high = float(row["high"]) >= float(prior_high) * (1 - float(exit_config["climax_near_high_pct"]) / 100)
    high_volume = float(volume_ratio) >= float(exit_config["climax_volume_ratio_min"])
    bearish = float(row["close"]) < float(row["open"])
    large_drop = float(row.get("change_pct", 0.0)) <= float(exit_config["climax_drop_pct_max"])
    upper_wick = float(row.get("upper_wick_ratio_exit", 0.0)) >= float(exit_config["climax_upper_wick_ratio_min"])
    return near_high and high_volume and bearish and (large_drop or upper_wick)


def simulate_trade(capture: dict, frame: pd.DataFrame, config: dict) -> dict:
    exit_config = config["exit"]
    capture_price = float(capture["capture_close"])
    initial_stop = get_initial_stop(capture, frame, exit_config)
    risk_per_share = capture_price - initial_stop
    if risk_per_share <= 0:
        initial_stop = capture_price * (1 - float(exit_config.get("fallback_stop_pct", 8.0)) / 100)
        risk_per_share = capture_price - initial_stop

    reward_multiple = float(exit_config.get("partial_reward_multiple", config["risk"]["reward_multiple"]))
    three_r_target = capture_price + risk_per_share * reward_multiple
    partial_sell_pct = float(exit_config["partial_sell_pct"])
    remaining_pct = 100.0
    partial_exit_date = None
    partial_exit_price = None
    exit_date = None
    exit_price = None
    exit_reason = None
    current_stop = initial_stop
    trailing_basis = "INITIAL"
    highest_close = capture_price

    trade_frame = frame[frame["date"] >= pd.Timestamp(capture["capture_date"])].sort_values("date")
    for _, row in trade_frame.iterrows():
        close = float(row["close"])
        high = float(row["high"])
        highest_close = max(highest_close, close)

        if partial_exit_date is None:
            if high >= three_r_target:
                partial_exit_date = row["date"].strftime("%Y-%m-%d")
                partial_exit_price = three_r_target
                remaining_pct = max(0.0, 100.0 - partial_sell_pct)
                current_stop = max(current_stop, capture_price)
                trailing_basis = "BE"

                if close <= current_stop:
                    exit_date = row["date"].strftime("%Y-%m-%d")
                    exit_price = close
                    exit_reason = "BREAKEVEN_EXIT"
                    remaining_pct = 0.0
                    break
                continue

            if close <= initial_stop:
                exit_date = row["date"].strftime("%Y-%m-%d")
                exit_price = close
                exit_reason = "STOP_LOSS"
                remaining_pct = 0.0
                break
            continue

        peak_r = (highest_close - capture_price) / risk_per_share if risk_per_share > 0 else 0.0
        if is_climax_exit(row, peak_r, exit_config):
            exit_date = row["date"].strftime("%Y-%m-%d")
            exit_price = close
            exit_reason = "CLIMAX_EXIT"
            remaining_pct = 0.0
            break

        _, ma_days = get_trend_pace(highest_close, capture_price, risk_per_share, exit_config)
        ma_value = row.get(f"sma{ma_days}_exit")
        if is_number(ma_value):
            current_stop = max(current_stop, capture_price, float(ma_value))
            trailing_basis = f"{ma_days}MA"
        else:
            current_stop = max(current_stop, capture_price)
            trailing_basis = "BE"

        if close <= current_stop:
            exit_date = row["date"].strftime("%Y-%m-%d")
            exit_price = close
            exit_reason = f"TREND_EXIT_{trailing_basis}" if trailing_basis != "BE" else "BREAKEVEN_EXIT"
            remaining_pct = 0.0
            break

    partial_return_pct = None if partial_exit_price is None else (partial_exit_price / capture_price - 1) * 100
    final_return_pct = None if exit_price is None else (exit_price / capture_price - 1) * 100

    if exit_date is not None:
        trade_status = "CLOSED"
        if partial_return_pct is not None:
            realized_return_pct = partial_return_pct * (partial_sell_pct / 100) + final_return_pct * ((100 - partial_sell_pct) / 100)
        else:
            realized_return_pct = final_return_pct
        strategy_return_pct = realized_return_pct
    elif partial_exit_date is not None:
        trade_status = "PARTIAL"
        latest_close = float(trade_frame.iloc[-1]["close"])
        open_return_pct = (latest_close / capture_price - 1) * 100
        realized_return_pct = partial_return_pct * (partial_sell_pct / 100)
        strategy_return_pct = realized_return_pct + open_return_pct * (remaining_pct / 100)
    else:
        trade_status = "OPEN"
        latest_close = float(trade_frame.iloc[-1]["close"])
        realized_return_pct = 0.0
        strategy_return_pct = (latest_close / capture_price - 1) * 100

    return {
        "trade_status": trade_status,
        "remaining_position_pct": remaining_pct,
        "initial_stop_price": initial_stop,
        "current_stop_price": current_stop,
        "three_r_target": three_r_target,
        "partial_exit_date": partial_exit_date,
        "partial_exit_price": partial_exit_price,
        "trailing_basis": trailing_basis,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "realized_return_pct": realized_return_pct,
        "strategy_return_pct": strategy_return_pct,
        "exit_rule_version": exit_config["rule_version"],
    }


def main() -> None:
    config = load_json(CONFIG_PATH, {})
    captures = load_captures()
    tracking = load_json(TRACKING_PATH, []) or []
    if not captures or not tracking:
        print(json.dumps({"rows": 0, "closed": 0, "partial": 0}, ensure_ascii=False))
        return

    tickers = {str(row["ticker"]) for row in captures if row.get("ticker")}
    history = add_exit_indicators(load_price_history(tickers), config)
    tracking_map = {(str(row.get("capture_date")), str(row.get("ticker"))): row for row in tracking}

    output = []
    for capture in captures:
        key = (str(capture.get("capture_date")), str(capture.get("ticker")))
        base_row = dict(tracking_map.get(key, {}))
        if not base_row:
            base_row = {
                "capture_date": str(capture.get("capture_date")),
                "ticker": str(capture.get("ticker")),
                "name": capture.get("name", ""),
                "capture_close": float(capture.get("capture_close") or 0),
                "rule_version": capture.get("rule_version", "unknown"),
                "capture_market_alignment": capture.get("market_alignment", "unknown"),
            }
        ticker_frame = history[history["ticker"] == str(capture["ticker"])]
        if ticker_frame.empty:
            output.append(base_row)
            continue
        base_row.update(simulate_trade(capture, ticker_frame, config))
        output.append(base_row)

    save_json(TRACKING_PATH, output)
    print(json.dumps({
        "rows": len(output),
        "closed": sum(1 for row in output if row.get("trade_status") == "CLOSED"),
        "partial": sum(1 for row in output if row.get("trade_status") == "PARTIAL"),
        "open": sum(1 for row in output if row.get("trade_status") == "OPEN"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
