"""
Enrich distinct cited doc_ids (those NOT already in the land-dispute Excel) with
canonical metadata from the Indian Kanoon API, so we can determine:
  - which cited targets are actual CASES vs statutes/sections (via docsource)
  - which cited cases are present in the 26k corpus (canonical title+date match)
  - which corpus cases are missing from the land-dispute list (reclassification)

Resumable checkpoint -> enriched_cited.jsonl  (one JSON line per doc_id).
Run from this directory:  python enrich_cited.py
"""
import json
import os
import re
import time

import pandas as pd
import requests
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
TOKEN = os.getenv("token")
BASE_URL = "https://api.indiankanoon.org"
HEADERS = {"Authorization": f"Token {TOKEN}", "Accept": "application/json"}

PROGRESS_FILE = "enriched_cited.jsonl"
DELAY = 0.5
MAX_RETRY = 3
RETRY_BACKOFF = 5


def doc_id(url):
    m = re.search(r"/doc/(\d+)", str(url))
    return m.group(1) if m else None


def targets():
    flat = pd.read_csv("citations_flat.csv", dtype=str)
    ids = {str(i).replace(".0", "") for i in flat["cited_doc_id"].dropna() if str(i).strip()}
    land = pd.read_excel("land_property_dispute_cases.xlsx")
    land_ids = {doc_id(u) for u in land["link"] if doc_id(u)}
    return sorted(ids - land_ids, key=int)


def fetch_meta(did):
    url = f"{BASE_URL}/doc/{did}/"
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.post(url, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
                continue
            r.raise_for_status()
            d = r.json()
            return {
                "doc_id": did,
                "title": d.get("title"),
                "docsource": d.get("docsource"),
                "publishdate": d.get("publishdate"),
                "numcites": d.get("numcites"),
                "numcitedby": d.get("numcitedby"),
                "error": None,
            }
        except Exception as e:
            if attempt == MAX_RETRY:
                return {"doc_id": did, "title": None, "docsource": None,
                        "publishdate": None, "numcites": None, "numcitedby": None,
                        "error": str(e)[:150]}
            time.sleep(RETRY_BACKOFF * attempt)


def load_done():
    done = set()
    if os.path.exists(PROGRESS_FILE):
        for line in open(PROGRESS_FILE):
            line = line.strip()
            if line:
                try:
                    done.add(str(json.loads(line)["doc_id"]))
                except Exception:
                    pass
    return done


def main():
    if not TOKEN:
        print("ERROR: token not found in .env")
        return
    ids = targets()
    done = load_done()
    todo = [i for i in ids if i not in done]
    print(f"non-land distinct cited doc_ids: {len(ids)} | done: {len(done)} | todo: {len(todo)}")
    errors = 0
    with open(PROGRESS_FILE, "a") as fh:
        for did in tqdm(todo, unit="doc"):
            rec = fetch_meta(did)
            errors += bool(rec["error"])
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            time.sleep(DELAY)
    print(f"done. {len(todo)} fetched, {errors} errors.")


if __name__ == "__main__":
    main()
