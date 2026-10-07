from __future__ import annotations

import json
import re
from pathlib import Path

import requests

OUTPUT = Path("data/wiseindex_sector_test.json")
TEST_DATE = "20261007"
TICKERS = {
    "446540": "메가터치",
    "031980": "피에스케이홀딩스",
    "095340": "ISC",
    "036930": "주성엔지니어링",
    "000660": "SK하이닉스",
    "005930": "삼성전자",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154.0 Safari/537.36",
    "Referer": "https://www.wiseindex.com/",
}

def request_json(url: str):
    response = requests.get(url, headers=HEADERS, timeout=20)
    result = {
        "url": url,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "text_prefix": response.text[:250],
    }
    try:
        payload = response.json()
    except Exception as exc:
        result["json_error"] = str(exc)
        return result, None
    result["json_keys"] = list(payload.keys()) if isinstance(payload, dict) else None
    return result, payload

def test_wiseindex():
    out = {}
    for sec_cd in ["G4510", "G4520", "G452010", "G452015", "G452020", "G452030", "G452040", "G4530", "G4535", "G453510", "G453520", "G4540", "G454010", "G454020", "G45"]:
        url = (
            "https://www.wiseindex.com/Index/GetIndexComponets"
            f"?ceil_yn=0&dt={TEST_DATE}&sec_cd={sec_cd}"
        )
        meta, payload = request_json(url)
        rows = payload.get("list", []) if isinstance(payload, dict) else []
        tickers = {str(row.get("CMP_CD", "")).zfill(6): row for row in rows}
        meta["row_count"] = len(rows)
        meta["matches"] = {
            ticker: {
                "name": name,
                "found": ticker in tickers,
                "row": tickers.get(ticker),
            }
            for ticker, name in TICKERS.items()
        }
        if rows:
            meta["sample_fields"] = list(rows[0].keys())
            meta["sample_rows"] = rows[:3]
        if isinstance(payload, dict):
            meta["sector_sample"] = payload.get("sector", [])[:10]
        out[sec_cd] = meta
    return out

def test_naver():
    out = {}
    for ticker, name in TICKERS.items():
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        response = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20)
        text = response.text
        # Naver 종목 페이지에는 업종/WICS 텍스트가 렌더링되거나 스크립트에 포함될 수 있다.
        snippets = []
        for pattern in [r"WICS.{0,160}", r"업종.{0,160}", r"반도체.{0,160}"]:
            snippets.extend(re.findall(pattern, text, flags=re.I | re.S)[:3])
        out[ticker] = {
            "name": name,
            "status": response.status_code,
            "encoding": response.encoding,
            "contains_wics": "WICS" in text.upper(),
            "contains_semiconductor": "반도체" in text,
            "snippets": [re.sub(r"\s+", " ", s)[:220] for s in snippets[:8]],
        }
    return out

def test_companyguide():
    out = {}
    for ticker, name in TICKERS.items():
        url = (
            "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp"
            f"?pGB=1&gicode=A{ticker}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"
        )
        response = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20)
        text = response.text
        snippets = []
        for pattern in [r"WICS.{0,180}", r"업종.{0,180}", r"반도체.{0,180}"]:
            snippets.extend(re.findall(pattern, text, flags=re.I | re.S)[:3])
        out[ticker] = {
            "name": name,
            "status": response.status_code,
            "contains_wics": "WICS" in text.upper(),
            "contains_semiconductor": "반도체" in text,
            "snippets": [re.sub(r"\s+", " ", s)[:240] for s in snippets[:8]],
        }
    return out

def main():
    result = {
        "test_date": TEST_DATE,
        "wiseindex": test_wiseindex(),
        "naver": test_naver(),
        "companyguide": test_companyguide(),
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
