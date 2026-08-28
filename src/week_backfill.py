from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"


def load_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def load_prices(tickers: set[str], from_date: str, to_date: str) -> dict[str, dict[str, float]]:
    prices = {ticker: {} for ticker in tickers}
    for path in sorted(SNAPSHOT_DIR.glob("*.csv.gz")):
        date = path.name.removesuffix(".csv.gz")
        if date < from_date or date > to_date:
            continue
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                ticker = str(row.get("ticker", ""))
                if ticker in prices:
                    prices[ticker][date] = float(row["close"])
    return prices


def export_buy(from_date: str, to_date: str, output_path: Path) -> int:
    history = load_json(DATA_DIR / "history_signals.json")
    signals = [row for row in history.get("signals", []) if from_date <= str(row.get("date", "")) <= to_date]
    prices = load_prices({str(row["ticker"]) for row in signals}, from_date, to_date)

    columns = [
        "포착일", "티커", "종목명", "시장상태", "BUY가", "최신일", "최신가", "수익률 %",
        "최대수익 %", "최대낙폭 %", "초과수익 %p", "RS", "MTT", "EARLY 상태",
        "최초 EARLY일", "최초 EARLY가", "EARLY후 상승률 %", "EARLY후 거래일", "룰 버전"
    ]
    rows = []
    for signal in sorted(signals, key=lambda row: (str(row["date"]), str(row["ticker"]))):
        ticker = str(signal["ticker"])
        capture_date = str(signal["date"])
        capture_close = float(signal["close"])
        series = [(date, close) for date, close in sorted(prices[ticker].items()) if date >= capture_date]
        if series:
            latest_date, latest_close = series[-1]
            max_close = max(close for _, close in series)
            min_close = min(close for _, close in series)
        else:
            latest_date, latest_close = capture_date, capture_close
            max_close = min_close = capture_close

        rows.append({
            "포착일": capture_date,
            "티커": ticker,
            "종목명": signal.get("name", ""),
            "시장상태": signal.get("market_alignment", ""),
            "BUY가": capture_close,
            "최신일": latest_date,
            "최신가": latest_close,
            "수익률 %": (latest_close / capture_close - 1) * 100,
            "최대수익 %": (max_close / capture_close - 1) * 100,
            "최대낙폭 %": (min_close / capture_close - 1) * 100,
            "초과수익 %p": "",
            "RS": signal.get("rs_score", ""),
            "MTT": signal.get("mtt", ""),
            "EARLY 상태": signal.get("early_state", ""),
            "최초 EARLY일": signal.get("first_early_date", ""),
            "최초 EARLY가": signal.get("first_early_close", ""),
            "EARLY후 상승률 %": signal.get("gain_since_early_pct", ""),
            "EARLY후 거래일": signal.get("trading_days_since_early", ""),
            "룰 버전": history.get("rule_version", ""),
        })

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_early(from_date: str, to_date: str, output_path: Path) -> int:
    tracking = load_json(DATA_DIR / "early_tracking.json")
    rows = [row for row in tracking if from_date <= str(row.get("first_early_date", "")) <= to_date]
    rows.sort(key=lambda row: (str(row.get("first_early_date", "")), str(row.get("ticker", ""))))

    columns = [
        "최초 EARLY일", "티커", "종목명", "시장", "최초 EARLY가", "최신일", "최신가",
        "EARLY후 수익률 %", "최대수익 %", "최대낙폭 %", "경과 거래일", "EARLY 상태",
        "현재 단계", "시장상태", "RS", "RS 가속", "MTT", "최초 LEADER일", "최초 SETUP일",
        "최초 BUY일", "최초 BUY가", "BUY 승격"
    ]
    mapping = {
        "최초 EARLY일": "first_early_date", "티커": "ticker", "종목명": "name", "시장": "market",
        "최초 EARLY가": "first_early_close", "최신일": "latest_date", "최신가": "latest_close",
        "EARLY후 수익률 %": "return_since_early_pct", "최대수익 %": "max_return_since_early_pct",
        "최대낙폭 %": "max_drawdown_since_early_pct", "경과 거래일": "trading_days_since_early",
        "EARLY 상태": "early_state", "현재 단계": "current_stage", "시장상태": "market_alignment",
        "RS": "rs_score", "RS 가속": "rs_acceleration", "MTT": "mtt",
        "최초 LEADER일": "first_leader_date", "최초 SETUP일": "first_setup_date",
        "최초 BUY일": "first_buy_date", "최초 BUY가": "first_buy_close", "BUY 승격": "buy_promoted"
    }

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(key, "") for column, key in mapping.items()})
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--prefix", default="week_backfill")
    args = parser.parse_args()

    buy_path = DATA_DIR / f"{args.prefix}_buy.csv"
    early_path = DATA_DIR / f"{args.prefix}_early.csv"
    buy_count = export_buy(args.from_date, args.to_date, buy_path)
    early_count = export_early(args.from_date, args.to_date, early_path)
    print(json.dumps({"buy_count": buy_count, "early_count": early_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
