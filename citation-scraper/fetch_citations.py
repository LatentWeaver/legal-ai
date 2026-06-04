"""
Fetch citations for rows 1501-2250 from the Indian Kanoon API.

Outputs:
  - citations_progress.jsonl  — one JSON line per processed doc (checkpoint, auto-resumed)
  - citations_output.csv      — final flat CSV (one row per citation)
  - citations_summary.csv     — one row per case with metadata + citation count

Run:
  python fetch_citations.py           # full run (rows 1501-2250)
  python fetch_citations.py --export  # skip fetching, just export existing progress to CSV
"""

import argparse
import json
import os
import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()
TOKEN = os.getenv("token")
BASE_URL = "https://api.indiankanoon.org"
HEADERS = {"Authorization": f"Token {TOKEN}", "Accept": "application/json"}

ROW_START = 1500        # 0-indexed (= row 1501 in the sheet)
ROW_END   = 2250        # exclusive
DELAY     = 1.0         # seconds between requests
MAX_RETRY = 3           # retries on transient errors
RETRY_BACKOFF = 5       # seconds to wait on first retry (doubles each time)

PROGRESS_FILE = "citations_progress.jsonl"
OUTPUT_FLAT   = "citations_output.csv"
OUTPUT_SUMMARY = "citations_summary.csv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_doc_id(link: str) -> str | None:
    m = re.search(r"/doc/(\d+)/", str(link))
    return m.group(1) if m else None


def fetch_doc(doc_id: str) -> dict:
    url = f"{BASE_URL}/doc/{doc_id}/"
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.post(url, headers=HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                tqdm.write(f"  [429] Rate limited — waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if attempt == MAX_RETRY:
                raise
            tqdm.write(f"  [HTTP {e.response.status_code}] retry {attempt}/{MAX_RETRY}")
            time.sleep(RETRY_BACKOFF * attempt)
        except requests.RequestException as e:
            if attempt == MAX_RETRY:
                raise
            tqdm.write(f"  [NET ERROR] {e} — retry {attempt}/{MAX_RETRY}")
            time.sleep(RETRY_BACKOFF * attempt)


def parse_citations(doc_html: str) -> list[dict]:
    soup = BeautifulSoup(doc_html, "html.parser")
    seen = set()
    citations = []
    for a in soup.find_all("a", href=re.compile(r"^/doc/\d+/")):
        cited_id = re.search(r"/doc/(\d+)/", a["href"]).group(1)
        if cited_id in seen:
            continue
        seen.add(cited_id)
        citations.append({
            "cited_doc_id": cited_id,
            "cited_title": a.get_text(separator=" ", strip=True),
            "cited_url": f"https://indiankanoon.org/doc/{cited_id}/",
        })
    return citations


# ---------------------------------------------------------------------------
# Progress file helpers
# ---------------------------------------------------------------------------

def load_progress() -> dict[str, dict]:
    """Returns {doc_id: record} for already-processed docs."""
    done = {}
    if not os.path.exists(PROGRESS_FILE):
        return done
    with open(PROGRESS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                done[str(rec["source_doc_id"])] = rec
    return done


def append_progress(rec: dict):
    with open(PROGRESS_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_csvs():
    if not os.path.exists(PROGRESS_FILE):
        print("No progress file found — nothing to export.")
        return

    records = []
    with open(PROGRESS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # Summary CSV — one row per case
    summary_rows = []
    for r in records:
        summary_rows.append({
            "case": r["case"],
            "source_doc_id": r["source_doc_id"],
            "title": r["title"],
            "court": r["court"],
            "date": r["date"],
            "numcites_api": r["numcites_api"],
            "numcitedby_api": r["numcitedby_api"],
            "citations_parsed": r["citations_parsed"],
            "error": r.get("error", ""),
        })
    pd.DataFrame(summary_rows).to_csv(OUTPUT_SUMMARY, index=False)
    print(f"Saved {OUTPUT_SUMMARY}  ({len(summary_rows)} rows)")

    # Flat CSV — one row per citation
    flat_rows = []
    for r in records:
        if r.get("error"):
            continue
        for c in r.get("citations", []):
            flat_rows.append({
                "case": r["case"],
                "source_doc_id": r["source_doc_id"],
                "source_title": r["title"],
                "source_court": r["court"],
                "source_date": r["date"],
                "cited_doc_id": c["cited_doc_id"],
                "cited_title": c["cited_title"],
                "cited_url": c["cited_url"],
            })
    pd.DataFrame(flat_rows).to_csv(OUTPUT_FLAT, index=False)
    print(f"Saved {OUTPUT_FLAT}  ({len(flat_rows)} citation rows)")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run():
    df = pd.read_excel("land_property_dispute_cases.xlsx")
    subset = df.iloc[ROW_START:ROW_END].reset_index(drop=True)
    total = len(subset)

    done = load_progress()
    print(f"Total cases: {total}  |  Already done: {len(done)}  |  Remaining: {total - len(done)}")
    if not TOKEN:
        print("ERROR: 'token' not found in .env")
        return

    errors = 0
    pbar = tqdm(subset.iterrows(), total=total, unit="case")
    for _, row in pbar:
        doc_id = extract_doc_id(row["link"])
        if not doc_id:
            tqdm.write(f"  [SKIP] No doc id: {row['link']}")
            continue

        if doc_id in done:
            pbar.set_postfix(status="cached")
            continue

        pbar.set_postfix(doc_id=doc_id)
        rec = {
            "case": row["case"],
            "source_doc_id": doc_id,
            "title": None,
            "court": None,
            "date": None,
            "numcites_api": 0,
            "numcitedby_api": 0,
            "citations_parsed": 0,
            "citations": [],
            "error": None,
        }

        try:
            data = fetch_doc(doc_id)
            citations = parse_citations(data.get("doc", ""))
            rec.update({
                "title": data.get("title"),
                "court": data.get("docsource"),
                "date": data.get("publishdate"),
                "numcites_api": data.get("numcites", 0),
                "numcitedby_api": data.get("numcitedby", 0),
                "citations_parsed": len(citations),
                "citations": citations,
            })
        except Exception as e:
            rec["error"] = str(e)
            errors += 1
            tqdm.write(f"  [FAILED] {doc_id}: {e}")

        append_progress(rec)
        done[doc_id] = rec
        time.sleep(DELAY)

    pbar.close()
    print(f"\nDone. {len(done)} processed, {errors} errors.")
    export_csvs()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true", help="Export existing progress to CSV without fetching")
    args = parser.parse_args()

    if args.export:
        export_csvs()
    else:
        run()
