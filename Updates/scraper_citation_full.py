"""
Indian Kanoon citation scraper — self-contained (no scraper.py needed).

Extends Saurabh's working notebook:
  - Reads the assigned bracket (4501-5250) from land_property_dispute_cases.csv
  - For each case, collects BOTH:
        * precedents  (cites / outbound)  -> via the #citeselect dropdown (proven method)
        * cited_by    (citedby / inbound) -> via the search-results page
  - Writes Anshul-format JSON, and is RESUME-SAFE: re-running skips finished cases.

SETUP (same as your notebook):
  1. Start Chrome with remote debugging and leave it open:
       /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
         --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-debug" \\
         --no-first-run --no-default-browser-check --remote-allow-origins="*"
  2. Put this file + land_property_dispute_cases.csv in the same folder.
  3. Run a TEST first, then the full bracket:
       python3 scraper_citation_full.py --max-cases 3
       python3 scraper_citation_full.py

If Cloudflare / a captcha appears in Chrome, the script pauses and asks you to
solve it by hand, then press Enter. Progress is saved continuously.
"""

import argparse
import csv
import json
import random
import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ── config ────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
DEFAULT_CSV = ROOT / "land_property_dispute_cases.csv"
OUTPUT_FILE = ROOT / "citations_4501_5250.json"

MY_START = 4501   # 1-indexed inclusive — Saurabh's bracket
MY_END = 5250

DEBUGGER_ADDRESS = "127.0.0.1:9222"


def extract_doc_id(link: str) -> str | None:
    m = re.search(r"/doc/(\d+)/", link or "")
    return m.group(1) if m else None


def clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").replace("_", " ")).strip()


def load_bracket(csv_path: Path, start: int, end: int) -> list[dict]:
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for offset, row in enumerate(rows[start - 1 : end]):
        doc_id = extract_doc_id(row.get("link", ""))
        if doc_id is None:
            print(f"  [skip] case #{start + offset}: no doc id in {row.get('link')!r}")
            continue
        out.append({
            "case_number": start + offset,
            "csv_name": row.get("case", ""),
            "doc_id": doc_id,
            "url": f"https://indiankanoon.org/doc/{doc_id}/",
        })
    return out


# ── resume support ────────────────────────────────────────────────────
def load_existing(path: Path) -> dict:
    """Return {doc_id: record} for cases already scraped, so we can skip them."""
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return {rec["source_doc_id"]: rec for rec in data}
    except Exception as e:
        print(f"  (could not read existing JSON, starting fresh: {e})")
        return {}


def save_all(path: Path, by_id: dict) -> None:
    # preserve case order by source_doc_id insertion
    with path.open("w", encoding="utf-8") as f:
        json.dump(list(by_id.values()), f, indent=2, ensure_ascii=False)


# ── page helpers ──────────────────────────────────────────────────────
def get_source_name(driver) -> str:
    for selector in ("h1", "h2", ".doc_title", ".judgments h2", ".judgments h1"):
        for elem in driver.find_elements(By.CSS_SELECTOR, selector):
            text = elem.text.strip()
            if text:
                return text
    return driver.title.replace(" | Indian Kanoon", "").strip()


def wait_or_prompt(driver, wait, css, label):
    """Wait for an element; if it doesn't appear, assume a captcha and ask the
    user to clear it manually (exactly like your notebook)."""
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
    except TimeoutException:
        print(f"  '{css}' not found while loading {label}.")
        print("  If a captcha/challenge is visible, solve it in Chrome.")
        input("  After the page loads fully, press Enter here... ")


def get_precedents(driver) -> list[dict]:
    """Cites / outbound — read from the #citeselect dropdown (your proven method)."""
    precedents = []
    for opt in driver.find_elements(By.CSS_SELECTOR, "#citeselect option[value]"):
        ref_id = opt.get_attribute("value")
        ref_name = (opt.get_attribute("textContent") or "").strip()
        if not ref_id or "select precedent" in ref_name.lower():
            continue
        precedents.append({
            "name": ref_name,
            "doc_id": ref_id,
            "url": f"https://indiankanoon.org/doc/{ref_id}/",
        })
    return precedents


