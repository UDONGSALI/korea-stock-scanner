from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pykrx import stock

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
RESULT_DIR = DATA_DIR / "results"
CONFIG_PATH = ROOT_DIR / "config.json"
CAPTURE_PATH = DATA_DIR / "captures.csv"


def loadConfig() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def ensureDirectories() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)


def getBusinessDays(history_days: int) -> list[pd.Timestamp]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=max(history_days * 2, 260))
    business_days = stock.get_previous_business_days(fromdate=start_date.strftime("%Y%m%d"), todate=end_date.strftime("%Y%m%d"))
    return business_days[-history_days:]


def fetchMarketSnapshot(date: pd.Timestamp, market: str) -> pd.DataFrame:
    date_text = date.strftime("%Y%m%d")
    frame = stock.get_market_ohlcv(date_text, market=market)
    if frame.empty:
        return frame

    frame = frame.reset_index().rename(columns={"티커": "ticker", "시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume", "거래대금": "trading_value", "등락률": "change_pct"})
    frame["date"] = date.strftime("%Y-%m-%d")
    frame["market"] = market
    return frame[["date", "market", "ticker", "open", "high", "low", "close", "volume", "trading_value", "change_pct"]]


def saveSnapshot(date: pd.Timestamp, markets: list[str]) -> Path:
    date_text = date.strftime("%Y-%m-%d")
    path = SNAPSHOT_DIR / f"{date_text}.csv.gz"
    if path.exists():
        return path

    frames = []
    for market in markets:
        frame = fetchMarketSnapshot(date, market)
        if not frame.empty:
            frames.append(frame)
        time.sleep(0.25)

    if not frames:
        raise RuntimeError(f"{date_text} 시장 데이터를 가져오지 못했습니다.")

    pd.concat(frames, ignore_index=True).to_csv(path, index=False, compression="gzip", encoding="utf-8-sig")
    return path


def loadHistory(business_days: list[pd.Timestamp]) -> pd.DataFrame:
    frames = []
    for date in business_days:
        path = SNAPSHOT_DIR / f"{date.strftime('%Y-%m-%d')}.csv.gz"
        if path.exists():
            frames.append(pd.read_csv(path, dtype={"ticker": str}))
    if not frames:
        raise RuntimeError("저장된 OHLCV 데이터가 없습니다.")

    history = pd.concat(frames, ignore_index=True)
    history["date"] = pd.to_datetime(history["date"])
    history = history.sort_values(["ticker", "date"]).reset_index(drop=True)
    return history


def fetchBenchmark(config: dict, business_days: list[pd.Timestamp]) -> pd.Series:
    from_date = business_days[0].strftime("%Y%m%d")
    to_date = business_days[-1].strftime("%Y%m%d")
    frame = stock.get_index_ohlcv(from_date, to_date, config["benchmark_index"])
    if frame.empty:
        raise RuntimeError("벤치마크 지수 데이터를 가져오지 못했습니다.")
    benchmark = frame["종가"].astype(float).rename("benchmark_close")
    benchmark.index = pd.to_datetime(benchmark.index)
    return benchmark


def addIndicators(history: pd.DataFrame, benchmark: pd.Series, config: dict) -> pd.DataFrame:
    output = []
    benchmark_return = benchmark.pct_change(config["relative_strength_days"])

    for _, frame in history.groupby("ticker", sort=False):
        frame = frame.copy().sort_values("date")
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        close = frame["close"].astype(float)
        volume = frame["volume"].astype(float)

        frame["sma20"] = close.rolling(20).mean()
        frame["sma60"] = close.rolling(60).mean()
        frame["sma120"] = close.rolling(120).mean()
        frame["prev_55_high"] = high.shift(1).rolling(config["breakout_days"]).max()
        frame["avg_volume20"] = volume.shift(1).rolling(20).mean()
        frame["volume_ratio"] = volume / frame["avg_volume20"]

        prev_close = close.shift(1)
        true_range = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        frame["atr14"] = true_range.rolling(config["atr_short"]).mean()
        frame["atr50"] = true_range.rolling(config["atr_long"]).mean()

        frame["stock_return60"] = close.pct_change(config["relative_strength_days"])
        frame["benchmark_return60"] = frame["date"].map(benchmark_return)
        frame["relative_strength60_pp"] = (frame["stock_return60"] - frame["benchmark_return60"]) * 100

        frame["signal"] = (
            (frame["avg_volume20"] >= config["min_avg_volume_20"])
            & (frame["volume_ratio"] >= config["volume_ratio_min"])
            & (close > frame["prev_55_high"])
            & (close > frame["sma20"])
            & (frame["sma20"] > frame["sma60"])
            & (frame["sma60"] > frame["sma120"])
            & (frame["relative_strength60_pp"] >= config["relative_strength_min_pct_point"])
            & (frame["atr14"] < frame["atr50"])
        )
        frame["new_signal"] = frame["signal"] & ~frame["signal"].shift(1, fill_value=False)
        output.append(frame)

    return pd.concat(output, ignore_index=True)


