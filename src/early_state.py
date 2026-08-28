from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CONFIG_PATH = ROOT_DIR / "config.json"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history_signals.json"
CAPTURE_PATH = DATA_DIR / "captures.csv"
TRACKING_PATH = DATA_DIR / "tracking.json"
EARLY_REGISTRY_PATH = DATA_DIR / "early_registry.csv"
EARLY_TRACKING_PATH = DATA_DIR / "early_tracking.json"

REGISTRY_COLUMNS = ["ticker", "name", "market", "first_early_date", "first_early_close"]


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)


def load_config() -> dict:
    return load_json(CONFIG_PATH, {})


def load_registry() -> dict[str, dict]:
    registry = {}
    if not EARLY_REGISTRY_PATH.exists():
        return registry

    with EARLY_REGISTRY_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if not row.get("ticker"):
                continue
            row["first_early_close"] = float(row["first_early_close"])
            registry[row["ticker"]] = row
    return registry


def save_registry(registry: dict[str, dict]) -> None:
    with EARLY_REGISTRY_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=REGISTRY_COLUMNS)
        writer.writeheader()
        for ticker in sorted(registry):
            writer.writerow({column: registry[ticker].get(column) for column in REGISTRY_COLUMNS})


def add_early_event(registry: dict[str, dict], row: dict) -> None:
    ticker = str(row.get("ticker", ""))
    date = str(row.get("date", ""))
    close = row.get("close")
    if not ticker or not date or close is None:
        return

    existing = registry.get(ticker)
    if existing is not None and existing["first_early_date"] <= date:
        return

    registry[ticker] = {
        "ticker": ticker,
        "name": row.get("name", ""),
        "market": row.get("market", ""),
        "first_early_date": date,
        "first_early_close": float(close),
    }


def update_registry(history: dict | None = None, latest: dict | None = None) -> dict[str, dict]:
    registry = load_registry()
    history = history if history is not None else load_json(HISTORY_PATH, {})
    latest = latest if latest is not None else load_json(LATEST_PATH, {})

    for row in (history or {}).get("early_leader_events", []):
        add_early_event(registry, row)
    for row in (latest or {}).get("early_leaders", []):
        add_early_event(registry, row)

    save_registry(registry)
    return registry


def get_snapshot_dates() -> list[str]:
    return sorted(path.name.removesuffix(".csv.gz") for path in SNAPSHOT_DIR.glob("*.csv.gz"))


def trading_days_since(snapshot_dates: list[str], start_date: str, end_date: str) -> int:
    return sum(1 for date in snapshot_dates if start_date < date <= end_date)


def classify_state(gain_pct: float, rs_acceleration: float | None, config: dict) -> tuple[str, str]:
    state_config = config["early_state"]
    acceleration = rs_acceleration if rs_acceleration is not None else 0.0

    if gain_pct >= state_config["climax_gain_pct"]:
        return "CLIMAX_RISK", f"EARLY 이후 +{gain_pct:.1f}%"

    if gain_pct >= state_config["climax_acceleration_min_gain_pct"] and acceleration >= state_config["climax_rs_acceleration_min"]:
        return "CLIMAX_RISK", f"EARLY 이후 +{gain_pct:.1f}% / RS 가속 +{acceleration:.1f}"

    if gain_pct >= state_config["late_min_gain_pct"]:
        return "LATE", f"EARLY 이후 +{gain_pct:.1f}%"

    if gain_pct >= state_config["normal_min_gain_pct"]:
        return "NORMAL", f"EARLY 이후 +{gain_pct:.1f}%"

    return "FRESH", f"EARLY 이후 {gain_pct:+.1f}%"


