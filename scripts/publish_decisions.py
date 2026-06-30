"""
Convert the cumulative decision-records JSONL into a JSON array for the dashboard.

Reads data/decisions/decisions.jsonl and writes the last N records to
dashboard/public/decisions.json so the deployed dashboard can fetch them
client-side. No database needed.

Usage: python scripts/publish_decisions.py [max_records]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "decisions", "decisions.jsonl")
DST = os.path.join(ROOT, "dashboard", "public", "decisions.json")


def main() -> int:
    max_records = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    if not os.path.exists(SRC):
        print(f"No decisions file at {SRC}; nothing to publish.")
        return 0
    with open(SRC, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()][-max_records:]
    records = []
    for ln in lines:
        try:
            records.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)
    print(f"Published {len(records)} decision record(s) to {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