def buildResult(indicators: pd.DataFrame, config: dict) -> dict:
    target_date = indicators["date"].max()
    latest = indicators[indicators["date"] == target_date].copy()
    signal_column = "new_signal" if config["only_new_signal"] else "signal"
    selected = latest[latest[signal_column]].copy()

    selected["name"] = selected["ticker"].map(stock.get_market_ticker_name)
    columns = ["ticker", "name", "market", "close", "volume", "avg_volume20", "volume_ratio", "prev_55_high", "sma20", "sma60", "sma120", "relative_strength60_pp", "atr14", "atr50"]
    selected = selected[columns].replace({np.nan: None})

    records = []
    for row in selected.to_dict(orient="records"):
        cleaned = {}
        for key, value in row.items():
            if isinstance(value, (np.integer,)):
                value = int(value)
            elif isinstance(value, (np.floating,)):
                value = float(value)
            cleaned[key] = value
        records.append(cleaned)

    return {
        "scan_date": target_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "markets": config["markets"],
        "signal_count": len(records),
        "signals": records,
        "rules": {
            "avg_volume20_min": config["min_avg_volume_20"],
            "volume_ratio_min": config["volume_ratio_min"],
            "breakout": f"close > previous {config['breakout_days']}-day high",
            "trend": "close > SMA20 > SMA60 > SMA120",
            "relative_strength": f"60-day stock return - KOSPI return >= {config['relative_strength_min_pct_point']}%p",
            "volatility": "ATR14 < ATR50",
            "new_signal_only": config["only_new_signal"]
        }
    }


def updateTracking(indicators: pd.DataFrame, benchmark: pd.Series) -> list[dict]:
    if not CAPTURE_PATH.exists():
        return []

    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    history = indicators[["date", "ticker", "close", "sma60", "sma120"]].copy()
    rows = []

    for capture in captures.to_dict(orient="records"):
        ticker = capture["ticker"]
        capture_date = pd.Timestamp(capture["capture_date"])
        capture_price = float(capture["capture_close"])
        frame = history[(history["ticker"] == ticker) & (history["date"] >= capture_date)].sort_values("date")
        if frame.empty:
            continue

        latest = frame.iloc[-1]
        benchmark_frame = benchmark[benchmark.index >= capture_date]
        if benchmark_frame.empty:
            benchmark_return = None
        else:
            benchmark_return = (benchmark_frame.iloc[-1] / benchmark_frame.iloc[0] - 1) * 100

        current_return = (latest["close"] / capture_price - 1) * 100
        max_return = (frame["close"].max() / capture_price - 1) * 100
        max_drawdown = (frame["close"].min() / capture_price - 1) * 100
        rows.append({
            "capture_date": str(capture["capture_date"]), "ticker": ticker, "name": capture["name"], "capture_close": capture_price,
            "latest_date": latest["date"].strftime("%Y-%m-%d"), "latest_close": float(latest["close"]), "return_pct": current_return,
            "max_return_pct": max_return, "max_drawdown_pct": max_drawdown, "trading_days": int(len(frame) - 1),
            "close_above_sma60": bool(latest["close"] > latest["sma60"]) if pd.notna(latest["sma60"]) else None,
            "sma60_above_sma120": bool(latest["sma60"] > latest["sma120"]) if pd.notna(latest["sma60"]) and pd.notna(latest["sma120"]) else None,
            "benchmark_return_pct": benchmark_return,
            "excess_return_pct_point": current_return - benchmark_return if benchmark_return is not None else None
        })
    return rows


def saveJson(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)


def main() -> None:
    ensureDirectories()
    config = loadConfig()
    business_days = getBusinessDays(config["history_days"])

    for date in business_days:
        saveSnapshot(date, config["markets"])

    history = loadHistory(business_days)
    benchmark = fetchBenchmark(config, business_days)
    indicators = addIndicators(history, benchmark, config)
    result = buildResult(indicators, config)
    tracking = updateTracking(indicators, benchmark)

    date_path = RESULT_DIR / f"{result['scan_date']}.json"
    saveJson(date_path, result)
    saveJson(DATA_DIR / "latest.json", result)
    saveJson(DATA_DIR / "tracking.json", tracking)
    print(json.dumps({"scan_date": result["scan_date"], "signal_count": result["signal_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