def enrich_record(row: dict, registry: dict[str, dict], snapshot_dates: list[str], config: dict) -> dict:
    row = dict(row)
    ticker = str(row.get("ticker", ""))
    date = str(row.get("date", ""))
    close = row.get("close")
    anchor = registry.get(ticker)

    if anchor is None:
        row.update({
            "early_state": "NO_EARLY",
            "first_early_date": None,
            "first_early_close": None,
            "gain_since_early_pct": None,
            "trading_days_since_early": None,
            "early_state_reason": "EARLY 기록 없음",
        })
        return row

    first_date = anchor["first_early_date"]
    first_close = float(anchor["first_early_close"])
    if not date or date < first_date or close is None:
        row.update({
            "early_state": "PRE_EARLY",
            "first_early_date": first_date,
            "first_early_close": first_close,
            "gain_since_early_pct": None,
            "trading_days_since_early": None,
            "early_state_reason": "최초 EARLY 이전",
        })
        return row

    gain_pct = (float(close) / first_close - 1) * 100
    days_since = trading_days_since(snapshot_dates, first_date, date)
    rs_acceleration = row.get("rs_acceleration")
    state, reason = classify_state(gain_pct, rs_acceleration, config)
    row.update({
        "early_state": state,
        "first_early_date": first_date,
        "first_early_close": first_close,
        "gain_since_early_pct": gain_pct,
        "trading_days_since_early": days_since,
        "early_state_reason": f"{reason} / {days_since}거래일",
    })
    return row


def group_by_date(records: list[dict]) -> dict:
    grouped = {}
    for row in records:
        grouped.setdefault(row["date"], []).append(row)
    return grouped


def enrich_history_data(history: dict, registry: dict[str, dict] | None = None) -> dict:
    config = load_config()
    snapshot_dates = get_snapshot_dates()
    registry = registry if registry is not None else update_registry(history=history)
    history = dict(history)

    for key in ["early_leader_events", "leader_events", "setup_events", "signals"]:
        history[key] = [enrich_record(row, registry, snapshot_dates, config) for row in history.get(key, [])]

    if "signals" in history:
        history["by_date"] = group_by_date(history["signals"])
    return history


def enrich_history_file(path: Path = HISTORY_PATH) -> None:
    history = load_json(path, {})
    if not history:
        return
    registry = update_registry(history=history)
    save_json(path, enrich_history_data(history, registry))


def enrich_latest_data(latest: dict, registry: dict[str, dict]) -> dict:
    config = load_config()
    snapshot_dates = get_snapshot_dates()
    latest = dict(latest)
    for key in ["signals", "early_leaders", "leaders", "setups"]:
        latest[key] = [enrich_record(row, registry, snapshot_dates, config) for row in latest.get(key, [])]
    latest.setdefault("rules", {})["early_state"] = config["early_state"]
    return latest


def enrich_captures(latest: dict, history: dict) -> None:
    if not CAPTURE_PATH.exists():
        return

    with CAPTURE_PATH.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        return

    buy_map = {}
    for source in [history.get("signals", []), latest.get("signals", [])]:
        for signal in source:
            buy_map[(str(signal.get("date")), str(signal.get("ticker")))] = signal

    extra_columns = ["early_state", "first_early_date", "first_early_close", "gain_since_early_pct", "trading_days_since_early"]
    columns = list(rows[0].keys())
    for column in extra_columns:
        if column not in columns:
            columns.append(column)

    for row in rows:
        signal = buy_map.get((str(row.get("capture_date")), str(row.get("ticker"))))
        if signal is None:
            continue
        for column in extra_columns:
            value = signal.get(column)
            row[column] = "" if value is None else value

    with CAPTURE_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def enrich_tracking(registry: dict[str, dict]) -> None:
    tracking = load_json(TRACKING_PATH, [])
    if not tracking:
        return

    config = load_config()
    snapshot_dates = get_snapshot_dates()
    enriched = []
    for row in tracking:
        ticker = str(row.get("ticker", ""))
        anchor = registry.get(ticker)
        latest_date = row.get("latest_date")
        latest_close = row.get("latest_close")
        if anchor is None or latest_date is None or latest_close is None:
            row["latest_early_state"] = "NO_EARLY"
            row["latest_gain_since_early_pct"] = None
            enriched.append(row)
            continue

        first_date = anchor["first_early_date"]
        first_close = float(anchor["first_early_close"])
        gain_pct = (float(latest_close) / first_close - 1) * 100
        state, _ = classify_state(gain_pct, None, config)
        row["first_early_date"] = first_date
        row["first_early_close"] = first_close
        row["latest_gain_since_early_pct"] = gain_pct
        row["latest_trading_days_since_early"] = trading_days_since(snapshot_dates, first_date, latest_date)
        row["latest_early_state"] = state
        enriched.append(row)

    save_json(TRACKING_PATH, enriched)


