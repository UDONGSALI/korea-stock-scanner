from __future__ import annotations
import csv
import json
from pathlib import Path
import pandas as pd
from apply_buy_ranking import load_history, add_ranking_indicators, fetch_sector_classifications, build_day_universe, load_json, CONFIG_PATH, DATA_DIR

config = load_json(CONFIG_PATH, {})
history = add_ranking_indicators(load_history())
date = pd.Timestamp("2026-10-07")
sectors = fetch_sector_classifications(date, config["markets"])
day = build_day_universe(history, date, sectors, config)
rows = day[(day["sector"] == "반도체소부장") & day["sector_leader_rank"].notna()].copy()
rows = rows.sort_values(["sector_leader_rank", "ticker"])
columns = ["ticker", "sector_leader_rank", "sector_leader_score_raw", "return20_rank", "return60_rank", "distance_high52_rank", "sector", "krx_sector"]
output = DATA_DIR / "sector_rank_audit_20261007.csv"
rows[columns].to_csv(output, index=False, encoding="utf-8-sig")
target = rows[rows["ticker"] == "036930"]
print(json.dumps({"target": target[columns].to_dict("records"), "higher_count": int((rows["sector_leader_rank"] < 25).sum()), "top25": rows.head(25)[columns].to_dict("records")}, ensure_ascii=False))
