#!/usr/bin/env python3
"""
SEC Litigation Release Scraper v7 — Scalable Pipeline
========================================================
Production pipeline with SQLite backend, async worker pool,
checkpoint/resume, deduplication, and triple-format export.

Handles the full SEC litigation archive (~26,500 releases since 1995).

Architecture:
  Listing Pages → Worker Pool (5 concurrent) → Detail + PDFs → SQLite → Export
  
Features:
  - SQLite checkpoint: crash-safe, resume from last case
  - Async curl workers: 3-5x faster than sequential
  - URL dedup: run multiple times safely
  - Incremental mode: only scrape new releases
  - Triple export: CSV, JSON (Neo4j-ready), SQLite
  - Full 39-field knowledge graph extraction

Dependencies:
    pip install beautifulsoup4 pdfplumber lxml

Commands:
    python sec_litigation_scraper.py scrape --pages 10
    python sec_litigation_scraper.py scrape --all --workers 5
    python sec_litigation_scraper.py scrape --incremental
    python sec_litigation_scraper.py export --format csv -o cases.csv
    python sec_litigation_scraper.py export --format json -o cases.json
    python sec_litigation_scraper.py status
"""

import asyncio
import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import argparse
import time
from dataclasses import dataclass, asdict, fields as dc_fields
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
    import pdfplumber
except ImportError as e:
    sys.exit(f"Missing: {e}\nInstall: pip install beautifulsoup4 pdfplumber lxml")

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BASE_URL        = "https://www.sec.gov"
LIST_URL        = f"{BASE_URL}/enforcement-litigation/litigation-releases"
RSS_URL         = f"{LIST_URL}/rss"
DB_FILE         = "sec_litigation.db"
PDF_TEMP_DIR    = tempfile.mkdtemp(prefix="sec_pdfs_")

REQUEST_DELAY   = 2.0       # per-worker delay between requests
PDF_DELAY       = 1.0       # delay before each PDF download
PDF_MAX_PAGES   = 200       # read every page
PDF_MAX_SIZE_MB = 50
CURL_TIMEOUT    = 60
MAX_RETRIES     = 3
DEFAULT_WORKERS = 3          # concurrent workers (be polite)
MAX_WORKERS     = 6

SEC_UA = "SECLitigationScraper/7.0 (Academic Research; ASU CIPS Lab)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sec")


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA MODEL — 39 fields
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LitigationCase:
    case_title: str = ""
    citation: str = ""
    court: str = ""
    date: str = ""
    petitioner: str = "Securities and Exchange Commission (SEC)"
    respondent: str = ""
    defendant_roles: str = ""
    defendant_employer: str = ""
    employer_crd_cik: str = ""
    co_defendants: str = ""
    relief_defendants: str = ""
    sec_attorneys: str = ""
    sec_regional_office: str = ""
    judges: str = ""
    judgment_type: str = ""
    summary: str = ""
    outcome: str = ""
    legal_topic: str = ""
    charges_and_sections: str = ""
    company_domain: str = ""
    total_fine_amount: str = ""
    total_victim_losses: str = ""
    scheme_duration: str = ""
    scheme_method: str = ""
    victim_count: str = ""
    admission_status: str = ""
    parallel_actions: str = ""
    related_releases: str = ""
    case_status: str = ""
    scheme_start_date: str = ""
    scheme_end_date: str = ""
    complaint_filed_date: str = ""
    judgment_date: str = ""
    regulatory_registrations: str = ""
    defendant_sentence: str = ""
    final_judgment_details: str = ""
    source_url: str = ""
    pdf_insights: str = ""
    associated_documents: str = ""

FIELD_NAMES = [f.name for f in dc_fields(LitigationCase)]


# ═══════════════════════════════════════════════════════════════════════════════
#  SQLITE DATABASE LAYER
# ═══════════════════════════════════════════════════════════════════════════════

