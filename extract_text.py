#!/usr/bin/env python3
"""
Extract text from every judgment PDF in the consolidated corpus into structured
per-year parquet tables (data/extracted_text/<year>.parquet).

Indian Kanoon PDFs are HTML-to-PDF exports (digital text, no OCR needed), so
pypdf is fast and accurate. Land-dispute cases are flagged and carry their IK
link by joining data/land_dispute_dataset.csv.

Columns: pdf_path, year, case_name, n_pages, n_chars, is_land_dispute, ik_link, text

Resumable: a year whose parquet already exists is skipped. Parallel across CPUs.
Usage: extract_text.py [--year YYYY] [--workers N]
"""
import argparse
import os
import re
from concurrent.futures import ProcessPoolExecutor

import pandas as pd
from pypdf import PdfReader

ROOT = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(ROOT, "data", "supreme_court_judgments")
OUTDIR = os.path.join(ROOT, "data", "extracted_text")
LAND = os.path.join(ROOT, "data", "land_dispute_dataset.csv")


def case_name(stem: str) -> str:
    """Human-readable case name from a sanitized filename stem."""
    stem = re.sub(r"_\d+$", "", stem)        # drop _<n> counter
    return stem.replace("_", " ").strip()


def extract_one(args):
    rel, abspath = args
    rec = {"pdf_path": rel, "n_pages": 0, "n_chars": 0, "text": "", "error": ""}
    try:
        reader = PdfReader(abspath)
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        rec["n_pages"] = len(reader.pages)
        rec["text"] = text
        rec["n_chars"] = len(text)
    except Exception as e:  # corrupt/encrypted PDF -> record, keep going
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    args = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)

    land = pd.read_csv(LAND).drop_duplicates("pdf_path")
    link_by_path = dict(zip(land["pdf_path"], land["link"]))

    years = [args.year] if args.year else sorted(
        y for y in os.listdir(CORPUS)
        if y.isdigit() and os.path.isdir(os.path.join(CORPUS, y))
    )

    for year in years:
        out = os.path.join(OUTDIR, f"{year}.parquet")
        if os.path.exists(out):
            print(f"{year}: skip (exists)")
            continue
        ydir = os.path.join(CORPUS, year)
        jobs = [
            (os.path.join("supreme_court_judgments", year, n), os.path.join(ydir, n))
            for n in sorted(os.listdir(ydir)) if n.lower().endswith(".pdf")
        ]
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            recs = list(ex.map(extract_one, jobs, chunksize=8))

        df = pd.DataFrame(recs)
        df["year"] = int(year)
        df["case_name"] = df["pdf_path"].map(lambda p: case_name(os.path.splitext(os.path.basename(p))[0]))
        df["is_land_dispute"] = df["pdf_path"].isin(link_by_path)
        df["ik_link"] = df["pdf_path"].map(link_by_path)
        df = df[["pdf_path", "year", "case_name", "n_pages", "n_chars",
                 "is_land_dispute", "ik_link", "text", "error"]]
        df.to_parquet(out, index=False)

        empties = int((df["n_chars"] == 0).sum())
        print(f"{year}: {len(df):>4} pdfs | empty/err={empties:>3} | "
              f"land={int(df['is_land_dispute'].sum()):>3} | -> {os.path.basename(out)}")


if __name__ == "__main__":
    main()
