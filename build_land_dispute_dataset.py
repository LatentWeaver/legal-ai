#!/usr/bin/env python3
"""
Step 1 (Filter -> land dispute cases): link the scraped land-dispute case list
(scraper-reference/land_property_dispute_cases.csv, ~7.5k Indian Kanoon cases)
against the consolidated corpus (data/supreme_court_judgments/<year>/*.pdf) to
produce the filtered land-dispute dataset with local PDF paths + IK links.

Matching is by a normalized key (lowercased, alphanumerics only) within the same
year. The corpus filenames carry a trailing _<n> counter that the case list does
not, so that suffix is stripped before keying.
"""
import os
import re
from difflib import SequenceMatcher

import pandas as pd

FUZZY_THRESHOLD = 0.90  # within-year similarity required to accept a fuzzy match

ROOT = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(ROOT, "data", "supreme_court_judgments")
CASE_LIST = os.path.join(ROOT, "scraper-reference", "land_property_dispute_cases.csv")
OUT = os.path.join(ROOT, "data", "land_dispute_dataset.csv")
UNMATCHED = os.path.join(ROOT, "data", "land_dispute_unmatched.csv")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def index_corpus():
    """year -> list of (key, relative pdf path) for every pdf in the corpus."""
    idx = {}
    for year in os.listdir(CORPUS):
        ydir = os.path.join(CORPUS, year)
        if not (year.isdigit() and os.path.isdir(ydir)):
            continue
        for name in os.listdir(ydir):
            if not name.lower().endswith(".pdf"):
                continue
            stem = re.sub(r"_\d+$", "", os.path.splitext(name)[0])  # drop _<n> counter
            rel = os.path.join("supreme_court_judgments", year, name)
            idx.setdefault(year, []).append((norm(stem), rel))
    return idx


def main():
    idx = index_corpus()
    exact = {(y, k): rel for y, lst in idx.items() for k, rel in lst}
    print(f"corpus pdfs indexed: {len(exact)} keys")

    cases = pd.read_csv(CASE_LIST)
    paths, methods = [], []
    for _, row in cases.iterrows():
        y, k = str(row["year"]), norm(row["case"])
        rel = exact.get((y, k))
        if rel:
            paths.append(rel); methods.append("exact"); continue
        # fuzzy fallback: best within-year candidate above threshold
        best = (0.0, None)
        for ck, crel in idx.get(y, []):
            r = SequenceMatcher(None, k, ck).ratio()
            if r > best[0]:
                best = (r, crel)
        if best[0] >= FUZZY_THRESHOLD:
            paths.append(best[1]); methods.append(f"fuzzy:{best[0]:.2f}")
        else:
            paths.append(None); methods.append("none")
    cases["pdf_path"] = paths
    cases["match"] = methods

    hit = cases[cases["pdf_path"].notna()].copy()
    miss = cases[cases["pdf_path"].isna()].copy()
    hit.to_csv(OUT, index=False)
    if len(miss):
        miss.to_csv(UNMATCHED, index=False)
    elif os.path.exists(UNMATCHED):
        os.remove(UNMATCHED)

    n_exact = (cases["match"] == "exact").sum()
    n_fuzzy = cases["match"].str.startswith("fuzzy").sum()
    print(f"land-dispute cases in list : {len(cases)}")
    print(f"matched (exact)            : {n_exact}")
    print(f"matched (fuzzy)            : {n_fuzzy}")
    print(f"matched total              : {len(hit)} ({len(hit)/len(cases):.1%})")
    print(f"unmatched                  : {len(miss)}")
    print(f"-> {OUT}")
    if len(miss):
        print(f"-> {UNMATCHED}")


if __name__ == "__main__":
    main()
