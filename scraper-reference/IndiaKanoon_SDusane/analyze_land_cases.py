"""Analyze Supreme Court judgment PDFs for land-related cases.

This script:
- recursively scans all year folders under the Supreme Court judgments directory
- finds both .pdf and .PDF files
- classifies land-related cases with a weighted scoring system
- assigns subtypes such as land acquisition, title/ownership, possession/eviction, etc.
- extracts approximate title, parties, case-number hints, and result hints
- measures commonality across land cases
- saves CSV summaries and multiple visualizations
- optionally uses OCR fallback for scanned/image-based PDFs

Dependencies:
    pip install pypdf pandas matplotlib

Optional OCR support:
    pip install pytesseract pdf2image pillow
    and install the system OCR binaries on the machine

Example:
    python analyze_land_cases.py \
        --root-dir /home/sdusane1/CIPS_Lab-Explainable_AI_Volunteer/Experiment_01/legal_dataset/supreme_court_judgments
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import argparse
import re
from typing import Dict, List, Sequence, Tuple

import matplotlib.pyplot as plt
import pandas as pd
from pypdf import PdfReader


# -----------------------------
# Configuration
# -----------------------------
DEFAULT_MAX_PAGES = 6
DEFAULT_LAND_THRESHOLD = 6
DEFAULT_OUTPUT_DIRNAME = "_land_analysis_outputs"

LAND_KEYWORDS = [
    "land", "property", "possession", "title", "ownership", "encroachment",
    "boundary", "mutation", "revenue", "khasra", "khata", "jamabandi",
    "acquisition", "compensation", "solatium", "rehabilitation", "resettlement",
    "partition", "tenancy", "lease", "ejectment", "eviction", "trespass",
    "survey", "record of rights", "sale deed", "declaration of title", "land dispute",
]

CATEGORY_RULES: Dict[str, List[str]] = {
    "land acquisition": ["land acquisition", "compensation", "solatium", "rehabilitation", "resettlement", "award"],
    "title / ownership": ["title", "ownership", "sale deed", "declaration of title", "conveyance"],
    "possession / eviction": ["possession", "ejectment", "eviction", "recovery of possession", "forcible possession"],
    "boundary / encroachment": ["boundary", "encroachment", "demarcation", "disputed boundary"],
    "mutation / revenue": ["mutation", "revenue", "khasra", "khata", "jamabandi", "record of rights"],
    "tenancy / lease": ["tenancy", "tenant", "landlord", "lease", "rent"],
    "partition / family property": ["partition", "joint family", "coparcener", "ancestral property"],
}

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")
CASE_NO_RE = re.compile(
    r"\b(?:civil|criminal|special|appeal|petition|s\.?l\.?p\.?|slp)\b.*?\b(?:no\.?|nos\.?|number|numbers)?\s*\d+",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b")

STOPWORDS = {
    "the", "and", "of", "to", "in", "for", "with", "a", "an", "by", "on", "is", "are", "be",
    "was", "were", "as", "at", "from", "or", "that", "this", "it", "we", "they", "their", "his",
    "her", "its", "but", "not", "have", "has", "had", "shall", "may", "must", "which", "who",
    "whom", "into", "upon", "under", "over", "between", "after", "before", "during", "against",
    "case", "appeal", "petition", "judgment", "judgement", "court", "supreme", "india", "appellant",
    "respondent", "vs", "v", "versus", "page", "pages", "mr", "mrs", "ms", "shri", "smt", "dr",
}


# -----------------------------
# Text helpers
# -----------------------------

def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_field_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("-–—:;| ")


def tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z\-']+", normalize_text(text))
    return [t for t in tokens if len(t) > 2 and t not in STOPWORDS]


def jaccard_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def safe_join(parts: Sequence[str]) -> str:
    return "\n".join(p for p in parts if p)


# -----------------------------
# File discovery and PDF extraction
# -----------------------------

def all_pdf_files(root: Path) -> List[Path]:
    return sorted([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"])


def extract_year_from_path(pdf_path: Path) -> str:
    for part in pdf_path.parts:
        if YEAR_RE.fullmatch(part):
            return part
    m = YEAR_RE.search(str(pdf_path))
    return m.group(1) if m else ""


def extract_text_from_pdf(pdf_path: Path, max_pages: int = DEFAULT_MAX_PAGES) -> str:
    try:
        reader = PdfReader(str(pdf_path))
        parts: List[str] = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return safe_join(parts)
    except Exception as e:
        print(f"[WARN] Could not read {pdf_path.name}: {e}")
        return ""


def try_ocr_fallback(pdf_path: Path, max_pages: int = 2) -> str:
    """Optional OCR fallback for scanned PDFs.

    Returns an empty string if OCR packages or system tools are unavailable.
    """
    try:
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract  # type: ignore
    except Exception:
        return ""

    try:
        images = convert_from_path(str(pdf_path), first_page=1, last_page=max_pages)
        parts: List[str] = []
        for img in images:
            try:
                parts.append(pytesseract.image_to_string(img) or "")
            except Exception:
                continue
        return safe_join(parts)
    except Exception:
        return ""


# -----------------------------
# Land classification
# -----------------------------

def score_land_related(text: str, filename: str, land_threshold: int = DEFAULT_LAND_THRESHOLD) -> Tuple[bool, int, str, int, List[str]]:
    """Return: is_land, score, category, category_score, matched_signals."""
    haystack = normalize_text(text + " " + filename)
    score = 0
    matched: List[str] = []

    for kw in LAND_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", haystack):
            matched.append(kw)
            if kw in {
                "acquisition", "compensation", "solatium", "rehabilitation", "resettlement",
                "encroachment", "boundary", "mutation", "khasra", "khata", "jamabandi",
                "tenancy", "lease", "ejectment", "eviction", "trespass", "possession",
                "ownership", "title", "record of rights", "declaration of title", "land dispute",
            }:
                score += 2
            else:
                score += 1

    if "land dispute" in haystack:
        score += 5
        matched.append("land dispute")
    if "property dispute" in haystack:
        score += 4
        matched.append("property dispute")
    if "declaration of title" in haystack:
        score += 3
        matched.append("declaration of title")

    category_scores: Dict[str, int] = {}
    for category, kws in CATEGORY_RULES.items():
        cscore = 0
        for kw in kws:
            if kw in haystack:
                cscore += 2 if " " in kw else 1
        category_scores[category] = cscore

    category = max(category_scores, key=category_scores.get) if category_scores else "uncategorized"
    category_score = category_scores.get(category, 0)
    is_land_related = (score >= land_threshold) or (category_score >= 2)
    return is_land_related, score, category, category_score, sorted(set(matched))


# -----------------------------
# Field extraction heuristics
# -----------------------------

def extract_title(text: str, filename: str) -> str:
    lines = [clean_field_text(line) for line in text.splitlines() if clean_field_text(line)]
    if not lines:
        return filename

    for line in lines[:40]:
        if re.search(r"\b(vs?\.?|versus|v\.)\b", line, re.IGNORECASE) and len(line) < 220:
            return line

    for line in lines[:40]:
        if 12 < len(line) < 220 and any(ch.isalpha() for ch in line):
            if not re.search(r"\b(?:supreme court|judgment|judgement|appeal|petition|order|reporter)\b", line, re.IGNORECASE):
                if sum(ch.isupper() for ch in line[:40]) >= 3 or re.search(r"\b[A-Z][a-z]+\b", line):
                    return line

    return filename


def extract_parties(text: str) -> Dict[str, str]:
    out = {"appellant": "", "respondent": "", "defendant": "", "plaintiff": ""}
    lines = [clean_field_text(line) for line in text.splitlines() if clean_field_text(line)]
    joined = " \n ".join(lines[:150])

    patterns = {
        "appellant": [r"(.{5,240}?)\bappellant(?:s)?\b", r"(.{5,240}?)\bpetitioner(?:s)?\b"],
        "respondent": [r"(.{5,240}?)\brespondent(?:s)?\b"],
        "defendant": [r"(.{5,240}?)\bdefendant(?:s)?\b"],
        "plaintiff": [r"(.{5,240}?)\bplaintiff(?:s)?\b"],
    }

    for field, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, joined, re.IGNORECASE)
            if m:
                out[field] = clean_field_text(m.group(1))
                break

    return out


def extract_case_number(text: str) -> str:
    m = CASE_NO_RE.search(text[:12000])
    return clean_field_text(m.group(0)) if m else ""


def extract_date_hint(text: str) -> str:
    m = DATE_RE.search(text[:12000])
    return m.group(0) if m else ""


def extract_result_hint(text: str) -> str:
    haystack = normalize_text(text[:18000])
    patterns = [
        (r"\bappeal\s+allowed\b", "appeal allowed"),
        (r"\bappeal\s+dismissed\b", "appeal dismissed"),
        (r"\bpetition\s+dismissed\b", "petition dismissed"),
        (r"\bpetition\s+allowed\b", "petition allowed"),
        (r"\bpartly\s+allowed\b", "partly allowed"),
        (r"\bdecree\s+set\s+aside\b", "decree set aside"),
        (r"\bjudgment\s+set\s+aside\b", "judgment set aside"),
        (r"\bremanded\b", "remanded"),
        (r"\bconfirmed\b", "confirmed"),
        (r"\bdismissed\b", "dismissed"),
        (r"\ballowed\b", "allowed"),
    ]
    for pat, label in patterns:
        if re.search(pat, haystack, re.IGNORECASE):
            return label
    return ""


def extract_front_page_hint(text: str) -> str:
    header = clean_field_text(" ".join(text.splitlines()[:20]))
    return header[:250] if header else ""


# -----------------------------
# Commonality analysis
# -----------------------------

def common_keywords(texts: Sequence[str], top_n: int = 40) -> List[Tuple[str, int]]:
    counter = Counter()
    for t in texts:
        counter.update(tokenize(t))
    return counter.most_common(top_n)


def pairwise_similarity_summary(texts: Sequence[str], sample_limit: int = 250) -> Dict[str, float]:
    if not texts:
        return {"avg_similarity": 0.0, "max_similarity": 0.0, "min_similarity": 0.0, "median_similarity": 0.0}

    sample = list(texts[:sample_limit])
    token_sets = [set(tokenize(t)) for t in sample]
    sims: List[float] = []

    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            sims.append(jaccard_similarity(token_sets[i], token_sets[j]))

    if not sims:
        return {"avg_similarity": 0.0, "max_similarity": 0.0, "min_similarity": 0.0, "median_similarity": 0.0}

    sims_sorted = sorted(sims)
    mid = len(sims_sorted) // 2
    median = sims_sorted[mid] if len(sims_sorted) % 2 == 1 else (sims_sorted[mid - 1] + sims_sorted[mid]) / 2

    return {
        "avg_similarity": float(sum(sims_sorted) / len(sims_sorted)),
        "max_similarity": float(max(sims_sorted)),
        "min_similarity": float(min(sims_sorted)),
        "median_similarity": float(median),
    }


def field_coverage_summary(df: pd.DataFrame, fields: Sequence[str]) -> pd.DataFrame:
    rows = []
    for field in fields:
        series = df.get(field, pd.Series(dtype=str)).fillna("").astype(str)
        nonempty = series[series.str.strip() != ""]
        if nonempty.empty:
            rows.append({"field": field, "non_empty_count": 0, "coverage_pct": 0.0, "top_value": "", "top_count": 0})
            continue
        vc = nonempty.value_counts()
        rows.append({
            "field": field,
            "non_empty_count": int(nonempty.shape[0]),
            "coverage_pct": float(nonempty.shape[0] / len(df)) if len(df) else 0.0,
            "top_value": vc.index[0],
            "top_count": int(vc.iloc[0]),
        })
    return pd.DataFrame(rows)


# -----------------------------
# Plot helpers
# -----------------------------

def save_bar_chart(series: pd.Series, title: str, xlabel: str, ylabel: str, outpath: Path, rotation: int = 35) -> None:
    plt.figure(figsize=(12, 6))
    plt.bar(series.index.astype(str), series.values)
    plt.xticks(rotation=rotation, ha="right")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()


def save_horizontal_bar(series: pd.Series, title: str, xlabel: str, ylabel: str, outpath: Path, top_n: int = 20) -> None:
    s = series.head(top_n).sort_values(ascending=True)
    plt.figure(figsize=(12, 7))
    plt.barh(s.index.astype(str), s.values)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()


def save_histogram(values: Sequence[float], title: str, xlabel: str, ylabel: str, outpath: Path) -> None:
    plt.figure(figsize=(10, 6))
    plt.hist(values, bins=20)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()


def save_line_chart(series: pd.Series, title: str, xlabel: str, ylabel: str, outpath: Path) -> None:
    plt.figure(figsize=(14, 6))
    plt.plot(series.index.astype(str), series.values, marker="o")
    plt.xticks(rotation=45, ha="right")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()


# -----------------------------
# Main analysis workflow
# -----------------------------

def analyze_folder(root_dir: Path, max_pages: int, land_threshold: int, output_dir: Path, enable_ocr: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfs = all_pdf_files(root_dir)

    if not pdfs:
        print(f"No PDF files found under: {root_dir}")
        return

    print(f"Found {len(pdfs)} PDF files under {root_dir}")

    rows = []
    land_texts: List[str] = []
    result_counts = Counter()

    for i, pdf in enumerate(pdfs, 1):
        print(f"Scanning {i}/{len(pdfs)}: {pdf}")

        front_text = extract_text_from_pdf(pdf, max_pages=max(2, max_pages // 2))
        full_text = extract_text_from_pdf(pdf, max_pages=max_pages)

        if enable_ocr and len(full_text.strip()) < 200:
            ocr_text = try_ocr_fallback(pdf, max_pages=2)
            if len(ocr_text.strip()) > len(full_text.strip()):
                full_text = ocr_text
                front_text = ocr_text

        year_folder = extract_year_from_path(pdf)
        is_land, land_score, category, category_score, matched = score_land_related(
            full_text, pdf.name, land_threshold=land_threshold
        )

        title = extract_title(front_text, pdf.name)
        parties = extract_parties(front_text)
        result_hint = extract_result_hint(full_text)
        case_number = extract_case_number(front_text)
        date_hint = extract_date_hint(front_text)
        front_page_hint = extract_front_page_hint(front_text)

        rows.append({
            "file_path": str(pdf),
            "file_name": pdf.name,
            "year_folder": year_folder,
            "is_land_related": is_land,
            "land_score": land_score,
            "category": category if is_land else "non-land",
            "category_score": category_score,
            "matched_signals": "; ".join(matched),
            "title": title,
            "appellant": parties.get("appellant", ""),
            "respondent": parties.get("respondent", ""),
            "defendant": parties.get("defendant", ""),
            "plaintiff": parties.get("plaintiff", ""),
            "result_hint": result_hint,
            "case_number_hint": case_number,
            "date_hint": date_hint,
            "front_page_hint": front_page_hint,
            "text_preview": full_text[:800].replace("\n", " "),
        })

        if is_land:
            land_texts.append(full_text)
            result_counts[result_hint or "unlabeled"] += 1

    df = pd.DataFrame(rows)

    detailed_csv = output_dir / "land_case_detailed_results.csv"
    df.to_csv(detailed_csv, index=False)

    land_df = df[df["is_land_related"]].copy()
    land_csv = output_dir / "land_case_only.csv"
    land_df.to_csv(land_csv, index=False)

    summary = {
        "total_pdfs": int(len(df)),
        "land_related_pdfs": int(len(land_df)),
        "land_related_ratio": float(len(land_df) / len(df)) if len(df) else 0.0,
    }
    summary_csv = output_dir / "analysis_summary.csv"
    pd.DataFrame([summary]).to_csv(summary_csv, index=False)

    common_fields = field_coverage_summary(
        land_df,
        ["title", "appellant", "respondent", "defendant", "plaintiff", "result_hint", "case_number_hint", "date_hint"],
    )
    common_fields_csv = output_dir / "common_fields_summary.csv"
    common_fields.to_csv(common_fields_csv, index=False)

    common_kw = common_keywords(land_texts, top_n=40)
    common_kw_df = pd.DataFrame(common_kw, columns=["keyword", "count"])
    common_kw_csv = output_dir / "common_keywords.csv"
    common_kw_df.to_csv(common_kw_csv, index=False)

    tokenized_texts = [" ".join(tokenize(t)) for t in land_texts]
    similarity_summary = pairwise_similarity_summary(tokenized_texts)
    similarity_csv = output_dir / "similarity_summary.csv"
    pd.DataFrame([similarity_summary]).to_csv(similarity_csv, index=False)

    pairwise_vs_first: List[float] = []
    if len(tokenized_texts) >= 2:
        base = set(tokenize(tokenized_texts[0]))
        for t in tokenized_texts[1:50]:
            pairwise_vs_first.append(jaccard_similarity(list(base), list(set(tokenize(t)))))
        pd.DataFrame({"similarity_to_first_case": pairwise_vs_first}).to_csv(
            output_dir / "similarity_distribution.csv", index=False
        )

    print("\n===== SUMMARY =====")
    print(f"Total PDFs scanned: {summary['total_pdfs']}")
    print(f"Land-related PDFs: {summary['land_related_pdfs']}")
    print(f"Land-related ratio: {summary['land_related_ratio']:.2%}")
    print(f"Detailed CSV: {detailed_csv}")
    print(f"Land-only CSV: {land_csv}")
    print(f"Summary CSV: {summary_csv}")
    print(f"Common fields CSV: {common_fields_csv}")
    print(f"Common keywords CSV: {common_kw_csv}")
    print(f"Similarity CSV: {similarity_csv}")

    if land_df.empty:
        print("No land-related cases found.")
        return

    # Visualizations
    cat_series = land_df["category"].value_counts().sort_values(ascending=False)
    save_bar_chart(
        cat_series,
        title="Land-related Supreme Court cases by subtype",
        xlabel="Subtype",
        ylabel="Count",
        outpath=output_dir / "land_case_categories.png",
        rotation=30,
    )

    year_series = land_df["year_folder"].replace("", pd.NA).dropna().value_counts().sort_index()
    if not year_series.empty:
        save_line_chart(
            year_series,
            title="Land-related cases by year folder",
            xlabel="Year",
            ylabel="Count",
            outpath=output_dir / "land_case_year_trend.png",
        )

    common_kw_series = common_kw_df.set_index("keyword")["count"].sort_values(ascending=False)
    save_horizontal_bar(
        common_kw_series,
        title="Most common words in land-related case text",
        xlabel="Frequency",
        ylabel="Keyword",
        outpath=output_dir / "common_keywords_bar.png",
        top_n=20,
    )

    coverage = common_fields.set_index("field")["non_empty_count"].sort_values(ascending=False)
    save_bar_chart(
        coverage,
        title="How often key fields were extracted from land cases",
        xlabel="Field",
        ylabel="Count with extracted value",
        outpath=output_dir / "field_extraction_coverage.png",
        rotation=25,
    )

    if result_counts:
        result_series = pd.Series(result_counts).sort_values(ascending=False)
        save_bar_chart(
            result_series,
            title="Outcome hints across land-related cases",
            xlabel="Outcome hint",
            ylabel="Count",
            outpath=output_dir / "result_hints.png",
            rotation=25,
        )

    if pairwise_vs_first:
        save_histogram(
            pairwise_vs_first,
            title="Similarity of land cases to the first detected land case",
            xlabel="Jaccard similarity",
            ylabel="Number of cases",
            outpath=output_dir / "similarity_histogram.png",
        )

    print("\n===== COMMONALITY SUMMARY =====")
    print(f"Average pairwise Jaccard similarity: {similarity_summary['avg_similarity']:.3f}")
    print(f"Median pairwise Jaccard similarity: {similarity_summary['median_similarity']:.3f}")
    print(f"Max pairwise Jaccard similarity: {similarity_summary['max_similarity']:.3f}")
    print(f"Min pairwise Jaccard similarity: {similarity_summary['min_similarity']:.3f}")

    print("\nTop common keywords:")
    for kw, cnt in common_kw[:20]:
        print(f"- {kw}: {cnt}")

    print("\nField extraction coverage:")
    for _, row in common_fields.iterrows():
        print(f"- {row['field']}: found in {int(row['non_empty_count'])} cases; top value count = {int(row['top_count'])}")

    print("\nOutputs saved in:")
    print(output_dir)


# -----------------------------
# CLI
# -----------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze Supreme Court PDFs for land-related cases.")
    parser.add_argument("--root-dir", type=str, required=True, help="Root folder containing yearly Supreme Court PDF subfolders.")
    parser.add_argument("--output-dir", type=str, default="", help="Output folder for CSVs and charts. Defaults to <root-dir>/_land_analysis_outputs")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="How many front pages to scan from each PDF.")
    parser.add_argument("--land-threshold", type=int, default=DEFAULT_LAND_THRESHOLD, help="Threshold for land-related classification.")
    parser.add_argument("--enable-ocr", action="store_true", help="Use OCR fallback for text-poor PDFs if OCR tools are available.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    root_dir = Path(args.root_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else root_dir / DEFAULT_OUTPUT_DIRNAME

    analyze_folder(
        root_dir=root_dir,
        max_pages=args.max_pages,
        land_threshold=args.land_threshold,
        output_dir=output_dir,
        enable_ocr=args.enable_ocr,
    )


if __name__ == "__main__":
    main()
