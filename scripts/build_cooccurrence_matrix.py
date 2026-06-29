#!/usr/bin/env python3
"""
Build term co-occurrence count and binary matrices from legal case texts.

The script reads informative terms from the noun-phrase and named-entity
codebooks, splits each document into context windows, detects codebook terms in
each window, and counts pairwise co-occurrences.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import DefaultDict, Iterable, Iterator


TOKEN_RE = re.compile(r"[a-z0-9]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def normalize_space(value: str) -> str:
    return " ".join(value.lower().strip().split())


def tokenize(value: str) -> tuple[str, ...]:
    return tuple(TOKEN_RE.findall(value.lower()))


def is_informative(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() == "informative"


def read_codebook_terms(path: Path, term_column: str) -> set[str]:
    terms: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if term_column not in (reader.fieldnames or []):
            raise ValueError(f"{path} is missing expected column: {term_column}")
        if "informative" not in (reader.fieldnames or []):
            raise ValueError(f"{path} is missing expected column: informative")

        for row in reader:
            if not is_informative(row.get("informative")):
                continue

            term = normalize_space(row.get(term_column, ""))
            if term and tokenize(term):
                terms.add(term)

    return terms


def load_terms(np_codebook: Path, ne_codebook: Path) -> list[str]:
    terms = set()
    terms.update(read_codebook_terms(np_codebook, "term"))
    terms.update(read_codebook_terms(ne_codebook, "entity"))
    return sorted(terms)


def build_term_index(terms: Iterable[str]) -> dict[str, list[tuple[tuple[str, ...], str]]]:
    index: DefaultDict[str, list[tuple[tuple[str, ...], str]]] = defaultdict(list)

    for term in terms:
        tokens = tokenize(term)
        if not tokens:
            continue
        index[tokens[0]].append((tokens, term))

    for first_token, candidates in index.items():
        index[first_token] = sorted(
            candidates,
            key=lambda item: len(item[0]),
            reverse=True,
        )

    return dict(index)


def iter_text_files(texts_dir: Path, extensions: tuple[str, ...]) -> Iterator[tuple[str, str]]:
    for path in sorted(texts_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue
        yield str(path.relative_to(texts_dir)), path.read_text(encoding="utf-8", errors="ignore")


def iter_csv_texts(csv_path: Path, text_column: str, id_column: str | None) -> Iterator[tuple[str, str]]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        if text_column not in fieldnames:
            raise ValueError(f"{csv_path} is missing expected text column: {text_column}")
        if id_column and id_column not in fieldnames:
            raise ValueError(f"{csv_path} is missing expected id column: {id_column}")

        for row_number, row in enumerate(reader, start=1):
            doc_id = row.get(id_column) if id_column else str(row_number)
            text = row.get(text_column, "")
            if text:
                yield doc_id or str(row_number), text


def iter_documents(args: argparse.Namespace) -> Iterator[tuple[str, str]]:
    extensions = tuple(ext if ext.startswith(".") else f".{ext}" for ext in args.text_extension)

    if args.texts_dir:
        yield from iter_text_files(args.texts_dir, extensions)

    if args.texts_csv:
        yield from iter_csv_texts(args.texts_csv, args.text_column, args.id_column)


def split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    return paragraphs or ([text.strip()] if text.strip() else [])


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in split_paragraphs(text):
        sentences.extend(part.strip() for part in SENTENCE_SPLIT_RE.split(paragraph) if part.strip())
    return sentences


def token_windows(text: str, size: int, step: int) -> list[str]:
    tokens = tokenize(text)
    if not tokens:
        return []

    windows = []
    for start in range(0, len(tokens), step):
        window_tokens = tokens[start : start + size]
        if not window_tokens:
            break
        windows.append(" ".join(window_tokens))
        if start + size >= len(tokens):
            break

    return windows


def split_windows(text: str, args: argparse.Namespace) -> list[str]:
    if args.window == "document":
        return [text] if text.strip() else []
    if args.window == "paragraph":
        return split_paragraphs(text)
    if args.window == "sentence":
        return split_sentences(text)
    if args.window == "tokens":
        return token_windows(text, args.token_window_size, args.token_window_step)
    raise ValueError(f"Unsupported window type: {args.window}")


def find_terms_in_window(window: str, term_index: dict[str, list[tuple[tuple[str, ...], str]]]) -> set[str]:
    tokens = tokenize(window)
    found: set[str] = set()

    for position, token in enumerate(tokens):
        candidates = term_index.get(token, [])
        for term_tokens, term in candidates:
            end = position + len(term_tokens)
            if tuple(tokens[position:end]) == term_tokens:
                found.add(term)

    return found


def build_pair_counts(
    documents: Iterable[tuple[str, str]],
    term_index: dict[str, list[tuple[tuple[str, ...], str]]],
    args: argparse.Namespace,
) -> tuple[Counter[tuple[str, str]], Counter[str], dict[str, int]]:
    pair_counts: Counter[tuple[str, str]] = Counter()
    term_window_counts: Counter[str] = Counter()
    stats = {
        "documents": 0,
        "windows": 0,
        "windows_with_terms": 0,
        "windows_skipped_for_size": 0,
    }

    for _doc_id, text in documents:
        stats["documents"] += 1
        for window in split_windows(text, args):
            stats["windows"] += 1
            found_terms = find_terms_in_window(window, term_index)
            if not found_terms:
                continue

            stats["windows_with_terms"] += 1
            for term in found_terms:
                term_window_counts[term] += 1

            if args.max_terms_per_window and len(found_terms) > args.max_terms_per_window:
                stats["windows_skipped_for_size"] += 1
                continue

            for term_a, term_b in combinations(sorted(found_terms), 2):
                pair_counts[(term_a, term_b)] += 1

    return pair_counts, term_window_counts, stats


def write_pair_counts(path: Path, pair_counts: Counter[tuple[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["term_a", "term_b", "count"])
        for (term_a, term_b), count in pair_counts.most_common():
            writer.writerow([term_a, term_b, count])


def write_term_window_counts(path: Path, term_window_counts: Counter[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["term", "window_count"])
        for term, count in term_window_counts.most_common():
            writer.writerow([term, count])


def write_matrix(
    path: Path,
    terms: list[str],
    pair_counts: Counter[tuple[str, str]],
    threshold: int | None = None,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["term", *terms])

        for row_term in terms:
            row = [row_term]
            for col_term in terms:
                if row_term == col_term:
                    row.append(0)
                    continue

                key = tuple(sorted((row_term, col_term)))
                count = pair_counts.get(key, 0)
                row.append(int(count >= threshold) if threshold is not None else count)
            writer.writerow(row)


def write_stats(path: Path, stats: dict[str, int], args: argparse.Namespace, term_count: int, pair_count: int) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"terms={term_count}\n")
        handle.write(f"nonzero_pairs={pair_count}\n")
        handle.write(f"window={args.window}\n")
        handle.write(f"threshold={args.threshold}\n")
        if args.window == "tokens":
            handle.write(f"token_window_size={args.token_window_size}\n")
            handle.write(f"token_window_step={args.token_window_step}\n")
        for key, value in stats.items():
            handle.write(f"{key}={value}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build legal term co-occurrence count and binary matrices.",
    )
    parser.add_argument("--np-codebook", type=Path, default=Path("codebooks/np_codebook.csv"))
    parser.add_argument("--ne-codebook", type=Path, default=Path("codebooks/ne_codebook.csv"))
    parser.add_argument("--texts-dir", type=Path, help="Folder containing case text files.")
    parser.add_argument("--texts-csv", type=Path, help="CSV containing case text in one column.")
    parser.add_argument("--text-column", default="text", help="CSV column containing case text.")
    parser.add_argument("--id-column", help="Optional CSV column containing document ids.")
    parser.add_argument(
        "--text-extension",
        action="append",
        default=[".txt"],
        help="Text file extension to read from --texts-dir. Can be repeated.",
    )
    parser.add_argument(
        "--window",
        choices=["paragraph", "sentence", "document", "tokens"],
        default="paragraph",
        help="Context window used for co-occurrence.",
    )
    parser.add_argument("--token-window-size", type=int, default=200)
    parser.add_argument("--token-window-step", type=int, default=200)
    parser.add_argument("--threshold", type=int, default=5)
    parser.add_argument(
        "--max-terms-per-window",
        type=int,
        default=0,
        help="Skip pair counting for windows with more than this many terms. 0 disables the limit.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/cooccurrence"))
    parser.add_argument(
        "--skip-dense-matrices",
        action="store_true",
        help="Only write pair lists and stats. Useful when the codebook is very large.",
    )

    args = parser.parse_args()

    if not args.texts_dir and not args.texts_csv:
        parser.error("Provide --texts-dir, --texts-csv, or both.")
    if args.token_window_size <= 0:
        parser.error("--token-window-size must be positive.")
    if args.token_window_step <= 0:
        parser.error("--token-window-step must be positive.")
    if args.threshold < 1:
        parser.error("--threshold must be at least 1.")

    return args


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    terms = load_terms(args.np_codebook, args.ne_codebook)
    term_index = build_term_index(terms)
    pair_counts, term_window_counts, stats = build_pair_counts(iter_documents(args), term_index, args)

    write_pair_counts(args.output_dir / "cooccurrence_pairs.csv", pair_counts)
    write_pair_counts(
        args.output_dir / "cooccurrence_pairs_binary.csv",
        Counter({pair: 1 for pair, count in pair_counts.items() if count >= args.threshold}),
    )
    write_term_window_counts(args.output_dir / "term_window_counts.csv", term_window_counts)

    if not args.skip_dense_matrices:
        write_matrix(args.output_dir / "cooccurrence_counts.csv", terms, pair_counts)
        write_matrix(args.output_dir / "cooccurrence_binary.csv", terms, pair_counts, threshold=args.threshold)

    write_stats(args.output_dir / "run_stats.txt", stats, args, len(terms), len(pair_counts))

    print(f"Loaded {len(terms)} informative terms.")
    print(f"Processed {stats['documents']} documents and {stats['windows']} windows.")
    print(f"Found {len(pair_counts)} non-zero co-occurring pairs.")
    print(f"Wrote outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
