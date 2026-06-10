#!/usr/bin/env python3
"""Convert the 6001-6750 master-citation data to the team's standardized JSON.

Anshul's integration schema (one object per master case):

    {
      "case": "<master case title>",
      "year": "<judgment year>",
      "url":  "https://indiankanoon.org/doc/<tid>/",
      "precedents": [ {"case": "<cited case name>", "url": "<doc url>"}, ... ]
    }

`precedents` = the *Cites* (outbound) direction only — this schema has no
`cited_by` field, so the inbound direction we collected is intentionally not
emitted here (it stays in output/master_citations_6001_6750.json + the DB).

Sources (all read-only):
  output/master_citations_cases.db   -> cases.title + citation_search (cites)
  land_property_dispute_cases.csv    -> per-master year (and name fallback)

Run from project root:
    python3 indiankanoon/to_standard_format.py
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
DB = OUTPUT / "master_citations_cases.db"
CSV = ROOT / "land_property_dispute_cases.csv"
OUT = OUTPUT / "master_citations_6001_6750_standard.json"
START, END = 6001, 6750  # 1-indexed inclusive bracket


def extract_tid(link: str) -> int | None:
    m = re.search(r"/doc/(\d+)/", link or "")
    return int(m.group(1)) if m else None


def clean_name(raw: str) -> str:
    """'Nanjappa_vs_State_..._on_24_July_2015' -> readable text."""
    return re.sub(r"\s+", " ", (raw or "").replace("_", " ")).strip()


_IK_DATE = re.compile(r"\s+on\s+\d{1,2}\s+\w+,?\s*\d{4}\s*$")


def strip_ik_date(name: str) -> str:
    """Drop Indian Kanoon's trailing ' on 24 July, 2015' so `case` is just the name
    (the year lives in its own field). Leaves names without that suffix untouched."""
    return _IK_DATE.sub("", name or "").strip()


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")
    if not CSV.exists():
        raise SystemExit(f"CSV not found: {CSV}")

    with CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    bracket = rows[START - 1 : END]  # 1-indexed inclusive

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    records: list[dict] = []
    skipped_no_tid = 0
    missing_in_db = 0

    for row in bracket:
        tid = extract_tid(row.get("link", ""))
        if tid is None:
            skipped_no_tid += 1
            continue

        title_row = conn.execute(
            "SELECT title FROM cases WHERE tid = ?", (tid,)
        ).fetchone()
        if title_row is None:
            missing_in_db += 1
        raw_title = (
            title_row[0]
            if title_row and title_row[0]
            else clean_name(row.get("case", ""))
        )
        title = strip_ik_date(raw_title)

        precedents = [
            {"case": ref_name or "", "url": f"https://indiankanoon.org/doc/{ref_tid}/"}
            for ref_tid, ref_name in conn.execute(
                "SELECT ref_tid, ref_name FROM citation_search "
                "WHERE case_tid = ? AND direction = 'cites' ORDER BY ref_tid",
                (tid,),
            )
        ]

        records.append({
            "case": title,
            "year": str(row.get("year", "") or "").strip(),
            "url": f"https://indiankanoon.org/doc/{tid}/",
            "precedents": precedents,
        })

    conn.close()

    OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    edges = sum(len(r["precedents"]) for r in records)
    with_year = sum(1 for r in records if r["year"])
    print(f"  records          : {len(records)}")
    print(f"  precedent edges  : {edges}")
    print(f"  records w/ year  : {with_year}/{len(records)}")
    print(f"  skipped (no tid) : {skipped_no_tid}")
    print(f"  master not in DB : {missing_in_db}")
    print(f"  wrote            : {OUT}")


if __name__ == "__main__":
    main()
