#!/usr/bin/env python3
"""Stream CourtListener bulk CSVs into the canonical citation JSON schema."""

from __future__ import annotations

import argparse
import bz2
import csv
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is expected locally, but optional.
    tqdm = None


BASE_SITE_URL = "https://www.courtlistener.com"
DEFAULT_SNAPSHOT = "2026-03-31"
DEFAULT_QUERY = "medical malpractice"
CHUNK_SIZE = 750


def set_csv_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def progress(iterable: Iterable, desc: str, disable: bool = False):
    if tqdm is None or disable:
        return iterable
    return tqdm(iterable, desc=desc, unit="row")


def open_csv(path: Path):
    if path.suffix == ".bz2":
        return bz2.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


def courtlistener_csv_reader(handle):
    return csv.reader(handle, escapechar="\\")


def make_url(cluster_id: int, slug: str | None) -> str:
    clean_slug = (slug or "").strip("/")
    if clean_slug:
        return f"{BASE_SITE_URL}/opinion/{cluster_id}/{clean_slug}/"
    return f"{BASE_SITE_URL}/opinion/{cluster_id}/"


def valid_year(date_filed: str | None) -> str:
    value = (date_filed or "").strip()[:4]
    return value if re.fullmatch(r"\d{4}", value) else ""


def best_case_name(case_name_full: str | None, case_name: str | None, short: str | None = None) -> str:
    for value in (case_name_full, case_name, short):
        if value and value.strip():
            return value.strip()
    return ""


def preferred_case_name(case_name: str | None, case_name_full: str | None, short: str | None = None) -> str:
    for value in (case_name, case_name_full, short):
        if value and value.strip():
            return value.strip()
    return ""


def make_matcher(query: str, mode: str):
    phrase = query.lower().strip()
    terms = [term for term in re.split(r"\s+", phrase) if term]

    def field_matches(value: str | None) -> bool:
        if not value:
            return False
        haystack = value.lower()
        if mode == "phrase":
            return phrase in haystack
        return all(term in haystack for term in terms)

    def row_matches(values: Iterable[str | None]) -> bool:
        return any(field_matches(value) for value in values)

    return row_matches


