#!/usr/bin/env python3
"""Download the CourtListener bulk files needed for the Stage 1 citation export."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


BUCKET = "s3://com-courtlistener-storage/bulk-data"
REGION = "us-west-2"
DEFAULT_SNAPSHOT = "2026-03-31"


def required_files(snapshot: str) -> list[str]:
    return [
        f"schema-{snapshot}.sql",
        f"opinion-clusters-{snapshot}.csv.bz2",
        f"opinions-{snapshot}.csv.bz2",
        f"citation-map-{snapshot}.csv.bz2",
    ]


def run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, check=True)


def download_file(filename: str, output_dir: Path, force: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / filename
    if target.exists() and not force:
        print(f"Skipping existing file: {target}")
        return
    run(
        [
            "aws",
            "s3",
            "cp",
            f"{BUCKET}/{filename}",
            str(target),
            "--no-sign-request",
            "--region",
            REGION,
            "--no-progress",
        ]
    )


def print_remote_sizes(snapshot: str) -> None:
    print("Remote files:")
    for filename in required_files(snapshot):
        command = [
            "aws",
            "s3",
            "ls",
            f"{BUCKET}/{filename}",
            "--no-sign-request",
            "--region",
            REGION,
        ]
        subprocess.run(command, check=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download CourtListener Phase 2 bulk files.")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="Bulk snapshot date.")
    parser.add_argument("--bulk-dir", default="./courtlistener-bulk", help="Where to store bulk files.")
    parser.add_argument("--force", action="store_true", help="Redownload existing files.")
    parser.add_argument("--sizes-only", action="store_true", help="Only print remote file sizes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("aws") is None:
        raise SystemExit("aws CLI is required. Install it before downloading bulk data.")

    print_remote_sizes(args.snapshot)
    if args.sizes_only:
        return 0

    output_dir = Path(args.bulk_dir)
    for filename in required_files(args.snapshot):
        download_file(filename, output_dir, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
