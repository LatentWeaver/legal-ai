#!/usr/bin/env python3
"""
pipeline/scrape_citations.py

Scrapes outgoing citation relationships (precedents) for all land dispute
cases from Indian Kanoon using Selenium + ChromeDriver.

For each case in data/land_disputes.csv:
  - Navigates to https://indiankanoon.org/doc/{doc_id}/
  - Extracts the #citeselect dropdown (precedents cited by this judgment)
  - Appends one JSON record per case to data/citations.jsonl

Fully resumable: already-scraped doc_ids are detected from the output file
and skipped on restart. Safe to Ctrl-C and re-run at any time.

Estimated runtime: ~5–9 hours for 6,381 cases (depending on delay settings).
Run in multiple sessions or overnight — progress is never lost.

Usage:
    python pipeline/scrape_citations.py
    python pipeline/scrape_citations.py --delay 3       # faster (min delay 3s)
    python pipeline/scrape_citations.py --limit 100     # test on first 100 cases
    python pipeline/scrape_citations.py --year-from 2000  # start from a given year
"""

import json
import random
import time
import sys
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT          = Path(__file__).resolve().parent.parent
LAND_DISPUTES_FILE = REPO_ROOT / "data" / "land_disputes.csv"
OUTPUT_FILE        = REPO_ROOT / "data" / "citations.jsonl"
BASE_URL           = "https://indiankanoon.org/doc/{}/"

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------
PAGE_LOAD_TIMEOUT  = 25   # seconds to wait for page body
CITESELECT_TIMEOUT = 15   # seconds to wait for #citeselect to appear
MAX_RETRIES        = 2    # retry count on timeout
LONG_PAUSE_EVERY   = 75   # extra pause every N cases to avoid rate limiting


# ---------------------------------------------------------------------------
# Driver setup
# ---------------------------------------------------------------------------

DEBUG_PORT = 9222   # Chrome must be started with --remote-debugging-port=9222


def setup_driver() -> webdriver.Chrome:
    """
    Connect to the user's existing Chrome window via the remote debugging port.

    The user must start Chrome BEFORE running this script with:
        open -na "Google Chrome" --args --remote-debugging-port=9222 --no-first-run

    Using the user's real Chrome profile means Cloudflare sees a genuine browser,
    not a bot-flagged automation instance.
    """
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    try:
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        return driver
    except WebDriverException as exc:
        print(
            f"\nERROR: Could not connect to Chrome on port {DEBUG_PORT}.\n"
            f"Please start Chrome first with:\n\n"
            f"  open -na \"Google Chrome\" --args "
            f"--remote-debugging-port={DEBUG_PORT} --no-first-run\n\n"
            f"Then re-run this script.\n"
        )
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# CAPTCHA detection
# ---------------------------------------------------------------------------

_CAPTCHA_SIGNALS = [
    "captcha",
    "are you human",
    "robot check",
    "verify you are human",
    "security check required",
    "access denied",
    "unusual traffic",
    # Cloudflare HUMAN challenge (not CDN headers — those always appear)
    "cf-challenge-form",
    "cf-spinner",
    "ray id",            # Cloudflare block page shows Ray ID
    "enable javascript and cookies",
]

# Signals that indicate a REAL page (not a challenge) — short-circuit early
_OK_SIGNALS = [
    "indiankanoon.org",
    "citeselect",
    "judgment",
    "petitioner",
    "respondent",
]

CLOUDFLARE_SETTLE_WAIT = 6   # seconds to let Cloudflare JS challenge auto-resolve


def is_captcha_page(driver: webdriver.Chrome) -> bool:
    src = driver.page_source.lower()
    # If the page already has real IK content, no challenge
    if any(sig in src for sig in _OK_SIGNALS):
        return False
    return any(sig in src for sig in _CAPTCHA_SIGNALS)


# ---------------------------------------------------------------------------
# Resumability
# ---------------------------------------------------------------------------

