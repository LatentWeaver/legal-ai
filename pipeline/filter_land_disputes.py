#!/usr/bin/env python3
"""
pipeline/filter_land_disputes.py

Filters land dispute cases from the full metadata corpus by scanning the
first 3 pages of each PDF for a comprehensive set of land/property keywords.

Reads   : data/metadata.csv
Writes  : data/land_disputes.csv  (all metadata columns + matched_keywords)

Usage:
    python pipeline/filter_land_disputes.py
    python pipeline/filter_land_disputes.py --workers 4
    python pipeline/filter_land_disputes.py --test 200
"""

import os
import re
import sys
import argparse
import pandas as pd
import pdfplumber
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT     = Path(__file__).resolve().parent.parent
DATA_ROOT     = REPO_ROOT.parent / "supreme_court_judgments"
METADATA_FILE = REPO_ROOT / "data" / "metadata.csv"
OUTPUT_FILE   = REPO_ROOT / "data" / "land_disputes.csv"
CHECKPOINT    = REPO_ROOT / "data" / "filter_checkpoint.csv"

PAGES_TO_READ = 3   # pages 1-3 cover enough subject-matter content

# ---------------------------------------------------------------------------
# Keyword patterns
# Each tuple: (regex_string, label)   — compiled once at module load
# ---------------------------------------------------------------------------
_RAW_PATTERNS = [

    # ── Legislation (very high precision) ───────────────────────────────────
    (r'land acquisition act',                                   'leg:land_acquisition_act'),
    (r'right to fair compensation',                             'leg:rfctlarr'),
    (r'transfer of property act',                               'leg:transfer_of_property'),
    (r'rent control act',                                       'leg:rent_control'),
    (r'\bland revenue act\b',                                   'leg:land_revenue_act'),
    (r'\bland revenue\b',                                       'leg:land_revenue'),
    (r'\bceiling on (?:land|holdings)\b',                       'leg:ceiling_holdings'),
    (r'\b(?:urban land|land) ceiling\b',                        'leg:land_ceiling'),
    (r'\bceiling act\b',                                        'leg:ceiling_act'),
    (r'\btenancy act\b',                                        'leg:tenancy_act'),
    (r'\bspecific relief act\b',                                'leg:specific_relief'),
    (r'\bregistration act\b',                                   'leg:registration_act'),
    (r'\bpunjab land\b',                                        'leg:punjab_land'),
    (r'\bharyana land\b',                                       'leg:haryana_land'),
    (r'\bmaharashtra.*land\b|\blandholding.*act\b',             'leg:state_land_act'),

    # ── High-precision domain phrases ────────────────────────────────────────
    (r'\bsuit land\b',                                          'phrase:suit_land'),
    (r'\bsuit property\b',                                      'phrase:suit_property'),
    (r'\bsuit premises\b',                                      'phrase:suit_premises'),
    (r'\bdemised premises\b',                                   'phrase:demised_premises'),
    (r'\bdisputed (?:land|property|plot)\b',                    'phrase:disputed_land'),
    (r'\btitle (?:to|of) (?:the )?land\b',                      'phrase:title_to_land'),
    (r'\btitle deed\b',                                         'phrase:title_deed'),
    (r'\bsale deed\b',                                          'phrase:sale_deed'),
    (r'\boccupancy rights?\b',                                  'phrase:occupancy_rights'),
    (r'\bpre-?emption\b',                                       'phrase:preemption'),
    (r'\bagricultural land\b',                                  'phrase:agricultural_land'),
    (r'\bforest land\b',                                        'phrase:forest_land'),
    (r'\bgovernment land\b',                                    'phrase:govt_land'),
    (r'\bwaste land\b',                                         'phrase:waste_land'),
    (r'\bcommon land\b',                                        'phrase:common_land'),
    (r'\bprivate land\b',                                       'phrase:private_land'),
    (r'\bstate land\b',                                         'phrase:state_land'),
    (r'\bnotified land\b',                                      'phrase:notified_land'),
    (r'\brevenue records?\b',                                   'phrase:revenue_records'),
    (r'\brevenue court\b',                                      'phrase:revenue_court'),
    (r'\bmutation (?:of|in|entry|order)\b',                     'phrase:mutation'),
    (r'\bdemarcation\b',                                        'phrase:demarcation'),
    (r'\bencroachment\b',                                       'phrase:encroachment'),
    (r'\beviction\b',                                           'phrase:eviction'),
    (r'\beasement\b',                                           'phrase:easement'),
    (r'\bmortgaged property\b|\bmortgage of (?:land|property)\b','phrase:mortgage_land'),
    (r'\bleasehold\b',                                          'phrase:leasehold'),
    (r'\bsub-?lease\b',                                         'phrase:sublease'),
    (r'\blandlord\b',                                           'phrase:landlord'),
    (r'\btenancy\b',                                            'phrase:tenancy'),
    (r'\btenant\b',                                             'phrase:tenant'),
    (r'\bcompulsory acquisition\b',                             'phrase:compulsory_acq'),
    (r'\bacquisition of (?:land|property)\b',                   'phrase:acquisition_of_land'),
    (r'\bcompensation for land\b',                              'phrase:compensation_land'),
    (r'\baward of compensation\b',                              'phrase:award_compensation'),
    (r'\bpossession of (?:the |said )?(?:land|property|plot|premises|suit)\b',
                                                                'phrase:possession_land'),
    (r'\bdispossession\b',                                      'phrase:dispossession'),
    (r'\bpartition of (?:the |said )?(?:land|property|estate|family property)\b',
                                                                'phrase:partition'),
    (r'\binheritance of (?:land|property)\b',                   'phrase:inheritance'),
    (r'\bcompensation (?:awarded|paid|fixed) (?:for|at)\b',     'phrase:compensation_award'),
    (r'\bsection 18 of the act\b',                              'phrase:s18_laa'),  # LAA reference
    (r'\bsection 4 notification\b|\bnotification under section 4\b', 'phrase:s4_notification'),

    # ── Revenue / cadastral identifiers (near-zero false positives) ───────
    (r'\bkhasra\b',                                             'record:khasra'),
    (r'\bkhatauni\b',                                           'record:khatauni'),
    (r'\bpatta\b',                                              'record:patta'),
    (r'\bpatwari\b',                                            'record:patwari'),
    (r'\btehsildar\b',                                          'record:tehsildar'),
    (r'\bnaib.?tehsildar\b',                                    'record:naib_tehsildar'),
    (r'\bkhatian\b',                                            'record:khatian'),
    (r'\bjamabandi\b',                                          'record:jamabandi'),
    (r'\bgirdawari\b',                                          'record:girdawari'),
    (r'\bdaakhil.?kharij\b',                                    'record:daakhil_kharij'),
    (r'\bchakbandi\b',                                          'record:chakbandi'),
    (r'\bsurvey no(?:\.|\b)',                                   'record:survey_no'),
    (r'\bplot no(?:\.|\b)',                                     'record:plot_no'),
    (r'\bkhasra no(?:\.|\b)',                                   'record:khasra_no'),
    (r'\bmarla\b',                                              'record:marla'),
    (r'\bkanal\b',                                              'record:kanal'),
    (r'\bguntha\b|\bgunta\b',                                   'record:guntha'),
    (r'\bbigha\b',                                              'record:bigha'),
    (r'\bkatto\b',                                              'record:katto'),
    (r'\bchak\b',                                               'record:chak'),

    # ── Official parties / bodies ──────────────────────────────────────────
    (r'\bland acquisition officer\b',                           'party:lao'),
    (r'\bspecial land acquisition officer\b',                   'party:slao'),
    (r'\bland tribunal\b',                                      'party:land_tribunal'),
    (r'\brent controller\b',                                    'party:rent_controller'),
    (r'\brevenue (?:divisional )?officer\b',                    'party:revenue_officer'),
    (r'\bboard of revenue\b',                                   'party:board_of_revenue'),
    (r'\bsettlement officer\b',                                 'party:settlement_officer'),
    (r'\bhousing board\b',                                      'party:housing_board'),
    (r'\bdevelopment authority\b',                              'party:dev_authority'),
    (r'\bimprovement trust\b',                                  'party:improvement_trust'),
    (r'\bsurplus land (?:determination)?\b',                    'party:surplus_land'),
    (r'\btaluq\b|\btaluqa\b|\btehsil\b',                       'party:taluq'),
]

