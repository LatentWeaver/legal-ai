from pathlib import Path
import re
from collections import Counter

import pandas as pd
import matplotlib.pyplot as plt
from pypdf import PdfReader

ROOT_DIR = Path("/home/sdusane1/CIPS_Lab-Explainable_AI_Volunteer/Experiment_01/legal_dataset/supreme_court_judgments")
OUTPUT_DIR = ROOT_DIR / "_land_analysis_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_PAGES = 5
LAND_THRESHOLD = 6

LAND_KEYWORDS = [
    "land", "property", "possession", "title", "ownership", "encroachment",
    "boundary", "mutation", "revenue", "khasra", "khata", "jamabandi",
    "acquisition", "compensation", "solatium", "rehabilitation", "resettlement",
    "partition", "tenancy", "lease", "ejectment", "eviction", "trespass"
]

CATEGORY_RULES = {
    "land acquisition": ["land acquisition", "compensation", "solatium", "rehabilitation", "resettlement", "award"],
    "title / ownership": ["title", "ownership", "sale deed", "declaration of title", "conveyance"],
    "possession / eviction": ["possession", "ejectment", "eviction", "recovery of possession", "forcible possession"],
    "boundary / encroachment": ["boundary", "encroachment", "demarcation", "disputed boundary"],
    "mutation / revenue": ["mutation", "revenue", "khasra", "khata", "jamabandi", "record of rights"],
    "tenancy / lease": ["tenancy", "tenant", "landlord", "lease", "rent"],
    "partition / family property": ["partition", "joint family", "coparcener", "ancestral property"],
}

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")

def all_pdfs(root: Path):
    return sorted([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"])

def extract_year_from_path(pdf_path: Path):
    for part in pdf_path.parts:
        if YEAR_RE.fullmatch(part):
            return part
    m = YEAR_RE.search(str(pdf_path))
    return m.group(1) if m else ""

def extract_text(pdf_path: Path, max_pages: int = MAX_PAGES):
    try:
        reader = PdfReader(str(pdf_path))
        parts = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                pass
        return "\n".join(parts)
    except Exception as e:
        print(f"[WARN] Failed to read {pdf_path.name}: {e}")
        return ""

def normalize(text: str):
    return re.sub(r"\s+", " ", text.lower())

def score_and_classify(text: str, filename: str):
    haystack = normalize(text + " " + filename)

    score = 0
    matched = []

    for kw in LAND_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", haystack):
            matched.append(kw)
            if kw in {"acquisition", "compensation", "solatium", "rehabilitation", "resettlement", "encroachment", "boundary", "mutation", "khasra", "khata", "jamabandi", "tenancy", "lease", "ejectment", "eviction"}:
                score += 2
            else:
                score += 1

    if "land dispute" in haystack:
        score += 5
        matched.append("land dispute")
    if "property dispute" in haystack:
        score += 4
        matched.append("property dispute")

    category_scores = {}
    for category, kws in CATEGORY_RULES.items():
        cscore = 0
        for kw in kws:
            if kw in haystack:
                cscore += 2 if " " in kw else 1
        category_scores[category] = cscore

    category = max(category_scores, key=category_scores.get) if category_scores else "uncategorized"
    category_score = category_scores.get(category, 0)

    is_land_related = (score >= LAND_THRESHOLD) or (category_score >= 2)

    return is_land_related, score, category, category_score, sorted(set(matched))

def main():
    pdfs = all_pdfs(ROOT_DIR)
    print(f"Found {len(pdfs)} PDF files under {ROOT_DIR}")

    rows = []

    for i, pdf in enumerate(pdfs, 1):
        print(f"Scanning {i}/{len(pdfs)}: {pdf}")
        text = extract_text(pdf)
        year = extract_year_from_path(pdf)
        is_land, score, category, category_score, matched = score_and_classify(text, pdf.name)

        rows.append({
            "file_path": str(pdf),
            "file_name": pdf.name,
            "year_folder": year,
            "is_land_related": is_land,
            "land_score": score,
            "category": category if is_land else "non-land",
            "category_score": category_score,
            "matched_signals": "; ".join(matched),
            "text_preview": text[:400].replace("\n", " ")
        })

    df = pd.DataFrame(rows)

    detailed_csv = OUTPUT_DIR / "land_case_detailed_results.csv"
    df.to_csv(detailed_csv, index=False)

    land_df = df[df["is_land_related"]].copy()
    land_csv = OUTPUT_DIR / "land_case_only.csv"
    land_df.to_csv(land_csv, index=False)

    print("\n===== SUMMARY =====")
    print(f"Total PDFs scanned: {len(df)}")
    print(f"Land-related PDFs: {len(land_df)}")
    print(f"Detailed CSV saved to: {detailed_csv}")
    print(f"Land-only CSV saved to: {land_csv}")

    if land_df.empty:
        print("No land-related cases found.")
        return

    # Chart 1: category counts
    cat_counts = land_df["category"].value_counts().sort_values(ascending=False)
    plt.figure(figsize=(12, 6))
    plt.bar(cat_counts.index, cat_counts.values)
    plt.xticks(rotation=35, ha="right")
    plt.title("Land-related Supreme Court cases by subtype")
    plt.xlabel("Subtype")
    plt.ylabel("Count")
    plt.tight_layout()
    category_chart = OUTPUT_DIR / "land_case_categories.png"
    plt.savefig(category_chart, dpi=200)
    plt.show()

    # Chart 2: year folder counts
    year_df = land_df[land_df["year_folder"].astype(str).str.match(r"^\d{4}$", na=False)]
    if not year_df.empty:
        year_counts = year_df["year_folder"].value_counts().sort_index()
        plt.figure(figsize=(14, 6))
        plt.plot(year_counts.index, year_counts.values, marker="o")
        plt.xticks(rotation=45)
        plt.title("Land-related cases by year folder")
        plt.xlabel("Year")
        plt.ylabel("Count")
        plt.tight_layout()
        year_chart = OUTPUT_DIR / "land_case_year_trend.png"
        plt.savefig(year_chart, dpi=200)
        plt.show()
    else:
        year_chart = None

    top_hits = land_df.sort_values(["land_score", "category_score"], ascending=False).head(25)
    top_csv = OUTPUT_DIR / "top_land_cases.csv"
    top_hits.to_csv(top_csv, index=False)

    print(f"Category chart saved to: {category_chart}")
    if year_chart:
        print(f"Year chart saved to: {year_chart}")
    print(f"Top cases saved to: {top_csv}")

if __name__ == "__main__":
    main()