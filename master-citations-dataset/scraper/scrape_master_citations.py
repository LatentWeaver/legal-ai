"""Runner: scrape the complete Cites + Cited-by lists for a bracket of master cases.

For each master case in land_property_dispute_cases.csv (default bracket
6001-6750, 1-indexed inclusive) the runner:

  1. Opens the /doc/ page through the existing BrowserScraper pipeline, so ALL
     the rich data your scraper already extracts is preserved in the DB
     (full text, semantic paragraphs, outcome, citetext sentiment, the
     [Cites N, Cited by M] header counts).

  2. Follows the *Cites* number  -> /search/?formInput=cites:<tid>   -> the COMPLETE
     list of cases this master case cites (matches the header count, not just the
     ~handful discussed inline in the body).

  3. Follows the *Cited by* number -> /search/?formInput=citedby:<tid> -> the
     COMPLETE list of cases that cite this master case.

  4. Emits an Anshul-aligned JSON file: each record has `precedents` (Cites) and
     `cited_by` (Cited by), every entry in his {name, doc_id, url} shape.

Outputs:
  output/master_citations_cases.db               -> rich schema + citation_search table
  output/master_citations_<start>_<end>.json     -> Anshul-format Cites + Cited-by

Run from project root (Chrome on CDP port 9222; pass CF manually when prompted):
    python3 indiankanoon/scrape_master_citations.py
    python3 indiankanoon/scrape_master_citations.py --start 6001 --end 6750
    python3 indiankanoon/scrape_master_citations.py --max-cases 3      # quick test
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bs4 import BeautifulSoup  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from scraper import BrowserScraper, ScrapeConfig, parse_case_html  # noqa: E402

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
DEFAULT_CSV = ROOT / "land_property_dispute_cases.csv"

DIRECTIONS = ("cites", "citedby")  # cites = outbound, citedby = inbound

# Heuristic to exclude statute-section pages from the related-case work-list
# (Kanoon's "Cites" list mixes cited cases with statute provisions).
STATUTE_FILTER = (
    "ref_name NOT LIKE 'Section %' AND ref_name NOT LIKE 'Article %' "
    "AND ref_name NOT LIKE '%Act%' AND ref_name NOT LIKE '%Constitution%' "
    "AND ref_name NOT LIKE '%Rules%'"
)

# Post-navigation settle wait (ms); overridable via --nav-wait. Gives Cloudflare's
# JS a moment to challenge/redirect before we read the page. Set in main().
_NAV_WAIT_MS = 3000


def extract_tid(link: str) -> int | None:
    m = re.search(r"/doc/(\d+)/", link or "")
    return int(m.group(1)) if m else None


def clean_csv_name(raw: str) -> str:
    """Turn 'Nanjappa_vs_State_..._on_24_July_2015' into readable text."""
    return re.sub(r"\s+", " ", (raw or "").replace("_", " ")).strip()


def load_bracket(csv_path: Path, start: int, end: int) -> list[dict]:
    """Return master-case rows for 1-indexed inclusive [start, end]."""
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for offset, row in enumerate(rows[start - 1 : end]):
        tid = extract_tid(row.get("link", ""))
        if tid is None:
            print(f"  [skip] case #{start + offset} has no parseable tid: {row.get('link')!r}")
            continue
        out.append({
            "case_number": start + offset,
            "csv_name": row.get("case", ""),
            "year": row.get("year", ""),
            "tid": tid,
        })
    return out


# ── schema helpers ────────────────────────────────────────────────────

def ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS citation_search (
            case_tid   INTEGER NOT NULL,
            direction  TEXT NOT NULL,          -- 'cites' | 'citedby'
            ref_tid    INTEGER NOT NULL,
            ref_name   TEXT,
            ref_date   TEXT,
            ref_court  TEXT,
            PRIMARY KEY (case_tid, direction, ref_tid)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_status (
            case_tid      INTEGER NOT NULL,
            direction     TEXT NOT NULL,
            fetched_count INTEGER,
            PRIMARY KEY (case_tid, direction)
        )
    """)
    conn.commit()