# Compile once at module level so worker processes inherit the compiled set
COMPILED = [(re.compile(pat, re.IGNORECASE), label) for pat, label in _RAW_PATTERNS]


# ---------------------------------------------------------------------------
# Worker (must be module-level for ProcessPoolExecutor pickling)
# ---------------------------------------------------------------------------

def scan_pdf(args: tuple) -> dict:
    """
    Read pages 1–PAGES_TO_READ of a PDF and return matched keyword labels.
    Returns a dict: {filename, matched_keywords (comma-sep string or '')}
    """
    row, pdf_path = args
    filename = row['filename']

    if not pdf_path.exists():
        return {'filename': filename, 'matched_keywords': ''}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages[:PAGES_TO_READ]
            text = ' '.join(p.extract_text() or '' for p in pages)
    except Exception:
        return {'filename': filename, 'matched_keywords': ''}

    matched = [label for pattern, label in COMPILED if pattern.search(text)]
    return {
        'filename':        filename,
        'matched_keywords': ','.join(matched),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Filter land dispute cases from metadata.csv")
    parser.add_argument('--workers', type=int, default=max(1, os.cpu_count() - 1),
                        help='Parallel worker processes (default: cpu_count - 1)')
    parser.add_argument('--test', type=int, default=0, metavar='N',
                        help='Process only the first N rows (dry run)')
    args = parser.parse_args()

    if not METADATA_FILE.exists():
        print(f"ERROR: {METADATA_FILE} not found — run extract_metadata.py first.", file=sys.stderr)
        sys.exit(1)

    df_meta = pd.read_csv(METADATA_FILE)
    if args.test:
        df_meta = df_meta.head(args.test)
        print(f"[TEST MODE] Processing first {args.test} rows")

    tasks = [
        (row.to_dict(), DATA_ROOT / str(int(row['year'])) / row['filename'])
        for _, row in df_meta.iterrows()
    ]

    print(f"Cases to scan   : {len(tasks):,}")
    print(f"Worker processes: {args.workers}")
    print(f"Pages per PDF   : {PAGES_TO_READ}")
    print(f"Keyword patterns: {len(COMPILED)}\n")

    scan_results = []
    CKPT_EVERY = 3000

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(scan_pdf, t): t for t in tasks}
        for i, future in enumerate(
            tqdm(as_completed(futures), total=len(tasks), desc="Scanning", unit="pdf")
        ):
            scan_results.append(future.result())
            if (i + 1) % CKPT_EVERY == 0:
                pd.DataFrame(scan_results).to_csv(CHECKPOINT, index=False)

    df_scan = pd.DataFrame(scan_results)
    df_merged = df_meta.merge(df_scan, on='filename', how='left')
    df_land = (
        df_merged[
            df_merged['matched_keywords'].notna() &
            (df_merged['matched_keywords'] != '')
        ]
        .copy()
        .sort_values(['year', 'filename'])
        .reset_index(drop=True)
    )

    if args.test:
        print("\n--- Matched cases (test mode) ---")
        for _, r in df_land.iterrows():
            print(f"  {r['year']}  {r['filename'][:60]:<60}  {r['matched_keywords']}")
        print(f"\nMatched {len(df_land)} / {len(df_meta)} in test sample")
        return

    df_land.to_csv(OUTPUT_FILE, index=False)
    if CHECKPOINT.exists():
        CHECKPOINT.unlink()

    total   = len(df_meta)
    matched = len(df_land)
    print(f"\nDone.")
    print(f"  Total scanned      : {total:,}")
    print(f"  Land dispute cases : {matched:,}  ({100 * matched / total:.1f}%)")
    print(f"  Saved to           : {OUTPUT_FILE}")
    print(f"\nTop keyword triggers:")
    kw_series = df_land['matched_keywords'].str.split(',').explode()
    print(kw_series.value_counts().head(25).to_string())
    print(f"\nBreakdown by year (sampled):")
    yr = df_land.groupby('year').size()
    print(yr[yr.index % 10 == 0].to_string())


if __name__ == '__main__':
    main()
