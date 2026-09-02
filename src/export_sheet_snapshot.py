from __future__ import annotations

import json
import shutil
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
LATEST_PATH = DATA_DIR / "latest.json"
BUY_SOURCE_PATH = DATA_DIR / "buy_tracking.csv"
EARLY_SOURCE_PATH = DATA_DIR / "early_tracking.csv"
BUY_OUTPUT_PATH = DATA_DIR / "sheet_buy_tracking.csv"
EARLY_OUTPUT_PATH = DATA_DIR / "sheet_early_tracking.csv"
SEOUL_TZ = ZoneInfo("Asia/Seoul")
EOD_CONFIRM_TIME = time(15, 35)


def isConfirmedEodSnapshot(latest: dict) -> bool:
    scan_date = date.fromisoformat(str(latest["scan_date"]))
    generated_at = datetime.fromisoformat(str(latest["generated_at"]))

    if generated_at.tzinfo is None:
        return False

    generated_at_kst = generated_at.astimezone(SEOUL_TZ)
    return generated_at_kst.date() == scan_date and generated_at_kst.time() >= EOD_CONFIRM_TIME


def main() -> None:
    if not LATEST_PATH.exists():
        raise RuntimeError("data/latest.json 파일이 없습니다.")

    with LATEST_PATH.open("r", encoding="utf-8-sig") as file:
        latest = json.load(file)

    if not isConfirmedEodSnapshot(latest):
        print(json.dumps({"updated": False, "reason": "not_confirmed_eod", "scan_date": latest.get("scan_date"), "generated_at": latest.get("generated_at")}, ensure_ascii=False))
        return

    for source_path in (BUY_SOURCE_PATH, EARLY_SOURCE_PATH):
        if not source_path.exists():
            raise RuntimeError(f"{source_path.relative_to(ROOT_DIR)} 파일이 없습니다.")

    shutil.copyfile(BUY_SOURCE_PATH, BUY_OUTPUT_PATH)
    shutil.copyfile(EARLY_SOURCE_PATH, EARLY_OUTPUT_PATH)

    print(json.dumps({"updated": True, "scan_date": latest.get("scan_date"), "generated_at": latest.get("generated_at"), "buy_output": str(BUY_OUTPUT_PATH), "early_output": str(EARLY_OUTPUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
