from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
LATEST_PATH = DATA_DIR / "latest.json"
OUTPUT_PATH = DATA_DIR / "strategic_sector_map.csv"
OVERRIDE_PATH = ROOT_DIR / "strategic_sector_overrides.csv"

WISEINDEX_URL = "https://www.wiseindex.com/Index/GetIndexComponets"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154.0 Safari/537.36",
    "Referer": "https://www.wiseindex.com/",
}

# 투자 판단용으로 너무 잘게 쪼개지 않은 단순 전략 섹터.
# WI26은 기업의 공식 산업분류 뼈대로 사용하고, 시장에서 다르게 묶이는 종목은 override CSV로 보정한다.
WI26_SECTORS = {
    "WI100": ("에너지", "에너지"),
    "WI110": ("화학", "소재"),
    "WI200": ("비철금속", "소재"),
    "WI210": ("철강", "소재"),
    "WI220": ("건설", "건설"),
    "WI230": ("기계", "산업재"),
    "WI240": ("조선", "조선"),
    "WI250": ("상사,자본재", "산업재"),
    "WI260": ("운송", "운송"),
    "WI300": ("자동차", "자동차"),
    "WI310": ("화장품,의류", "소비재"),
    "WI320": ("호텔,레저", "소비재"),
    "WI330": ("미디어,교육", "미디어"),
    "WI340": ("소매(유통)", "소비재"),
    "WI400": ("필수소비재", "소비재"),
    "WI410": ("건강관리", "제약바이오"),
    "WI500": ("은행", "금융"),
    "WI510": ("증권", "금융"),
    "WI520": ("보험", "금융"),
    "WI600": ("소프트웨어", "IT서비스"),
    "WI610": ("IT하드웨어", "IT하드웨어"),
    # 여기서 '반도체소부장'은 엄밀한 산업통계 명칭보다 매매용 비교군 명칭이다.
    "WI620": ("반도체", "반도체소부장"),
    "WI630": ("IT가전", "IT가전"),
    "WI640": ("디스플레이", "디스플레이"),
    "WI700": ("통신서비스", "통신"),
    "WI800": ("유틸리티", "유틸리티"),
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def target_date_text() -> str:
    latest = load_json(LATEST_PATH, {}) or {}
    scan_date = str(latest.get("scan_date") or "").replace("-", "")
    if len(scan_date) == 8 and scan_date.isdigit():
        return scan_date
    return datetime.now().strftime("%Y%m%d")


def fetch_components(date_text: str, sector_code: str) -> list[dict]:
    response = requests.get(
        WISEINDEX_URL,
        params={"ceil_yn": "0", "dt": date_text, "sec_cd": sector_code},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("list", []) if isinstance(payload, dict) else []


def resolve_available_date(target: str) -> str:
    target_date = datetime.strptime(target, "%Y%m%d")
    # 장 마감 전/휴일에도 안전하게 최근 실제 데이터 날짜로 후퇴한다.
    for offset in range(0, 12):
        candidate = (target_date - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            rows = fetch_components(candidate, "WI620")
        except Exception:
            continue
        if rows:
            return candidate
    raise RuntimeError(f"WiseIndex WI26 데이터를 찾지 못했습니다. target={target}")


def load_overrides() -> dict[str, dict]:
    if not OVERRIDE_PATH.exists():
        return {}

    output: dict[str, dict] = {}
    with OVERRIDE_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            ticker = str(row.get("ticker") or "").strip().zfill(6)
            sector = str(row.get("strategic_sector") or "").strip()
            if not ticker or ticker == "000000" or not sector:
                continue
            output[ticker] = {
                "name": str(row.get("name") or "").strip(),
                "strategic_sector": sector,
                "note": str(row.get("note") or "").strip(),
            }
    return output


def build_map(date_text: str) -> dict[str, dict]:
    mapped: dict[str, dict] = {}

    for wi26_code, (wi26_name, strategic_sector) in WI26_SECTORS.items():
        rows = fetch_components(date_text, wi26_code)
        for row in rows:
            ticker = str(row.get("CMP_CD") or "").strip().zfill(6)
            if not ticker or ticker == "000000":
                continue

            mapped[ticker] = {
                "ticker": ticker,
                "name": str(row.get("CMP_KOR") or "").strip(),
                "wi26_code": wi26_code,
                "wi26_name": wi26_name,
                "strategic_sector": strategic_sector,
                "source": "WI26",
                "note": "",
            }

    for ticker, override in load_overrides().items():
        current = mapped.get(ticker, {
            "ticker": ticker,
            "name": override.get("name", ""),
            "wi26_code": "",
            "wi26_name": "",
        })
        if override.get("name"):
            current["name"] = override["name"]
        current["strategic_sector"] = override["strategic_sector"]
        current["source"] = "MANUAL_OVERRIDE"
        current["note"] = override.get("note", "")
        mapped[ticker] = current

    return mapped


def write_map(rows: dict[str, dict], date_text: str) -> None:
    fieldnames = [
        "ticker",
        "name",
        "strategic_sector",
        "wi26_code",
        "wi26_name",
        "source",
        "note",
        "classification_date",
    ]
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for ticker in sorted(rows):
            row = dict(rows[ticker])
            row["classification_date"] = date_text
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main() -> None:
    target = target_date_text()
    try:
        available_date = resolve_available_date(target)
        mapped = build_map(available_date)
        if len(mapped) < 500:
            raise RuntimeError(f"전략 섹터 매핑 종목 수가 비정상적으로 적습니다: {len(mapped)}")
        write_map(mapped, available_date)
        print(json.dumps({
            "target_date": target,
            "classification_date": available_date,
            "mapped_count": len(mapped),
            "override_count": len(load_overrides()),
            "output": str(OUTPUT_PATH),
        }, ensure_ascii=False))
    except Exception as exc:
        # 외부 소스가 잠깐 실패해도 기존 정상 매핑이 있으면 일일 스캔을 막지 않는다.
        if OUTPUT_PATH.exists() and OUTPUT_PATH.stat().st_size > 100:
            print(json.dumps({
                "warning": "strategic_sector_refresh_failed_using_existing_map",
                "error": str(exc),
                "output": str(OUTPUT_PATH),
            }, ensure_ascii=False))
            return
        raise


if __name__ == "__main__":
    main()
