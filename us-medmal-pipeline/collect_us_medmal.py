#!/usr/bin/env python3
"""Collect CourtListener U.S. med-mal/personal-injury cases into canonical JSON."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from tqdm import tqdm


BASE_API_URL = "https://www.courtlistener.com/api/rest/v4/"
BASE_SITE_URL = "https://www.courtlistener.com"
CHUNK_SIZE = 750
CHECKPOINT_FILE = ".checkpoint.json"
DEFAULT_QUERY = "medical malpractice"
DEFAULT_MAX_CASES = 7500
DEFAULT_SLEEP_SECONDS = 13.0
SEARCH_PAGE_SIZE = 20
FIVE_XX_BACKOFFS = (5, 10, 20)


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


class CourtListenerClient:
    def __init__(self, token: str, sleep_seconds: float = DEFAULT_SLEEP_SECONDS) -> None:
        self.sleep_seconds = sleep_seconds
        self.last_request_at: float | None = None
        self.last_response_url: str | None = None
        self.requests_made = 0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "asu-davulcu-lab-legal-ai-stage1/0.1",
                "Authorization": f"Token {token}",
            }
        )

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        url = self._make_url(path_or_url)
        five_xx_attempt = 0
        cloudflare_attempt = 0

        while True:
            self._sleep_between_requests()
            self.last_request_at = time.monotonic()
            self.requests_made += 1

            try:
                response = self.session.get(url, params=params, timeout=60)
                self.last_response_url = response.url
            except requests.RequestException as exc:
                if five_xx_attempt < len(FIVE_XX_BACKOFFS):
                    wait = FIVE_XX_BACKOFFS[five_xx_attempt]
                    five_xx_attempt += 1
                    warn(f"request failed for {url}: {exc}; retrying in {wait}s")
                    time.sleep(wait)
                    continue
                warn(f"skipping {url}; request failed after retries: {exc}")
                return None

            if response.status_code == 429:
                warn("rate limited by CourtListener; waiting 60s before retry")
                time.sleep(60)
                continue

            if response.status_code in (522, 523):
                cloudflare_attempt += 1
                if cloudflare_attempt <= 5:
                    warn(f"CourtListener returned {response.status_code}; retrying in 5s")
                    time.sleep(5)
                    continue
                warn(f"skipping {url}; repeated {response.status_code} responses")
                return None

            if 500 <= response.status_code < 600:
                if five_xx_attempt < len(FIVE_XX_BACKOFFS):
                    wait = FIVE_XX_BACKOFFS[five_xx_attempt]
                    five_xx_attempt += 1
                    warn(f"CourtListener returned {response.status_code}; retrying in {wait}s")
                    time.sleep(wait)
                    continue
                warn(f"skipping {url}; repeated server errors")
                return None

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                warn(f"skipping {url}; HTTP error: {exc}")
                return None

            try:
                return response.json()
            except ValueError:
                warn(f"skipping {url}; response was not valid JSON")
                return None

    def _sleep_between_requests(self) -> None:
        if self.last_request_at is None:
            return
        elapsed = time.monotonic() - self.last_request_at
        remaining = self.sleep_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _make_url(path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return urljoin(BASE_API_URL, path_or_url.lstrip("/"))


def public_courtlistener_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value:
        return ""
    if value.startswith("http://www.courtlistener.com"):
        return "https://www.courtlistener.com" + value[len("http://www.courtlistener.com") :]
    if value.startswith("https://www.courtlistener.com"):
        return value
    if value.startswith("/"):
        return BASE_SITE_URL + value
    if value.startswith("https://"):
        return value
    return ""


def first_non_empty(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_api_id(uri: Any, resource: str) -> int | None:
    if isinstance(uri, int):
        return uri
    if not isinstance(uri, str):
        return None
    match = re.search(rf"/api/rest/v4/{re.escape(resource)}/(\d+)/?", uri)
    if match:
        return int(match.group(1))
    return None


def normalize_precedent(raw: dict[str, Any]) -> dict[str, str] | None:
    title = first_non_empty(
        raw,
        (
            "case",
            "case_name",
            "caseName",
            "case_name_full",
            "caseNameFull",
            "name",
            "title",
        ),
    )
    url = public_courtlistener_url(first_non_empty(raw, ("url", "absolute_url", "absoluteUrl")))

    if not url and raw.get("cluster_id"):
        url = f"{BASE_SITE_URL}/opinion/{raw['cluster_id']}/"

    if not title or not url.startswith("https://"):
        return None
    return {"case": title, "url": url}


def dedupe_precedents(precedents: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: OrderedDict[str, dict[str, str]] = OrderedDict()
    for precedent in precedents:
        key = precedent["url"] or precedent["case"]
        deduped.setdefault(key, precedent)
    return list(deduped.values())


def search_result_to_case(result: dict[str, Any], precedents: list[dict[str, str]]) -> dict[str, Any] | None:
    case_name = first_non_empty(result, ("caseName", "caseNameFull", "case_name", "case_name_full"))
    date_filed = result.get("dateFiled") or result.get("date_filed") or ""
    year = str(date_filed)[:4]
    url = public_courtlistener_url(result.get("absolute_url"))

    if not case_name:
        warn("skipping search result without a case name")
        return None
    if not re.fullmatch(r"\d{4}", year):
        warn(f"skipping {case_name}; missing four-digit filed year")
        return None
    if not url.startswith("https://"):
        warn(f"skipping {case_name}; missing CourtListener opinion URL")
        return None

    return {
        "case": case_name,
        "year": year,
        "url": url,
        "precedents": precedents,
    }


def extract_inline_precedents(result: dict[str, Any]) -> list[dict[str, str]]:
    precedents: list[dict[str, str]] = []
    for opinion in result.get("opinions") or []:
        if not isinstance(opinion, dict):
            continue
        for citation in opinion.get("cites") or []:
            if isinstance(citation, dict):
                precedent = normalize_precedent(citation)
                if precedent:
                    precedents.append(precedent)
    return precedents


def extract_cluster_precedents(cluster: dict[str, Any]) -> list[dict[str, str]]:
    precedents: list[dict[str, str]] = []
    for citation in cluster.get("citations") or []:
        if not isinstance(citation, dict):
            continue
        precedent = normalize_precedent(citation)
        if precedent:
            precedents.append(precedent)
    return precedents


def extract_opinion_ids(result: dict[str, Any], cluster: dict[str, Any] | None = None) -> list[int]:
    ids: list[int] = []
    for opinion in result.get("opinions") or []:
        if isinstance(opinion, dict):
            opinion_id = opinion.get("id")
            if isinstance(opinion_id, int):
                ids.append(opinion_id)

    if cluster:
        for opinion_uri in cluster.get("sub_opinions") or []:
            opinion_id = parse_api_id(opinion_uri, "opinions")
            if opinion_id is not None:
                ids.append(opinion_id)

    return list(OrderedDict.fromkeys(ids))


def cluster_precedent_from_opinion(
    client: CourtListenerClient,
    opinion_uri: str,
    opinion_cache: dict[int, dict[str, str] | None],
    cluster_cache: dict[int, dict[str, Any] | None],
) -> dict[str, str] | None:
    opinion_id = parse_api_id(opinion_uri, "opinions")
    if opinion_id is None:
        return None
    if opinion_id in opinion_cache:
        return opinion_cache[opinion_id]

    opinion = client.get(opinion_uri, params={"fields": "cluster"})
    if not opinion:
        opinion_cache[opinion_id] = None
        return None

    cluster_value = opinion.get("cluster")
    if isinstance(cluster_value, dict):
        precedent = normalize_precedent(cluster_value)
        opinion_cache[opinion_id] = precedent
        return precedent

    cluster_id = parse_api_id(cluster_value, "clusters")
    if cluster_id is None:
        opinion_cache[opinion_id] = None
        return None

    if cluster_id not in cluster_cache:
        cluster_cache[cluster_id] = client.get(
            f"clusters/{cluster_id}/",
            params={"fields": "id,case_name,case_name_full,absolute_url"},
        )
    cluster = cluster_cache.get(cluster_id)
    precedent = normalize_precedent(cluster) if cluster else None
    opinion_cache[opinion_id] = precedent
    return precedent


def fetch_graph_precedents(
    client: CourtListenerClient,
    opinion_ids: list[int],
    opinion_cache: dict[int, dict[str, str] | None],
    cluster_cache: dict[int, dict[str, Any] | None],
) -> list[dict[str, str]]:
    precedents: list[dict[str, str]] = []

    for opinion_id in opinion_ids:
        page_url = "opinions-cited/"
        params: dict[str, Any] | None = {
            "citing_opinion": opinion_id,
            "fields": "cited_opinion",
        }

        while page_url:
            data = client.get(page_url, params=params)
            params = None
            if not data:
                break

            for edge in data.get("results") or []:
                if not isinstance(edge, dict):
                    continue
                cited_opinion_uri = edge.get("cited_opinion")
                if not isinstance(cited_opinion_uri, str):
                    continue
                precedent = cluster_precedent_from_opinion(
                    client,
                    cited_opinion_uri,
                    opinion_cache,
                    cluster_cache,
                )
                if precedent:
                    precedents.append(precedent)

            page_url = data.get("next")

    return precedents


def fetch_precedents(
    client: CourtListenerClient,
    result: dict[str, Any],
    mode: str,
    opinion_cache: dict[int, dict[str, str] | None],
    cluster_cache: dict[int, dict[str, Any] | None],
) -> list[dict[str, str]]:
    precedents = extract_inline_precedents(result)
    cluster: dict[str, Any] | None = None

    cluster_id = result.get("cluster_id")
    if isinstance(cluster_id, int):
        cluster = cluster_cache.get(cluster_id)
        if cluster_id not in cluster_cache:
            cluster = client.get(
                f"clusters/{cluster_id}/",
                params={"fields": "id,case_name,case_name_full,absolute_url,citations,sub_opinions"},
            )
            cluster_cache[cluster_id] = cluster
        if cluster:
            precedents.extend(extract_cluster_precedents(cluster))

    if mode == "cluster":
        return dedupe_precedents(precedents)

    if mode == "auto" and precedents:
        return dedupe_precedents(precedents)

    opinion_ids = extract_opinion_ids(result, cluster)
    if not opinion_ids:
        return dedupe_precedents(precedents)

    precedents.extend(fetch_graph_precedents(client, opinion_ids, opinion_cache, cluster_cache))
    return dedupe_precedents(precedents)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temp_path.replace(path)


def write_case_chunk(output_dir: Path, cases: list[dict[str, Any]], start: int, end: int) -> str:
    folder = output_dir / f"{start}-{end}"
    write_json(folder / "citations_formatted.json", cases)
    return str(folder)


def checkpoint_path(output_dir: Path) -> Path:
    return output_dir / CHECKPOINT_FILE


def load_checkpoint(output_dir: Path, query: str, mode: str) -> dict[str, Any] | None:
    path = checkpoint_path(output_dir)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        checkpoint = json.load(handle)
    if checkpoint.get("query") != query:
        raise SystemExit(
            f"Checkpoint query is {checkpoint.get('query')!r}, not {query!r}. "
            f"Remove {path} or use the same query."
        )
    if checkpoint.get("precedent_mode") != mode:
        raise SystemExit(
            f"Checkpoint precedent mode is {checkpoint.get('precedent_mode')!r}, not {mode!r}. "
            f"Remove {path} or use the same --precedent-mode."
        )
    return checkpoint


def save_checkpoint(
    output_dir: Path,
    query: str,
    mode: str,
    total_collected: int,
    search_url: str | None,
    result_offset: int,
    seen_cluster_ids: set[int],
    stats: dict[str, Any],
    folders_created: list[str],
) -> None:
    serializable_stats = {
        "min_year": stats.get("min_year"),
        "max_year": stats.get("max_year"),
        "court_counts": dict(stats.get("court_counts", {})),
        "total_precedents": stats.get("total_precedents", 0),
    }
    checkpoint = {
        "query": query,
        "precedent_mode": mode,
        "total_collected": total_collected,
        "search_url": search_url,
        "result_offset": result_offset,
        "seen_cluster_ids": sorted(seen_cluster_ids),
        "stats": serializable_stats,
        "folders_created": folders_created,
    }
    write_json(checkpoint_path(output_dir), checkpoint)


def restore_stats(raw_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_stats = raw_stats or {}
    return {
        "min_year": raw_stats.get("min_year"),
        "max_year": raw_stats.get("max_year"),
        "court_counts": Counter(raw_stats.get("court_counts", {})),
        "total_precedents": int(raw_stats.get("total_precedents", 0)),
    }


def update_stats(stats: dict[str, Any], result: dict[str, Any], case: dict[str, Any]) -> None:
    year = int(case["year"])
    stats["min_year"] = year if stats["min_year"] is None else min(stats["min_year"], year)
    stats["max_year"] = year if stats["max_year"] is None else max(stats["max_year"], year)
    court = result.get("court") or result.get("court_id") or "Unknown"
    stats["court_counts"][str(court)] += 1
    stats["total_precedents"] += len(case["precedents"])


def output_size_mb(output_dir: Path) -> float:
    total_bytes = 0
    if output_dir.exists():
        for path in output_dir.glob("*/citations_formatted.json"):
            total_bytes += path.stat().st_size
    return total_bytes / (1024 * 1024)


def print_summary(
    total_collected: int,
    stats: dict[str, Any],
    folders_created: list[str],
    output_dir: Path,
    requests_made: int,
) -> None:
    min_year = stats.get("min_year")
    max_year = stats.get("max_year")
    date_range = "N/A" if min_year is None or max_year is None else f"{min_year} - {max_year}"
    avg_precedents = (
        stats.get("total_precedents", 0) / total_collected if total_collected else 0.0
    )

    print("\nSummary")
    print(f"Total cases collected : {total_collected}")
    print(f"Date range            : {date_range}")
    print("Top 10 courts by volume")
    for court, count in stats["court_counts"].most_common(10):
        print(f"  {court}: {count}")
    print(f"Avg precedents per case: {avg_precedents:.2f}")
    print(f"Total output size (MB) : {output_size_mb(output_dir):.2f}")
    print(f"API requests made      : {requests_made}")
    print("Folders created       :")
    if folders_created:
        for folder in folders_created:
            print(f"  {folder}/")
    else:
        print("  None")


def collect(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = None if args.dry_run else load_checkpoint(output_dir, args.query, args.precedent_mode)
    if checkpoint:
        total_collected = int(checkpoint.get("total_collected", 0))
        search_url = checkpoint.get("search_url")
        result_offset = int(checkpoint.get("result_offset", 0))
        seen_cluster_ids = set(int(value) for value in checkpoint.get("seen_cluster_ids", []))
        stats = restore_stats(checkpoint.get("stats"))
        folders_created = list(checkpoint.get("folders_created", []))
        search_params = None
        print(f"Resuming from checkpoint at {total_collected} cases")
    else:
        total_collected = 0
        search_url = "search/"
        result_offset = 0
        seen_cluster_ids: set[int] = set()
        stats = restore_stats()
        folders_created: list[str] = []
        search_params = {
            "q": args.query,
            "type": "o",
            "stat_Precedential": "on",
            "order_by": "score desc",
            "page_size": SEARCH_PAGE_SIZE,
        }

    target_cases = 5 if args.dry_run else args.max_cases
    current_chunk: list[dict[str, Any]] = []
    dry_run_cases: list[dict[str, Any]] = []
    opinion_cache: dict[int, dict[str, str] | None] = {}
    cluster_cache: dict[int, dict[str, Any] | None] = {}
    client = CourtListenerClient(args.token, args.sleep_seconds)
    last_position_url = search_url
    last_position_offset = result_offset

    progress = tqdm(
        total=target_cases,
        initial=min(total_collected, target_cases),
        desc="Collecting cases",
        unit="case",
        disable=args.dry_run,
    )

    while search_url and total_collected < target_cases:
        data = client.get(search_url, params=search_params)
        search_params = None
        if not data:
            break

        current_page_url = client.last_response_url or search_url
        results = data.get("results") or []

        for index, result in enumerate(results):
            if index < result_offset:
                continue
            result_offset = 0
            last_position_url = current_page_url
            last_position_offset = index + 1

            if total_collected >= target_cases:
                break
            if not isinstance(result, dict):
                continue

            cluster_id = result.get("cluster_id")
            if isinstance(cluster_id, int) and cluster_id in seen_cluster_ids:
                continue

            precedents = fetch_precedents(
                client,
                result,
                args.precedent_mode,
                opinion_cache,
                cluster_cache,
            )
            case = search_result_to_case(result, precedents)
            if case is None:
                continue

            if isinstance(cluster_id, int):
                seen_cluster_ids.add(cluster_id)
            total_collected += 1
            update_stats(stats, result, case)
            progress.update(1)

            if args.dry_run:
                dry_run_cases.append(case)
                continue

            current_chunk.append(case)
            if len(current_chunk) == CHUNK_SIZE:
                start = total_collected - CHUNK_SIZE + 1
                folder = write_case_chunk(output_dir, current_chunk, start, total_collected)
                folders_created.append(folder)
                current_chunk = []
                save_checkpoint(
                    output_dir,
                    args.query,
                    args.precedent_mode,
                    total_collected,
                    last_position_url,
                    last_position_offset,
                    seen_cluster_ids,
                    stats,
                    folders_created,
                )

        if total_collected >= target_cases:
            break
        search_url = data.get("next")
        result_offset = 0
        last_position_url = search_url
        last_position_offset = 0

    progress.close()

    if args.dry_run:
        print(json.dumps(dry_run_cases, indent=2, ensure_ascii=False))
    elif current_chunk:
        start = total_collected - len(current_chunk) + 1
        folder = write_case_chunk(output_dir, current_chunk, start, total_collected)
        folders_created.append(folder)
        save_checkpoint(
            output_dir,
            args.query,
            args.precedent_mode,
            total_collected,
            last_position_url,
            last_position_offset,
            seen_cluster_ids,
            stats,
            folders_created,
        )
    elif not args.dry_run:
        save_checkpoint(
            output_dir,
            args.query,
            args.precedent_mode,
            total_collected,
            last_position_url,
            last_position_offset,
            seen_cluster_ids,
            stats,
            folders_created,
        )

    print_summary(total_collected, stats, folders_created, output_dir, client.requests_made)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download U.S. med-mal/personal-injury CourtListener cases into canonical JSON."
    )
    parser.add_argument(
        "--token",
        default=os.getenv("COURTLISTENER_TOKEN"),
        help="CourtListener API token. Defaults to COURTLISTENER_TOKEN.",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help='Search query, e.g. "personal injury".')
    parser.add_argument("--max-cases", type=int, default=DEFAULT_MAX_CASES, help="Stop after N cases.")
    parser.add_argument("--output-dir", default="./citations-data-us", help="Output folder.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch 5 cases, print JSON, save nothing.")
    parser.add_argument(
        "--precedent-mode",
        choices=("auto", "cluster", "graph"),
        default="auto",
        help=(
            "auto tries inline/cluster citations first, then the documented citation graph; "
            "cluster uses only the faster cluster response; graph always uses opinions-cited."
        ),
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help="Seconds to sleep between requests. Keep 13 for the free CourtListener tier.",
    )
    args = parser.parse_args()
    if not args.token:
        parser.error("--token is required unless COURTLISTENER_TOKEN is set")
    if args.max_cases < 1:
        parser.error("--max-cases must be at least 1")
    if args.sleep_seconds < 0:
        parser.error("--sleep-seconds cannot be negative")
    return args


def main() -> int:
    return collect(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
