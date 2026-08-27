from __future__ import annotations

import json
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
    start_date = end_date - timedelta(days=max(history_days * 2, 520))
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


def fetchMarketIndices(config: dict, business_days: list[pd.Timestamp]) -> dict[str, pd.DataFrame]:
    from_date = business_days[0].strftime("%Y%m%d")
    to_date = business_days[-1].strftime("%Y%m%d")
    indices = {}

    for market, index_code in config["market_indices"].items():
        frame = stock.get_index_ohlcv(from_date, to_date, index_code)
        if frame.empty:
            raise RuntimeError(f"{market} 지수 데이터를 가져오지 못했습니다.")
        frame = frame[["종가"]].rename(columns={"종가": "close"}).astype(float)
        frame.index = pd.to_datetime(frame.index)
        indices[market] = frame

    return indices


def buildMarketMaps(config: dict, market_indices: dict[str, pd.DataFrame]) -> tuple[dict, dict]:
    market_ok_map = {}
    benchmark_return_maps = {period: {} for period in config["rs_periods"]}

    for market, frame in market_indices.items():
        close = frame["close"]
        ma_days = config["market_ma_days"][market]
        market_ok_map[market] = close > close.rolling(ma_days).mean()

        for period_text in config["rs_periods"]:
            period = int(period_text)
            benchmark_return_maps[period_text][market] = close.pct_change(period)

    return market_ok_map, benchmark_return_maps


