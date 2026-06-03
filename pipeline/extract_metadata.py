#!/usr/bin/env python3
"""
pipeline/extract_metadata.py

Builds data/metadata.csv — the master index of all Supreme Court PDF judgments.

For each PDF:
  - Reads page 1 to extract the Indian Kanoon doc_id from the footer URL
    (e.g. http://indiankanoon.org/doc/139315795/)
  - Parses party1, party2, and decision date from the filename
  - Records year from the folder name

Supports resuming: if data/metadata_checkpoint.csv exists from a previous
interrupted run, already-processed files are skipped.

Usage:
    python pipeline/extract_metadata.py
    python pipeline/extract_metadata.py --workers 4   # override worker count
"""

import os
import re
import sys
import argparse
import pandas as pd
import pdfplumber
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT.parent / "supreme_court_judgments"
OUTPUT_DIR = REPO_ROOT / "data"
OUTPUT_FILE = OUTPUT_DIR / "metadata.csv"
CHECKPOINT_FILE = OUTPUT_DIR / "metadata_checkpoint.csv"

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
IK_URL_RE = re.compile(r'indiankanoon\.org/doc/(\d+)/', re.IGNORECASE)
VS_RE = re.compile(r'\bvs\b', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Worker functions (must be module-level for ProcessPoolExecutor pickling)
# ---------------------------------------------------------------------------

def parse_filename(stem: str) -> dict:
    """
    Parse party names and decision date from a filename stem.

    Expected pattern: Party1_vs_Party2_on_DD_Month_YYYY_N
    Returns dict with keys: party1, party2, date_str
    """
    # Strip trailing sequence number (_1, _2, etc.)
    stem = re.sub(r'_\d+$', '', stem)

    vs_parts = re.split(r'_vs_', stem, maxsplit=1, flags=re.IGNORECASE)
    if len(vs_parts) == 2:
        party1 = vs_parts[0].replace('_', ' ').strip()
        rest = vs_parts[1]
        on_parts = re.split(r'_on_', rest, maxsplit=1, flags=re.IGNORECASE)
        party2 = on_parts[0].replace('_', ' ').strip()
        date_str = on_parts[1].replace('_', ' ').strip() if len(on_parts) == 2 else None
    else:
        party1 = stem.replace('_', ' ').strip()
        party2 = None
        date_str = None

    return {'party1': party1, 'party2': party2, 'date_str': date_str}


def extract_from_page1(pdf_path: Path) -> tuple:
    """
    Open page 1 of a PDF and return (doc_id, title).

    doc_id: numeric string from the Indian Kanoon footer URL, or None.
    title:  first text line that contains 'vs', or None.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None, None
            text = pdf.pages[0].extract_text() or ''

            match = IK_URL_RE.search(text)
            doc_id = match.group(1) if match else None

            title = None
            for line in text.splitlines():
                line = line.strip()
                if line and VS_RE.search(line):
                    title = line
                    break

            return doc_id, title
    except Exception:
        return None, None


def process_pdf(args: tuple) -> dict:
    """Worker: extract metadata for one PDF. Called in a subprocess."""
    pdf_path, year = args
    filename = pdf_path.name
    parsed = parse_filename(pdf_path.stem)
    doc_id, pdf_title = extract_from_page1(pdf_path)

    # Build a readable title: prefer the PDF header, fall back to filename parts
    if pdf_title:
        title = pdf_title
    elif parsed['party2']:
        title = f"{parsed['party1']} vs {parsed['party2']}"
    else:
        title = parsed['party1']

    return {
        'doc_id': doc_id,
        'year': year,
        'filename': filename,
        'title': title,
        'party1': parsed['party1'],
        'party2': parsed['party2'],
        'date_str': parsed['date_str'],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def collect_tasks(already_done: set) -> list:
    """Return list of (Path, year) tuples for all PDFs not yet processed."""
    tasks = []
    for year_dir in sorted(DATA_ROOT.iterdir()):
        if year_dir.is_dir() and year_dir.name.isdigit():
            year = int(year_dir.name)
            if 1950 <= year <= 2025:
                for pdf_file in sorted(year_dir.iterdir()):
                    if pdf_file.suffix.upper() == '.PDF' and pdf_file.name not in already_done:
                        tasks.append((pdf_file, year))
    return tasks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Extract metadata from all Supreme Court PDFs")
    parser.add_argument('--workers', type=int, default=max(1, os.cpu_count() - 1),
                        help='Number of parallel worker processes (default: cpu_count - 1)')
    parser.add_argument('--test', type=int, default=0, metavar='N',
                        help='Dry-run: process only the first N PDFs and print results')
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_ROOT.exists():
        print(f"ERROR: Data folder not found at {DATA_ROOT}", file=sys.stderr)
        sys.exit(1)

    # Resume from checkpoint if available
    existing_rows = []
    already_done: set = set()
    if CHECKPOINT_FILE.exists():
        df_ckpt = pd.read_csv(CHECKPOINT_FILE)
        already_done = set(df_ckpt['filename'].dropna().tolist())
        existing_rows = df_ckpt.to_dict('records')
        print(f"Resuming from checkpoint: {len(already_done):,} already processed")

    tasks = collect_tasks(already_done)
    if args.test:
        tasks = tasks[:args.test]
        print(f"[TEST MODE] Processing first {args.test} PDFs only")

    if not tasks:
        print("Nothing to process — metadata.csv is already up to date.")
        sys.exit(0)

    print(f"PDFs to process : {len(tasks):,}")
    print(f"Worker processes: {args.workers}")
    print(f"Output          : {OUTPUT_FILE}\n")

    results = list(existing_rows)
    CHECKPOINT_EVERY = 2000

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_pdf, t): t for t in tasks}
        for i, future in enumerate(
            tqdm(as_completed(futures), total=len(tasks), desc="Extracting", unit="pdf")
        ):
            results.append(future.result())

            # Save checkpoint periodically so the run is resumable
            if (i + 1) % CHECKPOINT_EVERY == 0:
                pd.DataFrame(results).to_csv(CHECKPOINT_FILE, index=False)

    # Final save
    df = (
        pd.DataFrame(results)
        .sort_values(['year', 'filename'])
        .reset_index(drop=True)
    )

    if args.test:
        print(df.to_string())
        return

    df.to_csv(OUTPUT_FILE, index=False)

    # Clean up checkpoint
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()

    total = len(df)
    found = df['doc_id'].notna().sum()
    print(f"\nDone.")
    print(f"  Total records   : {total:,}")
    print(f"  doc_id found    : {found:,}  ({100 * found / total:.1f}%)")
    print(f"  doc_id missing  : {total - found:,}")
    print(f"  Saved to        : {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
