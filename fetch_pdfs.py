#!/usr/bin/env python3
"""
fetch_pdfs.py
=============
Download the judgment PDFs for the sampled documents from their Google Drive
links (the `drive` column of sample_100_docs.csv) into documents_pdf/<id>.pdf.

Resumable: skips files that already exist and validate as PDFs.
Handles Google Drive's "virus scan" interstitial for larger files.
"""
from __future__ import annotations
import csv, os, re, sys, time
import requests

SAMPLE = "sample_100_docs.csv"
OUTDIR = "documents_pdf"
UA = {"User-Agent": "Mozilla/5.0 (compatible; legal-ai-research/0.1)"}


def file_id(drive_url: str):
    m = re.search(r"/d/([^/]+)/", drive_url) or re.search(r"[?&]id=([^&]+)", drive_url)
    return m.group(1) if m else None


def is_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def download(sess: requests.Session, fid: str, dest: str) -> bool:
    base = "https://drive.google.com/uc?export=download"
    r = sess.get(base, params={"id": fid}, headers=UA, timeout=60)
    # If Drive returns an HTML interstitial, find the confirm token / form action.
    if r.content[:5] != b"%PDF-":
        token = None
        for k, v in r.cookies.items():
            if k.startswith("download_warning"):
                token = v
        if not token:
            m = re.search(r"confirm=([0-9A-Za-z_-]+)", r.text)
            token = m.group(1) if m else None
        if token:
            r = sess.get(base, params={"id": fid, "confirm": token}, headers=UA, timeout=120)
        else:
            m = re.search(r'action="(https://[^"]+)"', r.text)
            if m:
                r = sess.get(m.group(1).replace("&amp;", "&"), headers=UA, timeout=120)
    if r.content[:5] == b"%PDF-":
        with open(dest, "wb") as fh:
            fh.write(r.content)
        return True
    return False


def main() -> int:
    os.makedirs(OUTDIR, exist_ok=True)
    rows = list(csv.DictReader(open(SAMPLE)))
    sess = requests.Session()
    ok = skip = fail = 0
    failures = []
    for i, row in enumerate(rows, 1):
        did = row["id"]
        dest = os.path.join(OUTDIR, f"{did}.pdf")
        if is_pdf(dest):
            skip += 1
            continue
        fid = file_id(row["drive"])
        if not fid:
            fail += 1; failures.append((did, "no-file-id")); continue
        try:
            if download(sess, fid, dest):
                ok += 1
            else:
                fail += 1; failures.append((did, "not-pdf"))
        except requests.RequestException as e:
            fail += 1; failures.append((did, str(e)[:60]))
        if i % 10 == 0:
            print(f"  {i}/{len(rows)}  ok={ok} skip={skip} fail={fail}", flush=True)
        time.sleep(0.5)
    print(f"DONE  downloaded={ok} skipped={skip} failed={fail}")
    if failures:
        print("failures:", failures)
    return 0


if __name__ == "__main__":
    sys.exit(main())
