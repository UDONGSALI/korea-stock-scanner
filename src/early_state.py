from __future__ import annotations

import csv
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

    return "FRESH", f"EARLY 이후 +{gain_pct:.1f}%"


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

    print(json.dumps({
        "early_registry_count": len(registry),
        "latest_signal_count": len(latest.get("signals", [])) if latest else 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
