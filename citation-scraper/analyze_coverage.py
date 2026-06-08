"""
Citation coverage analysis for our chunk (rows 1501-2250).

Combines the enriched cited-doc metadata (enriched_cited.jsonl) with the
land-dispute Excel and the 26k corpus index to answer:
  1. how many distinct cited targets are CASES vs statutes (by docsource)
  2. how many cited cases are in the 26k corpus (Supreme Court, title+year match)
  3. how many of those are already in the 7,500 land-dispute Excel
  4. corpus cases NOT in the land list -> reclassification candidates
     (scored by land/property keyword density in the corpus judgment text)

Outputs:
  coverage_report.txt
  reclassification_candidates.csv   (doc_id, title, year, pdf_path, land_score)
"""
import json
import os
import re

import pandas as pd

ROOT = "/Users/Shared/F_Drive/00_Study/5_legalAI/legal-ai"
LAND_KW = re.compile(
    r"land|propert|tenan|zamindar|acquisit|lease|estate|possession|eviction|"
    r"mortgage|landlord|occupanc|holding|ryot|easement|partition|encroach|"
    r"revenue|title|sale deed|lesse|lessor|trespass|boundary", re.I)


def doc_id(u):
    m = re.search(r"/doc/(\d+)", str(u))
    return m.group(1) if m else None


def canon_party(name):
    """Normalize a case name to a party key for corpus matching.

    Drops the trailing ' on <date>', unifies the v/vs separator (only when
    space-delimited, so initials like 'V.P.' aren't mangled), and collapses
    party-noise variants (& Anr / And Others / Etc / UOI). Validated at ~97%
    recall matching enriched IK titles to corpus filenames.
    """
    t = str(name).lower()
    t = re.sub(r"\s+on\s+\d.*$", "", t)
    t = re.sub(r"\s+(v\.?|vs\.?|versus)\s+", " vs ", t)
    t = re.sub(r"\b(anr|anrs|ano|ors|etc|uoi|another|others)\b", " ", t)
    t = re.sub(r"\band\b", " ", t)
    return re.sub(r"[^a-z0-9]", "", t)


def is_supreme_court(src):
    return bool(src) and "supreme court" in str(src).lower()


def is_statute(src):
    s = str(src).lower()
    return ("- act" in s) or ("- section" in s) or s.endswith("act") or "central government act" in s


def main():
    os.chdir(os.path.join(ROOT, "citation-scraper"))

    # --- enriched non-land cited docs ---
    enr = pd.DataFrame([json.loads(l) for l in open("enriched_cited.jsonl") if l.strip()])
    enr["doc_id"] = enr["doc_id"].astype(str)
    enr = enr.drop_duplicates("doc_id")

    # --- the 533 cited targets that ARE in the land list (known SC cases, in corpus) ---
    flat = pd.read_csv("citations_flat.csv", dtype=str)
    cited_ids = {str(i).replace(".0", "") for i in flat["cited_doc_id"].dropna() if str(i).strip()}
    land = pd.read_excel("land_property_dispute_cases.xlsx")
    land_ids = {doc_id(u) for u in land["link"] if doc_id(u)}
    cited_in_land = cited_ids & land_ids

    # --- corpus index ---
    ci = pd.read_parquet(os.path.join(ROOT, "data", "corpus_index.parquet"))
    ci["pkey"] = ci["case_name"].map(canon_party)
    corpus_by_key = {}
    for _, r in ci.iterrows():
        corpus_by_key.setdefault((r["pkey"], int(r["year"])), r["pdf_path"])

    def yr(s):
        m = re.search(r"(\d{4})", str(s))
        return int(m.group(1)) if m else None

    # classify + corpus-match the enriched (non-land) docs
    enr["is_sc"] = enr["docsource"].map(is_supreme_court)
    enr["is_statute"] = enr["docsource"].map(is_statute)
    enr["year"] = enr["publishdate"].map(yr)
    enr["pkey"] = enr["title"].map(canon_party)

    def match_corpus(row):
        if not row["is_sc"] or not row["year"]:
            return None
        return (corpus_by_key.get((row["pkey"], row["year"]))
                or corpus_by_key.get((row["pkey"], row["year"] - 1))
                or corpus_by_key.get((row["pkey"], row["year"] + 1)))
    enr["pdf_path"] = enr.apply(match_corpus, axis=1)
    enr["in_corpus"] = enr["pdf_path"].notna()

    n_total = len(cited_ids)
    n_sc = int(enr["is_sc"].sum()) + len(cited_in_land)
    n_statute = int(enr["is_statute"].sum())
    n_othercourt = int((~enr["is_sc"] & ~enr["is_statute"] & enr["error"].isna()).sum())
    n_err = int(enr["error"].notna().sum())
    corpus_nonland = int(enr["in_corpus"].sum())
    in_corpus_total = corpus_nonland + len(cited_in_land)

    # reclassification: in corpus, not in land -> score by land kw in judgment text
    cand = enr[enr["in_corpus"]].copy()
    text_by_path = {}
    import glob
    for pq in glob.glob(os.path.join(ROOT, "data", "extracted_text", "*.parquet")):
        d = pd.read_parquet(pq, columns=["pdf_path", "text"])
        text_by_path.update(dict(zip(d["pdf_path"], d["text"])))

    def land_score(p):
        t = text_by_path.get(p, "") or ""
        if not t:
            return 0.0
        hits = len(LAND_KW.findall(t))
        return round(hits / max(1, len(t) / 1000), 2)  # land-kw hits per 1k chars
    cand["land_score"] = cand["pdf_path"].map(land_score)
    cand = cand.sort_values("land_score", ascending=False)
    cand[["doc_id", "title", "year", "docsource", "pdf_path", "land_score"]].to_csv(
        "reclassification_candidates.csv", index=False)

    likely_land = int((cand["land_score"] >= 3).sum())

    report = f"""CITATION COVERAGE — our chunk (rows 1501-2250, 750 source cases)
{'='*66}
Citation edges                          : {len(flat[flat['cited_doc_id'].notna()])}
Distinct cited targets (doc_ids)        : {n_total}

By type (docsource):
  Supreme Court cases                   : {n_sc}
  Other-court cases (HC etc., not corpus): {n_othercourt}
  Statutes / sections                   : {n_statute}
  API errors                            : {n_err}

IN 26k CORPUS (Supreme Court, title+year match):
  cited cases in corpus (total)         : {in_corpus_total}
    - already in land-dispute Excel     : {len(cited_in_land)}
    - in corpus but NOT in land list    : {corpus_nonland}   <- reclassification pool

RECLASSIFICATION (corpus cases not in land list):
  candidates written to CSV             : {len(cand)}
  with strong land/property signal (>=3 kw/1k chars): {likely_land}
{'='*66}
-> reclassification_candidates.csv  (review these for adding to the land list)
"""
    open("coverage_report.txt", "w").write(report)
    print(report)


if __name__ == "__main__":
    main()