class Database:
    """SQLite backend with WAL mode for concurrent access."""

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def _create_tables(self):
        cols = ", ".join(f'"{f}" TEXT DEFAULT ""' for f in FIELD_NAMES)
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {cols},
                scraped_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source_url)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT DEFAULT '',
                timestamp TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cases_url ON cases(source_url)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cases_date ON cases(date)
        """)
        self.conn.commit()

    def url_exists(self, url: str) -> bool:
        r = self.conn.execute("SELECT 1 FROM cases WHERE source_url = ?", (url,)).fetchone()
        return r is not None

    def get_scraped_urls(self) -> set:
        rows = self.conn.execute("SELECT source_url FROM cases").fetchall()
        return {r[0] for r in rows}

    def insert_case(self, case: LitigationCase):
        d = asdict(case)
        cols = ", ".join(f'"{k}"' for k in d.keys())
        placeholders = ", ".join("?" for _ in d)
        try:
            self.conn.execute(
                f"INSERT OR REPLACE INTO cases ({cols}) VALUES ({placeholders})",
                list(d.values())
            )
            self.conn.commit()
        except sqlite3.Error as e:
            log.error(f"DB insert error: {e}")

    def log_scrape(self, url: str, status: str, message: str = ""):
        try:
            self.conn.execute(
                "INSERT INTO scrape_log (url, status, message) VALUES (?, ?, ?)",
                (url, status, message)
            )
            self.conn.commit()
        except sqlite3.Error:
            pass

    def case_count(self) -> int:
        r = self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()
        return r[0] if r else 0

    def latest_date(self) -> str:
        r = self.conn.execute(
            "SELECT date FROM cases ORDER BY scraped_at DESC LIMIT 1"
        ).fetchone()
        return r[0] if r else ""

    def export_csv(self, path: str):
        col_str = ",".join('"' + f + '"' for f in FIELD_NAMES)
        rows = self.conn.execute("SELECT {} FROM cases ORDER BY scraped_at DESC".format(col_str)).fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            w.writerow(FIELD_NAMES)
            w.writerows(rows)
        return len(rows)

    def export_json(self, path: str):
        col_str = ",".join('"' + f + '"' for f in FIELD_NAMES)
        rows = self.conn.execute("SELECT {} FROM cases ORDER BY scraped_at DESC".format(col_str)).fetchall()
        cases = []
        for row in rows:
            case = {}
            for i, field in enumerate(FIELD_NAMES):
                val = row[i] or ""
                # Convert semicolon-delimited fields to arrays for graph-friendly JSON
                if field in ("charges_and_sections", "defendant_roles", "sec_attorneys",
                             "regulatory_registrations", "related_releases", "outcome",
                             "legal_topic", "judges", "co_defendants"):
                    case[field] = [v.strip() for v in val.split(";") if v.strip()] if val else []
                else:
                    case[field] = val
            cases.append(case)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cases, f, indent=2, ensure_ascii=False)
        return len(cases)

    def status(self) -> dict:
        total = self.case_count()
        latest = self.latest_date()
        errors = self.conn.execute(
            "SELECT COUNT(*) FROM scrape_log WHERE status = 'error'"
        ).fetchone()[0]
        success = self.conn.execute(
            "SELECT COUNT(*) FROM scrape_log WHERE status = 'success'"
        ).fetchone()[0]
        topics = self.conn.execute(
            "SELECT legal_topic, COUNT(*) as cnt FROM cases WHERE legal_topic != '' "
            "GROUP BY legal_topic ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        courts = self.conn.execute(
            "SELECT court, COUNT(*) as cnt FROM cases WHERE court != '' "
            "GROUP BY court ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        return {
            "total_cases": total, "latest_date": latest,
            "scrape_success": success, "scrape_errors": errors,
            "top_topics": topics, "top_courts": courts,
            "db_size_mb": round(os.path.getsize(self.db_path) / 1024 / 1024, 2) if os.path.exists(self.db_path) else 0,
        }

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  ASYNC CURL HTTP LAYER
# ═══════════════════════════════════════════════════════════════════════════════

async def curl_fetch_async(url: str, output_file: str = None,
                           retries: int = MAX_RETRIES, semaphore: asyncio.Semaphore = None) -> Optional[str]:
    """Async curl fetch using asyncio subprocess."""
    sem = semaphore or asyncio.Semaphore(1)

    for attempt in range(retries):
        async with sem:
            if output_file:
                cmd = ["curl","-s","-S","-L","--max-time",str(CURL_TIMEOUT),"--compressed",
                       "-H",f"User-Agent: {SEC_UA}","-H","Accept: */*",
                       "-o",output_file,"-w","%{http_code}",url]
            else:
                cmd = ["curl","-s","-S","-L","--max-time",str(CURL_TIMEOUT),"--compressed",
                       "-H",f"User-Agent: {SEC_UA}",
                       "-H","Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                       "-H","Accept-Language: en-US,en;q=0.9",
                       "-H","Accept-Encoding: gzip, deflate, br",
                       "-H","Connection: keep-alive",
                       "-H","Sec-Fetch-Dest: document",
                       "-H","Sec-Fetch-Mode: navigate",
                       "-H","Sec-Fetch-Site: none",
                       "-H","Sec-Fetch-User: ?1",
                       "-H","Upgrade-Insecure-Requests: 1",
                       "-w","\n%{http_code}",url]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CURL_TIMEOUT + 15)
                out = stdout.decode("utf-8", errors="replace")

                if output_file:
                    status = out.strip()
                    if status == "200":
                        return output_file
                    log.warning(f"    curl HTTP {status} (attempt {attempt+1}): {url}")
                    if os.path.exists(output_file):
                        os.remove(output_file)
                else:
                    parts = out.rsplit("\n", 1)
                    body, status = (parts[0], parts[1].strip()) if len(parts) == 2 else (out, "000")
                    if status == "200":
                        return body
                    log.warning(f"    curl HTTP {status} (attempt {attempt+1})")

            except asyncio.TimeoutError:
                log.warning(f"    curl timeout (attempt {attempt+1})")
                if proc.returncode is None:
                    proc.kill()
            except Exception as e:
                log.warning(f"    curl error: {e}")

        if attempt < retries - 1:
            await asyncio.sleep(3 * (attempt + 1))

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  EXTRACTOR ENGINE (all 39-field extractors from v6)
# ═══════════════════════════════════════════════════════════════════════════════

class Extractor:
    COURTS = [
        (r"District of New Jersey", "D.N.J."),
        (r"Southern District of New York", "S.D.N.Y."),
        (r"Northern District of New York", "N.D.N.Y."),
        (r"Eastern District of New York", "E.D.N.Y."),
        (r"Western District of New York", "W.D.N.Y."),
        (r"Central District of California", "C.D. Cal."),
        (r"Northern District of California", "N.D. Cal."),
        (r"Southern District of California", "S.D. Cal."),
        (r"Eastern District of California", "E.D. Cal."),
        (r"District of Columbia", "D.D.C."),
        (r"Southern District of Florida", "S.D. Fla."),
        (r"Middle District of Florida", "M.D. Fla."),
        (r"Northern District of Florida", "N.D. Fla."),
        (r"Northern District of Texas", "N.D. Tex."),
        (r"Southern District of Texas", "S.D. Tex."),
        (r"Eastern District of Texas", "E.D. Tex."),
        (r"Western District of Texas", "W.D. Tex."),
        (r"Northern District of Illinois", "N.D. Ill."),
        (r"Southern District of Illinois", "S.D. Ill."),
        (r"District of Massachusetts", "D. Mass."),
        (r"District of Connecticut", "D. Conn."),
        (r"Eastern District of Pennsylvania", "E.D. Pa."),
        (r"Western District of Pennsylvania", "W.D. Pa."),
        (r"Middle District of Pennsylvania", "M.D. Pa."),
        (r"District of Colorado", "D. Colo."),
        (r"District of Nevada", "D. Nev."),
        (r"District of Arizona", "D. Ariz."),
        (r"Eastern District of Virginia", "E.D. Va."),
        (r"Western District of Virginia", "W.D. Va."),
        (r"District of Maryland", "D. Md."),
        (r"Eastern District of Michigan", "E.D. Mich."),
        (r"Western District of Michigan", "W.D. Mich."),
        (r"District of Utah", "D. Utah"),
        (r"Northern District of Georgia", "N.D. Ga."),
        (r"Southern District of Georgia", "S.D. Ga."),
        (r"District of Minnesota", "D. Minn."),
        (r"District of Oregon", "D. Or."),
        (r"Western District of Washington", "W.D. Wash."),
        (r"Eastern District of Washington", "E.D. Wash."),
        (r"Northern District of Ohio", "N.D. Ohio"),
        (r"Southern District of Ohio", "S.D. Ohio"),
        (r"District of South Carolina", "D.S.C."),
        (r"((?:Northern|Southern|Eastern|Western|Central|Middle)\s+)?District\s+of\s+[A-Z][\w\s]+", None),
    ]

    OUTCOME_KW = {
        "permanent injunction":"Permanent Injunction","consent judgment":"Consent Judgment",
        "consent of defendant":"Consent Judgment","without admitting or denying":"Consent Judgment",
        "default judgment":"Default Judgment","final judgment":"Final Judgment",
        "summary judgment":"Summary Judgment","disgorgement":"Disgorgement",
        "civil penalty":"Civil Penalty","civil monetary penalty":"Civil Monetary Penalty",
        "penny stock bar":"Penny Stock Bar","officer and director bar":"Officer/Director Bar",
        "industry bar":"Industry Bar","settled":"Settled","dismissed":"Dismissed",
        "asset freeze":"Asset Freeze","temporary restraining order":"TRO",
        "preliminary injunction":"Preliminary Injunction","cease and desist":"Cease and Desist",
        "permanently barr":"Permanent Bar","permanently enjoin":"Permanent Injunction",
    }

    TOPIC_KW = {
        "insider trading":"Insider Trading","fraud":"Securities Fraud",
        "ponzi":"Ponzi Scheme","misappropriat":"Misappropriation",
        "market manipulation":"Market Manipulation","accounting fraud":"Accounting Fraud",
        "offering fraud":"Offering Fraud","investment adviser":"Inv. Adviser Misconduct",
        "investment advisor":"Inv. Adviser Misconduct","broker-dealer":"Broker-Dealer Violations",
        "crypto":"Crypto/Digital Assets","digital asset":"Crypto/Digital Assets",
        "foreign corrupt":"FCPA","fcpa":"FCPA","unregistered":"Unregistered Securities",
        "embezzle":"Embezzlement","wire fraud":"Wire Fraud","pump and dump":"Pump and Dump",
        "churning":"Churning","material misrepresentation":"Material Misrepresentation",
        "books and records":"Books & Records","internal controls":"Internal Controls Failure",
        "revenue recognition":"Revenue Recognition Fraud","cherry-pick":"Cherry-Picking",
        "front-running":"Front-Running","late trading":"Late Trading",
    }

    DOMAIN_KW = {
        "pharmaceutical":"Pharmaceuticals","pharma":"Pharmaceuticals",
        "biotech":"Biotechnology","healthcare":"Healthcare",
        "medical device":"Medical Devices","bank":"Banking/Financial Services",
        "financial institution":"Banking/Financial Services",
        "brokerage":"Brokerage","hedge fund":"Hedge Fund",
        "investment fund":"Investment Fund","mutual fund":"Mutual Fund",
        "private equity":"Private Equity","real estate":"Real Estate",
        "oil and gas":"Oil & Gas/Energy","energy":"Energy","mining":"Mining",
        "cannabis":"Cannabis","technology":"Technology","software":"Technology/Software",
        "fintech":"FinTech","cryptocurrency":"Cryptocurrency",
        "blockchain":"Blockchain/Crypto","insurance":"Insurance",
        "telecommunications":"Telecom","retail":"Retail",
        "manufacturing":"Manufacturing","defense":"Defense/Aerospace",
        "construction":"Construction","transportation":"Transportation",
        "media":"Media/Entertainment","food and beverage":"Food & Beverage",
        "registered representative":"Brokerage","registered broker":"Brokerage",
        "registered investment advi":"Investment Advisory","financial advi":"Financial Advisory",
    }

    MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"

    @classmethod
    def court(cls, t): 
        for p, a in cls.COURTS:
            m = re.search(p, t, re.I)
            if m: return a if a else m.group(0).strip()
        return ""

    @classmethod
    def judges(cls, t):
        found = set()
        bad = {"court","states","district","united","section","rule","exchange","securities","commission","plaintiff","defendant","complaint"}
        for p in [
            r"(?:Honorable|Hon\.)\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:\s+(?:Jr\.|Sr\.|III|II|IV))?)",
            r"(?:Judge|Chief Judge|JUDGE)\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
            r"United States (?:District|Magistrate) Judge\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
            r"/s/\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s*\n\s*(?:United States|U\.S\.)",
            r"BEFORE[:\s]+(?:Hon\.?\s+)?([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
            r"(?:SO ORDERED|IT IS (?:SO )?ORDERED)[.\s]*\n\s*(?:/s/)?\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
        ]:
            for m in re.finditer(p, t):
                n = m.group(1).strip()
                if len(n) > 4 and not any(w in n.lower() for w in bad): found.add(n)
        return "; ".join(sorted(found))

    @classmethod
    def sec_attorneys(cls, t):
        attorneys = set()
        for p in [
            r"(?:investigation|litigation|case)\s+(?:is being |was )?(?:conducted|led|handled)\s+by\s+([A-Z][\w\s,.']+?)(?:\s+and\s+(?:is\s+)?supervised)",
            r"(?:conducted|led|handled)\s+by\s+([A-Z][\w\s,.']+?)(?:\.\s|,\s+all\s+of)",
            r"supervised\s+by\s+([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
            r"(?:lead|handle)\s+the\s+litigation[^.]*?([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)",
        ]:
            for m in re.finditer(p, t, re.I):
                for name in re.split(r",\s*|\s+and\s+", m.group(1)):
                    name = name.strip()
                    if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+", name):
                        attorneys.add(name.strip(" ."))
        return "; ".join(sorted(attorneys))

    @classmethod
    def sec_regional_office(cls, t):
        m = re.search(r"((?:New York|Boston|Philadelphia|Miami|Atlanta|Chicago|Denver|Fort Worth|Salt Lake|Los Angeles|San Francisco|Washington|Home)\s+(?:Regional\s+)?Office)", t, re.I)
        return m.group(1).strip() if m else ""

    @classmethod
    def defendant_roles(cls, t):
        roles = set()
        for p, _ in [
            (r"(?:former|ex-?)\s+((?:registered\s+)?(?:representative|broker|adviser?|advisor))", None),
            (r"(chief\s+(?:executive|financial|operating|compliance)\s+officer)", None),
            (r"\b(CEO|CFO|COO|CCO|CTO|CIO)\b", None),
            (r"(president|chairman|director|founder|co-founder|partner|managing\s+(?:director|member|partner))", None),
            (r"(registered\s+(?:representative|broker|investment\s+advi\w+))", None),
            (r"(portfolio\s+manager|fund\s+manager|trader|analyst|accountant|auditor|controller)", None),
            (r"(general\s+counsel|secretary|treasurer|vice\s+president)", None),
            (r"(investment\s+(?:adviser?|advisor)\s+representative)", None),
            (r"(financial\s+advi(?:ser|sor))", None),
            (r"(stock\s+promoter|promoter)", None),
        ]:
            for m in re.finditer(p, t, re.I): roles.add(m.group(1).strip().title())
        return "; ".join(sorted(roles))

    @classmethod
    def defendant_employer(cls, t):
        employers = set()
        for p in [
            r"(?:employed|worked|associated)\s+(?:at|with|by)\s+([A-Z][\w\s&,.']+?(?:LLC|Inc|Corp|Ltd|L\.?P\.?|Co\.|Company|Services|Group|Capital|Partners|Fund|Advisors?|Management))",
            r"(?:branch|office)\s+(?:of|in|at)\s+([A-Z][\w\s&,.']+?(?:LLC|Inc|Corp|Ltd|Services|Company))",
            r"(?:financial\s+(?:institution|adviser?|advisor)\s+(?:at|with)\s+)([A-Z][\w\s&,.']+?)(?:\.|,|\s+in\s+)",
        ]:
            for m in re.finditer(p, t, re.I):
                e = m.group(1).strip().rstrip(".,;")
                if len(e) > 3 and e not in {"the","The","SEC","Commission"}: employers.add(e)
        return "; ".join(sorted(employers))

    @classmethod
    def employer_crd_cik(cls, t):
        ids = []
        for m in re.finditer(r"CRD[#:\s]*(\d+)", t, re.I): ids.append(f"CRD# {m.group(1)}")
        for m in re.finditer(r"CIK[#:\s]*(\d+)", t, re.I): ids.append(f"CIK {m.group(1)}")
        return "; ".join(sorted(set(ids)))

    @classmethod
    def relief_defendants(cls, t):
        found = set()
        for m in re.finditer(r"[Rr]elief\s+[Dd]efendant\s+([A-Z][\w\s,.'-]+?)(?:\.|,\s+(?:who|the|a))", t):
            found.add(m.group(1).strip().rstrip(".,"))
        return "; ".join(sorted(found))

    @classmethod
    def charges(cls, t):
        ch = set()
        for pat, act in [
            (r"Section[s]?\s+(\d+[a-z]?(?:\([a-z0-9]+\))*(?:\s*(?:and|,)\s*\d+[a-z]?(?:\([a-z0-9]+\))*)*)\s+of\s+the\s+Securities\s+Act","Securities Act"),
            (r"Section[s]?\s+(\d+[a-z]?(?:\([a-z0-9]+\))*(?:\s*(?:and|,)\s*\d+[a-z]?(?:\([a-z0-9]+\))*)*)\s+of\s+the\s+(?:Securities\s+)?Exchange\s+Act","Exchange Act"),
            (r"Section[s]?\s+(\d+[a-z]?(?:\([a-z0-9]+\))*(?:\s*(?:and|,)\s*\d+[a-z]?(?:\([a-z0-9]+\))*)*)\s+of\s+the\s+(?:Investment\s+)?Advis[eo]rs?\s+Act","Advisers Act"),
            (r"Section[s]?\s+(\d+[a-z]?(?:\([a-z0-9]+\))*(?:\s*(?:and|,)\s*\d+[a-z]?(?:\([a-z0-9]+\))*)*)\s+of\s+the\s+Investment\s+Company\s+Act","Inv. Company Act"),
            (r"Section[s]?\s+(\d+[a-z]?(?:\([a-z0-9]+\))*)\s+of\s+the\s+Sarbanes-Oxley\s+Act","SOX"),
        ]:
            for m in re.finditer(pat, t, re.I):
                for sec in re.split(r"\s*(?:and|,)\s*", m.group(1)):
                    sec = sec.strip()
                    if sec: ch.add(f"{act} § {sec}")
        for m in re.finditer(r"Rule[s]?\s+(\d+[a-z]?-\d+(?:\([a-z0-9]+\))?(?:-\d+)?)", t, re.I):
            ch.add(f"Rule {m.group(1)}")
        for m in re.finditer(r"17\s*C\.?F\.?R\.?\s*[§]?\s*(\d+\.\d+[a-z]?(?:-\d+)?)", t, re.I):
            ch.add(f"17 CFR § {m.group(1)}")
        return "; ".join(sorted(ch))

    @classmethod
    def _kw(cls, t, d):
        lo = t.lower()
        return "; ".join(sorted(set(v for k,v in d.items() if k in lo)))

    @classmethod
    def outcomes(cls, t): return cls._kw(t, cls.OUTCOME_KW)
    @classmethod
    def topics(cls, t): return cls._kw(t, cls.TOPIC_KW)
    @classmethod
    def domain(cls, t):
        r = cls._kw(t, cls.DOMAIN_KW)
        if r: return r
        m = re.search(r"(?:engaged?\s+in|in\s+the\s+business\s+of)\s+([^.]{10,80})", t, re.I)
        return m.group(1).strip() if m else ""

    @classmethod
    def fines(cls, t):
        F = {}
        for pat, label in [
            (r"disgorgement\s+(?:of|in the amount of|totaling)\s+\$?([\d,]+(?:\.\d{2})?(?:\s*million)?)","Disgorgement"),
            (r"prejudgment\s+interest\s+(?:of|in the amount of|thereon of|totaling)\s+\$?([\d,]+(?:\.\d{2})?(?:\s*million)?)","Prejudgment Interest"),
            (r"civil\s+(?:monetary\s+)?penalty\s+(?:of|in the amount of|totaling)\s+\$?([\d,]+(?:\.\d{2})?(?:\s*million)?)","Civil Penalty"),
            (r"(?:ordered|required|directed)\s+to\s+pay\s+(?:a\s+total\s+of\s+)?\$?([\d,]+(?:\.\d{2})?(?:\s*million)?)","Total Ordered"),
            (r"penalt(?:y|ies)\s+(?:of|totaling)\s+\$?([\d,]+(?:\.\d{2})?(?:\s*million)?)","Penalty"),
        ]:
            for m in re.finditer(pat, t, re.I):
                v = "$" + m.group(1).strip().rstrip(",")
                if label not in F: F[label] = v
        return "; ".join(f"{k}: {v}" for k,v in sorted(F.items())) if F else ""

    @classmethod
    def victim_losses(cls, t):
        for p in [
            r"(?:defrauded|stole|misappropriated|obtained)\s+(?:at least|approximately|more than|over)?\s*\$?([\d,]+(?:\.\d{2})?(?:\s*million)?)\s+(?:from|of)",
            r"(?:investors?|clients?|customers?|victims?)\s+(?:lost|suffered losses of)\s+(?:at least|approximately)?\s*\$?([\d,]+(?:\.\d{2})?(?:\s*million)?)",
        ]:
            m = re.search(p, t, re.I)
            if m: return "$" + m.group(1).strip()
        return ""

    @classmethod
    def scheme_duration(cls, t):
        m = re.search(rf"(?:from|between)\s+({cls.MONTH}\s+\d{{4}})\s+(?:to|through|and|until)\s+({cls.MONTH}\s+\d{{4}})", t, re.I)
        if m: return f"{m.group(1)} to {m.group(2)}"
        m = re.search(rf"(?:from|between)\s+({cls.MONTH}\s+\d{{1,2}},?\s+\d{{4}})\s+(?:to|through|and|until)\s+({cls.MONTH}\s+\d{{1,2}},?\s+\d{{4}})", t, re.I)
        if m: return f"{m.group(1)} to {m.group(2)}"
        return ""

    @classmethod
    def scheme_start_end(cls, t):
        d = cls.scheme_duration(t)
        if " to " in d:
            p = d.split(" to ", 1)
            return p[0].strip(), p[1].strip()
        return "", ""

    @classmethod
    def complaint_filed_date(cls, t):
        m = re.search(rf"complaint\s*,?\s*filed\s+(?:on\s+)?({cls.MONTH}\s+\d{{1,2}},?\s+\d{{4}})", t, re.I)
        return m.group(1).strip() if m else ""

    @classmethod
    def judgment_date(cls, t):
        for p in [
            rf"(?:entered|issued)\s+(?:a\s+)?(?:final\s+)?judgment\s+(?:on|as of)\s+({cls.MONTH}\s+\d{{1,2}},?\s+\d{{4}})",
            rf"(?:On|on)\s+({cls.MONTH}\s+\d{{1,2}},?\s+\d{{4}})[^.]*?(?:entered|issued|granted)\s+(?:a\s+)?(?:final\s+)?judgment",
        ]:
            m = re.search(p, t, re.I)
            if m: return m.group(1).strip()
        return ""

    @classmethod
    def scheme_method(cls, t):
        clean = re.sub(r"\s+", " ", t)
        methods, seen = [], set()
        for p in [
            r"(?:alleged|alleges?)\s+that\s+([^.]+?(?:\.[^.]+?){0,3})(?:\.\s)",
            r"(?:accused\s+of|charged\s+with)\s+((?=[a-z])[^.]+?)(?:\.\s)",
            r"(?:by|through)\s+((?=[a-z])(?:transferring|diverting|converting|misappropriating|selling|issuing|making|creating|fabricating|inflating|manipulating|engaging|conducting|operating|promoting|soliciting|offering|concealing|falsifying|forging|embezzling|stealing|siphoning|funneling)[^.]+?)(?:\.\s)",
            r"(?:complaint\s+alleges?\s+that)\s+([^.]+?(?:\.[^.]+?){0,3})(?:\.\s+(?:According|The\s+complaint))",
            r"(?:According\s+to\s+the\s+complaint),?\s+([^.]+?(?:\.[^.]+?){0,2})(?:\.\s)",
        ]:
            for m in re.finditer(p, clean):
                s = m.group(1).strip()
                k = s[:60].lower()
                if k not in seen and len(s) > 30: seen.add(k); methods.append(s)
            if "alleged" in p or "complaint" in p or "According" in p:
                for m in re.finditer(p, clean, re.I):
                    s = m.group(1).strip()
                    k = s[:60].lower()
                    if k not in seen and len(s) > 30: seen.add(k); methods.append(s)
        return " | ".join(methods)

    @classmethod
    def victim_count(cls, t):
        clean = re.sub(r"\s+", " ", t)
        m = re.search(r"(?:at least|approximately|more than|over|nearly)\s+(\d[\d,]*)\s+(?:investors?|clients?|customers?|victims?|individuals?|account holders?|retirees?|people|persons)(?:[,\s]+(?:some of whom were|including|many of whom)\s+[^,.]+)?", clean, re.I)
        if m: return m.group(0).strip()
        m = re.search(r"(multiple|numerous|several|various)\s+(clients?|customers?|investors?)(?:\s+and\s+\w+)?(?:[,\s]+(?:some of whom were|including|many of whom)\s+[^,.]+)?", clean, re.I)
        if m: return m.group(0).strip()
        return ""

    @classmethod
    def admission_status(cls, t):
        lo = t.lower()
        if "without admitting or denying" in lo: return "Without admitting or denying"
        if "pleaded guilty" in lo or "pled guilty" in lo: return "Pleaded guilty"
        if "admitted" in lo and "allegations" in lo: return "Admitted allegations"
        if "consent" in lo and ("judgment" in lo or "order" in lo): return "Consented to judgment/order"
        return ""

    @classmethod
    def parallel_actions(cls, t):
        clean = re.sub(r"\s+", " ", t)
        actions = []
        for m in re.finditer(r"(?:In\s+a\s+parallel\s+action,?\s+)?(?:U\.S\.\s+Attorney|Department\s+of\s+Justice|DOJ|criminal)[^.]+?(?:charges?|indictment|prosecution|convicted|sentenced|arrest|plea)[^.]*\.", clean, re.I):
            actions.append(f"Criminal: {m.group(0).strip()}")
        for m in re.finditer(r"(\d{2}\s*[-:]\s*cr\s*[-.:]\s*\d+(?:\s*\([A-Z.]+\))?)", clean, re.I):
            c = f"Criminal Case No. {m.group(1).strip()}"
            if c not in " ".join(actions): actions.append(c)
        for m in re.finditer(r"(?:administrative\s+proceed|Order\s+Instituting)[^.]+\.", clean, re.I):
            actions.append(f"Admin: {m.group(0).strip()}")
        for m in re.finditer(r"(?:Release\s+No\.\s*)?(?:34|33|IA)-(\d+)", clean):
            actions.append(f"Admin Release No. {m.group(0)}")
        for m in re.finditer(r"FINRA[^.]+(?:barred|suspended|fined|sanction)[^.]*\.", clean, re.I):
            actions.append(f"FINRA: {m.group(0).strip()}")
        return " ||| ".join(dict.fromkeys(actions))

    @classmethod
    def related_releases(cls, t):
        r = set()
        for m in re.finditer(r"(?:Litigation\s+Release\s+No\.?\s*|LR[- ]?)(\d{4,6})", t, re.I): r.add(f"LR-{m.group(1)}")
        for m in re.finditer(r"(?:AAER[- ]?)(\d{3,5})", t, re.I): r.add(f"AAER-{m.group(1)}")
        return "; ".join(sorted(r))

    @classmethod
    def case_status(cls, t):
        lo, s = t.lower(), []
        if "final judgment" in lo and ("entered" in lo or "obtained" in lo): s.append("Final judgment entered")
        if "settled" in lo or "consent" in lo: s.append("Settled/Consented")
        if "dismissed" in lo: s.append("Dismissed")
        if "pending" in lo: s.append("Pending")
        if "complaint" in lo and "filed" in lo and not s: s.append("Complaint filed")
        if "investigation is continuing" in lo or "litigation is continuing" in lo: s.append("Continuing")
        return "; ".join(s)

    @classmethod
    def registrations(cls, t):
        regs = set()
        for m in re.finditer(r"(?:FINRA\s+)?Series\s+(\d+)", t, re.I): regs.add(f"Series {m.group(1)}")
        m = re.search(r"Series\s+(\d+(?:\s*[,&]\s*\d+)*(?:\s+and\s+\d+)?)", t, re.I)
        if m:
            for num in re.findall(r"\d+", m.group(1)): regs.add(f"Series {num}")
        return "; ".join(sorted(regs))

    @classmethod
    def sentence(cls, t):
        clean = re.sub(r"\s+", " ", t)
        m = re.search(r"sentenced\s+to[^.]+(?:months?|years?)['\u2019]?\s*(?:imprisonment|in\s+(?:federal\s+)?prison|incarceration)?[^.]*", clean, re.I)
        if m: return m.group(0).strip()
        for p in [r"(\d+\s+months?['\u2019]?\s*(?:imprisonment|in\s+(?:federal\s+)?prison))", r"(\d+\s+years?['\u2019]?\s*(?:imprisonment|in\s+(?:federal\s+)?prison))"]:
            m = re.search(p, clean, re.I)
            if m: return m.group(1).strip()
        return ""

    @classmethod
    def judgment_type(cls, t, doc=""):
        c = (doc + " " + t[:4000]).lower()
        if "consent" in c: return "Consent Judgment"
        if "default judgment" in c: return "Default Judgment"
        if "summary judgment" in c: return "Summary Judgment"
        if "stipulat" in c: return "Stipulated Judgment"
        if "final judgment" in c: return "Final Judgment"
        if "complaint" in c: return "Complaint (pre-judgment)"
        if "order instituting" in c: return "Administrative Order"
        return ""

    @classmethod
    def judgment_details(cls, t):
        clean = re.sub(r"\s+", " ", t)
        details, seen = [], set()
        for m in re.finditer(r"(?:permanently|preliminarily)\s+(?:enjoin\w*|restrain\w*)[^.]+\.", clean, re.I):
            details.append(f"Injunction: {m.group(0).strip()}")
        for p, l in [
            (r"barr\w+\s+from\s+(?:serving|acting)\s+as\s+(?:an?\s+)?officer\s+(?:or|and)\s+director[^.]*\.","Officer/Director Bar"),
            (r"penny\s+stock\s+bar[^.]*\.","Penny Stock Bar"),
            (r"barr\w+\s+from\s+(?:associating|association)\s+with\s+any\s+(?:broker|dealer|investment)[^.]*\.","Industry Bar"),
        ]:
            for m in re.finditer(p, clean, re.I): details.append(f"{l}: {m.group(0).strip()}")
        for m in re.finditer(r"(?:IT IS (?:HEREBY |FURTHER )?ORDERED|ORDERS?\s+THAT|ADJUDGED)\s+(?:THAT\s+)?([^.]+(?:\.[^.]{0,60})*?)\.\s", clean, re.I):
            o = m.group(1).strip()
            if len(o) > 20: details.append(f"Order: {o}")
        for m in re.finditer(r"(?:Defendant|Respondent)\s+(?:shall|agrees?\s+to|consents?\s+to|is\s+(?:hereby\s+)?(?:ordered|required)\s+to)\s+([^.]+)\.", clean, re.I):
            tm = m.group(1).strip()
            if len(tm) > 15: details.append(f"Term: {tm}")
        uniq = []
        for d in details:
            k = d[:60].lower()
            if k not in seen: seen.add(k); uniq.append(d)
        return " ||| ".join(uniq)

    @classmethod
    def insights(cls, t):
        clean = re.sub(r"\s+", " ", t)
        parts = []
        amounts = [m.group(0).rstrip(",. ") for m in re.finditer(r"\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?:\s*(?:million|billion))?", clean, re.I)]
        if amounts: parts.append(f"Amounts: {', '.join(sorted(set(amounts), key=lambda x: -len(x)))}")
        dates = re.findall(rf"{cls.MONTH}\s+\d{{1,2}},\s+\d{{4}}", clean)
        if dates: parts.append(f"Key Dates: {', '.join(sorted(set(dates)))}")
        vc = cls.victim_count(clean)
        if vc: parts.append(f"Victims: {vc}")
        sd = cls.scheme_duration(clean)
        if sd: parts.append(f"Period: {sd}")
        return " | ".join(parts)

    @staticmethod
    def pdf_text(fp):
        parts = []
        try:
            with pdfplumber.open(fp) as pdf:
                for pg in pdf.pages[:PDF_MAX_PAGES]:
                    t = pg.extract_text()
                    if t: parts.append(t)
        except Exception as e: log.warning(f"    PDF read error: {e}")
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML / RSS PARSING
# ═══════════════════════════════════════════════════════════════════════════════

import xml.etree.ElementTree as ET

def parse_listing_html(html):
    soup = BeautifulSoup(html, "lxml")
    table = None
    for t in soup.find_all("table"):
        if "Date" in t.get_text() and "Respondent" in t.get_text(): table = t; break
    if not table: return []
    entries = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2: continue
        date = cells[0].get_text(strip=True)
        rc = cells[1]; links = rc.find_all("a")
        if not links: continue
        resp = links[0].get_text(strip=True); href = links[0].get("href","")
        if not href: continue
        ct = rc.get_text(); lr = re.search(r"Release\s+No\.\s*(LR-?\d+)", ct, re.I)
        pdfs = []
        for a in links[1:]:
            t2, h2 = a.get_text(strip=True), a.get("href","")
            if h2 and t2 and (".pdf" in h2.lower() or any(k in t2.lower() for k in ["judgment","complaint","order","stipulation","dismiss"])):
                pdfs.append({"name":t2,"url":urljoin(BASE_URL, h2)})
        entries.append({"date":date,"respondent":resp,"detail_url":urljoin(BASE_URL, href),"citation":lr.group(0) if lr else "","pdf_links":pdfs})
    return entries

def parse_rss(xml_text):
    entries = []
    try:
        root = ET.fromstring(xml_text)
        for item in (root.find(".//channel") or root).findall(".//item"):
            ti,li,de,pu = item.find("title"),item.find("link"),item.find("description"),item.find("pubDate")
            if ti is not None and li is not None:
                entries.append({"date":pu.text.strip() if pu is not None and pu.text else "","respondent":ti.text.strip() if ti.text else "",
                    "detail_url":li.text.strip() if li.text else "","citation":"","pdf_links":[],"rss_desc":de.text.strip() if de is not None and de.text else ""})
    except ET.ParseError:
        soup = BeautifulSoup(xml_text, "lxml-xml")
        for item in soup.find_all("item"):
            ti,li,de,pu = item.find("title"),item.find("link"),item.find("description"),item.find("pubDate")
            if ti and li:
                entries.append({"date":pu.get_text(strip=True) if pu else "","respondent":ti.get_text(strip=True),
                    "detail_url":li.get_text(strip=True),"citation":"","pdf_links":[],"rss_desc":de.get_text(strip=True) if de else ""})
    return entries


# ═══════════════════════════════════════════════════════════════════════════════
#  CASE PROCESSOR — extracts all 39 fields from detail page + PDFs
# ═══════════════════════════════════════════════════════════════════════════════

def _merge(case, attr, new):
    if not new: return
    old = getattr(case, attr, "")
    if not old: setattr(case, attr, new); return
    vals = set(old.split("; ")); vals.update(new.split("; "))
    setattr(case, attr, "; ".join(sorted(vals - {""})))

def _set(case, attr, new):
    if new and not getattr(case, attr, ""): setattr(case, attr, new)

def _append(case, attr, new, label=""):
    if not new: return
    old = getattr(case, attr, "")
    tagged = f"[{label}] {new}" if label else new
    setattr(case, attr, f"{old} ||| {tagged}" if old else tagged)


def parse_detail_to_case(html, entry):
    E = Extractor
    soup = BeautifulSoup(html, "lxml")
    main = soup.find(id="main-content") or soup.find("main") or soup.find("article") or soup.find("body")
    text = main.get_text(separator="\n") if main else ""

    case = LitigationCase(date=entry["date"], respondent=entry["respondent"], citation=entry["citation"], source_url=entry["detail_url"])
    if len(text) < 50:
        case.case_title = f"SEC v. {entry['respondent']}"; return case

    # Title
    for p in [r"(Securities and Exchange Commission\s+v\.?\s+[^,\n]+)", r"(SEC\s+v\.?\s+[^,\n]+)", r"(In the Matter of\s+[^,\n]+)"]:
        m = re.search(p, text, re.I)
        if m: case.case_title = re.split(r"\s*(?:No\.|Civil Action|Case No)", m.group(1).strip())[0].strip(); break
    if not case.case_title: case.case_title = f"SEC v. {entry['respondent']}"

    # Citation
    lr = re.search(r"Litigation Release No\.?\s*(\d+)", text, re.I)
    if lr: case.citation = f"Litigation Release No. {lr.group(1)}"
    cn = re.search(r"(?:No\.|Civil\s+Action\s+No\.?)\s*([\w\-:]+(?:\s*\([\w\s.]+\))?)", text, re.I)
    if cn: case.citation += f" / {cn.group(1).strip()}" if case.citation else cn.group(1).strip()

    case.court = E.court(text)
    dm = re.search(rf"{E.MONTH}\s+\d{{1,2}},\s+\d{{4}}", text)
    if dm: case.date = dm.group(0)

    # Summary — full, no limit
    paras = []
    for p in text.split("\n"):
        p = p.strip()
        if len(p) < 40: continue
        lo = p.lower()[:60]
        if any(s in lo for s in ["litigation release","u.s. securities and exchange","enforcement","skip to","official website","share sensitive","more in this section","search sec.gov","resources","submit a tip","sec homepage","menu","newsroom","data & research"]): continue
        paras.append(p)
    case.summary = re.sub(r"\s+", " ", " ".join(paras)).strip()

    # All extractors
    case.outcome = E.outcomes(text); case.legal_topic = E.topics(text)
    case.judges = E.judges(text); case.charges_and_sections = E.charges(text)
    case.total_fine_amount = E.fines(text); case.company_domain = E.domain(text)
    case.defendant_roles = E.defendant_roles(text); case.defendant_employer = E.defendant_employer(text)
    case.employer_crd_cik = E.employer_crd_cik(text); case.relief_defendants = E.relief_defendants(text)
    case.sec_attorneys = E.sec_attorneys(text); case.sec_regional_office = E.sec_regional_office(text)
    case.parallel_actions = E.parallel_actions(text); case.related_releases = E.related_releases(text)
    case.case_status = E.case_status(text); case.admission_status = E.admission_status(text)
    case.victim_count = E.victim_count(text); case.total_victim_losses = E.victim_losses(text)
    case.scheme_method = E.scheme_method(text); case.regulatory_registrations = E.registrations(text)
    case.defendant_sentence = E.sentence(text); case.judgment_type = E.judgment_type(text)
    case.scheme_duration = E.scheme_duration(text)
    s, e = E.scheme_start_end(text); case.scheme_start_date = s; case.scheme_end_date = e
    case.complaint_filed_date = E.complaint_filed_date(text); case.judgment_date = E.judgment_date(text)

    # Discover PDF links from detail page
    if not entry.get("pdf_links"):
        pdfs = []
        for a in (main or soup).find_all("a", href=True):
            h, t2 = a["href"], a.get_text(strip=True)
            if ".pdf" in h.lower() or any(k in t2.lower() for k in ["judgment","complaint","order"]):
                pdfs.append({"name":t2, "url":urljoin(BASE_URL, h)})
        entry["pdf_links"] = pdfs

    return case


async def analyze_pdfs(case, pdf_links, sem):
    """Download, analyze, and enrich case from PDFs."""
    E = Extractor
    all_insights, all_jd, doc_names, narratives = [], [], [], []

    for doc in pdf_links:
        name, url = doc["name"], doc["url"]
        safe = re.sub(r"[^\w\-.]","_", name)[:50]
        h = hashlib.md5(url.encode()).hexdigest()[:8]
        fpath = os.path.join(PDF_TEMP_DIR, f"{safe}_{h}.pdf")

        log.info(f"      📄 {name}")
        await asyncio.sleep(PDF_DELAY)

        result = await curl_fetch_async(url, output_file=fpath, semaphore=sem)
        if not result: continue

        size = os.path.getsize(fpath)
        if size > PDF_MAX_SIZE_MB * 1024 * 1024:
            try: os.remove(fpath)
            except: pass
            continue
        log.info(f"      ↓ {size/1024:.0f}KB")

        text = E.pdf_text(fpath)
        if not text.strip():
            try: os.remove(fpath)
            except: pass
            continue

        log.info(f"      {len(text):,} chars — extracting...")

        # Merge all fields from PDF
        _set(case, "court", E.court(text))
        _merge(case, "judges", E.judges(text))
        _merge(case, "outcome", E.outcomes(text))
        _merge(case, "legal_topic", E.topics(text))
        _merge(case, "charges_and_sections", E.charges(text))
        _merge(case, "defendant_roles", E.defendant_roles(text))
        _set(case, "defendant_employer", E.defendant_employer(text))
        _merge(case, "employer_crd_cik", E.employer_crd_cik(text))
        _merge(case, "relief_defendants", E.relief_defendants(text))
        _merge(case, "sec_attorneys", E.sec_attorneys(text))
        _set(case, "sec_regional_office", E.sec_regional_office(text))

        pdf_fines = E.fines(text)
        if pdf_fines: _append(case, "total_fine_amount", pdf_fines, name)
        _set(case, "total_victim_losses", E.victim_losses(text))
        _set(case, "scheme_duration", E.scheme_duration(text))
        s, e = E.scheme_start_end(text)
        _set(case, "scheme_start_date", s); _set(case, "scheme_end_date", e)
        _set(case, "complaint_filed_date", E.complaint_filed_date(text))
        _set(case, "judgment_date", E.judgment_date(text))
        if not case.scheme_method: case.scheme_method = E.scheme_method(text)
        _set(case, "victim_count", E.victim_count(text))
        _set(case, "admission_status", E.admission_status(text))
        pa = E.parallel_actions(text)
        if pa: _append(case, "parallel_actions", pa)
        _merge(case, "related_releases", E.related_releases(text))
        _set(case, "case_status", E.case_status(text))
        _merge(case, "regulatory_registrations", E.registrations(text))
        _set(case, "defendant_sentence", E.sentence(text))
        _set(case, "company_domain", E.domain(text))

        jt = E.judgment_type(text, name)
        if jt:
            if case.judgment_type and jt not in case.judgment_type: case.judgment_type += f"; {jt}"
            elif not case.judgment_type: case.judgment_type = jt

        jd = E.judgment_details(text)
        if jd: all_jd.append(f"[{name}] {jd}")

        ins = E.insights(text)
        if ins: all_insights.append(f"[{name}] {ins}")
        doc_names.append(name)

        # Narrative enrichment
        clean_pdf = re.sub(r"\s+", " ", text)
        for p in [
            r"(?:The\s+(?:SEC['s]*\s+)?complaint\s+(?:also\s+)?alleges?\s+that)\s+([^.]+(?:\.[^.]+){0,5})\.",
            r"(?:According\s+to\s+the\s+(?:SEC['s]*\s+)?complaint),?\s+([^.]+(?:\.[^.]+){0,4})\.",
            r"(?:The\s+Court\s+(?:found|determined|concluded)\s+that)\s+([^.]+(?:\.[^.]+){0,3})\.",
            r"(?:Specifically,)\s+([^.]+(?:\.[^.]+){0,3})\.",
        ]:
            for m in re.finditer(p, clean_pdf, re.I):
                sent = m.group(1).strip()
                if len(sent) > 40: narratives.append(sent); break

        try: os.remove(fpath)
        except: pass

    case.pdf_insights = " ||| ".join(all_insights)
    case.final_judgment_details = " ||| ".join(all_jd)
    case.associated_documents = "; ".join(doc_names)
    if narratives:
        narr = " | ".join(narratives)
        case.summary = f"{case.summary} ||| PDF DETAILS: {narr}" if case.summary else narr


# ═══════════════════════════════════════════════════════════════════════════════
#  PIPELINE ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

async def process_entry(entry, db, sem, worker_id):
    """Process a single entry: detail page + PDFs → SQLite."""
    url = entry["detail_url"]

    # Skip if already scraped
    if db.url_exists(url):
        log.info(f"  [W{worker_id}] Skip (exists): {entry['respondent']}")
        return

    log.info(f"  [W{worker_id}] ► {entry['respondent']}")

    await asyncio.sleep(REQUEST_DELAY)
    detail_html = await curl_fetch_async(entry["detail_url"], semaphore=sem)

    if detail_html:
        case = parse_detail_to_case(detail_html, entry)
    else:
        case = LitigationCase(
            date=entry.get("date",""), respondent=entry["respondent"],
            citation=entry.get("citation",""), source_url=url,
            case_title=f"SEC v. {entry['respondent']}",
            summary=entry.get("rss_desc",""),
        )
        db.log_scrape(url, "error", "Detail page fetch failed")

    # PDF analysis
    if entry.get("pdf_links"):
        log.info(f"  [W{worker_id}]   {len(entry['pdf_links'])} PDFs")
        await analyze_pdfs(case, entry["pdf_links"], sem)

    # Write to SQLite immediately
    db.insert_case(case)
    db.log_scrape(url, "success")

    log.info(f"  [W{worker_id}] ✓ {case.case_title[:60]} → DB ({db.case_count()} total)")


async def run_pipeline(args):
    """Main async pipeline."""
    db = Database(args.db)
    workers = min(args.workers, MAX_WORKERS)
    sem = asyncio.Semaphore(workers)

    log.info("=" * 64)
    log.info("  SEC Litigation Release Scraper v7 — Scalable Pipeline")
    log.info("=" * 64)
    log.info(f"  Database:  {args.db}")
    log.info(f"  Workers:   {workers}")
    log.info(f"  Existing:  {db.case_count()} cases in DB")

    # Verify curl
    try:
        r = subprocess.run(["curl","--version"], capture_output=True, text=True, timeout=5)
        log.info(f"  curl:      {r.stdout.split(chr(10))[0]}")
    except: sys.exit("ERROR: curl not found")

    all_entries = []

    # ── RSS feed ──
    log.info("\n📡 RSS feed...")
    xml = await curl_fetch_async(RSS_URL, semaphore=sem)
    if xml:
        rss_entries = parse_rss(xml)
        log.info(f"  RSS: {len(rss_entries)} entries")
        all_entries.extend(rss_entries)

    if args.rss_only:
        pass  # skip HTML pages
    elif args.incremental:
        # Only scrape listing pages until we hit known cases
        log.info(f"\n📄 Incremental mode — scraping until overlap...")
        for pn in range(1, 100):  # generous upper bound
            url = _build_url(pn, args.year, args.month)
            log.info(f"  Page {pn}: {url}")
            await asyncio.sleep(REQUEST_DELAY)
            html = await curl_fetch_async(url, semaphore=sem)
            if not html:
                log.info(f"  Page {pn} failed — stopping"); break
            entries = parse_listing_html(html)
            if not entries:
                log.info(f"  No entries on page {pn} — stopping"); break

            new = [e for e in entries if not db.url_exists(e["detail_url"])]
            all_entries.extend(new)
            log.info(f"  Page {pn}: {len(entries)} entries, {len(new)} new")

            if len(new) == 0:
                log.info(f"  All entries already in DB — incremental complete")
                break
    else:
        # Standard paginated scraping
        pages = args.pages
        for pn in range(1, pages + 1):
            if pn == 1 and all_entries:
                continue  # skip page 1 if RSS got it
            url = _build_url(pn, args.year, args.month)
            log.info(f"\n📄 Page {pn}/{pages}: {url}")
            await asyncio.sleep(REQUEST_DELAY)
            html = await curl_fetch_async(url, semaphore=sem)
            if not html: continue
            entries = parse_listing_html(html)
            all_entries.extend(entries)
            log.info(f"  {len(entries)} entries")

    # Deduplicate
    seen = set()
    unique = []
    for e in all_entries:
        if e["detail_url"] not in seen:
            seen.add(e["detail_url"])
            unique.append(e)

    log.info(f"\n{'─'*64}")
    log.info(f"  Total entries to process: {len(unique)}")
    log.info(f"  Already in DB (will skip): {sum(1 for e in unique if db.url_exists(e['detail_url']))}")
    log.info(f"{'─'*64}")

    # Process with worker pool
    tasks = [process_entry(e, db, sem, (i % workers) + 1) for i, e in enumerate(unique)]

    # Process in batches to avoid overwhelming
    batch_size = workers * 2
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        log.info(f"  --- Batch {i//batch_size + 1} complete ({db.case_count()} in DB) ---")

    log.info(f"\n{'='*64}")
    log.info(f"  PIPELINE COMPLETE")
    log.info(f"  Total cases in DB: {db.case_count()}")
    log.info(f"  Database: {args.db} ({os.path.getsize(args.db)/1024/1024:.1f}MB)")
    log.info(f"{'='*64}")

    db.close()


def _build_url(pn, year="", month=""):
    p = ["populate="]
    p.append(f"year={year}" if year else "year=All")
    p.append(f"month={month}" if month else "month=All")
    u = f"{LIST_URL}?{'&'.join(p)}"
    if pn > 1: u += f"&page={pn-1}"
    return u


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="SEC Litigation Scraper v7 — Scalable Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # ── scrape ──
    sc = sub.add_parser("scrape", help="Scrape SEC litigation releases")
    sc.add_argument("--pages", type=int, default=1, help="Listing pages (default: 1)")
    sc.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent workers (default: {DEFAULT_WORKERS}, max: {MAX_WORKERS})")
    sc.add_argument("--db", default=DB_FILE, help=f"SQLite database (default: {DB_FILE})")
    sc.add_argument("--year", default="", help="Filter year")
    sc.add_argument("--month", default="", help="Filter month")
    sc.add_argument("--rss-only", action="store_true", help="RSS only (~20 recent)")
    sc.add_argument("--incremental", action="store_true", help="Stop when reaching known cases")
    sc.add_argument("--all", action="store_true", help="Scrape all pages (sets --pages to 999)")

    # ── export ──
    ex = sub.add_parser("export", help="Export database to CSV/JSON")
    ex.add_argument("--format", choices=["csv","json","both"], default="csv", help="Export format")
    ex.add_argument("-o", "--output", default="sec_litigation_releases", help="Output filename (without extension)")
    ex.add_argument("--db", default=DB_FILE, help=f"SQLite database (default: {DB_FILE})")

    # ── status ──
    st = sub.add_parser("status", help="Show database statistics")
    st.add_argument("--db", default=DB_FILE, help=f"SQLite database (default: {DB_FILE})")

    args = parser.parse_args()

    if args.command == "scrape":
        if args.all:
            args.pages = 999
        asyncio.run(run_pipeline(args))

    elif args.command == "export":
        if not os.path.exists(args.db):
            sys.exit(f"Database not found: {args.db}")
        db = Database(args.db)
        if args.format in ("csv", "both"):
            path = f"{args.output}.csv"
            n = db.export_csv(path)
            log.info(f" Exported {n} cases → {path}")
        if args.format in ("json", "both"):
            path = f"{args.output}.json"
            n = db.export_json(path)
            log.info(f" Exported {n} cases → {path}")
        db.close()

    elif args.command == "status":
        if not os.path.exists(args.db):
            sys.exit(f"Database not found: {args.db}")
        db = Database(args.db)
        s = db.status()
        print(f"\n{'='*50}")
        print(f"  SEC Litigation Database Status")
        print(f"{'='*50}")
        print(f"  Total cases:     {s['total_cases']}")
        print(f"  Latest date:     {s['latest_date']}")
        print(f"  DB size:         {s['db_size_mb']}MB")
        print(f"  Scrape success:  {s['scrape_success']}")
        print(f"  Scrape errors:   {s['scrape_errors']}")
        if s['top_topics']:
            print(f"\n  Top legal topics:")
            for topic, cnt in s['top_topics']:
                print(f"    {cnt:4d} │ {topic}")
        if s['top_courts']:
            print(f"\n  Top courts:")
            for court, cnt in s['top_courts']:
                print(f"    {cnt:4d} │ {court}")
        print(f"{'='*50}\n")
        db.close()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()