from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
INPUT_PATH = DATA_DIR / "tracking.json"
OUTPUT_PATH = DATA_DIR / "buy_tracking.csv"

# 매일 확인하는 포지션 관리 정보 → 성과 → 종결 → 보조지표 → 이력/기술정보 → 매수등급 순서로 배치한다.
# 기존 앞쪽 컬럼 순서는 유지해 포트폴리오 시뮬레이션의 참조 위치가 바뀌지 않도록 한다.
COLUMNS = [
    ("name", "종목명"),
    ("ticker", "티커"),
    ("trade_status", "매매상태"),
    ("remaining_position_pct", "잔여비중 %"),
    ("latest_close", "최신가"),
    ("capture_close", "BUY가"),
    ("strategy_return_pct", "전략수익률 %"),
    ("current_stop_price", "현재손절가"),
    ("three_r_target", "3R 목표가"),
    ("trailing_basis", "트레일링 기준"),
    ("capture_date", "포착일"),
    ("latest_date", "최신일"),
    ("return_pct", "수익률 %"),
    ("max_return_pct", "최대수익 %"),
    ("max_drawdown_pct", "최대낙폭 %"),
    ("partial_exit_date", "3R 달성일"),
    ("partial_exit_price", "1차매도가"),
    ("exit_date", "종결일"),
    ("exit_price", "종결가"),
    ("exit_reason", "종결사유"),
    ("realized_return_pct", "실현수익률 %"),
    ("rs_score", "RS"),
    ("mtt", "MTT"),
    ("capture_market_alignment", "시장상태"),
    ("latest_early_state", "EARLY 상태"),
    ("first_early_date", "최초 EARLY일"),
    ("first_early_close", "최초 EARLY가"),
    ("latest_gain_since_early_pct", "EARLY후 상승률 %"),
    ("latest_trading_days_since_early", "EARLY후 거래일"),
    ("initial_stop_price", "최초손절가"),
    ("excess_return_pct_point", "초과수익 %p"),
    ("priority_selected", "매수검토"),
    ("buy_grade", "매수등급"),
    ("buy_score", "매수점수"),
    ("sector", "섹터"),
    ("sector_score", "섹터점수"),
    ("sector_leader_rank", "섹터내순위"),
    ("weekly_state", "주봉상태"),
    ("atr20_pct", "ATR20 %"),
    ("institutional_fit", "기관적합"),
    ("capture_rs_score", "포착RS"),
    ("ranking_reason", "등급근거"),
    ("ranking_rule_version", "등급룰 버전"),
    ("rule_version", "룰 버전"),
    ("exit_rule_version", "매도룰 버전"),
]


def main() -> None:
    if not INPUT_PATH.exists():
        raise RuntimeError("data/tracking.json 파일이 없습니다.")

    with INPUT_PATH.open("r", encoding="utf-8-sig") as file:
        rows = json.load(file)

    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[label for _, label in COLUMNS])
        writer.writeheader()
        for row in rows:
            writer.writerow({label: row.get(key) for key, label in COLUMNS})

    print(json.dumps({"rows": len(rows), "output": str(OUTPUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
