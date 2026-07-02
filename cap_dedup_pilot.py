#!/usr/bin/env python3
"""Small pilot for CAP -> CourtListener deduplication.

This is intentionally a bounded prototype:
- it accepts a small CAP-style JSON sample
- it accepts a small CourtListener-style JSON sample
- it tries dedup in the recommended order:
  1. citation string
  2. normalized case name + decision year

The goal is to validate the mechanics of the strategy on a tiny set before
trying anything larger.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a top-level JSON list")
    return [item for item in data if isinstance(item, dict)]


def normalize_case_name(name: str) -> str:
    value = name.lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\bv[.]?\b", " v ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def parse_year(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"(\d{4})", text)
    return match.group(1) if match else ""


def build_citation_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        citation = str(record.get("citation_string") or "").strip().lower()
        if citation:
            index[citation] = record
    return index


def build_name_year_index(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        case_name = normalize_case_name(str(record.get("case") or ""))
        year = parse_year(record.get("year"))
        if case_name and year:
            index[(case_name, year)] = record
    return index


def match_cap_record(
    cap_record: dict[str, Any],
    citation_index: dict[str, dict[str, Any]],
    name_year_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    cap_name = str(cap_record.get("name") or "")
    cap_year = parse_year(cap_record.get("decision_date"))

    for citation in cap_record.get("citations") or []:
        citation_text = str(citation).strip().lower()
        if citation_text and citation_text in citation_index:
            matched = citation_index[citation_text]
            return {
                "cap_id": cap_record.get("id"),
                "cap_name": cap_name,
                "cap_year": cap_year,
                "match_method": "citation_string",
                "matched_case": matched.get("case"),
                "matched_year": matched.get("year"),
                "matched_url": matched.get("url"),
                "matched_citation_string": matched.get("citation_string"),
            }

    name_key = (normalize_case_name(cap_name), cap_year)
    matched = name_year_index.get(name_key)
    if matched:
        return {
            "cap_id": cap_record.get("id"),
            "cap_name": cap_name,
            "cap_year": cap_year,
            "match_method": "case_name_plus_year",
            "matched_case": matched.get("case"),
            "matched_year": matched.get("year"),
            "matched_url": matched.get("url"),
            "matched_citation_string": matched.get("citation_string"),
        }

    return {
        "cap_id": cap_record.get("id"),
        "cap_name": cap_name,
        "cap_year": cap_year,
        "match_method": "unmatched",
        "matched_case": None,
        "matched_year": None,
        "matched_url": None,
        "matched_citation_string": None,
    }


def summarize(results: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for result in results:
        key = str(result["match_method"])
        counts[key] = counts.get(key, 0) + 1

    lines = ["Summary"]
    lines.append(f"Total CAP records tested : {len(results)}")
    for method in ("citation_string", "case_name_plus_year", "unmatched"):
        lines.append(f"{method:22}: {counts.get(method, 0)}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a small CAP dedup pilot against CourtListener.")
    parser.add_argument(
        "--cap-file",
        default="fixtures/cap_dedup_pilot/cap_sample.json",
        help="Path to CAP-style sample JSON",
    )
    parser.add_argument(
        "--courtlistener-file",
        default="fixtures/cap_dedup_pilot/courtlistener_sample.json",
        help="Path to CourtListener-style sample JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cap_records = load_json(Path(args.cap_file))
    courtlistener_records = load_json(Path(args.courtlistener_file))

    citation_index = build_citation_index(courtlistener_records)
    name_year_index = build_name_year_index(courtlistener_records)

    results = [
        match_cap_record(record, citation_index, name_year_index)
        for record in cap_records
    ]

    print(json.dumps(results, indent=2, ensure_ascii=False))
    print()
    print(summarize(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
