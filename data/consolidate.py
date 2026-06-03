#!/usr/bin/env python3
"""
Consolidate the split supreme_court_judgments dirs into one uniform tree.

The Google-Drive zip parts each unzipped to the same name, so macOS produced
`supreme_court_judgments`, `... 2`, `... 3`, `... 4`. Together they partition the
~26k-case corpus by year. This merges them into a single canonical directory:

    data/supreme_court_judgments/<year>/<sanitized-name>.pdf

- filenames sanitized: spaces, parens and ' [ ] ; -> '_', repeats collapsed
- extensions normalized to lowercase .pdf
- on a name collision, identical content is dropped as a duplicate; differing
  content keeps both (a _dupN suffix is added)
"""
import hashlib
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(BASE, "supreme_court_judgments")
SOURCES = [
    os.path.join(BASE, "supreme_court_judgments 2"),
    os.path.join(BASE, "supreme_court_judgments 3"),
    os.path.join(BASE, "supreme_court_judgments 4"),
]
DRY_RUN = "--apply" not in sys.argv


def sanitize(stem: str) -> str:
    stem = re.sub(r"[ '\[\]();]", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


stats = {"moved": 0, "renamed_in_place": 0, "dup_dropped": 0, "dup_kept": 0}


def place(src_path: str, year: str, in_place: bool):
    """Move/rename one pdf into TARGET/year with a sanitized, .pdf name."""
    stem, _ = os.path.splitext(os.path.basename(src_path))
    base = sanitize(stem)
    dest_dir = os.path.join(TARGET, year)
    dest = os.path.join(dest_dir, base + ".pdf")

    if os.path.abspath(src_path) == os.path.abspath(dest):
        return  # already exactly canonical (incl. extension case)

    # Case-insensitive filesystems (default on macOS): src and dest can be the
    # SAME file when only the extension case differs (X.PDF -> X.pdf). Detect
    # that via samefile and rename through a temp name — never treat as a dup.
    if os.path.exists(dest) and os.path.samefile(src_path, dest):
        stats["renamed_in_place"] += 1
        if not DRY_RUN:
            tmp = os.path.join(dest_dir, base + ".pdf.tmprename")
            os.rename(src_path, tmp)
            os.rename(tmp, dest)
        return

    if os.path.exists(dest):  # genuinely different file occupying the name
        if sha256(src_path) == sha256(dest):
            stats["dup_dropped"] += 1
            if not DRY_RUN:
                os.remove(src_path)
            return
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{base}_dup{n}.pdf")
            n += 1
        stats["dup_kept"] += 1

    stats["renamed_in_place" if in_place else "moved"] += 1
    if not DRY_RUN:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src_path, dest)


def walk(root: str, in_place: bool):
    for year in sorted(os.listdir(root)):
        ypath = os.path.join(root, year)
        if not (os.path.isdir(ypath) and year.isdigit()):
            continue
        for name in sorted(os.listdir(ypath)):
            if name.lower().endswith(".pdf"):
                place(os.path.join(ypath, name), year, in_place)


def main():
    print(f"{'DRY RUN' if DRY_RUN else 'APPLY'} — consolidating into {TARGET}\n")
    for src in SOURCES:
        if os.path.isdir(src):
            walk(src, in_place=False)
    walk(TARGET, in_place=True)  # normalize names/extensions already in canonical dir
    print("Result:")
    for k, v in stats.items():
        print(f"  {k:18} {v}")
    if DRY_RUN:
        print("\n(no changes written — rerun with --apply)")


if __name__ == "__main__":
    main()
