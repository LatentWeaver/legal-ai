"""Scrape precedent citations from the official Indian Kanoon API (headless).

This is the no-browser path. Indian Kanoon offers a token-based HTTP API
(https://api.indiankanoon.org); every request is a POST with an
`Authorization: Token <token>` header. The document endpoint returns the
judgment plus its citations as JSON, so there is no Cloudflare, no captcha, and
no Chrome involved.

It writes the SAME data/citations.jsonl schema as scrape_citations.py, so
build_graph.py and run_louvain.py work unchanged:

    {"source_doc_id": "...", "source_case": "...", "source_year": ...,
     "source_url": "...", "source_name": "...",
     "precedent_count": N, "precedents": [{"name","doc_id","url"}, ...]}

The API charges a small fee per document, so this scraper is resumable (skips
docs already in the output) and can optionally cache each raw API response
under data/raw_docs/<docid>.json (use --save-raw) so the judgment text can be
reused later by the codebook / knowledge-graph steps without paying again.

USAGE
-----
    export IK_API_TOKEN=xxxxxxxx                 # or pass --token
    python src/scrape_citations_api.py --limit 10            # smoke test
    python src/scrape_citations_api.py --start 1 --end 750   # your chunk
    python src/scrape_citations_api.py                        # everything

Get a token by registering at https://api.indiankanoon.org/ .
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

API_HOST = "https://api.indiankanoon.org"
DOC_ID_RE = re.compile(r"/doc(?:fragment)?/(\d+)")
# IK structured fields that may carry the list of cited docs (cites, not citedby)
CITE_FIELDS = ("citeList", "citetidList", "cites", "citedTo")


def extract_doc_id(url: str):
    m = DOC_ID_RE.search(url or "")
    return m.group(1) if m else None


def load_done_ids(output_file: str) -> set:
    done = set()
    if not os.path.exists(output_file):
        return done
    with open(output_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("source_doc_id"):
                    done.add(str(rec["source_doc_id"]))
            except json.JSONDecodeError:
                continue
    return done


def fetch_doc(session: requests.Session, doc_id: str, timeout: float) -> dict | None:
    """POST to /doc/<id>/ and return parsed JSON, or None on failure."""
    url = f"{API_HOST}/doc/{doc_id}/"
    for attempt in range(3):
        try:
            r = session.post(url, timeout=timeout)
        except requests.RequestException as e:
            print(f"  request error: {e} (attempt {attempt + 1})")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                print("  response was not JSON; skipping")
                return None
        if r.status_code == 429:  # rate limited
            wait = 5 * (attempt + 1)
            print(f"  rate limited (429); backing off {wait}s")
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            sys.exit(f"\nAuth failed ({r.status_code}). Check your IK API token.")
        print(f"  HTTP {r.status_code}; skipping")
        return None
    return None


def parse_precedents(doc_json: dict, source_id: str) -> list[dict]:
    """Pull cited precedents from a /doc/ API response.

    Prefers IK's structured citation list; falls back to parsing the judgment
    HTML for inline citation links (/doc/<id>/ or /docfragment/<id>/).
    """
    seen: dict[str, dict] = {}

    # 1) structured field, if present
    for field in CITE_FIELDS:
        items = doc_json.get(field)
        if isinstance(items, list) and items:
            for it in items:
                if not isinstance(it, dict):
                    continue
                cid = str(it.get("tid") or it.get("docid") or it.get("doc_id") or "").strip()
                if not cid or cid == source_id:
                    continue
                name = (it.get("title") or it.get("name") or "").strip()
                seen.setdefault(cid, {"name": name, "doc_id": cid,
                                      "url": f"https://indiankanoon.org/doc/{cid}/"})
            if seen:
                return list(seen.values())

    # 2) fallback: parse citation links out of the judgment HTML
    html = doc_json.get("doc") or ""
    if html:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            cid = extract_doc_id(a.get("href", ""))
            if not cid or cid == source_id:
                continue
            name = a.get_text(strip=True)
            seen.setdefault(cid, {"name": name, "doc_id": cid,
                                  "url": f"https://indiankanoon.org/doc/{cid}/"})
    return list(seen.values())


def select_rows(df: pd.DataFrame, args) -> pd.DataFrame:
    if args.start is not None or args.end is not None:
        start = (args.start - 1) if args.start else 0
        end = args.end if args.end is not None else len(df)
        df = df.iloc[start:end]
        print(f"Row range: rows {start + 1}-{end} of {args.input}")
    if args.year is not None:
        df = df[df["year"] == args.year]
    df = df.copy()
    df["doc_id"] = df["link"].map(extract_doc_id)
    df = df[df["doc_id"].notna()]
    if args.limit is not None:
        df = df.head(args.limit)
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/cases.csv")
    ap.add_argument("--output", default="data/citations.jsonl")
    ap.add_argument("--token", default=os.environ.get("IK_API_TOKEN"),
                    help="IK API token (or set IK_API_TOKEN env var)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start", type=int, default=None, help="1-based start row (inclusive)")
    ap.add_argument("--end", type=int, default=None, help="1-based end row (inclusive)")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between API calls")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--save-raw", action="store_true",
                    help="cache each raw API response under data/raw_docs/<id>.json")
    args = ap.parse_args()

    if not args.token:
        sys.exit("No API token. Set IK_API_TOKEN env var or pass --token.\n"
                 "Register at https://api.indiankanoon.org/")

    df = select_rows(pd.read_csv(args.input), args)
    done = load_done_ids(args.output)
    todo = df[~df["doc_id"].isin(done)]
    print(f"Cases selected: {len(df)} | already done: {len(done & set(df['doc_id']))} "
          f"| to fetch now: {len(todo)}")
    if todo.empty:
        print("Nothing to do.")
        return

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    raw_dir = "data/raw_docs"
    if args.save_raw:
        os.makedirs(raw_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update({"Authorization": f"Token {args.token}",
                            "Accept": "application/json"})

    n_ok = n_fail = 0
    out = open(args.output, "a", encoding="utf-8")
    try:
        for i, (_, row) in enumerate(todo.iterrows(), start=1):
            doc_id = row["doc_id"]
            print(f"[{i}/{len(todo)}] doc {doc_id} ({row.get('year','?')})")
            doc_json = fetch_doc(session, doc_id, args.timeout)
            if doc_json is None:
                n_fail += 1
                continue
            if args.save_raw:
                with open(os.path.join(raw_dir, f"{doc_id}.json"), "w", encoding="utf-8") as rf:
                    json.dump(doc_json, rf, ensure_ascii=False)
            precedents = parse_precedents(doc_json, doc_id)
            rec = {
                "source_doc_id": doc_id,
                "source_case": row.get("case"),
                "source_year": int(row["year"]) if pd.notna(row.get("year")) else None,
                "source_url": f"https://indiankanoon.org/doc/{doc_id}/",
                "source_name": (doc_json.get("title") or row.get("case") or "").strip(),
                "precedent_count": len(precedents),
                "precedents": precedents,
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            n_ok += 1
            print(f"  -> {len(precedents)} precedents")
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\nInterrupted — progress is saved; rerun to resume.")
    finally:
        out.close()

    print(f"\nDone. ok={n_ok} fail={n_fail}. Output: {args.output}")


if __name__ == "__main__":
    main()
