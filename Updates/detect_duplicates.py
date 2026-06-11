"""
Duplicate-case detector for the scraped citation JSON.

WHY: Indian Kanoon sometimes has the same judgment under multiple doc IDs
(a judgment + its companion order, re-uploads, etc.). Two records of the
SAME case share almost all precedents, which inflates "relatedness" and
distorts community detection. This finds those likely-duplicate pairs so
they can be reviewed and merged/excluded before graph construction.

HOW IT DECIDES "likely duplicate":
  - high number of shared precedents (--min-shared), AND
  - high name similarity (--min-name-sim), OR
  - one case's precedent set is almost a subset of the other's (--min-jaccard)
A pair must clear the name OR the jaccard test (plus the shared-count test)
to be flagged. Tunable so you can be strict or loose.

OUTPUTS (next to input JSON):
  - likely_duplicates.csv   ranked candidate duplicate pairs for manual review
  - (with --write-deduped) citations_deduped.json  keeps one record per dup group

USAGE:
  python detect_duplicates.py
  python detect_duplicates.py --min-shared 8 --min-name-sim 0.6
  python detect_duplicates.py --write-deduped     # also write a cleaned JSON

Dependencies: none beyond the standard library.
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

DEFAULT_JSON = Path("citations_4501_5250.json")
DEFAULT_CSV = Path("land_property_dispute_cases.csv")


def _clean_name(raw: str) -> str:
    raw = re.sub(r"_on_\d+.*$", "", raw or "")
    return re.sub(r"\s+", " ", raw.replace("_", " ")).strip()


def _normalize_for_compare(name: str) -> str:
    """Lowercase, strip common noise tokens so 'UOI' vs 'Union Of India' etc.
    don't block an otherwise-obvious match."""
    n = name.lower()
    n = n.replace("uoi", "union of india")
    n = re.sub(r"\b(d|dead|by|l rs|lrs|ors|anr|etc|the|and|others|by lrs)\b", " ", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def load_csv_names(csv_path: Path) -> dict:
    names = {}
    if not csv_path.exists():
        return names
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = re.search(r"/doc/(\d+)/", row.get("link", "") or "")
            if m:
                names[m.group(1)] = _clean_name(row.get("case", ""))
    return names


def load_cases(path: Path, csv_names: dict) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for rec in data:
        sid = rec.get("source_doc_id")
        if not sid:
            continue
        sid = str(sid)
        sname = rec.get("source_name") or ""
        if "search engine" in sname.lower() or not sname:
            sname = csv_names.get(sid, sid)
        cites = {str(p["doc_id"]) for p in rec.get("precedents", []) if p.get("doc_id")}
        cases.append({
            "id": sid,
            "name": csv_names.get(sid, sname),
            "cites": cites,
            "raw": rec,  # keep original record for deduped output
        })
    return cases


def find_duplicates(cases, min_shared, min_name_sim, min_jaccard):
    # invert precedent -> cases, to only compare pairs that share something
    prec_to_cases = defaultdict(set)
    by_id = {c["id"]: c for c in cases}
    for c in cases:
        for p in c["cites"]:
            prec_to_cases[p].add(c["id"])

    candidate_pairs = set()
    for citing in prec_to_cases.values():
        if 2 <= len(citing) <= 60:  # skip ultra-common precedents (noise)
            for a, b in combinations(sorted(citing), 2):
                candidate_pairs.add((a, b))

    flagged = []
    for a, b in candidate_pairs:
        ca, cb = by_id[a], by_id[b]
        shared = ca["cites"] & cb["cites"]
        if len(shared) < min_shared:
            continue
        union = ca["cites"] | cb["cites"]
        jaccard = len(shared) / len(union) if union else 0.0
        name_sim = SequenceMatcher(
            None, _normalize_for_compare(ca["name"]), _normalize_for_compare(cb["name"])
        ).ratio()

        # flag if names look alike OR precedent sets nearly coincide
        if name_sim >= min_name_sim or jaccard >= min_jaccard:
            flagged.append({
                "case_a_id": a, "case_a_name": ca["name"],
                "case_b_id": b, "case_b_name": cb["name"],
                "shared_precedents": len(shared),
                "jaccard": round(jaccard, 3),
                "name_similarity": round(name_sim, 3),
                "reason": ("name+precedents" if name_sim >= min_name_sim and jaccard >= min_jaccard
                           else "name" if name_sim >= min_name_sim
                           else "precedents"),
            })
    flagged.sort(key=lambda r: (r["name_similarity"], r["jaccard"]), reverse=True)
    return flagged


def dedupe_groups(flagged):
    """Union-find the flagged pairs into groups of duplicates."""
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(x, y):
        parent[find(x)] = find(y)
    for r in flagged:
        union(r["case_a_id"], r["case_b_id"])
    groups = defaultdict(list)
    for node in parent:
        groups[find(node)].append(node)
    return [g for g in groups.values() if len(g) > 1]


def main():
    ap = argparse.ArgumentParser(description="Detect likely duplicate cases in the scraped JSON.")
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--min-shared", type=int, default=8,
                    help="min shared precedents to even consider a pair (default 8)")
    ap.add_argument("--min-name-sim", type=float, default=0.6,
                    help="name-similarity threshold 0-1 to flag on names (default 0.6)")
    ap.add_argument("--min-jaccard", type=float, default=0.7,
                    help="precedent-set overlap 0-1 to flag on precedents alone (default 0.7)")
    ap.add_argument("--write-deduped", action="store_true",
                    help="also write citations_deduped.json keeping one record per group")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: file not found: {args.json}")
        return

    csv_names = load_csv_names(args.csv)
    cases = load_cases(args.json, csv_names)
    print(f"Loaded {len(cases)} cases")

    flagged = find_duplicates(cases, args.min_shared, args.min_name_sim, args.min_jaccard)
    out = args.json.parent

    with (out / "likely_duplicates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "case_a_id", "case_a_name", "case_b_id", "case_b_name",
            "shared_precedents", "jaccard", "name_similarity", "reason"])
        w.writeheader()
        for r in flagged:
            w.writerow(r)

    groups = dedupe_groups(flagged)
    print(f"\nFlagged {len(flagged)} likely-duplicate pairs, forming {len(groups)} duplicate groups.")
    print("Wrote: likely_duplicates.csv  (review these before merging)\n")

    print("Top flagged pairs:")
    for r in flagged[:12]:
        print(f"  shared={r['shared_precedents']:>2} jac={r['jaccard']:.2f} "
              f"nsim={r['name_similarity']:.2f} [{r['reason']:<15}] "
              f"{r['case_a_name'][:30]} <-> {r['case_b_name'][:30]}")

    if args.write_deduped:
        # keep the record with the MORE precedents from each group; drop the rest
        by_id = {c["id"]: c for c in cases}
        drop = set()
        for g in groups:
            keep = max(g, key=lambda i: len(by_id[i]["cites"]))
            drop.update(x for x in g if x != keep)
        kept_records = [c["raw"] for c in cases if c["id"] not in drop]
        with (out / "citations_deduped.json").open("w", encoding="utf-8") as f:
            json.dump(kept_records, f, indent=2, ensure_ascii=False)
        print(f"\nWrote citations_deduped.json: {len(kept_records)} records "
              f"({len(drop)} duplicate records removed).")
        print("NOTE: review likely_duplicates.csv first — this auto-keeps the record")
        print("with more precedents per group, which is a heuristic, not a guarantee.")


if __name__ == "__main__":
    main()
