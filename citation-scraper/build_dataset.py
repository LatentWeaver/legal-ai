"""
Build structured citation dataset from citations_progress.jsonl.

Outputs:
  citations_dataset.json  — list of case objects, each with a citations array
  citations_flat.csv      — one row per (case, cited_doc) pair
  citations_summary.csv   — one row per case with metadata + citation count
"""

import json
import re
import pandas as pd

PROGRESS_FILE = "citations_progress.jsonl"
OUT_JSON      = "citations_dataset.json"
OUT_FLAT_CSV  = "citations_flat.csv"
OUT_SUMMARY   = "citations_summary.csv"


def clean(text: str) -> str:
    """Collapse whitespace and newlines in scraped title text."""
    return re.sub(r"\s+", " ", str(text)).strip()


def load_records() -> list[dict]:
    records = []
    with open(PROGRESS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_dataset(records: list[dict]) -> list[dict]:
    dataset = []
    for r in records:
        citations = [
            {
                "doc_id": c["cited_doc_id"],
                "title":  clean(c["cited_title"]),
                "url":    c["cited_url"],
            }
            for c in r.get("citations", [])
        ]
        dataset.append({
            "case":            r["case"],
            "source_doc_id":   r["source_doc_id"],
            "title":           clean(r["title"] or ""),
            "court":           r.get("court", ""),
            "date":            r.get("date", ""),
            "numcites_api":    r.get("numcites_api", 0),
            "numcitedby_api":  r.get("numcitedby_api", 0),
            "citations_count": len(citations),
            "citations":       citations,
            "error":           r.get("error") or "",
        })
    return dataset


def save_json(dataset: list[dict]):
    with open(OUT_JSON, "w") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    print(f"Saved {OUT_JSON}  ({len(dataset)} cases)")


def save_flat_csv(dataset: list[dict]):
    rows = []
    for d in dataset:
        if not d["citations"]:
            # Keep cases with no citations as a single row so nothing is lost
            rows.append({
                "case":            d["case"],
                "source_doc_id":   d["source_doc_id"],
                "source_title":    d["title"],
                "court":           d["court"],
                "date":            d["date"],
                "cited_doc_id":    "",
                "cited_title":     "",
                "cited_url":       "",
                "error":           d["error"],
            })
        else:
            for c in d["citations"]:
                rows.append({
                    "case":            d["case"],
                    "source_doc_id":   d["source_doc_id"],
                    "source_title":    d["title"],
                    "court":           d["court"],
                    "date":            d["date"],
                    "cited_doc_id":    c["doc_id"],
                    "cited_title":     c["title"],
                    "cited_url":       c["url"],
                    "error":           d["error"],
                })
    df = pd.DataFrame(rows)
    df.to_csv(OUT_FLAT_CSV, index=False)
    citation_rows = df[df["cited_doc_id"] != ""]
    print(f"Saved {OUT_FLAT_CSV}  ({len(df)} rows, {len(citation_rows)} citation pairs)")


def save_summary_csv(dataset: list[dict]):
    rows = [
        {
            "case":            d["case"],
            "source_doc_id":   d["source_doc_id"],
            "title":           d["title"],
            "court":           d["court"],
            "date":            d["date"],
            "numcites_api":    d["numcites_api"],
            "numcitedby_api":  d["numcitedby_api"],
            "citations_count": d["citations_count"],
            "error":           d["error"],
        }
        for d in dataset
    ]
    df = pd.DataFrame(rows)
    df.to_csv(OUT_SUMMARY, index=False)
    print(f"Saved {OUT_SUMMARY}  ({len(df)} rows)")


def print_stats(dataset: list[dict]):
    total_cases      = len(dataset)
    cases_with_cites = sum(1 for d in dataset if d["citations_count"] > 0)
    total_citations  = sum(d["citations_count"] for d in dataset)
    error_count      = sum(1 for d in dataset if d["error"])
    top5 = sorted(dataset, key=lambda d: d["citations_count"], reverse=True)[:5]

    print(f"\n{'='*55}")
    print(f"  Cases processed    : {total_cases}")
    print(f"  Cases with cites   : {cases_with_cites}")
    print(f"  Cases with 0 cites : {total_cases - cases_with_cites}")
    print(f"  Total citation pairs: {total_citations}")
    print(f"  Avg citations/case : {total_citations / total_cases:.1f}")
    print(f"  Errors             : {error_count}")
    print(f"\n  Top 5 most-citing cases:")
    for d in top5:
        print(f"    [{d['citations_count']:3d}]  {d['title'][:60]}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    print(f"Loading {PROGRESS_FILE}...")
    records = load_records()
    dataset = build_dataset(records)
    print_stats(dataset)
    save_json(dataset)
    save_flat_csv(dataset)
    save_summary_csv(dataset)
    print("\nAll done.")