def load_done_ids(output_file: Path) -> set:
    """Return set of source_doc_ids already written to the output file."""
    done = set()
    if not output_file.exists():
        return done
    with open(output_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done.add(str(rec["source_doc_id"]))
            except (json.JSONDecodeError, KeyError):
                pass
    return done


# ---------------------------------------------------------------------------
# Core scraping logic
# ---------------------------------------------------------------------------

def scrape_one(driver: webdriver.Chrome, doc_id: str,
               title: str, year: int) -> dict:
    """
    Navigate to an Indian Kanoon case page and extract its precedents
    from the #citeselect dropdown.

    Returns a record dict always — sets 'error' key on failure.
    """
    url = BASE_URL.format(doc_id)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            driver.get(url)

            # Wait for basic page body
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # Give Cloudflare JS challenge a moment to auto-resolve before
            # deciding whether a human challenge is actually present
            time.sleep(CLOUDFLARE_SETTLE_WAIT)

            # CAPTCHA gate
            if is_captcha_page(driver):
                print(
                    f"\n  ┌─ CAPTCHA DETECTED ──────────────────────────────────────┐\n"
                    f"  │ Please solve the challenge in the Chrome window.          │\n"
                    f"  │ After the case page loads fully, press Enter to continue. │\n"
                    f"  └───────────────────────────────────────────────────────────┘"
                )
                input("  > ")
                # retry same URL after user solved CAPTCHA
                continue

            # Wait for #citeselect (may not exist if case has no citations)
            citations = []
            try:
                select_el = WebDriverWait(driver, CITESELECT_TIMEOUT).until(
                    EC.presence_of_element_located((By.ID, "citeselect"))
                )
                options = select_el.find_elements(
                    By.CSS_SELECTOR, "option[value]"
                )
                for opt in options:
                    ref_id   = (opt.get_attribute("value") or "").strip()
                    ref_name = (opt.get_attribute("textContent") or opt.text or "").strip()
                    if not ref_id:
                        continue
                    if "select precedent" in ref_name.lower():
                        continue
                    citations.append({"doc_id": ref_id, "name": ref_name})

            except TimeoutException:
                # No #citeselect means this case has no cited precedents — valid
                pass

            return {
                "source_doc_id":   doc_id,
                "source_title":    title,
                "year":            year,
                "citation_count":  len(citations),
                "citations":       citations,
                "scraped_at":      datetime.utcnow().isoformat(),
            }

        except TimeoutException:
            if attempt < MAX_RETRIES:
                wait = random.uniform(12, 20)
                print(f"\n  [Timeout attempt {attempt}] Waiting {wait:.0f}s before retry…")
                time.sleep(wait)
            else:
                print(f"\n  [Failed after {MAX_RETRIES} attempts] doc_id={doc_id}")
                return _error_record(doc_id, title, year, "timeout")

        except WebDriverException as exc:
            print(f"\n  [WebDriver error] doc_id={doc_id}: {exc!s:.120}")
            return _error_record(doc_id, title, year, f"webdriver:{exc!s:.80}")

    return _error_record(doc_id, title, year, "max_retries")


def _error_record(doc_id, title, year, reason):
    return {
        "source_doc_id":  doc_id,
        "source_title":   title,
        "year":           year,
        "citation_count": 0,
        "citations":      [],
        "error":          reason,
        "scraped_at":     datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _fmt_dur(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    h, rem = divmod(td.seconds, 3600)
    m, s   = divmod(rem, 60)
    if td.days:
        h += td.days * 24
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape citations for land dispute cases from Indian Kanoon"
    )
    parser.add_argument(
        "--delay", type=float, default=4.0,
        help="Base seconds between requests (default: 4.0)"
    )
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Stop after N cases (0 = all)"
    )
    parser.add_argument(
        "--year-from", type=int, default=0, metavar="YYYY",
        help="Only scrape cases from this year onwards"
    )
    args = parser.parse_args()

    if not LAND_DISPUTES_FILE.exists():
        print(
            f"ERROR: {LAND_DISPUTES_FILE} not found.\n"
            "Run filter_land_disputes.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load land disputes and clean doc_ids (stored as float in CSV)
    df = pd.read_csv(LAND_DISPUTES_FILE)
    df["doc_id"] = df["doc_id"].astype(str).str.replace(r"\.0$", "", regex=True)

    if args.year_from:
        df = df[df["year"] >= args.year_from].reset_index(drop=True)
        print(f"Filtered to year >= {args.year_from}: {len(df):,} cases")

    if args.limit:
        df = df.head(args.limit)

    # Determine remaining work
    done_ids  = load_done_ids(OUTPUT_FILE)
    df_todo   = df[~df["doc_id"].isin(done_ids)].reset_index(drop=True)
    total     = len(df)
    remaining = len(df_todo)
    done      = total - remaining

    avg_secs = args.delay * 1.4 + 1.0   # rough estimate per case
    est_secs = remaining * avg_secs

    print(f"\nLand dispute cases : {total:,}")
    print(f"Already scraped    : {done:,}")
    print(f"To scrape          : {remaining:,}")
    print(f"Est. total time    : {_fmt_dur(est_secs)}")
    print(f"Output file        : {OUTPUT_FILE}")
    print(f"""
┌─ BEFORE CONTINUING ─────────────────────────────────────────────────┐
│ This script connects to YOUR existing Chrome window so that          │
│ Cloudflare sees a real browser (not a bot).                          │
│                                                                       │
│ Start Chrome with the remote debugging port (run this ONCE):         │
│                                                                       │
│   open -na "Google Chrome" --args \\                                  │
│       --remote-debugging-port=9222 --no-first-run                    │
│                                                                       │
│ Then come back here and press Enter.                                  │
│ You can browse normally in that window while scraping runs.          │
└───────────────────────────────────────────────────────────────────────┘"""
    )
    input("Press Enter once Chrome is open with --remote-debugging-port=9222 > ")
    print("\nConnecting to Chrome…\n")

    if remaining == 0:
        print("Nothing to do — all cases already scraped.")
        sys.exit(0)

    driver = setup_driver()

    scraped = 0
    errors  = 0
    total_citations = 0
    start_ts = time.time()

    try:
        with open(OUTPUT_FILE, "a", encoding="utf-8") as out_f:
            for _, row in df_todo.iterrows():
                doc_id = str(row["doc_id"])
                title  = str(row.get("title", ""))
                year   = int(row["year"])

                result = scrape_one(driver, doc_id, title, year)
                out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
                out_f.flush()

                scraped += 1
                total_citations += result["citation_count"]
                if result.get("error"):
                    errors += 1

                # ── Per-case progress line ──
                elapsed = time.time() - start_ts
                rate    = scraped / elapsed if elapsed > 0 else 0
                eta     = (remaining - scraped) / rate if rate > 0 else 0
                status  = f"[err:{result['error'][:12]}]" if result.get("error") else ""
                print(
                    f"  [{scraped:>5}/{remaining}]  "
                    f"{year}  "
                    f"doc:{doc_id:<12}  "
                    f"cites:{result['citation_count']:>3}  "
                    f"ETA:{_fmt_dur(eta)}  "
                    f"{title[:40]:<40}  "
                    f"{status}"
                )

                # ── Milestone summary every 100 ──
                if scraped % 100 == 0:
                    pct = scraped / remaining * 100
                    avg_cites = total_citations / scraped
                    print(
                        f"\n  ── {scraped:,} done ({pct:.1f}%)  "
                        f"avg cites/case: {avg_cites:.1f}  "
                        f"errors: {errors}  "
                        f"elapsed: {_fmt_dur(elapsed)} ──\n"
                    )

                # ── Pacing ──
                if scraped % LONG_PAUSE_EVERY == 0:
                    pause = random.uniform(20, 35)
                    print(f"\n  [Rate-limit pause: {pause:.0f}s — resumable at any time]\n")
                    time.sleep(pause)
                else:
                    time.sleep(random.uniform(args.delay * 0.6, args.delay * 1.6))

    except KeyboardInterrupt:
        print(f"\n\n  Interrupted. {scraped:,} cases scraped this session.")
        print(f"  Re-run the script to continue from where you left off.")

    finally:
        driver.quit()

    elapsed_total = time.time() - start_ts
    print(f"\n{'='*60}")
    print(f"Session complete.")
    print(f"  Cases scraped : {scraped:,}")
    print(f"  Total citations found : {total_citations:,}")
    print(f"  Avg citations/case    : {total_citations/max(scraped,1):.1f}")
    print(f"  Errors                : {errors:,}")
    print(f"  Time elapsed          : {_fmt_dur(elapsed_total)}")
    print(f"  Output                : {OUTPUT_FILE}")

    done_now = load_done_ids(OUTPUT_FILE)
    print(f"\n  Total scraped so far  : {len(done_now):,} / {total:,}")
    if len(done_now) < total:
        print(f"  Re-run to scrape remaining {total - len(done_now):,} cases.")


if __name__ == "__main__":
    main()