def addIndicators(history: pd.DataFrame, market_indices: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    market_ok_map, benchmark_return_maps = buildMarketMaps(config, market_indices)
    output = []

    for _, frame in history.groupby("ticker", sort=False):
        frame = frame.copy().sort_values("date")
        market = str(frame["market"].iloc[-1])
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        close = frame["close"].astype(float)
        volume = frame["volume"].astype(float)

        frame["sma50"] = close.rolling(50).mean()
        frame["sma150"] = close.rolling(150).mean()
        frame["sma200"] = close.rolling(200).mean()
        frame["sma200_rising"] = frame["sma200"] > frame["sma200"].shift(config["mtt"]["sma200_rising_lookback"])
        frame["high52"] = high.rolling(config["high52_days"]).max()
        frame["low52"] = low.rolling(config["high52_days"]).min()
        frame["prev_high52"] = high.shift(1).rolling(config["high52_days"]).max()
        frame["distance_from_high52_pct"] = (close / frame["high52"] - 1) * 100
        frame["rise_from_low52_pct"] = (close / frame["low52"] - 1) * 100

        base_days = config["base"]["days"]
        frame["base_high"] = high.shift(1).rolling(base_days).max()
        frame["base_low"] = low.shift(1).rolling(base_days).min()
        frame["base_depth_pct"] = (frame["base_high"] / frame["base_low"] - 1) * 100
        frame["avg_volume20"] = volume.shift(1).rolling(20).mean()
        frame["avg_volume5_base"] = volume.shift(1).rolling(5).mean()
        frame["volume_ratio"] = volume / frame["avg_volume20"]
        frame["base_volume_contracted"] = frame["avg_volume5_base"] <= frame["avg_volume20"]
        frame["breakout"] = close > frame["base_high"]
        frame["weekly_trend_proxy"] = (close > frame["sma50"]) & (frame["sma50"] > frame["sma150"])
        frame["market_ok"] = frame["date"].map(market_ok_map[market]).fillna(False).astype(bool)

        rs_raw = pd.Series(0.0, index=frame.index)
        rs_valid = pd.Series(True, index=frame.index)
        for period_text, weight in config["rs_periods"].items():
            period = int(period_text)
            stock_return = close.pct_change(period)
            benchmark_return = frame["date"].map(benchmark_return_maps[period_text][market])
            relative_return = stock_return - benchmark_return
            frame[f"rs_{period}d_relative"] = relative_return
            rs_raw = rs_raw + relative_return.fillna(0) * float(weight)
            rs_valid &= relative_return.notna()

        frame["rs_raw"] = rs_raw.where(rs_valid)
        output.append(frame)

    indicators = pd.concat(output, ignore_index=True)
    indicators["rs_score"] = indicators.groupby("date")["rs_raw"].rank(pct=True, method="average") * 99

    mtt = config["mtt"]
    indicators["mtt"] = (
        (indicators["close"] > indicators["sma150"])
        & (indicators["close"] > indicators["sma200"])
        & (indicators["sma150"] > indicators["sma200"])
        & indicators["sma200_rising"]
        & (indicators["sma50"] > indicators["sma150"])
        & (indicators["sma50"] > indicators["sma200"])
        & (indicators["close"] > indicators["sma50"])
        & (indicators["close"] >= indicators["low52"] * (1 + mtt["min_above_52w_low_pct"] / 100))
        & (indicators["close"] >= indicators["high52"] * (1 - mtt["max_below_52w_high_pct"] / 100))
    )

    base = config["base"]
    indicators["stock_setup"] = (
        (indicators["rs_score"] >= config["rs_min_score"])
        & indicators["mtt"]
        & indicators["weekly_trend_proxy"]
        & (indicators["base_depth_pct"] <= base["max_depth_pct"])
        & indicators["base_volume_contracted"]
        & indicators["breakout"]
        & (indicators["volume_ratio"] >= config["breakout_volume_ratio_min"])
    )
    indicators["technical_signal"] = indicators["stock_setup"] & indicators["market_ok"]
    return indicators


def fetchMarketCaps(date: pd.Timestamp, markets: list[str]) -> pd.DataFrame:
    date_text = date.strftime("%Y%m%d")
    frames = []
    for market in markets:
        frame = stock.get_market_cap(date_text, market=market)
        if frame.empty:
            continue
        frame = frame.reset_index().rename(columns={"티커": "ticker", "시가총액": "market_cap"})
        frame["market"] = market
        frames.append(frame[["ticker", "market", "market_cap"]])
        time.sleep(0.15)
    if not frames:
        return pd.DataFrame(columns=["ticker", "market", "market_cap"])
    return pd.concat(frames, ignore_index=True)


def finalizeSignals(indicators: pd.DataFrame, config: dict, dates: list[pd.Timestamp]) -> pd.DataFrame:
    target_dates = sorted(pd.to_datetime(pd.Series(dates).drop_duplicates()).tolist())
    selected = indicators[indicators["date"].isin(target_dates)].copy()
    selected["market_cap"] = np.nan

    for date in target_dates:
        day_mask = selected["date"] == date
        if not selected.loc[day_mask, "stock_setup"].any():
            continue
        caps = fetchMarketCaps(date, config["markets"])
        if caps.empty:
            continue
        cap_map = caps.set_index(["ticker", "market"])["market_cap"]
        candidate_index = selected.index[day_mask & selected["stock_setup"]]
        for row_index in candidate_index:
            key = (str(selected.at[row_index, "ticker"]), str(selected.at[row_index, "market"]))
            selected.at[row_index, "market_cap"] = cap_map.get(key, np.nan)

    selected["eligible_setup"] = selected["stock_setup"] & (selected["market_cap"] >= config["min_market_cap"])
    selected["signal"] = selected["eligible_setup"] & selected["market_ok"]
    selected["countertrend_candidate"] = selected["eligible_setup"] & ~selected["market_ok"]
    selected = selected.sort_values(["ticker", "date"])
    selected["new_signal"] = selected["signal"] & ~selected.groupby("ticker")["signal"].shift(1, fill_value=False)
    selected["new_countertrend_candidate"] = selected["countertrend_candidate"] & ~selected.groupby("ticker")["countertrend_candidate"].shift(1, fill_value=False)
    return selected.sort_values(["date", "market", "ticker"]).reset_index(drop=True)


def calculateRiskFields(selected: pd.DataFrame, config: dict) -> pd.DataFrame:
    selected = selected.copy()
    selected["stop_price"] = selected["base_low"]
    selected["risk_per_share"] = selected["close"] - selected["stop_price"]
    selected["stop_distance_pct"] = selected["risk_per_share"] / selected["close"] * 100
    selected["three_r_target"] = selected["close"] + selected["risk_per_share"] * config["risk"]["reward_multiple"]
    selected["max_position_pct_for_risk"] = np.where(selected["stop_distance_pct"] > 0, np.minimum(100.0, config["risk"]["max_account_risk_pct"] / selected["stop_distance_pct"] * 100), np.nan)
    return selected


def cleanRecord(row: dict) -> dict:
    cleaned = {}
    for key, value in row.items():
        if isinstance(value, np.integer):
            value = int(value)
        elif isinstance(value, np.floating):
            value = None if np.isnan(value) else float(value)
        elif isinstance(value, pd.Timestamp):
            value = value.strftime("%Y-%m-%d")
        cleaned[key] = value
    return cleaned


def buildCandidateRecords(selected: pd.DataFrame, config: dict, signal_type: str) -> list[dict]:
    if selected.empty:
        return []

    selected = calculateRiskFields(selected, config)
    ticker_names = {ticker: stock.get_market_ticker_name(ticker) for ticker in selected["ticker"].unique()}
    selected["name"] = selected["ticker"].map(ticker_names)
    selected["signal_type"] = signal_type
    columns = ["ticker", "name", "market", "signal_type", "market_ok", "close", "market_cap", "rs_score", "sma50", "sma150", "sma200", "high52", "low52", "base_high", "base_low", "base_depth_pct", "volume", "avg_volume20", "volume_ratio", "stop_price", "stop_distance_pct", "three_r_target", "max_position_pct_for_risk"]
    if signal_type == "COUNTERTREND":
        selected["is_new"] = selected["new_countertrend_candidate"]
        columns.insert(4, "is_new")
    return [cleanRecord(row) for row in selected[columns].to_dict(orient="records")]


def buildResult(finalized: pd.DataFrame, config: dict) -> dict:
    target_date = finalized["date"].max()
    latest = finalized[finalized["date"] == target_date].copy()
    buy_records = buildCandidateRecords(latest[latest["new_signal"]].copy(), config, "BUY")
    countertrend_records = buildCandidateRecords(latest[latest["countertrend_candidate"]].copy(), config, "COUNTERTREND")

    market_status = {}
    for market in config["markets"]:
        market_rows = latest[latest["market"] == market]
        market_status[market] = bool(market_rows["market_ok"].iloc[0]) if not market_rows.empty else None

    return {
        "rule_version": config["rule_version"],
        "scan_date": target_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "market_status": market_status,
        "signal_count": len(buy_records),
        "signals": buy_records,
        "countertrend_count": len(countertrend_records),
        "countertrend_candidates": countertrend_records,
        "rules": {
            "market_filter": config["market_ma_days"],
            "min_market_cap": config["min_market_cap"],
            "rs_min_score": config["rs_min_score"],
            "rs_periods": config["rs_periods"],
            "trend": "MTT + weekly trend proxy (SMA50 > SMA150)",
            "base": config["base"],
            "breakout_volume_ratio_min": config["breakout_volume_ratio_min"],
            "risk": config["risk"]
        },
        "manual_checks": ["주도 섹터/섹터 RS", "인더스트리 액션", "기관 수급", "EPS 성장 및 실적"]
    }


def appendCaptures(result: dict) -> None:
    if not result["signals"]:
        return
    if CAPTURE_PATH.exists():
        captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    else:
        captures = pd.DataFrame(columns=["capture_date", "ticker", "name", "capture_close", "source", "rule_version"])
    rows = []
    existing = set(zip(captures.get("capture_date", pd.Series(dtype=str)).astype(str), captures.get("ticker", pd.Series(dtype=str)).astype(str)))
    for signal in result["signals"]:
        key = (result["scan_date"], signal["ticker"])
        if key not in existing:
            rows.append({"capture_date": result["scan_date"], "ticker": signal["ticker"], "name": signal["name"], "capture_close": signal["close"], "source": "kangto_core", "rule_version": result["rule_version"]})
    if rows:
        captures = pd.concat([captures, pd.DataFrame(rows)], ignore_index=True)
        captures.to_csv(CAPTURE_PATH, index=False, encoding="utf-8-sig")


def updateTracking(indicators: pd.DataFrame, market_indices: dict[str, pd.DataFrame]) -> list[dict]:
    if not CAPTURE_PATH.exists():
        return []
    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    if captures.empty:
        return []
    history = indicators[["date", "ticker", "market", "close", "sma50", "sma150", "sma200", "rs_score", "mtt", "market_ok"]].copy()
    rows = []
    for capture in captures.to_dict(orient="records"):
        ticker = capture["ticker"]
        capture_date = pd.Timestamp(capture["capture_date"])
        capture_price = float(capture["capture_close"])
        frame = history[(history["ticker"] == ticker) & (history["date"] >= capture_date)].sort_values("date")
        if frame.empty:
            continue
        latest = frame.iloc[-1]
        market = str(latest["market"])
        benchmark = market_indices[market]["close"]
        benchmark_frame = benchmark[benchmark.index >= capture_date]
        benchmark_return = None if benchmark_frame.empty else (benchmark_frame.iloc[-1] / benchmark_frame.iloc[0] - 1) * 100
        current_return = (latest["close"] / capture_price - 1) * 100
        rows.append({"capture_date": str(capture["capture_date"]), "ticker": ticker, "name": capture["name"], "capture_close": capture_price, "rule_version": capture.get("rule_version", "unknown"), "latest_date": latest["date"].strftime("%Y-%m-%d"), "latest_close": float(latest["close"]), "return_pct": current_return, "max_return_pct": (frame["close"].max() / capture_price - 1) * 100, "max_drawdown_pct": (frame["close"].min() / capture_price - 1) * 100, "trading_days": int(len(frame) - 1), "market_ok": bool(latest["market_ok"]), "mtt": bool(latest["mtt"]), "rs_score": None if pd.isna(latest["rs_score"]) else float(latest["rs_score"]), "benchmark_return_pct": benchmark_return, "excess_return_pct_point": current_return - benchmark_return if benchmark_return is not None else None})
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
    market_indices = fetchMarketIndices(config, business_days)
    indicators = addIndicators(history, market_indices, config)
    finalized = finalizeSignals(indicators, config, business_days[-2:])
    result = buildResult(finalized, config)
    appendCaptures(result)
    tracking = updateTracking(indicators, market_indices)
    saveJson(RESULT_DIR / f"{result['scan_date']}.json", result)
    saveJson(DATA_DIR / "latest.json", result)
    saveJson(DATA_DIR / "tracking.json", tracking)
    print(json.dumps({"rule_version": result["rule_version"], "scan_date": result["scan_date"], "signal_count": result["signal_count"], "countertrend_count": result["countertrend_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
