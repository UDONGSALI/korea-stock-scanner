from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
INPUT_PATH = DATA_DIR / "early_tracking.json"
OUTPUT_PATH = DATA_DIR / "early_tracking.csv"

COLUMNS = [
    ("first_early_date", "최초 EARLY일"),
    ("ticker", "티커"),
    ("name", "종목명"),
    ("market", "시장"),
    ("first_early_close", "최초 EARLY가"),
    ("latest_date", "최신일"),
    ("latest_close", "최신가"),
    ("return_since_early_pct", "EARLY후 수익률 %"),
    ("max_return_since_early_pct", "최대수익 %"),
    ("max_drawdown_since_early_pct", "최대낙폭 %"),
    ("trading_days_since_early", "경과 거래일"),
    ("early_state", "EARLY 상태"),
    ("current_stage", "현재 단계"),
    ("market_alignment", "시장상태"),
    ("rs_score", "RS"),
    ("rs_acceleration", "RS 가속"),
    ("mtt", "MTT"),
    ("first_leader_date", "최초 LEADER일"),
    ("first_setup_date", "최초 SETUP일"),
    ("first_buy_date", "최초 BUY일"),
    ("first_buy_close", "최초 BUY가"),
    ("buy_promoted", "BUY 승격"),
]


def main() -> None:
    if not INPUT_PATH.exists():
        raise RuntimeError("data/early_tracking.json 파일이 없습니다.")

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
