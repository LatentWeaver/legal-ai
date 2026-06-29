#!/usr/bin/env python3
"""Validate canonical citation JSON files for the Legal AI pipeline."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


CASE_KEYS = {"case", "year", "url", "precedents"}
PRECEDENT_KEYS = {"case", "url"}


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_https_url(value: Any) -> bool:
    return is_non_empty_string(value) and value.startswith("https://")


def is_valid_case(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if set(entry.keys()) != CASE_KEYS:
        return False
    if not is_non_empty_string(entry["case"]):
        return False
    if not isinstance(entry["year"], str) or not re.fullmatch(r"\d{4}", entry["year"]):
        return False
    if not is_https_url(entry["url"]):
        return False
    if not isinstance(entry["precedents"], list):
        return False

    for precedent in entry["precedents"]:
        if not isinstance(precedent, dict):
            return False
        if set(precedent.keys()) != PRECEDENT_KEYS:
            return False
        if not is_non_empty_string(precedent["case"]):
            return False
        if not is_https_url(precedent["url"]):
            return False
    return True


def validate(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read valid JSON: {exc}", file=sys.stderr)
        print("Total cases: 0")
        print("Malformed:   1  (100.00%)")
        return 1

    if not isinstance(data, list):
        print("Top-level JSON value must be a list.", file=sys.stderr)
        print("Total cases: 0")
        print("Malformed:   1  (100.00%)")
        return 1

    total = len(data)
    malformed = sum(1 for entry in data if not is_valid_case(entry))
    percent = (malformed / total * 100) if total else 0.0

    print(f"Total cases: {total}")
    print(f"Malformed:   {malformed}  ({percent:.2f}%)")
    return 1 if percent > 5.0 else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a citations_formatted.json file.")
    parser.add_argument("--file", required=True, help="Path to citations_formatted.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return validate(Path(args.file))


if __name__ == "__main__":
    raise SystemExit(main())