# ── citation search fetch (cites OR citedby) ──────────────────────────

def _goto_with_retry(scraper, page, url: str, retries: int = 4) -> None:
    """Navigate + clear CF, retrying transient Cloudflare aborts/timeouts.

    net::ERR_ABORTED (CF challenge intercepting the navigation), CF 52x
    'connection timed out', and net::ERR_TIMED_OUT usually mean Indian Kanoon's
    CF rate-limited us or the origin was momentarily slow. After an abort the
    tab often sits on the CF page, so we try to clear it, then back off and retry
    instead of abandoning the whole fetch.
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(_NAV_WAIT_MS)
            scraper._ensure_cleared(page)
            return
        except Exception as e:
            last_err = e
            try:
                scraper._ensure_cleared(page)  # tab may be on a CF page now
            except Exception:
                pass
            backoff = 15 * (attempt + 1)
            print(f"      goto retry {attempt+1}/{retries} after {backoff}s: {e}")
            page.wait_for_timeout(backoff * 1000)
    assert last_err is not None
    raise last_err


def fetch_citation_search(scraper, page, tid: int, direction: str) -> list[dict]:
    """Paginate /search/?formInput=<direction>:<tid> and return ref-case rows.

    direction: 'cites'  -> cases this master case cites (outbound)
               'citedby'-> cases that cite this master case (inbound)
    Result pages use <article class="result"> with the ref-case link in
    <h4 class="result_title"> and the court in .hlbottom .docsource.
    """
    rows: list[dict] = []
    seen: set[int] = set()
    pagenum = 0
    while True:
        url = (
            "https://indiankanoon.org/search/"
            f"?formInput={direction}:{tid}&pagenum={pagenum}"
        )
        _goto_with_retry(scraper, page, url)

        soup = BeautifulSoup(scraper._safe_page_content(page), "html.parser")
        articles = soup.select("article.result")
        if not articles:
            break

        new_in_page = 0
        for art in articles:
            link = art.select_one("h4.result_title a")
            if link is None:
                continue
            m = re.search(r"/(?:doc|docfragment)/(\d+)/", link.get("href", ""))
            if not m:
                continue
            ref_tid = int(m.group(1))
            if ref_tid == tid or ref_tid in seen:
                continue
            seen.add(ref_tid)

            title = link.get_text(strip=True)
            dm = re.search(r"on\s+(\d{1,2}\s+\w+,?\s*\d{4})", title)
            ref_date = dm.group(1).strip() if dm else ""
            ref_name = re.sub(r"\s+on\s+\d+\s+\w+,?\s*\d{4}\s*$", "", title).strip()
            court_el = (
                art.select_one("div.hlbottom span.docsource")
                or art.select_one(".docsource")
            )
            ref_court = court_el.get_text(strip=True) if court_el else ""

            rows.append({
                "ref_tid": ref_tid,
                "ref_name": ref_name,
                "ref_date": ref_date,
                "ref_court": ref_court,
            })
            new_in_page += 1

        if new_in_page == 0:
            break
        if soup.find("a", string="Next") is None:
            break
        pagenum += 1
        time.sleep(scraper.cfg.delay_between_pages)

    return rows


def save_citation_search(conn, case_tid: int, direction: str, rows: list[dict]) -> None:
    conn.execute(
        "DELETE FROM citation_search WHERE case_tid = ? AND direction = ?",
        (case_tid, direction),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO citation_search "
        "(case_tid, direction, ref_tid, ref_name, ref_date, ref_court) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(case_tid, direction, r["ref_tid"], r["ref_name"], r["ref_date"], r["ref_court"])
         for r in rows],
    )
    conn.execute(
        "INSERT OR REPLACE INTO search_status (case_tid, direction, fetched_count) "
        "VALUES (?, ?, ?)",
        (case_tid, direction, len(rows)),
    )
    conn.commit()


# ── Anshul JSON export ────────────────────────────────────────────────

def _list_for(conn, tid: int, direction: str) -> list[dict]:
    out = []
    for ref_tid, ref_name in conn.execute(
        "SELECT ref_tid, ref_name FROM citation_search "
        "WHERE case_tid = ? AND direction = ? ORDER BY ref_tid",
        (tid, direction),
    ).fetchall():
        out.append({
            "name": ref_name or "",
            "doc_id": str(ref_tid),
            "url": f"https://indiankanoon.org/doc/{ref_tid}/",
        })
    return out


def to_anshul_record(conn, tid: int, csv_name: str) -> dict:
    title_row = conn.execute("SELECT title FROM cases WHERE tid = ?", (tid,)).fetchone()
    source_name = (title_row[0] if title_row and title_row[0] else clean_csv_name(csv_name))
    precedents = _list_for(conn, tid, "cites")
    cited_by = _list_for(conn, tid, "citedby")
    return {
        "source_name": source_name,
        "source_url": f"https://indiankanoon.org/doc/{tid}/",
        "precedent_count": len(precedents),
        "precedents": precedents,
        "cited_by_count": len(cited_by),
        "cited_by": cited_by,
    }


def export_anshul_json(conn, ordered_cases: list[dict], json_path: Path) -> None:
    done = {r[0] for r in conn.execute(
        "SELECT DISTINCT case_tid FROM search_status").fetchall()}
    records = [
        to_anshul_record(conn, c["tid"], c["csv_name"])
        for c in ordered_cases if c["tid"] in done
    ]
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


# ── main ──────────────────────────────────────────────────────────────

def scrape_doc(scraper, page, conn, tid: int) -> str:
    """Doc-only scrape (content, no citation searches). Returns ok|notcase|error."""
    try:
        _goto_with_retry(scraper, page, f"https://indiankanoon.org/doc/{tid}/")
        html = scraper._safe_page_content(page)
        if "doc_title" not in html:
            return "notcase"
        case = parse_case_html(html, tid)
        scraper._save_case(conn, case)
        return "ok"
    except Exception as e:
        print(f"      doc ERROR: {e}")
        return "error"


def run_content_mode(args) -> None:
    """Scrape ONLY the content of related cases (work-list from citation_search).

    Does not fetch or store the related cases' own citations — pure content.
    """
    if not args.source_db.exists():
        print(f"ERROR: source DB not found: {args.source_db}")
        sys.exit(1)

    src = sqlite3.connect(f"file:{args.source_db}?mode=ro", uri=True)
    dir_clause = "" if args.direction == "both" else f"AND direction = '{args.direction}'"
    worklist = src.execute(
        f"SELECT ref_tid, MIN(ref_name) FROM citation_search "
        f"WHERE {STATUTE_FILTER} {dir_clause} "
        f"AND ref_tid NOT IN (SELECT tid FROM cases) "
        f"GROUP BY ref_tid ORDER BY ref_tid"
    ).fetchall()
    src.close()
    print(f"  Content mode | direction={args.direction} | statutes excluded")
    print(f"  Work-list: {len(worklist)} unique related cases")

    name = args.name or "related_cases"
    config = ScrapeConfig(
        name=name, doctype="", date_ranges=(),
        docsource="Indian Kanoon (related case content)",
        output_dir=OUTPUT, cf_coords_path=ROOT / "cf_coords.json",
        delay_between_pages=args.delay,
    )
    scraper = BrowserScraper(config)
    conn = scraper._init_db()

    already = scraper._get_scraped_tids(conn)
    todo = [(t, n) for t, n in worklist if t not in already]
    if args.limit is not None:
        todo = todo[: args.limit]
    print(f"  {len(already)} already scraped, {len(todo)} to do")
    if not todo:
        print("  Nothing to do.")
        conn.close()
        return

    print("\n[Step 1] Connect Chrome via CDP (port 9222). Pass CF manually if prompted.")
    input("  Press ENTER when ready... ")

    ok = notcase = err = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        scraper._ensure_cleared(page)

        consecutive_suspect = 0
        for i, (tid, nm) in enumerate(todo):
            print(f"  [{i+1}/{len(todo)}] tid={tid}  {(nm or '')[:46]}")
            result = scrape_doc(scraper, page, conn, tid)
            if result == "ok":
                ok += 1
                print("      doc saved")
                consecutive_suspect = 0
            else:
                if result == "notcase":
                    notcase += 1
                    print("      not a case page (CF? skipped)")
                else:
                    err += 1
                consecutive_suspect += 1

            if consecutive_suspect >= 3:
                print("\n  ⚠️  3 in a row failed — Cloudflare is likely blocking.")
                print("  Reload https://indiankanoon.org/ in Chrome, pass the challenge.")
                input("  Then press ENTER to resume (Ctrl+C to stop; resume is safe)... ")
                try:
                    scraper._ensure_cleared(page)
                except Exception:
                    pass
                consecutive_suspect = 0

            if (i + 1) % 50 == 0:
                print(f"    [progress] {i+1}/{len(todo)}  ok={ok} notcase={notcase} err={err}")
            time.sleep(config.delay_between_pages)

        browser.close()

    print(f"\n  Done. ok={ok} notcase={notcase} err={err}")
    print(f"  Content DB: {scraper.db_path}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape master-case citations, or related-case content.")
    parser.add_argument("--mode", choices=["master", "content"], default="master",
                        help="master: CSV bracket -> doc+Cites+CitedBy; content: related-case content only")
    # master-mode args
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--start", type=int, default=6001, help="1-indexed inclusive")
    parser.add_argument("--end", type=int, default=6750, help="1-indexed inclusive")
    parser.add_argument("--max-cases", type=int, default=None, help="cap for testing")
    parser.add_argument("--skip-doc", action="store_true",
                        help="master mode: skip the rich /doc/ scrape; only fetch Cites + Cited-by")
    # content-mode args
    parser.add_argument("--source-db", type=Path,
                        default=OUTPUT / "master_citations_cases.db",
                        help="content mode: DB holding citation_search (the work-list)")
    parser.add_argument("--direction", choices=["cites", "citedby", "both"], default="cites",
                        help="content mode: which related cases to scrape (default cites = upstream precedents)")
    parser.add_argument("--name", default=None,
                        help="content mode: output DB name (default 'related_cases')")
    parser.add_argument("--limit", type=int, default=None, help="content mode: cap work-list size")
    # timing knobs (both modes)
    parser.add_argument("--delay", type=float, default=3.0,
                        help="seconds between requests (default 3.0; lower=faster but trips CF sooner)")
    parser.add_argument("--nav-wait", type=float, default=3.0,
                        help="seconds to wait after each navigation for CF to settle (default 3.0)")
    args = parser.parse_args()

    global _NAV_WAIT_MS
    _NAV_WAIT_MS = int(args.nav_wait * 1000)

    if args.mode == "content":
        run_content_mode(args)
        return

    if not args.csv.exists():
        print(f"ERROR: CSV not found: {args.csv}")
        sys.exit(1)

    cases = load_bracket(args.csv, args.start, args.end)
    if args.max_cases is not None:
        cases = cases[: args.max_cases]
    print(f"  Loaded {len(cases)} master cases (bracket {args.start}-{args.end})")

    json_path = OUTPUT / f"master_citations_{args.start}_{args.end}.json"

    config = ScrapeConfig(
        name="master_citations",
        doctype="",
        date_ranges=(),
        docsource="Indian Kanoon (land/property master case)",
        output_dir=OUTPUT,
        cf_coords_path=ROOT / "cf_coords.json",
        delay_between_pages=args.delay,  # gentler on CF than the 2s default
    )
    scraper = BrowserScraper(config)
    conn = scraper._init_db()
    ensure_tables(conn)

    scraped_docs = scraper._get_scraped_tids(conn)
    status_done = {(r[0], r[1]) for r in conn.execute(
        "SELECT case_tid, direction FROM search_status").fetchall()}

    def fully_done(c) -> bool:
        doc_ok = args.skip_doc or c["tid"] in scraped_docs
        search_ok = all((c["tid"], d) in status_done for d in DIRECTIONS)
        return doc_ok and search_ok

    todo = [c for c in cases if not fully_done(c)]
    print(f"  {len(cases) - len(todo)} already complete, {len(todo)} to process")
    if not todo:
        export_anshul_json(conn, cases, json_path)
        print(f"  JSON regenerated: {json_path}")
        conn.close()
        return

    print("\n[Step 1] Connect Chrome via CDP (port 9222). Pass CF manually if prompted.")
    input("  Press ENTER when ready... ")

    failed: list[tuple[int, str]] = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        scraper._ensure_cleared(page)

        consecutive_suspect = 0
        for i, c in enumerate(todo):
            tid = c["tid"]
            print(f"  [{i+1}/{len(todo)}] case #{c['case_number']} tid={tid}  {clean_csv_name(c['csv_name'])[:46]}")
            suspect = False

            # Phase A: rich /doc/ scrape (kept; gives title, text, sentiment, header counts)
            if not args.skip_doc and tid not in scraped_docs:
                try:
                    _goto_with_retry(scraper, page, f"https://indiankanoon.org/doc/{tid}/")
                    html = scraper._safe_page_content(page)
                    if "doc_title" in html:
                        case = parse_case_html(html, tid)
                        scraper._save_case(conn, case)
                        scraped_docs.add(tid)
                        print(f"      doc: outcome={case['outcome']} "
                              f"header=[Cites {case['cites_count']}, Cited by {case['cited_by_count']}]")
                    else:
                        print("      doc: not a case page (CF? skipped rich data)")
                        suspect = True
                except Exception as e:
                    print(f"      doc ERROR: {e}")
                    suspect = True

            # Phase B/C: complete Cites and Cited-by lists via the clickable numbers
            for direction in DIRECTIONS:
                if (tid, direction) in status_done:
                    continue
                try:
                    rows = fetch_citation_search(scraper, page, tid, direction)
                    save_citation_search(conn, tid, direction, rows)
                    status_done.add((tid, direction))
                    print(f"      {direction}: {len(rows)} cases")
                except Exception as e:
                    print(f"      {direction} ERROR: {e}")
                    failed.append((tid, direction))
                    suspect = True

            # Circuit breaker: repeated suspect cases => CF is blocking this session.
            consecutive_suspect = consecutive_suspect + 1 if suspect else 0
            if consecutive_suspect >= 3:
                print("\n  ⚠️  3 cases in a row failed — Cloudflare is likely blocking this session.")
                print("  In the Chrome window: reload https://indiankanoon.org/, pass the")
                print("  challenge/timeout page, and confirm a normal IK page loads.")
                input("  Then press ENTER to resume (or Ctrl+C to stop; resume is safe)... ")
                try:
                    scraper._ensure_cleared(page)
                except Exception:
                    pass
                consecutive_suspect = 0

            if (i + 1) % 25 == 0:
                export_anshul_json(conn, cases, json_path)
                print(f"    [checkpoint] JSON refreshed at {i+1}/{len(todo)}")
            time.sleep(config.delay_between_pages)

        browser.close()

    export_anshul_json(conn, cases, json_path)
    complete = sum(1 for c in cases if fully_done(c))
    print(f"\n  Done. {complete}/{len(cases)} cases fully processed.")
    if failed:
        print(f"  Failed fetches ({len(failed)}): {failed[:20]}")
    print(f"  Rich DB    : {scraper.db_path}")
    print(f"  Anshul JSON: {json_path}")
    conn.close()


if __name__ == "__main__":
    main()
