#!/usr/bin/env python3
"""
extract_citations.py
=====================

Citation extraction over a filtered Indian Kanoon case corpus.

Input : a CSV with columns  case, year, link [, downloaded file]
        where `link` is an Indian Kanoon URL of the form
        http://indiankanoon.org/doc/<id>/

Output (written to --outdir, default ./data):
  nodes.csv             id, case, year          (one row per corpus case)
  edges.csv             citing_id, cited_id     (intra-corpus edges only)
  out_citations_raw.csv citing_id, cited_id     (every observed out-cite,
                                                  including external targets)

Design notes
------------
* Two fetch backends:
    - API  (preferred): set INDIANKANOON_API_TOKEN env var.
    - HTML (fallback): fetches IK search-result pages
      /search/?formInput=citedby:<id>  (cases that cite this case)
      /search/?formInput=cites:<id>    (cases this case cites)
      and extracts doc ids from /docfragment/<id>/ links in the results.
      This avoids the judgment-page /doc/ links which are mostly statutes.
* Fully cached + resumable: every raw response is written to
  <cachedir>/ and reused on re-run.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

import requests

# Global rate limiter: ensures all workers together don't exceed a target request rate.
# Workers must call _global_rate_limit() before every HTTP request.
_rate_lock = threading.Lock()
_last_request_time = 0.0
_min_global_interval = 1.0  # seconds between ANY two requests across all workers

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore

LOG = logging.getLogger("extract_citations")

API_BASE = "https://api.indiankanoon.org"
HTML_BASE = "https://indiankanoon.org"
DOC_ID_RE = re.compile(r"/doc/(\d+)")
DOCFRAG_RE = re.compile(r"/docfragment/(\d+)")
USER_AGENT = (
    "Mozilla/5.0 (compatible; legal-ai-research/0.1; citation-graph; "
    "contact: research team)"
)


# --------------------------------------------------------------------------- #
# Input parsing
# --------------------------------------------------------------------------- #
def parse_doc_id(url: str) -> Optional[int]:
    if not url:
        return None
    m = DOC_ID_RE.search(url)
    return int(m.group(1)) if m else None


def load_corpus(csv_path: str) -> tuple[list[dict], set[int]]:
    rows: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            doc_id = parse_doc_id(r.get("link", ""))
            if doc_id is None:
                LOG.warning("Skipping row with unparseable link: %r", r.get("link"))
                continue
            rows.append(
                {"id": doc_id, "case": r.get("case", "").strip(), "year": r.get("year", "").strip()}
            )
    seen: set[int] = set()
    deduped: list[dict] = []
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        deduped.append(r)
    return deduped, seen


# --------------------------------------------------------------------------- #
# Shared HTTP helper
# --------------------------------------------------------------------------- #
def _global_rate_limit():
    """Block until enough time has passed since the last request across ALL workers."""
    global _last_request_time
    with _rate_lock:
        now = time.time()
        wait = _min_global_interval - (now - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()


def _request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict,
    max_retries: int = 5,
    timeout: int = 30,
) -> Optional[requests.Response]:
    backoff = 4.0
    for attempt in range(1, max_retries + 1):
        _global_rate_limit()  # respect global rate limit before every request
        try:
            resp = session.request(
                method, url, headers=headers,
                data=b"" if method == "POST" else None, timeout=timeout,
            )
        except requests.RequestException as exc:
            LOG.warning("  request error (%s/%s) %s: %s", attempt, max_retries, url, exc)
            time.sleep(backoff)
            backoff *= 2
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in (403, 429, 500, 502, 503, 504):
            LOG.warning("  HTTP %s (%s/%s) backing off %.0fs — %s",
                        resp.status_code, attempt, max_retries, backoff, url)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)  # cap at 2 minutes
            continue
        LOG.error("  HTTP %s for %s (giving up)", resp.status_code, url)
        return None
    LOG.error("  exhausted retries for %s", url)
    return None


# --------------------------------------------------------------------------- #
# API backend (unchanged)
# --------------------------------------------------------------------------- #
def _cache_path(cachedir: str, doc_id: int, ext: str) -> str:
    return os.path.join(cachedir, f"{doc_id}.{ext}")


def fetch_api(
    session: requests.Session,
    doc_id: int,
    token: str,
    cachedir: str,
    maxcites: int,
    maxcitedby: int,
    force: bool,
) -> Optional[str]:
    path = _cache_path(cachedir, doc_id, "json")
    if not force and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    url = f"{API_BASE}/doc/{doc_id}/?maxcites={maxcites}&maxcitedby={maxcitedby}"
    headers = {"Authorization": f"Token {token}", "Accept": "application/json", "User-Agent": USER_AGENT}
    resp = _request_with_retry(session, "POST", url, headers=headers)
    if resp is None:
        return None
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(resp.text)
    return resp.text


def _ids_from_list(entries: Iterable[dict]) -> list[int]:
    out: list[int] = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        tid = e.get("tid")
        if tid is None:
            tid = parse_doc_id(str(e.get("link", "")))
        if tid is not None:
            try:
                out.append(int(tid))
            except (TypeError, ValueError):
                pass
    return out


def extract_from_api(raw_json: str) -> tuple[list[int], list[int]]:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return [], []
    cites = _ids_from_list(data.get("citeList", []))
    citedby = _ids_from_list(data.get("citedbyList", []))
    if not cites:
        body = data.get("doc", "") or ""
        cites = sorted({int(x) for x in DOC_ID_RE.findall(body)})
    return cites, citedby


# --------------------------------------------------------------------------- #
# HTML backend — search-page approach
# --------------------------------------------------------------------------- #
def _search_cache_path(cachedir: str, doc_id: int, direction: str, page: int) -> str:
    return os.path.join(cachedir, f"{doc_id}_{direction}_p{page}.html")


def fetch_search_page(
    session: requests.Session,
    doc_id: int,
    direction: str,
    page: int,
    cachedir: str,
    rate: float,
    force: bool,
) -> Optional[str]:
    """Fetch one page of IK search results for cites/citedby a given doc."""
    path = _search_cache_path(cachedir, doc_id, direction, page)
    if not force and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    url = f"{HTML_BASE}/search/?formInput={direction}:{doc_id}"
    if page > 0:
        url += f"&pagenum={page}"
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    resp = _request_with_retry(session, "GET", url, headers=headers)
    if resp is None:
        return None
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(resp.text)
    return resp.text


def extract_ids_from_search_page(raw_html: str) -> list[int]:
    """Extract doc ids from /docfragment/<id>/ links in a search results page."""
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 is required for the HTML path")
    soup = BeautifulSoup(raw_html, "html.parser")
    ids: list[int] = []
    seen: set[int] = set()
    for a in soup.find_all("a", href=DOCFRAG_RE):
        m = DOCFRAG_RE.search(a.get("href", ""))
        if m:
            did = int(m.group(1))
            if did not in seen:
                seen.add(did)
                ids.append(did)
    if not ids:
        for r in soup.find_all(class_="result"):
            for a in r.find_all("a", href=DOC_ID_RE):
                if "Full Document" in a.get_text():
                    m = DOC_ID_RE.search(a.get("href", ""))
                    if m:
                        did = int(m.group(1))
                        if did not in seen:
                            seen.add(did)
                            ids.append(did)
    return ids


def has_next_page(raw_html: str) -> bool:
    if BeautifulSoup is None:
        return False
    soup = BeautifulSoup(raw_html, "html.parser")
    return bool(soup.find_all("a", href=re.compile(r"pagenum")))


def fetch_html_citations(
    session: requests.Session,
    doc_id: int,
    cachedir: str,
    rate: float,
    max_pages: int,
    force: bool,
) -> tuple[list[int], list[int]]:
    """
    Fetch citation data for a case via IK search pages.

    Returns (cites, citedby) as lists of doc ids.
    - cites:   from /search/?formInput=cites:<id>     (docs this case cites)
    - citedby: from /search/?formInput=citedby:<id>   (docs that cite this case)
    """
    cites: list[int] = []
    citedby: list[int] = []

    for direction, out_list in [("citedby", citedby), ("cites", cites)]:
        for page in range(max_pages):
            html = fetch_search_page(session, doc_id, direction, page, cachedir, rate, force)
            if html is None:
                break
            ids = extract_ids_from_search_page(html)
            # first result on page 0 of citedby is sometimes the source doc itself
            ids = [i for i in ids if i != doc_id]
            out_list.extend(ids)
            if not has_next_page(html):
                break

    return cites, citedby


# --------------------------------------------------------------------------- #
# Per-case worker (thread-safe)
# --------------------------------------------------------------------------- #
def _process_one_case(
    doc_id: int,
    corpus_ids: set[int],
    cachedir: str,
    rate: float,
    max_pages: int,
    force: bool,
    use_api: bool,
    token: str,
    maxcites: int,
    maxcitedby: int,
) -> tuple[int, list[tuple[int, int]], list[tuple[int, int]]]:
    """
    Fetch and return citation data for one case.
    Returns (doc_id, intra_edges_list, raw_out_list).
    Each worker creates its own requests.Session for thread safety.
    """
    session = requests.Session()
    if use_api:
        raw = fetch_api(session, doc_id, token, cachedir, maxcites, maxcitedby, force)
        if raw is None:
            return doc_id, [], []
        cites, citedby = extract_from_api(raw)
    else:
        cites, citedby = fetch_html_citations(
            session, doc_id, cachedir, rate, max_pages, force,
        )
    session.close()

    intra: list[tuple[int, int]] = []
    raw: list[tuple[int, int]] = []

    for x in cites:
        raw.append((doc_id, x))
        if x in corpus_ids:
            intra.append((doc_id, x))
    for y in citedby:
        raw.append((y, doc_id))
        if y in corpus_ids:
            intra.append((y, doc_id))

    return doc_id, intra, raw


# --------------------------------------------------------------------------- #
# Main pipeline (parallel)
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> int:
    os.makedirs(args.cachedir, exist_ok=True)
    os.makedirs(args.outdir, exist_ok=True)

    corpus, corpus_ids = load_corpus(args.csv)
    LOG.info("Loaded %d unique cases from %s", len(corpus), args.csv)

    token = os.environ.get("INDIANKANOON_API_TOKEN", "").strip()
    use_api = bool(token)
    LOG.info("Backend: %s", "Indian Kanoon API" if use_api else "HTML search-page scrape")

    # Window selection: [start, end) by corpus index.
    # --start is the 0-based offset; --limit caps how many cases after start (0 = to end).
    start = max(args.start, 0)
    if args.limit in (0, None):
        targets = corpus[start:]
    else:
        targets = corpus[start: start + args.limit]
    LOG.info("Fetching citations for %d cases (window=[%d:%d], limit=%s, workers=%d, global_rate=%.1fs)",
             len(targets), start, start + len(targets), args.limit, args.workers, args.rate)

    # Set the global rate limiter interval
    global _min_global_interval
    _min_global_interval = args.rate

    # Count how many are cached vs uncached for ETA estimate
    cached_count = 0
    for row in targets:
        p0 = _search_cache_path(args.cachedir, row["id"], "citedby", 0)
        if os.path.exists(p0):
            cached_count += 1
    uncached_count = len(targets) - cached_count
    LOG.info("  %d already cached, %d need fetching", cached_count, uncached_count)

    intra_edges: set[tuple[int, int]] = set()
    raw_out: set[tuple[int, int]] = set()
    assembled = 0
    lock = threading.Lock()
    done_count = 0
    start_time = time.time()

    def on_result(doc_id: int, intra: list, raw_list: list):
        nonlocal assembled, done_count
        with lock:
            assembled += 1
            done_count += 1
            for e in intra:
                intra_edges.add(e)
            for e in raw_list:
                raw_out.add(e)
            if done_count % 50 == 0 or done_count == len(targets):
                elapsed = time.time() - start_time
                rate_per_s = done_count / elapsed if elapsed > 0 else 0
                eta_s = (len(targets) - done_count) / rate_per_s if rate_per_s > 0 else 0
                LOG.info("  processed %d/%d  (intra-edges: %d | %.1f cases/s | ETA: %.0fm%.0fs)",
                         done_count, len(targets), len(intra_edges),
                         rate_per_s, eta_s // 60, eta_s % 60)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for row in targets:
            f = pool.submit(
                _process_one_case,
                row["id"], corpus_ids, args.cachedir, args.rate,
                args.max_pages, args.force, use_api, token,
                args.maxcites, args.maxcitedby,
            )
            futures[f] = row["id"]

        for f in as_completed(futures):
            try:
                doc_id, intra, raw_list = f.result()
                on_result(doc_id, intra, raw_list)
            except Exception as exc:
                LOG.error("  worker error for doc %s: %s", futures[f], exc)

    LOG.info("Assembled citations from %d cases", assembled)
    LOG.info("Intra-corpus edges: %d | raw out-citations: %d", len(intra_edges), len(raw_out))

    # ---- Write outputs ---- #
    nodes_path = os.path.join(args.outdir, "nodes.csv")
    with open(nodes_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "case", "year"])
        for r in corpus:
            w.writerow([r["id"], r["case"], r["year"]])

    edges_path = os.path.join(args.outdir, "edges.csv")
    with open(edges_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["citing_id", "cited_id"])
        for a, b in sorted(intra_edges):
            w.writerow([a, b])

    raw_path = os.path.join(args.outdir, "out_citations_raw.csv")
    with open(raw_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["citing_id", "cited_id"])
        for a, b in sorted(raw_out):
            w.writerow([a, b])

    LOG.info("Wrote %s, %s, %s", nodes_path, edges_path, raw_path)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract an intra-corpus citation edge list from Indian Kanoon.")
    p.add_argument("--csv", default="land_property_dispute_cases.csv", help="input CSV path")
    p.add_argument("--start", type=int, default=0, help="0-based corpus index to start from (for windowed/team-split runs)")
    p.add_argument("--limit", type=int, default=100, help="number of cases to fetch after --start (0 = to end)")
    p.add_argument("--maxcites", type=int, default=50, help="max out-cites per case (API only)")
    p.add_argument("--maxcitedby", type=int, default=50, help="max cited-by per case (API only)")
    p.add_argument("--max-pages", type=int, default=3, dest="max_pages",
                   help="max search-result pages per direction per case (HTML; 10 results/page)")
    p.add_argument("--outdir", default="data", help="output directory")
    p.add_argument("--cachedir", default="cache", help="raw-response cache directory")
    p.add_argument("--rate", type=float, default=1.5, help="minimum seconds between ANY two HTTP requests globally (across all workers)")
    p.add_argument("--workers", type=int, default=4,
                   help="number of parallel fetch workers (default 4; be polite to IK servers)")
    p.add_argument("--force", action="store_true", help="ignore cache and refetch")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_arg_parser().parse_args(argv)
    if not os.path.exists(args.csv):
        LOG.error("Input CSV not found: %s", args.csv)
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
