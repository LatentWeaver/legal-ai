"""Flatten data/citations.jsonl into a readable CSV (one row per citation).

The JSONL is convenient for code but awkward to read. This writes a flat edge
list with human-readable names so the citations can be reviewed in a
spreadsheet:

    source_doc_id, source_case, source_year, source_url,
    cited_doc_id, cited_name, cited_url

USAGE
-----
    python src/export_citations_csv.py
    python src/export_citations_csv.py --input data/citations.jsonl \
        --output data/citations.csv
"""

from __future__ import annotations

import argparse
import json
import re

import pandas as pd


def clean(text):
    """Collapse runs of whitespace (HTML leaves stray tabs/newlines in names)."""
    if not text:
        return text
    return re.sub(r"\s+", " ", str(text)).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/citations.jsonl")
    ap.add_argument("--output", default="data/citations.csv")
    args = ap.parse_args()

    rows = []
    n_cases = n_links = 0
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            n_cases += 1
            precedents = rec.get("precedents", [])
            if not precedents:
                # keep cases with no citations as a single row (cited cols blank)
                rows.append({
                    "source_doc_id": rec.get("source_doc_id"),
                    "source_case": rec.get("source_case"),
                    "source_year": rec.get("source_year"),
                    "source_url": rec.get("source_url"),
                    "cited_doc_id": "", "cited_name": "", "cited_url": "",
                })
                continue
            for p in precedents:
                n_links += 1
                rows.append({
                    "source_doc_id": rec.get("source_doc_id"),
                    "source_case": rec.get("source_case"),
                    "source_year": rec.get("source_year"),
                    "source_url": rec.get("source_url"),
                    "cited_doc_id": p.get("doc_id"),
                    "cited_name": clean(p.get("name")),
                    "cited_url": p.get("url"),
                })

    df = pd.DataFrame(rows)
    df.to_csv(args.output, index=False)
    print(f"Wrote {args.output}: {len(df)} rows "
          f"({n_cases} cases, {n_links} citation links).")


if __name__ == "__main__":
    main()
