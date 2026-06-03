#!/usr/bin/env python3
"""
Step 2 (Graph Construction): scrape the citation/precedent edges for the 7,496
land-dispute cases from Indian Kanoon.

IK blocks plain HTTP (403 even on the homepage) behind bot protection, so — like
scraper-reference/scraper_citation.ipynb — this drives a REAL Chrome you launch
yourself with remote debugging, letting you solve any CAPTCHA by hand. Each doc
page exposes its precedents in the `#citeselect` dropdown.

SETUP (run once, in a separate terminal):
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        --remote-debugging-port=9222 --user-data-dir="$HOME/ik-chrome"
  Then log in / solve any CAPTCHA in that Chrome window if prompted.

RUN:
    .venv/bin/python scrape_citations.py            # resumes where it left off
    .venv/bin/python scrape_citations.py --limit 50 # small test batch

Outputs (data/citations/):
    precedents.jsonl       one line per source case (resumable progress log)
    citation_edges.csv     source_doc_id, target_doc_id, target_name, target_in_corpus
"""
import argparse
import json
import os
import random
import re
import time

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

ROOT = os.path.dirname(os.path.abspath(__file__))
LAND = os.path.join(ROOT, "data", "land_dispute_dataset.csv")
OUTDIR = os.path.join(ROOT, "data", "citations")
JSONL = os.path.join(OUTDIR, "precedents.jsonl")
EDGES = os.path.join(OUTDIR, "citation_edges.csv")

DOC_ID_RE = re.compile(r"/doc/(\d+)")


def doc_id(url: str):
    m = DOC_ID_RE.search(str(url))
    return m.group(1) if m else None


def load_done():
    done = set()
    if os.path.exists(JSONL):
        with open(JSONL, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["source_doc_id"])
                except Exception:
                    pass
    return done


HEADER_RE = re.compile(r"Cites\s+(\d+),\s*Cited by\s+(\d+)")


def _loaded(driver):
    return bool(driver.find_elements(By.CSS_SELECTOR, "div.judgments"))


def _is_challenge(driver):
    title = (driver.title or "").lower()
    if any(k in title for k in ("just a moment", "attention required", "access denied")):
        return True
    return bool(driver.find_elements(
        By.CSS_SELECTOR, "#challenge-form, iframe[src*='captcha'], .g-recaptcha, form[action*='captcha']"))


def scrape_page(driver, wait, url):
    """Return {precedents, cites_header, cited_by_header} or None to retry later.

    Citations are the inline <a href=/doc/ID/> links inside div.judgments (these
    match the page's 'Cites N' count and include both case precedents and cited
    statutes). #citeselect is unreliable/JS-dependent, so it is not used.
    """
    driver.get(url)
    try:
        wait.until(lambda d: _loaded(d))
    except TimeoutException:
        if _is_challenge(driver):
            input("  CAPTCHA/challenge — solve it in Chrome, then press Enter...")
            try:
                wait.until(lambda d: _loaded(d))
            except TimeoutException:
                return None
        else:
            return None  # page didn't render as a doc page; retry on a later run

    self_id = doc_id(url)
    cites = {}  # doc_id -> best (longest) anchor text
    for a in driver.find_elements(By.CSS_SELECTOR, "div.judgments a[href*='/doc/']"):
        rid = doc_id(a.get_attribute("href"))
        if not rid or rid == self_id:
            continue
        txt = (a.text or "").strip()
        if rid not in cites or len(txt) > len(cites[rid]):
            cites[rid] = txt

    m = HEADER_RE.search(driver.find_element(By.TAG_NAME, "body").text)
    return {
        "precedents": [{"doc_id": k, "name": v} for k, v in cites.items()],
        "cites_header": int(m.group(1)) if m else None,
        "cited_by_header": int(m.group(2)) if m else None,
    }


def build_edges():
    """Flatten precedents.jsonl into an edge list, flagging intra-corpus targets."""
    corpus_ids = set()
    land = pd.read_csv(LAND)
    for u in land["link"]:
        if doc_id(u):
            corpus_ids.add(doc_id(u))
    rows = []
    with open(JSONL, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            for p in rec["precedents"]:
                rows.append({
                    "source_doc_id": rec["source_doc_id"],
                    "target_doc_id": p["doc_id"],
                    "target_name": p["name"],
                    "target_in_corpus": p["doc_id"] in corpus_ids,
                })
    pd.DataFrame(rows).to_csv(EDGES, index=False)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="max new cases to scrape this run")
    ap.add_argument("--port", default="9222")
    ap.add_argument("--edges-only", action="store_true", help="just rebuild edge list")
    args = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)

    if args.edges_only:
        print(f"edges written: {build_edges()} -> {EDGES}")
        return

    land = pd.read_csv(LAND).drop_duplicates("link")
    land["doc_id"] = land["link"].map(doc_id)
    land = land[land["doc_id"].notna()]
    done = load_done()
    todo = land[~land["doc_id"].isin(done)]
    if args.limit:
        todo = todo.head(args.limit)
    print(f"land-dispute cases: {len(land)} | already done: {len(done)} | this run: {len(todo)}")

    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{args.port}")
    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, 25)

    n = 0
    with open(JSONL, "a", encoding="utf-8") as fh:
        for _, row in todo.iterrows():
            url = f"https://indiankanoon.org/doc/{row['doc_id']}/"
            result = scrape_page(driver, wait, url)
            if result is None:
                print(f"  SKIP (retry later): {row['doc_id']}")
                continue
            precedents = result["precedents"]
            fh.write(json.dumps({
                "source_doc_id": row["doc_id"],
                "source_pdf_path": row.get("pdf_path"),
                "source_url": url,
                "precedent_count": len(precedents),
                "cites_header": result["cites_header"],
                "cited_by_header": result["cited_by_header"],
                "precedents": precedents,
            }, ensure_ascii=False) + "\n")
            fh.flush()
            n += 1
            hdr = result["cites_header"]
            flag = "" if hdr is None or hdr == len(precedents) else f"  (!= header {hdr})"
            print(f"  [{n}/{len(todo)}] {row['doc_id']}: {len(precedents)} cites{flag}")
            time.sleep(random.uniform(3, 7))

    print(f"\nscraped {n} new cases. total done: {len(done)+n}/{len(land)}")
    print(f"edges written: {build_edges()} -> {EDGES}")


if __name__ == "__main__":
    main()
