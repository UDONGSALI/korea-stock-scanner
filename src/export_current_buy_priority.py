from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from apply_buy_ranking import (
    PRIORITY_OUTPUT_PATH,
    add_ranking_indicators,
    build_day_universe,
    fetch_sector_classifications,
    load_history,
    load_json,
    score_capture,
    select_priority,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CONFIG_PATH = ROOT_DIR / "config.json"
CAPTURE_PATH = DATA_DIR / "captures.csv"
TRACKING_PATH = DATA_DIR / "tracking.json"


def to_float(value, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        number = float(value)
        return number if pd.notna(number) else default
    except (TypeError, ValueError):
        return default


def get_priority_label(value) -> str:
    return "우선검토" if bool(value) else "대기"


def load_active_candidates(captures: pd.DataFrame, tracking: list[dict]) -> list[tuple[dict, dict]]:
    capture_map = {
        (str(row.get("capture_date")), str(row.get("ticker", "")).zfill(6)): row
        for row in captures.to_dict(orient="records")
    }

    active_rows = []
    for row in tracking:
        if str(row.get("trade_status")) not in {"OPEN", "PARTIAL"}:
            continue

        key = (str(row.get("capture_date")), str(row.get("ticker", "")).zfill(6))
        capture = capture_map.get(key)
        if capture:
            active_rows.append((capture, row))

    # 같은 종목이 여러 번 포착된 경우 신규 진입 판단에서는 가장 최근 포착만 사용한다.
    latest_by_ticker: dict[str, tuple[dict, dict]] = {}
    for capture, tracking_row in active_rows:
        ticker = str(capture.get("ticker", "")).zfill(6)
        current = latest_by_ticker.get(ticker)
        if current is None or str(capture.get("capture_date")) > str(current[0].get("capture_date")):
            latest_by_ticker[ticker] = (capture, tracking_row)

    return list(latest_by_ticker.values())


def build_current_priority_rows(config: dict, captures: pd.DataFrame, tracking: list[dict]) -> tuple[pd.Timestamp, list[dict]]:
    history = add_ranking_indicators(load_history())
    evaluation_date = pd.Timestamp(history["date"].max())
    sector_frame = fetch_sector_classifications(evaluation_date, config["markets"])
    day = build_day_universe(history, evaluation_date, sector_frame, config)

    active_candidates = load_active_candidates(captures, tracking)
    scored_rows: list[tuple[int, dict]] = []
    output_rows: list[dict] = []
    max_chase_pct = float(config["ranking"].get("current_max_chase_pct", 12.0))

    for index, (capture, tracking_row) in enumerate(active_candidates):
        ticker = str(capture.get("ticker", "")).zfill(6)
        capture_for_score = dict(capture)
        market_alignment = "WITH_MARKET" if bool(tracking_row.get("market_ok")) else "COUNTERTREND"
        capture_for_score["market_alignment"] = market_alignment

        current_rs = to_float(tracking_row.get("rs_score"), 50.0)
        signal_map = {ticker: {"rs_score": current_rs}}
        ranking = score_capture(capture_for_score, evaluation_date, day, history, signal_map, config)

        current_return_pct = to_float(tracking_row.get("return_pct"))
        if current_return_pct is None:
            latest_close = to_float(tracking_row.get("latest_close"), 0.0) or 0.0
            capture_close = to_float(capture.get("capture_close"), 0.0) or 0.0
            current_return_pct = (latest_close / capture_close - 1) * 100 if capture_close > 0 else 0.0

        current_entry_status = "추격주의" if current_return_pct > max_chase_pct else "검토가능"
        ranking.update({
            "evaluation_date": evaluation_date.strftime("%Y-%m-%d"),
            "capture_date": str(capture.get("capture_date")),
            "name": str(capture.get("name", "")),
            "ticker": ticker,
            "current_return_pct": current_return_pct,
            "trading_days": int(tracking_row.get("trading_days") or 0),
            "current_entry_status": current_entry_status,
            "market_alignment": market_alignment,
        })

        output_rows.append(ranking)

        # S/A라도 이미 BUY가에서 너무 멀리 올라간 종목은 신규 매수 검토 대상에서 제외한다.
        if current_entry_status == "검토가능":
            scored_rows.append((index, ranking))

    select_priority(scored_rows, config)
    return evaluation_date, output_rows


def export_priority_csv(rows: list[dict]) -> None:
    grade_order = {"S": 4, "A": 3, "B": 2, "C": 1}
    rows = sorted(
        rows,
        key=lambda row: (
            bool(row.get("priority_selected")),
            grade_order.get(str(row.get("buy_grade")), 0),
            float(row.get("buy_score") or 0),
        ),
        reverse=True,
    )

    columns = [
        ("evaluation_date", "평가기준일"),
        ("capture_date", "포착일"),
        ("priority_selected", "매수검토"),
        ("buy_grade", "매수품질"),
        ("buy_score", "종합점수"),
        ("name", "종목명"),
        ("ticker", "티커"),
        ("current_return_pct", "현재수익률 %"),
        ("trading_days", "경과거래일"),
        ("current_entry_status", "현재진입상태"),
        ("sector", "섹터"),
        ("sector_score", "섹터점수"),
        ("sector_leader_rank", "섹터내순위"),
        ("weekly_state", "주봉상태"),
        ("atr20_pct", "ATR20 %"),
        ("institutional_fit", "기관적합"),
        ("capture_rs_score", "현재RS"),
        ("market_alignment", "시장상태"),
        ("ranking_reason", "등급근거"),
    ]

    with PRIORITY_OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[label for _, label in columns])
        writer.writeheader()
        for row in rows:
            output_row = {}
            for key, label in columns:
                value = row.get(key)
                if key == "priority_selected":
                    value = get_priority_label(value)
                output_row[label] = value
            writer.writerow(output_row)


def main() -> None:
    config = load_json(CONFIG_PATH, {}) or {}
    if not CAPTURE_PATH.exists():
        raise RuntimeError("data/captures.csv 파일이 없습니다.")

    captures = pd.read_csv(CAPTURE_PATH, dtype={"ticker": str})
    captures["ticker"] = captures["ticker"].astype(str).str.zfill(6)
    tracking = load_json(TRACKING_PATH, []) or []

    evaluation_date, rows = build_current_priority_rows(config, captures, tracking)
    export_priority_csv(rows)

    grade_counts: dict[str, int] = {}
    for row in rows:
        grade = str(row.get("buy_grade") or "")
        grade_counts[grade] = grade_counts.get(grade, 0) + 1

    print(json.dumps({
        "evaluation_date": evaluation_date.strftime("%Y-%m-%d"),
        "active_candidates": len(rows),
        "priority_selected": sum(1 for row in rows if row.get("priority_selected")),
        "grade_counts": grade_counts,
        "output": str(PRIORITY_OUTPUT_PATH),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
