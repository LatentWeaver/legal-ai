#!/usr/bin/env python3
"""Run a bounded live CAP -> CourtListener dedup test.

This script is intentionally practical rather than exhaustive:
- it loads the local CourtListener med-mal export already in this repo
- it fetches real CAP cases from static.case.law
- it scans a bounded number of cases from modern reporters
- it filters for med-mal-like language using a lightweight text heuristic
- it attempts dedup using the fallback available in the local export:
  normalized case name + decision year

The local CourtListener export does not expose citation strings, so this live
check measures the fallback match strategy on real data. Citation-string
matching can be layered in later if CourtListener citation fields are added.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_COURTLISTENER_DIR = "citations-data-us-medical-malpractice"
DEFAULT_REPORTERS = ("ne2d", "so2d", "p3d", "sw3d")
DEFAULT_OUTPUT = "cap_dedup_live_summary.json"
DEFAULT_SCAN_LIMIT = 1000
DEFAULT_VOLUMES_PER_REPORTER = 2

MEDICAL_TERMS = (
    "doctor",
    "physician",
    "surgeon",
    "hospital",
    "medical",
    "clinic",
    "dentist",
    "nursing home",
    "emergency room",
)

LIABILITY_TERMS = (
    "malpractice",
    "negligence",
    "standard of care",
    "informed consent",
    "wrongful death",
    "failure to diagnose",
    "misdiagnosis",
)

ROLE_LABEL_PATTERN = re.compile(
    r"\b("
    r"appellant|appellants|appellee|appellees|defendant|defendants|plaintiff|plaintiffs|"
    r"petitioner|petitioners|respondent|respondents|cross appellant|cross appellants|"
    r"cross appellee|cross appellees|third party|third parties"
    r")\b",
    re.IGNORECASE,
)


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "asu-davulcu-lab-legal-ai-stage1/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "asu-davulcu-lab-legal-ai-stage1/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def normalize_case_name(name: str) -> str:
    value = name.lower()
    value = value.replace("&", " and ")
    value = value.replace("et al.", "")
    value = value.replace("et al", "")
    value = ROLE_LABEL_PATTERN.sub(" ", value)
    value = re.sub(r"\bv[.]?\b", " v ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def parse_year(value: Any) -> str:
    match = re.search(r"(\d{4})", str(value or ""))
    return match.group(1) if match else ""


def load_courtlistener_index(base_dir: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(base_dir.glob("*/citations_formatted.json")):
        with path.open("r", encoding="utf-8") as handle:
            cases = json.load(handle)
        for case in cases:
            name = normalize_case_name(str(case.get("case") or ""))
            year = parse_year(case.get("year"))
            if name and year:
                index[(name, year)].append(case)
    return index


def build_year_index(
    exact_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, list[tuple[str, list[dict[str, Any]]]]]:
    year_index: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    for (name, year), cases in exact_index.items():
        year_index[year].append((name, cases))
    return year_index


def fuzzy_name_year_match(
    normalized_name: str,
    year: str,
    year_index: dict[str, list[tuple[str, list[dict[str, Any]]]]],
    threshold: float = 0.985,
) -> list[dict[str, Any]]:
    best_cases: list[dict[str, Any]] = []
    best_ratio = 0.0
    for candidate_name, cases in year_index.get(year, []):
        ratio = difflib.SequenceMatcher(a=normalized_name, b=candidate_name).ratio()
        if ratio >= threshold and ratio > best_ratio:
            best_ratio = ratio
            best_cases = cases
    return best_cases


def case_text(record: dict[str, Any]) -> str:
    casebody = record.get("casebody") or {}
    parts: list[str] = []
    head_matter = casebody.get("head_matter")
    if isinstance(head_matter, str):
        parts.append(head_matter)
    for opinion in casebody.get("opinions") or []:
        if isinstance(opinion, dict):
            text = opinion.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).lower()


def is_medmal_candidate(record: dict[str, Any]) -> bool:
    text = case_text(record)
    if not text:
        return False
    has_medical_term = any(term in text for term in MEDICAL_TERMS)
    has_liability_term = any(term in text for term in LIABILITY_TERMS)
    return has_medical_term and has_liability_term


def fetch_recent_volumes(reporter: str, limit: int) -> list[dict[str, Any]]:
    url = f"https://static.case.law/{reporter}/VolumesMetadata.json"
    volumes = fetch_json(url)
    if not isinstance(volumes, list):
        raise RuntimeError(f"{url} did not return a list")

    modern = [
        volume
        for volume in volumes
        if isinstance(volume, dict)
        and isinstance(volume.get("volume_folder"), str)
        and isinstance(volume.get("publication_year"), int)
        and 1980 <= volume["publication_year"] <= 2020
    ]
    modern.sort(
        key=lambda volume: (int(volume["publication_year"]), str(volume["volume_folder"])),
        reverse=True,
    )
    return modern[:limit]


def iter_cap_cases(reporters: tuple[str, ...], volumes_per_reporter: int):
    for reporter in reporters:
        volumes = fetch_recent_volumes(reporter, volumes_per_reporter)
        for volume in volumes:
            volume_folder = volume["volume_folder"]
            zip_url = f"https://static.case.law/{reporter}/{volume_folder}.zip"
            try:
                payload = fetch_bytes(zip_url)
            except urllib.error.URLError as exc:
                print(f"WARNING: failed to fetch {zip_url}: {exc}", file=sys.stderr)
                continue
            print(f"Fetched {reporter}/{volume_folder}.zip", file=sys.stderr)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for name in sorted(archive.namelist()):
                    if not name.startswith("json/") or not name.endswith(".json"):
                        continue
                    with archive.open(name) as handle:
                        full_case = json.load(handle)
                    if not isinstance(full_case, dict):
                        continue
                    yield reporter, volume_folder, full_case


def summarize_matches(results: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [row for row in results if row["match_method"] != "unmatched"]
    return {
        "candidate_cases_tested": len(results),
        "matched_cases": len(matched),
        "unmatched_cases": len(results) - len(matched),
        "sample_matches": matched[:10],
        "sample_unmatched": [row for row in results if row["match_method"] == "unmatched"][:10],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live CAP dedup test against the local CourtListener export.")
    parser.add_argument(
        "--courtlistener-dir",
        default=DEFAULT_COURTLISTENER_DIR,
        help="Path to the local CourtListener med-mal export",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=DEFAULT_SCAN_LIMIT,
        help="Stop after scanning this many real CAP cases",
    )
    parser.add_argument(
        "--volumes-per-reporter",
        type=int,
        default=DEFAULT_VOLUMES_PER_REPORTER,
        help="How many recent volumes to scan from each reporter",
    )
    parser.add_argument(
        "--reporters",
        nargs="+",
        default=list(DEFAULT_REPORTERS),
        help="CAP reporters to scan",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Where to write the summary JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    courtlistener_dir = Path(args.courtlistener_dir)
    if not courtlistener_dir.exists():
        raise SystemExit(f"Missing CourtListener directory: {courtlistener_dir}")

    print("Loading local CourtListener med-mal index...", file=sys.stderr)
    courtlistener_index = load_courtlistener_index(courtlistener_dir)
    courtlistener_year_index = build_year_index(courtlistener_index)
    print(f"Indexed {sum(len(rows) for rows in courtlistener_index.values())} local CourtListener cases.", file=sys.stderr)

    scanned_cases = 0
    medmal_candidates = 0
    results: list[dict[str, Any]] = []

    for reporter, volume_folder, record in iter_cap_cases(tuple(args.reporters), args.volumes_per_reporter):
        scanned_cases += 1
        if scanned_cases > args.scan_limit:
            break

        if scanned_cases % 100 == 0:
            print(f"Scanned {scanned_cases} CAP cases so far...", file=sys.stderr)

        if not is_medmal_candidate(record):
            continue

        medmal_candidates += 1
        cap_name = str(record.get("name") or "")
        cap_year = parse_year(record.get("decision_date"))
        key = (normalize_case_name(cap_name), cap_year)
        matches = courtlistener_index.get(key, [])
        match_method = "case_name_plus_year"

        if not matches:
            matches = fuzzy_name_year_match(key[0], cap_year, courtlistener_year_index)
            if matches:
                match_method = "case_name_plus_year_fuzzy"

        if matches:
            results.append(
                {
                    "reporter": reporter,
                    "volume_folder": volume_folder,
                    "cap_id": record.get("id"),
                    "cap_name": cap_name,
                    "cap_year": cap_year,
                    "match_method": match_method,
                    "match_count": len(matches),
                    "matched_cases": [
                        {
                            "case": match.get("case"),
                            "year": match.get("year"),
                            "url": match.get("url"),
                        }
                        for match in matches[:5]
                    ],
                }
            )
        else:
            results.append(
                {
                    "reporter": reporter,
                    "volume_folder": volume_folder,
                    "cap_id": record.get("id"),
                    "cap_name": cap_name,
                    "cap_year": cap_year,
                    "match_method": "unmatched",
                    "match_count": 0,
                    "matched_cases": [],
                }
            )

    summary = summarize_matches(results)
    summary["scanned_cases_total"] = scanned_cases
    summary["medmal_candidates_total"] = medmal_candidates
    summary["reporters"] = args.reporters
    summary["volumes_per_reporter"] = args.volumes_per_reporter
    summary["courtlistener_dir"] = str(courtlistener_dir)

    output_path = Path(args.output)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