def get_current_stage_map(latest: dict) -> dict[str, dict]:
    stage_map = {}
    stage_sources = [
        ("EARLY_LEADER", latest.get("early_leaders", [])),
        ("LEADER", latest.get("leaders", [])),
        ("SETUP", latest.get("setups", [])),
        ("BUY", latest.get("signals", [])),
    ]
    priority = {"EARLY_LEADER": 1, "LEADER": 2, "SETUP": 3, "BUY": 4}

    for stage, records in stage_sources:
        for row in records:
            ticker = str(row.get("ticker", ""))
            if not ticker:
                continue
            existing = stage_map.get(ticker)
            if existing is None or priority[stage] >= priority.get(existing.get("current_stage", ""), 0):
                stage_map[ticker] = {
                    "current_stage": stage,
                    "market_alignment": row.get("market_alignment"),
                    "rs_score": row.get("rs_score"),
                    "rs_acceleration": row.get("rs_acceleration"),
                    "mtt": row.get("mtt"),
                }
    return stage_map


def get_events_by_ticker(history: dict, key: str) -> dict[str, list[dict]]:
    grouped = {}
    for row in sorted(history.get(key, []), key=lambda item: str(item.get("date", ""))):
        ticker = str(row.get("ticker", ""))
        if ticker:
            grouped.setdefault(ticker, []).append(row)
    return grouped


def first_event_after(events: dict[str, list[dict]], ticker: str, first_date: str) -> dict | None:
    for row in events.get(ticker, []):
        if str(row.get("date", "")) >= first_date:
            return row
    return None


def load_snapshot_prices(tickers: set[str], first_dates: dict[str, str]) -> dict[str, list[tuple[str, float]]]:
    prices = {ticker: [] for ticker in tickers}
    if not tickers:
        return prices

    min_date = min(first_dates.values())
    for path in sorted(SNAPSHOT_DIR.glob("*.csv.gz")):
        date = path.name.removesuffix(".csv.gz")
        if date < min_date:
            continue
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                ticker = str(row.get("ticker", ""))
                if ticker not in tickers or date < first_dates[ticker]:
                    continue
                close = row.get("close")
                if close not in (None, ""):
                    prices[ticker].append((date, float(close)))
    return prices


