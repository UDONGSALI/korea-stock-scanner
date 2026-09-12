from __future__ import annotations

import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SOURCE_PATH = DATA_DIR / "compare_2026-08-28_vs_2026-09-04.json"
OUTPUT_PATH = DATA_DIR / "compare_2026-08-28_vs_2026-09-04_summary.json"

NUMERIC_FIELDS = [
    "rs_score",
    "rs_20_score",
    "rs_60_score",
    "rs_acceleration",
    "sector_score",
    "sector_leader_rank",
    "distance_from_high52_pct",
    "base_days",
    "base_depth_pct",
    "distance_to_base_high_pct",
    "volume_ratio",
    "atr20_pct",
    "gain_since_early_pct",
]


def pick_numeric(numeric: dict) -> dict:
    return {
        field: {
            "mean": numeric.get(field, {}).get("mean"),
            "median": numeric.get(field, {}).get("median"),
        }
        for field in NUMERIC_FIELDS
    }


def main() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8-sig"))
    compact = {"comparison": {}, "combined_winner_loser": {}}

    for date_text, cohort in source["comparison"].items():
        compact["comparison"][date_text] = {
            "count": cohort["count"],
            "performance": cohort["performance"],
            "numeric": pick_numeric(cohort["numeric"]),
            "thresholds": cohort["thresholds"],
            "market": cohort["categorical"]["market"],
            "buy_grade": cohort["categorical"]["buy_grade"],
            "weekly_state": cohort["categorical"]["weekly_state"],
            "early_state": cohort["categorical"]["early_state"],
            "sector": cohort["categorical"]["sector"],
            "mtt": cohort["categorical"]["mtt"],
            "institutional_fit": cohort["categorical"]["institutional_fit"],
        }

    for group_name, group in source["combined_winner_loser"].items():
        compact["combined_winner_loser"][group_name] = {
            "count": group["count"],
            "numeric": {
                field: {
                    "mean": stats.get("mean"),
                    "median": stats.get("median"),
                }
                for field, stats in group["numeric"].items()
            },
            "weekly_state": group["weekly_state"],
            "early_state": group["early_state"],
        }

    OUTPUT_PATH.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(compact, ensure_ascii=False))


if __name__ == "__main__":
    main()
