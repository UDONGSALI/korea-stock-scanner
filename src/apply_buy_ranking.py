from __future__ import annotations

import csv
import gzip
import json
import math
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
TRACKING_PATH = DATA_DIR / "tracking.json"
LATEST_PATH = DATA_DIR / "latest.json"
PRIORITY_OUTPUT_PATH = DATA_DIR / "buy_priority.csv"

RANKING_FIELDS = [
    "buy_grade",
    "buy_score",
    "priority_selected",
    "sector",
    "sector_score",
    "sector_leader_rank",
    "sector_member_count",
    "weekly_state",
    "atr20_pct",
    "institutional_fit",
    "avg_trading_value20",
    "capture_rs_score",
    "ranking_reason",
    "ranking_rule_version",
]


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)


def is_number(value) -> bool:
    try:
        return value is not None and value != "" and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def load_history() -> pd.DataFrame:
    frames = []
    for path in sorted(SNAPSHOT_DIR.glob("*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as file:
            frame = pd.read_csv(file, dtype={"ticker": str})
        if not frame.empty:
            frames.append(frame)

    if not frames:
        raise RuntimeError("data/snapshots 가격 데이터가 없습니다.")

    history = pd.concat(frames, ignore_index=True)
    history["ticker"] = history["ticker"].astype(str).str.zfill(6)
    history["date"] = pd.to_datetime(history["date"])
    return history.sort_values(["ticker", "date"]).reset_index(drop=True)


def add_ranking_indicators(history: pd.DataFrame) -> pd.DataFrame:
    output = []
    for _, frame in history.groupby("ticker", sort=False):
        frame = frame.copy().sort_values("date")
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        close = frame["close"].astype(float)
        prev_close = close.shift(1)

        true_range = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        frame["return20_rank"] = close.pct_change(20) * 100
        frame["return60_rank"] = close.pct_change(60) * 100
        frame["sma50_rank"] = close.rolling(50).mean()
        frame["high52_rank"] = high.rolling(252).max()
        frame["distance_high52_rank"] = (close / frame["high52_rank"] - 1) * 100
        frame["atr20_pct_rank"] = true_range.rolling(20).mean() / close * 100
        frame["avg_trading_value20_rank"] = frame["trading_value"].astype(float).rolling(20).mean()
        output.append(frame)

    return pd.concat(output, ignore_index=True).sort_values(["ticker", "date"]).reset_index(drop=True)


def fetch_sector_classifications(date: pd.Timestamp, markets: list[str]) -> pd.DataFrame:
    date_text = date.strftime("%Y%m%d")
    frames = []

    for market in markets:
        try:
            frame = stock.get_market_sector_classifications(date_text, market)
        except Exception as exc:  # 외부 KRX 응답 실패 시 전체 작업을 중단하지 않는다.
            print(json.dumps({"warning": "sector_fetch_failed", "date": date_text, "market": market, "error": str(exc)}, ensure_ascii=False))
            continue

        if frame is None or frame.empty:
            continue

        frame = frame.reset_index()
        ticker_column = "종목코드" if "종목코드" in frame.columns else frame.columns[0]
        frame = frame.rename(columns={ticker_column: "ticker", "업종명": "sector", "시가총액": "market_cap_sector"})
        if "sector" not in frame.columns:
            continue
        if "market_cap_sector" not in frame.columns:
            frame["market_cap_sector"] = np.nan
        frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
        frame["market"] = market
        frames.append(frame[["ticker", "market", "sector", "market_cap_sector"]])

    if not frames:
        return pd.DataFrame(columns=["ticker", "market", "sector", "market_cap_sector"])

    return pd.concat(frames, ignore_index=True).drop_duplicates(["ticker", "market"], keep="last")


def load_capture_signal_map(capture_date: str) -> dict[str, dict]:
    result_path = RESULT_DIR / f"{capture_date}.json"
    result = load_json(result_path, {}) or {}
    return {str(row.get("ticker", "")).zfill(6): row for row in result.get("signals", [])}


def get_weekly_state(ticker_history: pd.DataFrame, capture_date: pd.Timestamp) -> tuple[str, float]:
    frame = ticker_history[ticker_history["date"] <= capture_date].copy()
    if frame.empty:
        return "UNKNOWN", 50.0

    weekly = frame.set_index("date")["close"].astype(float).resample("W-FRI").last().dropna()
    ma10 = weekly.rolling(10).mean()
    ma30 = weekly.rolling(30).mean()
    if len(weekly) < 30 or pd.isna(ma10.iloc[-1]) or pd.isna(ma30.iloc[-1]):
        return "UNKNOWN", 50.0

    close = float(weekly.iloc[-1])
    ma10_now = float(ma10.iloc[-1])
    ma30_now = float(ma30.iloc[-1])
    ma10_rising = len(ma10.dropna()) >= 5 and ma10_now > float(ma10.dropna().iloc[-5])

    if close > ma10_now > ma30_now and ma10_rising:
        return "STRONG", 100.0
    if close > ma10_now > ma30_now:
        return "GOOD", 80.0
    if close > ma10_now:
        return "RECOVERING", 60.0
    return "WEAK", 25.0


def build_day_universe(history: pd.DataFrame, capture_date: pd.Timestamp, sector_frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    day = history[history["date"] == capture_date].copy()
    if day.empty:
        return day

    day = day.merge(sector_frame, on=["ticker", "market"], how="left")
    day["sector"] = day["sector"].fillna("UNKNOWN")
    day["market_cap_sector"] = pd.to_numeric(day["market_cap_sector"], errors="coerce")

    min_market_cap = float(config["min_market_cap"])
    eligible = day[(day["market_cap_sector"].fillna(0) >= min_market_cap) & day["return20_rank"].notna() & day["return60_rank"].notna()].copy()
    if eligible.empty:
        eligible = day[day["return20_rank"].notna() & day["return60_rank"].notna()].copy()

    if eligible.empty:
        return day

    sector_base = eligible[eligible["sector"] != "UNKNOWN"].copy()
    if not sector_base.empty:
        sector_stats = sector_base.groupby("sector").agg(
            sector_member_count=("ticker", "count"),
            sector_return20=("return20_rank", "median"),
            sector_return60=("return60_rank", "median"),
            sector_breadth=("sma50_rank", lambda value: 0.0),
        ).reset_index()

        breadth = sector_base.assign(above_sma50=sector_base["close"].astype(float) > sector_base["sma50_rank"].astype(float)).groupby("sector")["above_sma50"].mean()
        sector_stats["sector_breadth"] = sector_stats["sector"].map(breadth).astype(float)

        valid_sector_mask = sector_stats["sector_member_count"] >= int(config["ranking"].get("min_sector_members", 3))
        valid_stats = sector_stats[valid_sector_mask].copy()
        if not valid_stats.empty:
            valid_stats["ret20_score"] = valid_stats["sector_return20"].rank(pct=True) * 100
            valid_stats["ret60_score"] = valid_stats["sector_return60"].rank(pct=True) * 100
            valid_stats["breadth_score"] = valid_stats["sector_breadth"].rank(pct=True) * 100
            valid_stats["sector_score"] = (
                valid_stats["ret20_score"] * 0.45
                + valid_stats["ret60_score"] * 0.35
                + valid_stats["breadth_score"] * 0.20
            )
            sector_stats = sector_stats.merge(valid_stats[["sector", "sector_score"]], on="sector", how="left")
        else:
            sector_stats["sector_score"] = np.nan

        day = day.merge(sector_stats[["sector", "sector_member_count", "sector_score"]], on="sector", how="left")
    else:
        day["sector_member_count"] = 0
        day["sector_score"] = np.nan

    eligible = day[day["return20_rank"].notna() & day["return60_rank"].notna()].copy()
    eligible["high_proximity_score"] = np.clip(100 + eligible["distance_high52_rank"].fillna(-25) * 4, 0, 100)
    eligible["sector_ret20_rank"] = eligible.groupby("sector")["return20_rank"].rank(pct=True) * 100
    eligible["sector_ret60_rank"] = eligible.groupby("sector")["return60_rank"].rank(pct=True) * 100
    eligible["sector_leader_score_raw"] = (
        eligible["sector_ret20_rank"] * 0.45
        + eligible["sector_ret60_rank"] * 0.35
        + eligible["high_proximity_score"] * 0.20
    )
    eligible["sector_leader_rank"] = eligible.groupby("sector")["sector_leader_score_raw"].rank(method="min", ascending=False)

    eligible["volatility_score"] = eligible["atr20_pct_rank"].rank(pct=True, ascending=False) * 100
    eligible["cap_score"] = eligible["market_cap_sector"].rank(pct=True) * 100
    eligible["liquidity_score"] = eligible["avg_trading_value20_rank"].rank(pct=True) * 100
    eligible["institutional_score"] = eligible["cap_score"].fillna(50) * 0.50 + eligible["liquidity_score"].fillna(50) * 0.50

    merge_columns = [
        "ticker",
        "market",
        "high_proximity_score",
        "sector_leader_score_raw",
        "sector_leader_rank",
        "volatility_score",
        "institutional_score",
    ]
    day = day.drop(columns=[column for column in merge_columns[2:] if column in day.columns], errors="ignore")
    return day.merge(eligible[merge_columns], on=["ticker", "market"], how="left")


def get_grade(score: float, sector_score: float | None, leader_rank: float | None, weekly_score: float, sector: str, config: dict) -> str:
    ranking_config = config["ranking"]
    if sector == "UNKNOWN" or not is_number(sector_score) or not is_number(leader_rank):
        return "B" if score >= float(ranking_config["grade_b_min"]) else "C"

    if (
        score >= float(ranking_config["grade_s_min"])
        and float(sector_score) >= float(ranking_config["s_min_sector_score"])
        and float(leader_rank) <= float(ranking_config["s_max_sector_rank"])
        and weekly_score >= float(ranking_config["s_min_weekly_score"])
    ):
        return "S"

    if (
        score >= float(ranking_config["grade_a_min"])
        and float(sector_score) >= float(ranking_config["a_min_sector_score"])
        and float(leader_rank) <= float(ranking_config["a_max_sector_rank"])
        and weekly_score >= float(ranking_config["a_min_weekly_score"])
    ):
        return "A"

    if score >= float(ranking_config["grade_b_min"]):
        return "B"
    return "C"


def score_capture(capture: dict, capture_date: pd.Timestamp, day: pd.DataFrame, history: pd.DataFrame, signal_map: dict[str, dict], config: dict) -> dict:
    ticker = str(capture["ticker"]).zfill(6)
    row_frame = day[day["ticker"] == ticker]
    row = row_frame.iloc[-1] if not row_frame.empty else pd.Series(dtype=object)
    signal = signal_map.get(ticker, {})

    sector = str(row.get("sector", "UNKNOWN") or "UNKNOWN")
    sector_score = float(row["sector_score"]) if is_number(row.get("sector_score")) else None
    leader_rank = float(row["sector_leader_rank"]) if is_number(row.get("sector_leader_rank")) else None
    leader_raw = float(row["sector_leader_score_raw"]) if is_number(row.get("sector_leader_score_raw")) else 50.0
    capture_rs_score = float(signal["rs_score"]) if is_number(signal.get("rs_score")) else 50.0
    leader_score = leader_raw * 0.70 + capture_rs_score * 0.30

    ticker_history = history[history["ticker"] == ticker]
    weekly_state, weekly_score = get_weekly_state(ticker_history, capture_date)

    atr20_pct = float(row["atr20_pct_rank"]) if is_number(row.get("atr20_pct_rank")) else None
    volatility_score = float(row["volatility_score"]) if is_number(row.get("volatility_score")) else 50.0
    institutional_score = float(row["institutional_score"]) if is_number(row.get("institutional_score")) else 50.0
    high_proximity_score = float(row["high_proximity_score"]) if is_number(row.get("high_proximity_score")) else 50.0
    avg_trading_value20 = float(row["avg_trading_value20_rank"]) if is_number(row.get("avg_trading_value20_rank")) else None
    market_cap = float(row["market_cap_sector"]) if is_number(row.get("market_cap_sector")) else float(signal.get("market_cap") or 0)

    ranking_config = config["ranking"]
    institutional_fit = bool(
        market_cap >= float(ranking_config["institutional_min_market_cap"])
        and (avg_trading_value20 or 0) >= float(ranking_config["institutional_min_avg_trading_value"])
    )

    market_alignment = str(capture.get("market_alignment") or signal.get("market_alignment") or "COUNTERTREND")
    market_score = 100.0 if market_alignment == "WITH_MARKET" else 35.0
    sector_component = sector_score if sector_score is not None else 50.0

    score = (
        sector_component * 0.25
        + leader_score * 0.25
        + weekly_score * 0.15
        + volatility_score * 0.10
        + institutional_score * 0.10
        + high_proximity_score * 0.10
        + market_score * 0.05
    )
    score = round(float(score), 2)
    grade = get_grade(score, sector_score, leader_rank, weekly_score, sector, config)

    rank_text = "?" if leader_rank is None else str(int(leader_rank))
    sector_text = "?" if sector_score is None else f"{sector_score:.0f}"
    atr_text = "?" if atr20_pct is None else f"{atr20_pct:.1f}%"
    reason = f"섹터 {sector_text} / 대장 {rank_text}위 / 주봉 {weekly_state} / ATR {atr_text}"

    return {
        "buy_grade": grade,
        "buy_score": score,
        "priority_selected": False,
        "sector": sector,
        "sector_score": sector_score,
        "sector_leader_rank": leader_rank,
        "sector_member_count": int(row.get("sector_member_count") or 0) if is_number(row.get("sector_member_count")) else 0,
        "weekly_state": weekly_state,
        "atr20_pct": atr20_pct,
        "institutional_fit": institutional_fit,
        "avg_trading_value20": avg_trading_value20,
        "capture_rs_score": capture_rs_score,
        "ranking_reason": reason,
        "ranking_rule_version": ranking_config["rule_version"],
    }


def select_priority(capture_rows: list[tuple[int, dict]], config: dict) -> None:
    ranking_config = config["ranking"]
    max_daily_candidates = int(ranking_config.get("max_daily_candidates", 2))
    max_per_sector = int(ranking_config.get("max_per_sector", 1))

    grade_order = {"S": 4, "A": 3, "B": 2, "C": 1}
    candidates = [item for item in capture_rows if item[1].get("buy_grade") in {"S", "A"}]
    candidates.sort(key=lambda item: (grade_order.get(item[1].get("buy_grade"), 0), float(item[1].get("buy_score") or 0)), reverse=True)

    selected_count = 0
    sector_count: dict[str, int] = {}
    for _, ranking in candidates:
        if selected_count >= max_daily_candidates:
            break
        sector = str(ranking.get("sector") or "UNKNOWN")
        if sector_count.get(sector, 0) >= max_per_sector:
            continue
        ranking["priority_selected"] = True
        sector_count[sector] = sector_count.get(sector, 0) + 1
        selected_count += 1


def update_result_files(captures: pd.DataFrame) -> None:
    ranking_map = {}
    for row in captures.to_dict(orient="records"):
        key = (str(row.get("capture_date")), str(row.get("ticker")).zfill(6))
        ranking_map[key] = {field: row.get(field) for field in RANKING_FIELDS}

    for path in list(RESULT_DIR.glob("*.json")) + ([LATEST_PATH] if LATEST_PATH.exists() else []):
        result = load_json(path, {}) or {}
        scan_date = str(result.get("scan_date", ""))
        signals = result.get("signals", [])
        changed = False
        for signal in signals:
            key = (scan_date, str(signal.get("ticker", "")).zfill(6))
            ranking = ranking_map.get(key)
            if not ranking:
                continue
            signal.update(ranking)
            changed = True

        if changed:
            result["priority_signals"] = sorted(
                [row for row in signals if row.get("priority_selected")],
                key=lambda row: float(row.get("buy_score") or 0),
                reverse=True,
            )
            save_json(path, result)


def update_tracking(captures: pd.DataFrame) -> None:
    tracking = load_json(TRACKING_PATH, []) or []
    if not tracking:
        return

    capture_map = {}
    for row in captures.to_dict(orient="records"):
        key = (str(row.get("capture_date")), str(row.get("ticker")).zfill(6))
        capture_map[key] = row

    for row in tracking:
        key = (str(row.get("capture_date")), str(row.get("ticker")).zfill(6))
        capture = capture_map.get(key)
        if not capture:
            continue
        for field in RANKING_FIELDS:
            value = capture.get(field)
            if isinstance(value, float) and math.isnan(value):
                value = None
            row[field] = value

    save_json(TRACKING_PATH, tracking)


def export_priority_csv(captures: pd.DataFrame) -> None:
    if captures.empty:
        return

    latest_date = captures["capture_date"].astype(str).max()
    rows = captures[captures["capture_date"].astype(str) == latest_date].copy()
    grade_order = {"S": 4, "A": 3, "B": 2, "C": 1}
    rows["grade_order"] = rows["buy_grade"].map(grade_order).fillna(0)
    rows = rows.sort_values(["priority_selected", "grade_order", "buy_score"], ascending=[False, False, False])

    columns = [
        ("capture_date", "포착일"),
        ("priority_selected", "매수검토"),
        ("buy_grade", "매수등급"),
        ("buy_score", "매수점수"),
        ("name", "종목명"),
        ("ticker", "티커"),
        ("sector", "섹터"),
        ("sector_score", "섹터점수"),
        ("sector_leader_rank", "섹터내순위"),
        ("weekly_state", "주봉상태"),
        ("atr20_pct", "ATR20 %"),
        ("institutional_fit", "기관적합"),
        ("capture_rs_score", "포착RS"),
        ("market_alignment", "시장상태"),
        ("ranking_reason", "등급근거"),
    ]

    with PRIORITY_OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[label for _, label in columns])
        writer.writeheader()
        for row in rows.to_dict(orient="records"):
            writer.writerow({label: row.get(key) for key, label in columns})


def main() -> None:
    config = load_json(CONFIG_PATH, {}) or {}
    if not CAPTURE_PATH.exists():
        print(json.dumps({"rows": 0, "reason": "no_captures"}, ensure_ascii=False))
        return

    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    if captures.empty:
        print(json.dumps({"rows": 0, "reason": "no_captures"}, ensure_ascii=False))
        return

    captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
    history = add_ranking_indicators(load_history())
    ranking_by_index: dict[int, dict] = {}

    for capture_date_text, date_rows in captures.groupby(captures["capture_date"].astype(str), sort=True):
        capture_date = pd.Timestamp(capture_date_text)
        sector_frame = fetch_sector_classifications(capture_date, config["markets"])
        day = build_day_universe(history, capture_date, sector_frame, config)
        signal_map = load_capture_signal_map(capture_date_text)
        scored_rows = []

        for index, capture in date_rows.iterrows():
            ranking = score_capture(capture.to_dict(), capture_date, day, history, signal_map, config)
            ranking_by_index[index] = ranking
            scored_rows.append((index, ranking))

        select_priority(scored_rows, config)

    for field in RANKING_FIELDS:
        captures[field] = None

    for index, ranking in ranking_by_index.items():
        for field, value in ranking.items():
            captures.at[index, field] = value

    captures.to_csv(CAPTURE_PATH, index=False, encoding="utf-8-sig")
    update_tracking(captures)
    update_result_files(captures)
    export_priority_csv(captures)

    latest_date = captures["capture_date"].astype(str).max()
    latest_rows = captures[captures["capture_date"].astype(str) == latest_date]
    print(json.dumps({
        "rows": len(captures),
        "latest_date": latest_date,
        "latest_signals": len(latest_rows),
        "priority_selected": int(latest_rows["priority_selected"].fillna(False).astype(bool).sum()),
        "grade_counts": latest_rows["buy_grade"].value_counts(dropna=False).to_dict(),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