def build_early_tracking(registry: dict[str, dict], latest: dict, history: dict) -> list[dict]:
    if not registry:
        return []

    config = load_config()
    previous = {str(row.get("ticker")): row for row in load_json(EARLY_TRACKING_PATH, []) or []}
    current_stage_map = get_current_stage_map(latest)
    leader_events = get_events_by_ticker(history, "leader_events")
    setup_events = get_events_by_ticker(history, "setup_events")
    buy_events = get_events_by_ticker(history, "signals")

    first_dates = {ticker: str(row["first_early_date"]) for ticker, row in registry.items()}
    prices = load_snapshot_prices(set(registry.keys()), first_dates)
    latest_scan_date = str(latest.get("scan_date") or "")
    rows = []

    for ticker, anchor in registry.items():
        series = prices.get(ticker, [])
        if not series:
            continue

        first_date = str(anchor["first_early_date"])
        first_close = float(anchor["first_early_close"])
        latest_date, latest_close = series[-1]
        max_close = max(close for _, close in series)
        min_close = min(close for _, close in series)
        gain_pct = (latest_close / first_close - 1) * 100
        max_gain_pct = (max_close / first_close - 1) * 100
        max_drawdown_pct = (min_close / first_close - 1) * 100

        current_info = current_stage_map.get(ticker, {})
        current_stage = current_info.get("current_stage", "INACTIVE")
        rs_acceleration = current_info.get("rs_acceleration")
        early_state, early_state_reason = classify_state(gain_pct, rs_acceleration, config)

        old = previous.get(ticker, {})
        first_leader = first_event_after(leader_events, ticker, first_date)
        first_setup = first_event_after(setup_events, ticker, first_date)
        first_buy = first_event_after(buy_events, ticker, first_date)

        old_leader_date = old.get("first_leader_date")
        old_setup_date = old.get("first_setup_date")
        old_buy_date = old.get("first_buy_date")
        if old_leader_date and old_leader_date < first_date:
            old_leader_date = None
        if old_setup_date and old_setup_date < first_date:
            old_setup_date = None
        if old_buy_date and old_buy_date < first_date:
            old_buy_date = None

        first_leader_date = old_leader_date or (first_leader or {}).get("date")
        first_setup_date = old_setup_date or (first_setup or {}).get("date")
        first_buy_date = old_buy_date or (first_buy or {}).get("date")
        first_buy_close = old.get("first_buy_close") if old_buy_date else None
        if first_buy_close is None:
            first_buy_close = (first_buy or {}).get("close")

        if latest_scan_date and current_stage == "LEADER" and not first_leader_date and latest_scan_date >= first_date:
            first_leader_date = latest_scan_date
        if latest_scan_date and current_stage == "SETUP" and not first_setup_date and latest_scan_date >= first_date:
            first_setup_date = latest_scan_date
        if latest_scan_date and current_stage == "BUY" and not first_buy_date and latest_scan_date >= first_date:
            first_buy_date = latest_scan_date
            first_buy_close = latest_close

        rows.append({
            "ticker": ticker,
            "name": anchor.get("name", ""),
            "market": anchor.get("market", ""),
            "first_early_date": first_date,
            "first_early_close": first_close,
            "latest_date": latest_date,
            "latest_close": latest_close,
            "return_since_early_pct": gain_pct,
            "max_return_since_early_pct": max_gain_pct,
            "max_drawdown_since_early_pct": max_drawdown_pct,
            "trading_days_since_early": max(0, len(series) - 1),
            "early_state": early_state,
            "early_state_reason": early_state_reason,
            "current_stage": current_stage,
            "market_alignment": current_info.get("market_alignment"),
            "rs_score": current_info.get("rs_score"),
            "rs_acceleration": rs_acceleration,
            "mtt": current_info.get("mtt"),
            "first_leader_date": first_leader_date,
            "first_setup_date": first_setup_date,
            "first_buy_date": first_buy_date,
            "first_buy_close": first_buy_close,
            "buy_promoted": bool(first_buy_date),
        })

    return sorted(rows, key=lambda row: (row["first_early_date"], row["ticker"]), reverse=True)


def main() -> None:
    latest = load_json(LATEST_PATH, {})
    history = load_json(HISTORY_PATH, {})
    registry = update_registry(history=history, latest=latest)

    if history:
        history = enrich_history_data(history, registry)
        save_json(HISTORY_PATH, history)

    if latest:
        latest = enrich_latest_data(latest, registry)
        save_json(LATEST_PATH, latest)
        scan_date = latest.get("scan_date")
        if scan_date:
            result_path = DATA_DIR / "results" / f"{scan_date}.json"
            if result_path.exists():
                save_json(result_path, latest)

    enrich_captures(latest, history)
    enrich_tracking(registry)
    early_tracking = build_early_tracking(registry, latest, history)
    save_json(EARLY_TRACKING_PATH, early_tracking)

    print(json.dumps({
        "early_registry_count": len(registry),
        "early_tracking_count": len(early_tracking),
        "latest_signal_count": len(latest.get("signals", [])) if latest else 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
