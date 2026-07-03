#!/usr/bin/env python3
"""Fetch a small RECAP/CourtListener sample and print a readable summary.

This script is intentionally small and practical:
- fetch one PACER/RECAP docket from CourtListener
- fetch a few docket entries for that docket
- fetch a few RECAP documents for that docket
- save the raw payload locally so the schema can be inspected

By default it uses the public docket ID shown in the official CourtListener
PACER API docs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_DOCKET_ID = 4214664
DEFAULT_BASE_URL = "https://www.courtlistener.com/api/rest/v4"
DEFAULT_OUTPUT = "recap_demo_output.json"


def build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"
    return url


def fetch_json(url: str, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "legal-ai/recap-fetch-demo",
    }
    if token:
        headers["Authorization"] = f"Token {token}"

    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        auth_hint = ""
        if exc.code == 401:
            auth_hint = (
                "\nHint: export COURTLISTENER_TOKEN='<your-token>' "
                "before running this script."
            )
        raise SystemExit(
            f"HTTP {exc.code} while fetching {url}\n"
            f"Response body:\n{body[:2000]}"
            f"{auth_hint}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error while fetching {url}: {exc}") from exc


def trim_docket(docket: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "case_name",
        "case_name_short",
        "court_id",
        "docket_number",
        "date_filed",
        "date_terminated",
        "nature_of_suit",
        "cause",
        "jurisdiction_type",
        "pacer_case_id",
        "absolute_url",
        "filepath_ia",
        "filepath_ia_json",
    ]
    return {key: docket.get(key) for key in keys}


def trim_docket_entry(entry: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "docket",
        "date_filed",
        "entry_number",
        "description",
        "recap_documents",
    ]
    trimmed = {key: entry.get(key) for key in keys}
    docs = trimmed.get("recap_documents") or []
    trimmed["recap_documents"] = [
        {
            "id": doc.get("id"),
            "document_number": doc.get("document_number"),
            "attachment_number": doc.get("attachment_number"),
            "description": doc.get("description"),
            "is_available": doc.get("is_available"),
        }
        for doc in docs[:3]
    ]
    return trimmed


def trim_recap_document(document: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "docket_entry",
        "document_number",
        "attachment_number",
        "short_description",
        "description",
        "is_available",
        "filepath_local",
        "page_count",
        "file_size",
        "ocr_status",
        "plain_text",
        "pacer_doc_id",
    ]
    trimmed = {key: document.get(key) for key in keys}
    plain_text = trimmed.get("plain_text") or ""
    trimmed["plain_text_preview"] = plain_text[:500]
    trimmed.pop("plain_text", None)
    return trimmed


def summarize(docket: dict[str, Any], entry_rows: list[dict[str, Any]], document_rows: list[dict[str, Any]]) -> None:
    print("Docket")
    print(json.dumps(trim_docket(docket), indent=2))
    print()

    print(f"Docket entries fetched: {len(entry_rows)}")
    for index, entry in enumerate(entry_rows[:3], start=1):
        print(f"Entry {index}")
        print(json.dumps(trim_docket_entry(entry), indent=2))
        print()

    print(f"RECAP documents fetched: {len(document_rows)}")
    for index, document in enumerate(document_rows[:3], start=1):
        print(f"Document {index}")
        print(json.dumps(trim_recap_document(document), indent=2))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a small RECAP/CourtListener sample.")
    parser.add_argument("--docket-id", type=int, default=DEFAULT_DOCKET_ID, help="CourtListener docket ID to fetch")
    parser.add_argument("--entry-limit", type=int, default=5, help="How many docket entries to fetch")
    parser.add_argument("--document-limit", type=int, default=5, help="How many RECAP documents to fetch")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="CourtListener API base URL")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Path to save raw output JSON")
    args = parser.parse_args()

    token = os.environ.get("COURTLISTENER_TOKEN")

    docket_url = build_url(args.base_url, f"dockets/{args.docket_id}/")
    entries_url = build_url(
        args.base_url,
        "docket-entries/",
        {"docket": args.docket_id, "page_size": args.entry_limit},
    )
    documents_url = build_url(
        args.base_url,
        "recap-documents/",
        {"docket_entry__docket": args.docket_id, "page_size": args.document_limit},
    )

    print(f"Fetching docket {args.docket_id}...", file=sys.stderr)
    docket = fetch_json(docket_url, token=token)

    print("Fetching docket entries...", file=sys.stderr)
    entries_payload = fetch_json(entries_url, token=token)

    print("Fetching RECAP documents...", file=sys.stderr)
    documents_payload = fetch_json(documents_url, token=token)

    entry_rows = entries_payload.get("results", [])
    document_rows = documents_payload.get("results", [])

    payload = {
        "docket": docket,
        "docket_entries": entry_rows,
        "recap_documents": document_rows,
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summarize(docket, entry_rows, document_rows)
    print(f"Saved raw output to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