def get_cited_by(driver, wait, doc_id: str) -> list[dict]:
    """Cited-by / inbound — paginate the search endpoint and read result links.

    NOTE: if this returns 0 for cases you expect to have inbound citations,
    the result-row selector below is the thing to check. Indian Kanoon result
    rows are <div class="result"> / <article class="result"> with the link in
    a <div class="result_title"> a  — both variants are tried.
    """
    cited_by = []
    seen = set()
    pagenum = 0
    while True:
        url = f"https://indiankanoon.org/search/?formInput=citedby:{doc_id}&pagenum={pagenum}"
        driver.get(url)
        time.sleep(random.uniform(2, 4))
        wait_or_prompt(driver, wait, "body", f"citedby page {pagenum} for {doc_id}")

        links = driver.find_elements(
            By.CSS_SELECTOR,
            "div.result_title a, h4.result_title a, .result .result_title a",
        )
        if not links:
            break

        new_in_page = 0
        for a in links:
            href = a.get_attribute("href") or ""
            m = re.search(r"/(?:doc|docfragment)/(\d+)/", href)
            if not m:
                continue
            ref_id = m.group(1)
            if ref_id == doc_id or ref_id in seen:
                continue
            seen.add(ref_id)
            title = a.text.strip()
            ref_name = re.sub(r"\s+on\s+\d+\s+\w+,?\s*\d{4}\s*$", "", title).strip()
            cited_by.append({
                "name": ref_name,
                "doc_id": ref_id,
                "url": f"https://indiankanoon.org/doc/{ref_id}/",
            })
            new_in_page += 1

        if new_in_page == 0:
            break

        # tolerant "Next" detection
        next_links = [
            el for el in driver.find_elements(By.TAG_NAME, "a")
            if el.text.strip().lower().startswith("next")
        ]
        if not next_links:
            break
        pagenum += 1
        time.sleep(random.uniform(2, 4))
    return cited_by


# ── main ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Indian Kanoon cites + cited-by scraper (self-contained)")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--start", type=int, default=MY_START)
    ap.add_argument("--end", type=int, default=MY_END)
    ap.add_argument("--max-cases", type=int, default=None, help="cap for a quick test")
    ap.add_argument("--out", type=Path, default=OUTPUT_FILE)
    ap.add_argument("--skip-citedby", action="store_true",
                    help="only collect precedents (cites), like the original notebook")
    args = ap.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV not found: {args.csv}")
        return

    cases = load_bracket(args.csv, args.start, args.end)
    if args.max_cases is not None:
        cases = cases[: args.max_cases]
    print(f"Loaded {len(cases)} cases (bracket {args.start}-{args.end})")

    by_id = load_existing(args.out)
    todo = [c for c in cases if c["doc_id"] not in by_id]
    print(f"{len(cases) - len(todo)} already done, {len(todo)} to process")
    if not todo:
        save_all(args.out, by_id)
        print(f"Nothing to do. JSON at {args.out}")
        return

    options = Options()
    options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 25)

    try:
        for i, c in enumerate(todo, start=1):
            doc_id, url = c["doc_id"], c["url"]
            print(f"\n[{i}/{len(todo)}] case #{c['case_number']} doc {doc_id}: {clean_name(c['csv_name'])[:46]}")

            driver.get(url)
            time.sleep(random.uniform(2, 4))
            wait_or_prompt(driver, wait, "#citeselect", f"doc {doc_id}")

            source_name = get_source_name(driver)
            precedents = get_precedents(driver)
            print(f"    precedents (cites): {len(precedents)}")

            cited_by = []
            if not args.skip_citedby:
                cited_by = get_cited_by(driver, wait, doc_id)
                print(f"    cited_by (inbound): {len(cited_by)}")

            by_id[doc_id] = {
                "source_name": source_name,
                "source_doc_id": doc_id,
                "source_url": url,
                "precedent_count": len(precedents),
                "precedents": precedents,
                "cited_by_count": len(cited_by),
                "cited_by": cited_by,
            }

            # save after EVERY case — resume-safe
            save_all(args.out, by_id)
            time.sleep(random.uniform(3, 7))
    finally:
        # never lose progress, even on crash / Ctrl+C
        save_all(args.out, by_id)
        print(f"\nSaved {len(by_id)} cases to {args.out}")
        print("Done. (Safe to re-run; finished cases are skipped.)")


if __name__ == "__main__":
    main()
