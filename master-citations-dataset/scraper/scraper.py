"""Generic Indian Kanoon browser scraper.

Configurable via ScrapeConfig; runs are launched by thin per-court runner scripts
(e.g. scrape_sc_2024.py). DB schema is intentionally identical to
scraper_sc_2025.py so downstream Phase-2 code stays compatible.

Run from project root:
    DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib python3 indiankanoon/scrape_sc_2024.py
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bs4 import BeautifulSoup  # noqa: E402
from playwright.sync_api import Page, sync_playwright  # noqa: E402

from cloudflare_bypass import bypass as cf_bypass  # noqa: E402


SEMANTIC_LABELS: tuple[str, ...] = (
    "Facts", "Issue", "PetArg", "RespArg",
    "Section", "Precedent", "CDiscource", "Conclusion",
)

# Mapping from Kanoon's data-structure label to a human-readable text_* column
# (kept identical to the original scraper_sc_2025.py's schema so historical
# 2025 data merges cleanly with newer years).
SEMANTIC_TEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Facts",      "text_Facts"),
    ("Issue",      "text_Issues"),
    ("PetArg",     "text_Petitioners_Arguments"),
    ("RespArg",    "text_Respondents_Arguments"),
    ("Section",    "text_Analysis_of_the_law"),
    ("Precedent",  "text_Precedent_Analysis"),
    ("CDiscource", "text_Courts_Reasoning"),
    ("Conclusion", "text_Conclusion"),
)

OUTCOME_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"appeal\s+is\s+allowed", "allowed"),
    (r"appeals?\s+(?:are|is)\s+allowed", "allowed"),
    (r"petition\s+is\s+allowed", "allowed"),
    (r"appeal\s+is\s+dismissed", "dismissed"),
    (r"appeals?\s+(?:are|is)\s+dismissed", "dismissed"),
    (r"petition\s+is\s+dismissed", "dismissed"),
    (r"partly\s+allowed", "partly_allowed"),
    (r"appeal\s+allowed", "allowed"),
    (r"appeal\s+dismissed", "dismissed"),
    (r"(?:set\s+aside\s+and\s+)?remanded", "remanded"),
    (r"disposed\s+of", "disposed_of"),
    (r"set\s+aside", "set_aside"),
    (r"quashed", "quashed"),
)


@dataclass(frozen=True)
class ScrapeConfig:
    name: str
    doctype: str
    date_ranges: tuple[tuple[str, str], ...]
    docsource: str
    output_dir: Path
    cf_coords_path: Path
    delay_between_pages: float = 2.0
    max_cases: int | None = None
    scrape_inbound: bool = True
    text_query: str = ""  # free-text prefix; e.g. '"Order XIIIA"' for SJ filtering


def extract_outcome(text: str) -> str:
    text_lower = text.lower()
    for pattern, label in OUTCOME_PATTERNS:
        if re.search(pattern, text_lower):
            return label
    return "unknown"


def _parse_citetop(soup: BeautifulSoup) -> tuple[int, int]:
    """Extract (cites_count, cited_by_count) from the citetop header span.

    Markup: <span class="citetop">[Cites <a href="...cites:N">M</a>,
                                   Cited by <a href="...citedby:N">K</a>]</span>
    """
    citetop = soup.find("span", class_="citetop")
    if citetop is None:
        return 0, 0
    nums = [int(a.get_text(strip=True)) for a in citetop.find_all("a")
            if a.get_text(strip=True).isdigit()]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return 0, 0


def _clean_cited_name(raw: str) -> str:
    """Normalize a cited case name extracted from a citetext <a> tag.

    Kanoon's source HTML often interleaves list markers and newlines inside
    the link text (e.g. '2)    Selveraj Vs. The State of Tamil Nadu' or
    'Munna Kumar Vs. State of Andhra\\n\\n\\nPradesh'). This function:
      - collapses any whitespace run (spaces, tabs, newlines) to a single space
      - strips a leading 'NN)' or 'NN.' list marker if present
      - trims leading/trailing whitespace
    """
    cleaned = re.sub(r"\s+", " ", raw).strip()
    cleaned = re.sub(r"^\d+[\)\.]\s*", "", cleaned)
    return cleaned.strip()


def _parse_outbound_citations(soup: BeautifulSoup) -> list[dict]:
    """Extract one row per <span class="citetext"> with sentiment + cited case info."""
    rows: list[dict] = []
    for span in soup.find_all("span", class_="citetext"):
        docid = span.get("data-docid")
        if not docid:
            continue
        try:
            cited_tid = int(docid)
        except ValueError:
            continue
        cited_name = ""
        for a in span.find_all("a", href=True):
            if f"/doc/{docid}/" in a["href"]:
                cited_name = _clean_cited_name(a.get_text())
                break
        rows.append({
            "span_id": span.get("id", ""),
            "cited_tid": cited_tid,
            "cited_name": cited_name,
            "sentiment": span.get("data-sentiment", ""),
            "span_text": span.get_text(separator=" ", strip=True),
        })
    return rows


def parse_case_html(html: str, tid: int) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.find("h2", class_="doc_title")
    title = title_el.get_text(strip=True) if title_el else ""

    author_el = soup.find("h3", class_="doc_author")
    judge = author_el.get_text(strip=True) if author_el else ""
    judge = re.sub(r"^Author:\s*", "", judge)

    bench_el = soup.find("h3", class_="doc_bench")
    bench = bench_el.get_text(strip=True) if bench_el else ""

    pre_blocks = soup.find_all("pre")
    header_text = "\n".join(pre.get_text() for pre in pre_blocks[:3])

    case_number = ""
    case_num_match = re.search(
        r"(?:CIVIL APPEAL|CRIMINAL APPEAL|WRIT PETITION|SPECIAL LEAVE PETITION|"
        r"TRANSFER PETITION|REVIEW PETITION|SLP\s*\([^)]+\)|W\.P\.\s*\([^)]+\)|"
        r"C\.A\.|Crl\.A\.)[^\n]*(?:NO\.|No\.)[^\n]*\d+[^\n]*(?:OF|of)\s*\d{4}",
        header_text, re.IGNORECASE,
    )
    if case_num_match:
        case_number = case_num_match.group(0).strip()

    citation = ""
    cite_match = re.search(r"\d{4}\s+INSC\s+\d+", header_text)
    if cite_match:
        citation = cite_match.group(0).strip()

    jurisdiction = ""
    for jtype in ("CIVIL ORIGINAL", "CRIMINAL APPELLATE", "CIVIL APPELLATE",
                  "CRIMINAL ORIGINAL", "APPELLATE", "ORIGINAL"):
        if jtype in header_text.upper():
            jurisdiction = jtype + " JURISDICTION"
            break

    publishdate = ""
    date_match = re.search(r"on\s+(\d{1,2}\s+\w+,?\s*\d{4})", title)
    if date_match:
        publishdate = date_match.group(1).strip()

    petitioner, respondent = "", ""
    if " vs " in title.lower():
        parts = re.split(r"\s+vs\.?\s+", title, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            petitioner = parts[0].strip()
            respondent = re.sub(r"\s+on\s+\d+\s+\w+,?\s*\d{4}$", "", parts[1]).strip()

    semantic_counts = {
        label: len(soup.find_all("p", {"data-structure": label}))
        for label in SEMANTIC_LABELS
    }
    semantic_texts: dict[str, str] = {}
    for label, column in SEMANTIC_TEXT_COLUMNS:
        paragraphs = soup.find_all("p", {"data-structure": label})
        semantic_texts[column] = "\n\n".join(
            p.get_text(separator=" ", strip=True) for p in paragraphs
        )

    conclusion_paragraphs = soup.find_all("p", {"data-structure": "Conclusion"})
    conclusion_text = " ".join(p.get_text() for p in conclusion_paragraphs)
    all_paragraphs = soup.find_all("p")
    last_text = " ".join(p.get_text() for p in all_paragraphs[-20:])
    outcome = extract_outcome(conclusion_text + " " + last_text)

    statute_count = len(soup.find_all("a", href=re.compile(r"^/doc/\d+/")))

    cites_count, cited_by_count = _parse_citetop(soup)
    outbound_citations = _parse_outbound_citations(soup)
    sentiment_counts = Counter(c["sentiment"] for c in outbound_citations)

    doc_div = soup.find("div", class_="judgments") or soup.find("div", class_="akoma-ntoso")
    if doc_div:
        doc_html = str(doc_div)
        doc_text = doc_div.get_text(separator=" ", strip=True)
    else:
        doc_html = ""
        doc_text = re.sub(
            r"\s+", " ",
            " ".join(el.get_text() for el in soup.find_all(("pre", "p"))),
        ).strip()

    return {
        "tid": tid,
        "title": title,
        "publishdate": publishdate,
        "judge": judge,
        "bench": bench,
        "case_number": case_number,
        "citation": citation,
        "jurisdiction": jurisdiction,
        "petitioner": petitioner,
        "respondent": respondent,
        "outcome": outcome,
        "num_statutes_cited": statute_count,
        "cites_count": cites_count,
        "cited_by_count": cited_by_count,
        "cite_pos": sentiment_counts.get("Pos", 0),
        "cite_neg": sentiment_counts.get("Neg", 0),
        "cite_party": sentiment_counts.get("PARTY", 0),
        "cite_neutral": sentiment_counts.get("Neutral", 0),
        **{f"semantic_{label}": semantic_counts[label] for label in SEMANTIC_LABELS},
        **semantic_texts,
        "outbound_citations": outbound_citations,
        "doc_text": doc_text,
        "doc_html": doc_html,
    }


class BrowserScraper:
    def __init__(self, config: ScrapeConfig) -> None:
        self.cfg = config
        self.output_dir = config.output_dir
        self.output_dir.mkdir(exist_ok=True)
        self.tids_file = self.output_dir / f"{config.name}_tids.json"
        self.db_path = self.output_dir / f"{config.name}_cases.db"
        self.csv_path = self.output_dir / f"{config.name}_cases.csv"

    def run(self) -> None:
        print("=" * 60)
        print(f"  Indian Kanoon Scraper — {self.cfg.name}")
        print(f"  doctype={self.cfg.doctype}  docsource={self.cfg.docsource}")
        if self.cfg.max_cases is not None:
            print(f"  TEST MODE: max_cases={self.cfg.max_cases}")
        if self.cfg.cf_coords_path.exists():
            print(f"  CF coords: loaded from {self.cfg.cf_coords_path}")
        else:
            print(f"  CF coords: not yet set — will calibrate on first CF block")
        print("=" * 60)

        conn = self._init_db()

        print("\n[Step 1] Connect to Chrome via CDP (port 9222)")
        print("  Quit Chrome (Cmd+Q) and run in a new terminal:")
        print()
        print('  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\')
        print("    --remote-debugging-port=9222 \\")
        print("    --user-data-dir=/tmp/chrome-debug \\")
        print("    --window-size=1280,900")
        print()
        print("  Navigate to https://indiankanoon.org/ and pass CF once.")
        input("  Press ENTER when ready... ")

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()

            self._ensure_cleared(page)

            print("\n[Step 2] Collecting tids from search...")
            all_docs = self._collect_tids(page)
            print(f"  Total tids: {len(all_docs)}")

            if self.cfg.max_cases is not None:
                all_docs = all_docs[: self.cfg.max_cases]
                print(f"  Limited to first {self.cfg.max_cases} for test")

            already_scraped = self._get_scraped_tids(conn)
            remaining = [d for d in all_docs if d["tid"] not in already_scraped]
            print(f"\n[Step 3] {len(already_scraped)} done, {len(remaining)} remaining")

            if not remaining:
                print("  Nothing to scrape!")
                browser.close()
                self._export_csv(conn)
                conn.close()
                return

            failed = self._scrape_docs(page, remaining, conn)
            browser.close()

        final_count = len(self._get_scraped_tids(conn))
        print(f"\n{'=' * 60}")
        print(f"  Done! {final_count} cases in {self.db_path}")
        if failed:
            print(f"  Failed ({len(failed)}): {failed[:20]}...")
        self._export_csv(conn)
        conn.close()

    def _ensure_cleared(self, page: Page) -> None:
        result = cf_bypass(page, self.cfg.cf_coords_path)
        if result == "still_blocked":
            raise RuntimeError("Cloudflare could not be cleared.")

    def _safe_page_content(self, page: Page, retries: int = 3) -> str:
        """page.content() that retries on Playwright's mid-navigation race."""
        from playwright.sync_api import Error as PlaywrightError
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                return page.content()
            except PlaywrightError as e:
                last_err = e
                print(f"    page.content retry {attempt+1}/{retries}: {e}")
                page.wait_for_timeout(2000)
        assert last_err is not None
        raise last_err

    def _load_partial_tids_cache(self) -> tuple[list[dict], int]:
        """Return (existing_tids, completed_range_count) for resume support.

        Supports both the new wrapped format and the legacy flat-list format
        (which is treated as fully complete).
        """
        if not self.tids_file.exists():
            return [], 0
        with self.tids_file.open() as f:
            data = json.load(f)
        if isinstance(data, list):
            return data, len(self.cfg.date_ranges)
        if isinstance(data, dict) and "tids" in data:
            return data["tids"], int(data.get("completed_range_count", 0))
        return [], 0

    def _save_tids_cache(self, tids: list[dict], completed_range_count: int) -> None:
        with self.tids_file.open("w") as f:
            json.dump({
                "completed_range_count": completed_range_count,
                "tids": tids,
            }, f, indent=2)

    def _collect_tids(self, page: Page) -> list[dict]:
        all_docs, completed = self._load_partial_tids_cache()
        total_ranges = len(self.cfg.date_ranges)

        if completed >= total_ranges and all_docs:
            print(f"  Loaded {len(all_docs)} cached tids from {self.tids_file}")
            return all_docs

        seen_tids: set[int] = {d["tid"] for d in all_docs}
        cap = self.cfg.max_cases
        if completed > 0:
            print(
                f"  Resuming tid collection: {len(all_docs)} cached, "
                f"{completed}/{total_ranges} ranges already complete"
            )

        for idx, (from_d, to_d) in enumerate(self.cfg.date_ranges):
            if idx < completed:
                continue
            if cap is not None and len(all_docs) >= cap:
                print(f"\n  Reached max_cases={cap}; stopping search early")
                break
            print(f"\n  Range {idx+1}/{total_ranges}: {from_d} → {to_d}")
            pagenum = 0
            while True:
                if cap is not None and len(all_docs) >= cap:
                    break
                query_parts = []
                if self.cfg.text_query.strip():
                    query_parts.append(self.cfg.text_query.strip())
                query_parts.append(f"doctypes: {self.cfg.doctype}")
                query_parts.append(f"fromdate: {from_d}")
                query_parts.append(f"todate: {to_d}")
                query = " ".join(query_parts)
                url = (
                    f"https://indiankanoon.org/search/"
                    f"?formInput={query}&pagenum={pagenum}"
                )
                page.goto(url, timeout=30000)
                page.wait_for_timeout(2000)
                self._ensure_cleared(page)

                html = self._safe_page_content(page)
                soup = BeautifulSoup(html, "html.parser")

                results = soup.select("div.result_title a[href*='/doc/']")
                if not results:
                    results = soup.select("a[href*='/docfragment/']")
                if not results:
                    break

                count_before = len(all_docs)
                for a_tag in results:
                    href = a_tag.get("href", "")
                    tid_match = re.search(r"/(?:doc|docfragment)/(\d+)/", href)
                    if not tid_match:
                        continue
                    tid = int(tid_match.group(1))
                    if tid in seen_tids:
                        continue
                    seen_tids.add(tid)
                    all_docs.append({
                        "tid": tid,
                        "title": a_tag.get_text(strip=True),
                        "publishdate": "",
                        "docsource": self.cfg.docsource,
                    })

                new_count = len(all_docs) - count_before
                print(f"    Page {pagenum}: +{new_count} (total {len(all_docs)})")
                if new_count == 0:
                    break

                next_link = soup.find("a", string="Next")
                if not next_link:
                    break
                pagenum += 1
                time.sleep(self.cfg.delay_between_pages)

            self._save_tids_cache(all_docs, idx + 1)

        self._save_tids_cache(all_docs, total_ranges)
        print(f"\n  Saved {len(all_docs)} tids to {self.tids_file}")
        return all_docs

    def _scrape_docs(
        self,
        page: Page,
        docs: list[dict],
        conn: sqlite3.Connection,
    ) -> list[int]:
        failed: list[int] = []
        for i, doc_info in enumerate(docs):
            tid = doc_info["tid"]
            title = doc_info.get("title", "")[:60]
            print(f"  [{i+1}/{len(docs)}] tid={tid}  {title}")

            for attempt in range(3):
                try:
                    url = f"https://indiankanoon.org/doc/{tid}/"
                    page.evaluate(f'window.location.href = "{url}"')
                    page.wait_for_timeout(3000)
                    self._ensure_cleared(page)

                    case_html = page.content()
                    if "doc_title" not in case_html:
                        print(f"    Not a case page (attempt {attempt+1}/3), retrying...")
                        time.sleep(3)
                        continue

                    case = parse_case_html(case_html, tid)
                    self._save_case(conn, case)
                    print(f"    OK: outcome={case['outcome']}, judge={case['judge'][:30]} "
                          f"out={len(case.get('outbound_citations', []))} "
                          f"in_count={case['cited_by_count']}")

                    if self.cfg.scrape_inbound and case["cited_by_count"] > 0:
                        inbound = self._fetch_inbound_citations(page, tid)
                        self._save_inbound(conn, tid, inbound)
                        print(f"    Inbound fetched: {len(inbound)} citing cases")
                    break
                except Exception as e:
                    print(f"    ERROR (attempt {attempt+1}/3): {e}")
                    if attempt == 2:
                        failed.append(tid)
                    time.sleep(3)

            time.sleep(self.cfg.delay_between_pages)
        return failed

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                tid             INTEGER PRIMARY KEY,
                title           TEXT,
                publishdate     TEXT,
                judge           TEXT,
                bench           TEXT,
                case_number     TEXT,
                citation        TEXT,
                jurisdiction    TEXT,
                petitioner      TEXT,
                respondent      TEXT,
                docsource       TEXT,
                outcome         TEXT,
                num_statutes_cited INTEGER,
                semantic_Facts       INTEGER,
                semantic_Issue       INTEGER,
                semantic_PetArg      INTEGER,
                semantic_RespArg     INTEGER,
                semantic_Section     INTEGER,
                semantic_Precedent   INTEGER,
                semantic_CDiscource  INTEGER,
                semantic_Conclusion  INTEGER,
                doc_text        TEXT,
                doc_html        TEXT
            )
        """)

        # Idempotent migration for existing DBs: add citation aggregate columns
        # and the 8 text_* per-semantic-label columns.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(cases)")}
        migrations: list[tuple[str, str]] = [
            ("cites_count",    "INTEGER"),
            ("cited_by_count", "INTEGER"),
            ("cite_pos",       "INTEGER"),
            ("cite_neg",       "INTEGER"),
            ("cite_party",     "INTEGER"),
            ("cite_neutral",   "INTEGER"),
        ]
        for _, column in SEMANTIC_TEXT_COLUMNS:
            migrations.append((column, "TEXT"))
        for col, sql_type in migrations:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE cases ADD COLUMN {col} {sql_type}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS outbound_citations (
                case_tid     INTEGER NOT NULL,
                span_id      TEXT,
                cited_tid    INTEGER NOT NULL,
                cited_name   TEXT,
                sentiment    TEXT,
                span_text    TEXT,
                PRIMARY KEY (case_tid, span_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbound_cited "
            "ON outbound_citations(cited_tid)"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS inbound_citations (
                case_tid       INTEGER NOT NULL,
                citing_tid     INTEGER NOT NULL,
                citing_name    TEXT,
                citing_date    TEXT,
                citing_court   TEXT,
                PRIMARY KEY (case_tid, citing_tid)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbound_citing "
            "ON inbound_citations(citing_tid)"
        )

        conn.commit()
        return conn

    def _save_case(self, conn: sqlite3.Connection, case: dict) -> None:
        record = {**case, "docsource": self.cfg.docsource}
        conn.execute("""
            INSERT OR REPLACE INTO cases (
                tid, title, publishdate, judge, bench, case_number, citation,
                jurisdiction, petitioner, respondent, docsource, outcome,
                num_statutes_cited,
                semantic_Facts, semantic_Issue, semantic_PetArg, semantic_RespArg,
                semantic_Section, semantic_Precedent, semantic_CDiscource,
                semantic_Conclusion,
                doc_text, doc_html,
                cites_count, cited_by_count,
                cite_pos, cite_neg, cite_party, cite_neutral,
                text_Facts, text_Issues,
                text_Petitioners_Arguments, text_Respondents_Arguments,
                text_Analysis_of_the_law, text_Precedent_Analysis,
                text_Courts_Reasoning, text_Conclusion
            ) VALUES (
                :tid, :title, :publishdate, :judge, :bench, :case_number, :citation,
                :jurisdiction, :petitioner, :respondent, :docsource, :outcome,
                :num_statutes_cited,
                :semantic_Facts, :semantic_Issue, :semantic_PetArg, :semantic_RespArg,
                :semantic_Section, :semantic_Precedent, :semantic_CDiscource,
                :semantic_Conclusion,
                :doc_text, :doc_html,
                :cites_count, :cited_by_count,
                :cite_pos, :cite_neg, :cite_party, :cite_neutral,
                :text_Facts, :text_Issues,
                :text_Petitioners_Arguments, :text_Respondents_Arguments,
                :text_Analysis_of_the_law, :text_Precedent_Analysis,
                :text_Courts_Reasoning, :text_Conclusion
            )
        """, record)

        tid = case["tid"]
        conn.execute("DELETE FROM outbound_citations WHERE case_tid = ?", (tid,))
        conn.executemany("""
            INSERT INTO outbound_citations (
                case_tid, span_id, cited_tid, cited_name, sentiment, span_text
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (tid, c["span_id"], c["cited_tid"], c["cited_name"],
             c["sentiment"], c["span_text"])
            for c in case.get("outbound_citations", [])
        ])

        conn.commit()

    def _fetch_inbound_citations(self, page: Page, tid: int) -> list[dict]:
        """Paginate /search/?formInput=citedby:<tid> and return citing case rows.

        Uses page.goto rather than window.location.href = ... because CF treats
        JS-assigned navigation as bot-like on /search/ endpoints. Each result is
        <article class="result"> with the citing-case link in <h4 class="result_title">
        (href may be /doc/N/ or /docfragment/N/...). Court is in
        <div class="hlbottom"><span class="docsource">.
        """
        rows: list[dict] = []
        seen: set[int] = set()
        pagenum = 0
        while True:
            url = (
                "https://indiankanoon.org/search/"
                f"?formInput=citedby:{tid}&pagenum={pagenum}"
            )
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            self._ensure_cleared(page)

            soup = BeautifulSoup(self._safe_page_content(page), "html.parser")
            articles = soup.select("article.result")
            if not articles:
                break

            new_in_page = 0
            for art in articles:
                title_link = art.select_one("h4.result_title a")
                if title_link is None:
                    continue
                m = re.search(r"/(?:doc|docfragment)/(\d+)/", title_link.get("href", ""))
                if not m:
                    continue
                citing_tid = int(m.group(1))
                if citing_tid == tid or citing_tid in seen:
                    continue
                seen.add(citing_tid)

                title = title_link.get_text(strip=True)
                date_match = re.search(r"on\s+(\d{1,2}\s+\w+,?\s*\d{4})", title)
                citing_date = date_match.group(1).strip() if date_match else ""
                citing_name = re.sub(
                    r"\s+on\s+\d+\s+\w+,?\s*\d{4}\s*$", "", title
                ).strip()

                court_el = (
                    art.select_one("div.hlbottom span.docsource")
                    or art.select_one(".docsource")
                )
                citing_court = court_el.get_text(strip=True) if court_el else ""

                rows.append({
                    "citing_tid": citing_tid,
                    "citing_name": citing_name,
                    "citing_date": citing_date,
                    "citing_court": citing_court,
                })
                new_in_page += 1

            if new_in_page == 0:
                break
            if soup.find("a", string="Next") is None:
                break

            pagenum += 1
            time.sleep(self.cfg.delay_between_pages)

        return rows

    def _save_inbound(
        self,
        conn: sqlite3.Connection,
        case_tid: int,
        inbound: list[dict],
    ) -> None:
        conn.execute("DELETE FROM inbound_citations WHERE case_tid = ?", (case_tid,))
        conn.executemany("""
            INSERT INTO inbound_citations (
                case_tid, citing_tid, citing_name, citing_date, citing_court
            ) VALUES (?, ?, ?, ?, ?)
        """, [
            (case_tid, c["citing_tid"], c["citing_name"],
             c["citing_date"], c["citing_court"])
            for c in inbound
        ])
        conn.commit()

    def _get_scraped_tids(self, conn: sqlite3.Connection) -> set[int]:
        rows = conn.execute("SELECT tid FROM cases").fetchall()
        return {r[0] for r in rows}

    def _export_csv(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("""
            SELECT tid, title, publishdate, judge, bench, case_number, citation,
                   jurisdiction, petitioner, respondent, docsource, outcome,
                   num_statutes_cited,
                   semantic_Facts, semantic_Issue, semantic_PetArg, semantic_RespArg,
                   semantic_Section, semantic_Precedent, semantic_CDiscource,
                   semantic_Conclusion
            FROM cases ORDER BY publishdate
        """)
        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchall()

        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            writer.writerows(rows)
        print(f"\nCSV exported: {self.csv_path} ({len(rows)} rows)")
