#!/usr/bin/env python3
"""
export_precedents_json.py
=========================

Export the citation data into a per-case JSON schema that is easy to merge
across team members working on different corpus windows:

    [
      {
        "case": "<case name>",
        "year": "<year>",
        "url":  "https://indiankanoon.org/doc/<id>/",
        "precedents": [
          {"case": "<cited case name>", "url": "https://indiankanoon.org/doc/<id>/"},
          ...
        ]
      },
      ...
    ]

"precedents" = the cases each seed case CITES (the `cites:` search direction),
read straight from the cached IK search-result pages
(<cachedir>/<id>_cites_p*.html) produced by extract_citations.py.

Join-friendliness notes
------------------------
* Every URL is canonicalised to  https://indiankanoon.org/doc/<id>/  so the
  same case produces an identical URL string in every teammate's output,
  regardless of which window it appeared in. The URL is the reliable join key.
* A precedent that is itself in the shared corpus CSV uses the corpus case
  name, so it matches that case's own entry when it is a seed in another
  window. Precedents outside the corpus keep the title shown in IK search
  results.
"""

from __future__ import annotations

import argparse
import csv
import glob
import html
import json
import logging
import os
import re
import sys
from typing import Optional

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore

LOG = logging.getLogger("export_precedents_json")

DOC_ID_RE = re.compile(r"/doc/(\d+)")
DOCFRAG_RE = re.compile(r"/docfragment/(\d+)")


def canonical_url(doc_id: int) -> str:
    return f"https://indiankanoon.org/doc/{doc_id}/"


def parse_doc_id(url: str) -> Optional[int]:
    if not url:
        return None
    m = DOC_ID_RE.search(url)
    return int(m.group(1)) if m else None


def load_corpus(csv_path: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[int] = set()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            doc_id = parse_doc_id(r.get("link", ""))
            if doc_id is None or doc_id in seen:
                continue
            seen.add(doc_id)
            rows.append({
                "id": doc_id,
                "case": (r.get("case", "") or "").strip(),
                "year": (r.get("year", "") or "").strip(),
            })
    return rows


def extract_precedents(raw_html: str, source_id: int) -> list[tuple[int, str]]:
    """
    Return [(doc_id, title), ...] for every case-judgment link in a cached
    `cites:` search-results page. Order-preserving, deduped, self excluded.
    """
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 is required")
    soup = BeautifulSoup(raw_html, "html.parser")
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for a in soup.find_all("a", href=DOCFRAG_RE):
        m = DOCFRAG_RE.search(a.get("href", ""))
        if not m:
            continue
        did = int(m.group(1))
        if did == source_id or did in seen:
            continue
        seen.add(did)
        title = html.unescape(" ".join(a.get_text().split()))
        out.append((did, title))
    return out


def build_records(corpus: list[dict], cachedir: str, start: int, limit: int) -> list[dict]:
    by_id = {r["id"]: r for r in corpus}
    window = corpus[start:] if limit in (0, None) else corpus[start: start + limit]

    records: list[dict] = []
    missing_cache = 0
    for r in window:
        sid = r["id"]
        precedents: list[dict] = []
        seen_prec: set[int] = set()

        pages = sorted(glob.glob(os.path.join(cachedir, f"{sid}_cites_p*.html")))
        if not pages:
            missing_cache += 1
        for page in pages:
            with open(page, encoding="utf-8") as fh:
                for pid, title in extract_precedents(fh.read(), sid):
                    if pid in seen_prec:
                        continue
                    seen_prec.add(pid)
                    # Prefer the shared corpus name when the precedent is in-corpus
                    name = by_id[pid]["case"] if pid in by_id else title
                    precedents.append({"case": name, "url": canonical_url(pid)})

        records.append({
            "case": r["case"],
            "year": r["year"],
            "url": canonical_url(sid),
            "precedents": precedents,
        })

    if missing_cache:
        LOG.warning("%d/%d window cases had no cached cites page", missing_cache, len(window))
    return records


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Export precedent citations to JSON.")
    p.add_argument("--csv", default="land_property_dispute_cases.csv")
    p.add_argument("--cachedir", default="cache")
    p.add_argument("--start", type=int, default=0, help="0-based corpus index to start from")
    p.add_argument("--limit", type=int, default=0, help="cases after --start (0 = to end)")
    p.add_argument("--out", default="data_750_1500/citations.json", help="output JSON path")
    p.add_argument("--indent", type=int, default=2, help="JSON indent (0 = compact)")
    args = p.parse_args(argv)

    if not os.path.exists(args.csv):
        LOG.error("CSV not found: %s", args.csv)
        return 2

    corpus = load_corpus(args.csv)
    LOG.info("Loaded %d unique corpus cases", len(corpus))

    records = build_records(corpus, args.cachedir, args.start, args.limit)
    total_prec = sum(len(r["precedents"]) for r in records)
    LOG.info("Built %d case records, %d total precedent links", len(records), total_prec)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=args.indent or None)
    LOG.info("Wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