def init_db(path: Path, reset: bool) -> sqlite3.Connection:
    if reset:
        for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if candidate.exists():
                candidate.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute("PRAGMA cache_size=-200000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clusters (
          id INTEGER PRIMARY KEY,
          case_name TEXT NOT NULL,
          case_name_full TEXT NOT NULL,
          date_filed TEXT NOT NULL,
          slug TEXT NOT NULL,
          precedential_status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS opinions (
          id INTEGER PRIMARY KEY,
          cluster_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS matched_clusters (
          cluster_id INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS edges (
          source_cluster_id INTEGER NOT NULL,
          cited_cluster_id INTEGER NOT NULL,
          PRIMARY KEY (source_cluster_id, cited_cluster_id)
        );

        CREATE TABLE IF NOT EXISTS edge_opinions (
          source_cluster_id INTEGER NOT NULL,
          cited_cluster_id INTEGER NOT NULL,
          citing_opinion_id INTEGER NOT NULL,
          cited_opinion_id INTEGER NOT NULL,
          PRIMARY KEY (
            source_cluster_id,
            cited_cluster_id,
            citing_opinion_id,
            cited_opinion_id
          )
        );
        """
    )
    return conn


def csv_path(bulk_dir: Path, stem: str, snapshot: str) -> Path:
    compressed = bulk_dir / f"{stem}-{snapshot}.csv.bz2"
    if compressed.exists():
        return compressed
    plain = bulk_dir / f"{stem}-{snapshot}.csv"
    if plain.exists():
        return plain
    raise SystemExit(f"Missing {compressed}. Run download_courtlistener_bulk.py first.")


def require_columns(header: list[str], names: list[str], path: Path) -> dict[str, int]:
    index = {name: pos for pos, name in enumerate(header)}
    missing = [name for name in names if name not in index]
    if missing:
        raise SystemExit(f"{path} is missing expected columns: {', '.join(missing)}")
    return index


def row_value(row: list[str], idx: dict[str, int], name: str) -> str:
    position = idx[name]
    return row[position] if position < len(row) else ""


def opinion_match_values(row: list[str], idx: dict[str, int]) -> tuple[str, ...]:
    plain_text = row_value(row, idx, "plain_text")
    if plain_text.strip():
        return (plain_text,)
    return (
        row_value(row, idx, "html_with_citations"),
        row_value(row, idx, "html"),
    )


def insert_many(conn: sqlite3.Connection, sql: str, rows: list[tuple], batch_size: int) -> None:
    if len(rows) >= batch_size:
        conn.executemany(sql, rows)
        rows.clear()


def index_clusters(
    conn: sqlite3.Connection,
    path: Path,
    query: str,
    match_mode: str,
    batch_size: int,
    max_rows: int | None,
    quiet: bool,
    progress_every: int,
) -> int:
    matcher = make_matcher(query, match_mode)
    inserted = 0
    cluster_rows: list[tuple] = []
    match_rows: list[tuple] = []

    with open_csv(path) as handle:
        reader = courtlistener_csv_reader(handle)
        header = next(reader)
        idx = require_columns(
            header,
            [
                "id",
                "date_filed",
                "slug",
                "case_name_short",
                "case_name",
                "case_name_full",
                "precedential_status",
                "nature_of_suit",
                "syllabus",
                "headnotes",
                "summary",
            ],
            path,
        )
        for row_num, row in enumerate(progress(reader, "Indexing clusters", quiet), start=1):
            if max_rows and row_num > max_rows:
                break
            try:
                cluster_id = int(row_value(row, idx, "id"))
            except (ValueError, IndexError):
                continue

            case_name = preferred_case_name(
                row_value(row, idx, "case_name"),
                row_value(row, idx, "case_name_full"),
                row_value(row, idx, "case_name_short"),
            )
            case_name_full = best_case_name(
                row_value(row, idx, "case_name_full"),
                row_value(row, idx, "case_name"),
                row_value(row, idx, "case_name_short"),
            )
            cluster_rows.append(
                (
                    cluster_id,
                    case_name,
                    case_name_full,
                    row_value(row, idx, "date_filed"),
                    row_value(row, idx, "slug"),
                    row_value(row, idx, "precedential_status"),
                )
            )

            if matcher(
                (
                    row_value(row, idx, "case_name_short"),
                    row_value(row, idx, "case_name"),
                    row_value(row, idx, "case_name_full"),
                    row_value(row, idx, "nature_of_suit"),
                    row_value(row, idx, "syllabus"),
                    row_value(row, idx, "headnotes"),
                    row_value(row, idx, "summary"),
                )
            ):
                match_rows.append((cluster_id,))

            insert_many(
                conn,
                "INSERT OR REPLACE INTO clusters VALUES (?, ?, ?, ?, ?, ?)",
                cluster_rows,
                batch_size,
            )
            insert_many(
                conn,
                "INSERT OR IGNORE INTO matched_clusters VALUES (?)",
                match_rows,
                batch_size,
            )
            inserted += 1
            if progress_every and inserted % progress_every == 0:
                print(f"Indexed cluster rows so far: {inserted}", flush=True)

    if cluster_rows:
        conn.executemany("INSERT OR REPLACE INTO clusters VALUES (?, ?, ?, ?, ?, ?)", cluster_rows)
    if match_rows:
        conn.executemany("INSERT OR IGNORE INTO matched_clusters VALUES (?)", match_rows)
    conn.commit()
    return inserted


def index_opinions(
    conn: sqlite3.Connection,
    path: Path,
    query: str,
    match_mode: str,
    batch_size: int,
    max_rows: int | None,
    quiet: bool,
    progress_every: int,
) -> int:
    matcher = make_matcher(query, match_mode)
    inserted = 0
    opinion_rows: list[tuple] = []
    match_rows: list[tuple] = []

    with open_csv(path) as handle:
        reader = courtlistener_csv_reader(handle)
        header = next(reader)
        idx = require_columns(
            header,
            ["id", "cluster_id", "plain_text", "html_with_citations", "html"],
            path,
        )
        for row_num, row in enumerate(progress(reader, "Indexing opinions", quiet), start=1):
            if max_rows and row_num > max_rows:
                break
            try:
                opinion_id = int(row_value(row, idx, "id"))
                cluster_id = int(row_value(row, idx, "cluster_id"))
            except (ValueError, IndexError):
                continue

            opinion_rows.append((opinion_id, cluster_id))
            if matcher(opinion_match_values(row, idx)):
                match_rows.append((cluster_id,))

            insert_many(
                conn,
                "INSERT OR REPLACE INTO opinions VALUES (?, ?)",
                opinion_rows,
                batch_size,
            )
            insert_many(
                conn,
                "INSERT OR IGNORE INTO matched_clusters VALUES (?)",
                match_rows,
                batch_size,
            )
            inserted += 1
            if progress_every and inserted % progress_every == 0:
                print(f"Indexed opinion rows so far: {inserted}", flush=True)

    if opinion_rows:
        conn.executemany("INSERT OR REPLACE INTO opinions VALUES (?, ?)", opinion_rows)
    if match_rows:
        conn.executemany("INSERT OR IGNORE INTO matched_clusters VALUES (?)", match_rows)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opinions_cluster_id ON opinions(cluster_id)")
    conn.commit()
    return inserted


def load_source_opinions(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute(
        """
        SELECT o.id, o.cluster_id
        FROM opinions o
        JOIN matched_clusters m ON m.cluster_id = o.cluster_id
        """
    )
    return {int(opinion_id): int(cluster_id) for opinion_id, cluster_id in rows}


def build_edges(
    conn: sqlite3.Connection,
    path: Path,
    source_opinions: dict[int, int],
    batch_size: int,
    max_rows: int | None,
    quiet: bool,
    keep_self_citations: bool,
) -> int:
    if not source_opinions:
        warn("No source opinions matched the query; skipping citation-map join.")
        return 0

    inserted = 0
    edge_rows: list[tuple] = []
    edge_opinion_rows: list[tuple] = []
    cited_cache: dict[int, int | None] = {}
    lookup = conn.execute

    with open_csv(path) as handle:
        reader = courtlistener_csv_reader(handle)
        header = next(reader)
        idx = require_columns(header, ["cited_opinion_id", "citing_opinion_id"], path)
        for row_num, row in enumerate(progress(reader, "Joining citation map", quiet), start=1):
            if max_rows and row_num > max_rows:
                break
            try:
                citing_opinion_id = int(row_value(row, idx, "citing_opinion_id"))
            except (ValueError, IndexError):
                continue

            source_cluster_id = source_opinions.get(citing_opinion_id)
            if source_cluster_id is None:
                continue

            try:
                cited_opinion_id = int(row_value(row, idx, "cited_opinion_id"))
            except (ValueError, IndexError):
                continue

            if cited_opinion_id not in cited_cache:
                result = lookup("SELECT cluster_id FROM opinions WHERE id = ?", (cited_opinion_id,)).fetchone()
                cited_cache[cited_opinion_id] = int(result[0]) if result else None
            cited_cluster_id = cited_cache[cited_opinion_id]
            if cited_cluster_id is None:
                continue
            if not keep_self_citations and cited_cluster_id == source_cluster_id:
                continue

            edge_rows.append((source_cluster_id, cited_cluster_id))
            edge_opinion_rows.append(
                (source_cluster_id, cited_cluster_id, citing_opinion_id, cited_opinion_id)
            )
            insert_many(
                conn,
                "INSERT OR IGNORE INTO edges VALUES (?, ?)",
                edge_rows,
                batch_size,
            )
            insert_many(
                conn,
                "INSERT OR IGNORE INTO edge_opinions VALUES (?, ?, ?, ?)",
                edge_opinion_rows,
                batch_size,
            )
            inserted += 1

    if edge_rows:
        conn.executemany("INSERT OR IGNORE INTO edges VALUES (?, ?)", edge_rows)
    if edge_opinion_rows:
        conn.executemany("INSERT OR IGNORE INTO edge_opinions VALUES (?, ?, ?, ?)", edge_opinion_rows)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_cluster_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_opinions_source ON edge_opinions(source_cluster_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_opinions_cited ON edge_opinions(cited_cluster_id)")
    conn.commit()
    return inserted


def canonical_case(row: sqlite3.Row) -> dict[str, object] | None:
    cluster_id = int(row["id"])
    year = valid_year(row["date_filed"])
    case_name_full = row["case_name_full"] if "case_name_full" in row.keys() else None
    case_name = preferred_case_name(row["case_name"], case_name_full)
    if not case_name or not year:
        return None
    return {
        "case": case_name,
        "year": year,
        "url": make_url(cluster_id, row["slug"]),
        "precedents": [],
    }


def canonical_precedent(row: sqlite3.Row) -> dict[str, str] | None:
    cluster_id = int(row["id"])
    case_name_full = row["case_name_full"] if "case_name_full" in row.keys() else None
    case_name = preferred_case_name(row["case_name"], case_name_full)
    if not case_name:
        return None
    return {
        "case": case_name,
        "url": make_url(cluster_id, row["slug"]),
    }


def write_json(path: Path, data: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temp.replace(path)


def split_ids(value: str | None) -> list[str]:
    if not value:
        return []
    unique = {item for item in value.split(",") if item}
    return sorted(unique, key=lambda item: int(item) if item.isdigit() else item)


def load_source_opinion_ids(conn: sqlite3.Connection) -> dict[int, list[str]]:
    rows = conn.execute(
        """
        SELECT o.cluster_id, GROUP_CONCAT(o.id)
        FROM opinions o
        JOIN matched_clusters m ON m.cluster_id = o.cluster_id
        GROUP BY o.cluster_id
        """
    )
    return {int(cluster_id): split_ids(ids) for cluster_id, ids in rows}


def rich_case(
    source: sqlite3.Row,
    source_opinion_ids: list[str],
    precedents: list[dict[str, object]],
) -> dict[str, object] | None:
    year = valid_year(source["date_filed"])
    case_name = preferred_case_name(source["case_name"], source["case_name_full"])
    if not case_name or not year:
        return None
    source_name = best_case_name(source["case_name_full"], source["case_name"])
    return {
        "source": "courtlistener",
        "source_cluster_id": str(source["id"]),
        "source_opinion_ids": source_opinion_ids,
        "source_case": case_name,
        "source_year": int(year),
        "source_url": make_url(int(source["id"]), source["slug"]),
        "source_name": source_name,
        "precedent_count": len(precedents),
        "precedents": precedents,
    }


def write_jsonl(path: Path, data: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        for item in data:
            handle.write(json.dumps(item, ensure_ascii=False))
            handle.write("\n")
    temp.replace(path)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    fieldnames = [
        "source",
        "source_cluster_id",
        "source_opinion_ids",
        "source_case",
        "source_year",
        "source_url",
        "cited_cluster_id",
        "citing_opinion_ids",
        "cited_opinion_ids",
        "cited_name",
        "cited_url",
    ]
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def write_chunk(
    folder: Path,
    canonical_chunk: list[dict[str, object]],
    rich_chunk: list[dict[str, object]],
    csv_rows: list[dict[str, object]],
) -> None:
    write_json(folder / "citations_formatted.json", canonical_chunk)
    write_jsonl(folder / "citations.jsonl", rich_chunk)
    write_csv(folder / "citations.csv", csv_rows)


def export_chunks(
    conn: sqlite3.Connection,
    output_dir: Path,
    chunk_size: int,
    max_cases: int | None,
) -> tuple[int, list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn.row_factory = sqlite3.Row
    canonical_chunk: list[dict[str, object]] = []
    rich_chunk: list[dict[str, object]] = []
    csv_rows: list[dict[str, object]] = []
    folders: list[str] = []
    total = 0
    source_opinion_ids_by_cluster = load_source_opinion_ids(conn)

    sources = conn.execute(
        """
        SELECT c.id, c.case_name, c.case_name_full, c.date_filed, c.slug
        FROM matched_clusters m
        JOIN clusters c ON c.id = m.cluster_id
        ORDER BY c.date_filed, c.id
        """
    )

    for source in sources:
        if max_cases and total >= max_cases:
            break
        case = canonical_case(source)
        if case is None:
            continue

        precedents: list[dict[str, str]] = []
        rich_precedents: list[dict[str, object]] = []
        for precedent_row in conn.execute(
            """
            SELECT
              c.id,
              c.case_name,
              c.case_name_full,
              c.date_filed,
              c.slug,
              GROUP_CONCAT(DISTINCT eo.citing_opinion_id) AS citing_opinion_ids,
              GROUP_CONCAT(DISTINCT eo.cited_opinion_id) AS cited_opinion_ids
            FROM edge_opinions eo
            JOIN clusters c ON c.id = eo.cited_cluster_id
            WHERE eo.source_cluster_id = ?
            GROUP BY c.id, c.case_name, c.case_name_full, c.date_filed, c.slug
            ORDER BY c.date_filed, c.case_name, c.id
            """,
            (source["id"],),
        ):
            precedent = canonical_precedent(precedent_row)
            if precedent:
                precedents.append(precedent)
                cited_name = preferred_case_name(
                    precedent_row["case_name"],
                    precedent_row["case_name_full"],
                )
                cited_opinion_ids = split_ids(precedent_row["cited_opinion_ids"])
                citing_opinion_ids = split_ids(precedent_row["citing_opinion_ids"])
                rich_precedent = {
                    "name": cited_name,
                    "cluster_id": str(precedent_row["id"]),
                    "opinion_ids": cited_opinion_ids,
                    "citing_opinion_ids": citing_opinion_ids,
                    "url": make_url(int(precedent_row["id"]), precedent_row["slug"]),
                }
                rich_precedents.append(rich_precedent)

        case["precedents"] = precedents
        source_opinion_ids = source_opinion_ids_by_cluster.get(int(source["id"]), [])
        rich = rich_case(source, source_opinion_ids, rich_precedents)
        if rich is None:
            continue

        canonical_chunk.append(case)
        rich_chunk.append(rich)
        for precedent in rich_precedents:
            csv_rows.append(
                {
                    "source": "courtlistener",
                    "source_cluster_id": str(source["id"]),
                    "source_opinion_ids": "|".join(source_opinion_ids),
                    "source_case": rich["source_case"],
                    "source_year": rich["source_year"],
                    "source_url": rich["source_url"],
                    "cited_cluster_id": precedent["cluster_id"],
                    "citing_opinion_ids": "|".join(precedent["citing_opinion_ids"]),
                    "cited_opinion_ids": "|".join(precedent["opinion_ids"]),
                    "cited_name": precedent["name"],
                    "cited_url": precedent["url"],
                }
            )
        total += 1

        if len(canonical_chunk) == chunk_size:
            start = total - chunk_size + 1
            folder = output_dir / f"{start}-{total}"
            write_chunk(folder, canonical_chunk, rich_chunk, csv_rows)
            folders.append(str(folder))
            canonical_chunk = []
            rich_chunk = []
            csv_rows = []

    if canonical_chunk:
        start = total - len(canonical_chunk) + 1
        folder = output_dir / f"{start}-{total}"
        write_chunk(folder, canonical_chunk, rich_chunk, csv_rows)
        folders.append(str(folder))

    return total, folders


def print_stats(conn: sqlite3.Connection, total_exported: int, folders: list[str], start_time: float) -> None:
    matched = conn.execute("SELECT COUNT(*) FROM matched_clusters").fetchone()[0]
    opinions = conn.execute("SELECT COUNT(*) FROM opinions").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    edge_opinions = conn.execute("SELECT COUNT(*) FROM edge_opinions").fetchone()[0]
    elapsed = time.time() - start_time
    print("\nSummary")
    print(f"Matched clusters indexed : {matched}")
    print(f"Opinion rows indexed     : {opinions}")
    print(f"Cluster citation edges   : {edges}")
    print(f"Opinion citation rows    : {edge_opinions}")
    print(f"Total cases exported     : {total_exported}")
    print(f"Elapsed seconds          : {elapsed:.1f}")
    print("Folders created          :")
    if folders:
        for folder in folders:
            print(f"  {folder}/")
    else:
        print("  None")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract CourtListener bulk case-law data into canonical citation JSON."
    )
    parser.add_argument("--bulk-dir", default="./courtlistener-bulk", help="Folder with bulk CSV files.")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="Bulk snapshot date.")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Query to match in title/text.")
    parser.add_argument(
        "--match-mode",
        choices=("all-terms", "phrase"),
        default="all-terms",
        help="all-terms approximates CourtListener q=medical malpractice; phrase is stricter.",
    )
    parser.add_argument("--output-dir", default="./citations-data-us", help="Output JSON folder.")
    parser.add_argument("--db", default="./courtlistener-bulk/stage2_index.sqlite", help="SQLite index path.")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE, help="Cases per output folder.")
    parser.add_argument("--max-cases", type=int, help="Optional export cap after indexing.")
    parser.add_argument("--batch-size", type=int, default=5000, help="SQLite insert batch size.")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=500000,
        help="Print progress after this many opinion rows; 0 disables checkpoint prints.",
    )
    parser.add_argument("--reset-db", action="store_true", help="Rebuild the SQLite index from scratch.")
    parser.add_argument("--quiet", action="store_true", help="Disable progress bars.")
    parser.add_argument("--keep-self-citations", action="store_true", help="Keep same-cluster citation edges.")
    parser.add_argument(
        "--max-cluster-rows",
        type=int,
        help="Debug only: stop after N cluster rows.",
    )
    parser.add_argument(
        "--max-opinion-rows",
        type=int,
        help="Debug only: stop after N opinion rows.",
    )
    parser.add_argument(
        "--max-citation-rows",
        type=int,
        help="Debug only: stop after N citation-map rows.",
    )
    return parser.parse_args()


def main() -> int:
    set_csv_limit()
    args = parse_args()
    start_time = time.time()

    bulk_dir = Path(args.bulk_dir)
    cluster_path = csv_path(bulk_dir, "opinion-clusters", args.snapshot)
    opinion_path = csv_path(bulk_dir, "opinions", args.snapshot)
    citation_path = csv_path(bulk_dir, "citation-map", args.snapshot)

    conn = init_db(Path(args.db), args.reset_db)
    print(f"SQLite index: {Path(args.db).resolve()}")

    if args.reset_db or conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0] == 0:
        cluster_count = index_clusters(
            conn,
            cluster_path,
            args.query,
            args.match_mode,
            args.batch_size,
            args.max_cluster_rows,
            args.quiet,
            args.progress_every,
        )
        print(f"Indexed cluster rows: {cluster_count}")
    else:
        print("Cluster index already exists; use --reset-db to rebuild.")

    if args.reset_db or conn.execute("SELECT COUNT(*) FROM opinions").fetchone()[0] == 0:
        opinion_count = index_opinions(
            conn,
            opinion_path,
            args.query,
            args.match_mode,
            args.batch_size,
            args.max_opinion_rows,
            args.quiet,
            args.progress_every,
        )
        print(f"Indexed opinion rows: {opinion_count}")
    else:
        print("Opinion index already exists; use --reset-db to rebuild.")

    if args.reset_db or conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0:
        source_opinions = load_source_opinions(conn)
        print(f"Matched source opinions: {len(source_opinions)}")
        edge_count = build_edges(
            conn,
            citation_path,
            source_opinions,
            args.batch_size,
            args.max_citation_rows,
            args.quiet,
            args.keep_self_citations,
        )
        print(f"Observed relevant citation rows: {edge_count}")
    else:
        print("Citation edge index already exists; use --reset-db to rebuild.")

    total_exported, folders = export_chunks(
        conn,
        Path(args.output_dir),
        args.chunk_size,
        args.max_cases,
    )
    print_stats(conn, total_exported, folders, start_time)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
