"""
Cleanup pass for citation JSON: find & remove captcha-skipped cases.

THE PROBLEM THIS FIXES
----------------------
When Cloudflare interrupted a case mid-run, the #citeselect dropdown didn't
load, so that case got saved with precedents (cites) = 0 even though it really
has precedents. Because the scraper treats any saved doc_id as "done", a plain
re-run would SKIP these and freeze the wrong 0. This script removes those
suspect entries so the next scraper run re-fetches them properly.

HOW IT DECIDES A CASE IS SUSPECT
--------------------------------
precedent_count == 0  AND  cited_by_count > 0
A case cited by others almost always cites something itself, so cites=0 there
is almost certainly a captcha miss, not a true zero. (A genuine cites=0 with
cited_by=0 is left alone.)

USAGE
-----
  1. DRY RUN first (shows suspects, changes nothing):
       python cleanup_zero_cites.py

  2. If the list looks right, REMOVE them (writes a .bak backup first):
       python cleanup_zero_cites.py --fix

  3. Then just re-run your scraper normally — it re-fetches only the removed cases:
       python scraper_citation_full.py

Optional: --min-cited-by N   (default 1) only flag cases with at least N
inbound citations, if you want to be stricter about what counts as suspect.
"""

import argparse
import json
import shutil
from pathlib import Path

DEFAULT_JSON = Path("citations_4501_5250.json")


def main():
    ap = argparse.ArgumentParser(description="Find/remove captcha-skipped zero-cites cases.")
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON,
                    help="path to the citations JSON (default: citations_4501_5250.json)")
    ap.add_argument("--fix", action="store_true",
                    help="actually remove suspect cases (default is dry-run / list only)")
    ap.add_argument("--min-cited-by", type=int, default=1,
                    help="only flag cases with at least this many inbound citations (default 1)")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: file not found: {args.json}")
        return

    with args.json.open(encoding="utf-8") as f:
        data = json.load(f)

    total = len(data)

    # classify
    suspects = []
    genuine_zero = []   # cites 0 AND cited_by 0 — left alone, probably real
    for rec in data:
        cites = rec.get("precedent_count", len(rec.get("precedents", [])))
        cited = rec.get("cited_by_count", len(rec.get("cited_by", [])))
        if cites == 0 and cited >= args.min_cited_by:
            suspects.append(rec)
        elif cites == 0 and cited == 0:
            genuine_zero.append(rec)

    print(f"Loaded {total} cases from {args.json.name}")
    print(f"  Suspect (cites=0 but cited_by>={args.min_cited_by}): {len(suspects)}")
    print(f"  Genuine-looking zero (cites=0 and cited_by=0):        {len(genuine_zero)}  [left alone]")
    print()

    if not suspects:
        print("No suspect cases found — nothing to clean up. You're good.")
        return

    print("Suspect cases (these will be removed and re-fetched on next scraper run):")
    for rec in suspects:
        did = rec.get("source_doc_id", "?")
        name = (rec.get("source_name") or "")[:50]
        cited = rec.get("cited_by_count", len(rec.get("cited_by", [])))
        print(f"  doc {did:<12} cited_by={cited:<4} {name}")
    print()

    if not args.fix:
        print("DRY RUN — nothing changed.")
        print("If this list looks right, re-run with --fix to remove them, then")
        print("re-run your scraper:  python scraper_citation_full.py")
        return

    # --fix: back up, then write the filtered list
    backup = args.json.with_suffix(args.json.suffix + ".bak")
    shutil.copy2(args.json, backup)

    suspect_ids = {rec.get("source_doc_id") for rec in suspects}
    kept = [rec for rec in data if rec.get("source_doc_id") not in suspect_ids]

    with args.json.open("w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)

    print(f"Backup written: {backup.name}")
    print(f"Removed {len(suspects)} suspect cases. {len(kept)} remain in {args.json.name}.")
    print("Now re-run your scraper to re-fetch the removed cases:")
    print("  python scraper_citation_full.py")


if __name__ == "__main__":
    main()
