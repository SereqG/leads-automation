import csv
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from openpyxl import Workbook

from core import validation as core_validation
from core.logging import configure_logging

from . import schemas

QUERIES_COPY_FILENAME = "queries-copy.csv"


def validate_search_inputs(
    per_query: int, contact_email: str, log_file: Optional[Path]
) -> schemas.ProspectSearchConfig:
    bootstrap_logger = configure_logging(log_file=None)
    bootstrap_logger.info("Validating prospect search inputs...")

    resolved_log_file = core_validation.resolve_log_path(
        log_file, schemas.DEFAULT_LOG_DIR
    )

    config = core_validation.validate_config(
        schemas.ProspectSearchConfig,
        {
            "per_query": per_query,
            "contact_email": contact_email,
            "log_file": resolved_log_file,
        },
        resolved_log_file,
        schemas.DEFAULT_LOG_DIR,
        "prospect search inputs",
    )

    logger = configure_logging(log_file=config.log_file)
    logger.info(
        "Validation passed: per_query=%s contact_email=%s log_file=%s",
        config.per_query,
        config.contact_email,
        config.log_file,
    )
    return config


@dataclass(frozen=True)
class DuplicateQueryReport:
    duplicate_queries: list[str]
    duplicate_row_count: int
    total_rows: int


@dataclass(frozen=True)
class DeduplicationResult:
    dest_path: Path
    total_rows: int
    unique_rows: int
    removed_count: int


def _read_csv_rows(csv_path: Path) -> tuple[list[str], list[list[str]]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _normalize_row(row: list[str]) -> tuple[str, ...]:
    return tuple(cell.strip().casefold() for cell in row)


def resolve_queries_copy_path(queries_csv_path: Path) -> Path:
    return queries_csv_path.parent / QUERIES_COPY_FILENAME


def find_duplicate_queries(csv_path: Path) -> DuplicateQueryReport:
    _, data_rows = _read_csv_rows(csv_path)

    counts: dict[tuple[str, ...], int] = {}
    first_seen_text: dict[tuple[str, ...], str] = {}
    for row in data_rows:
        key = _normalize_row(row)
        counts[key] = counts.get(key, 0) + 1
        first_seen_text.setdefault(key, ",".join(row))

    duplicate_queries = [
        text for key, text in first_seen_text.items() if counts[key] > 1
    ]
    duplicate_row_count = sum(count - 1 for count in counts.values() if count > 1)

    return DuplicateQueryReport(
        duplicate_queries=duplicate_queries,
        duplicate_row_count=duplicate_row_count,
        total_rows=len(data_rows),
    )


def deduplicate_queries_csv(source_path: Path, dest_path: Path) -> DeduplicationResult:
    header, data_rows = _read_csv_rows(source_path)

    seen: set[tuple[str, ...]] = set()
    unique_rows: list[list[str]] = []
    for row in data_rows:
        key = _normalize_row(row)
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)

    with dest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(header)
        writer.writerows(unique_rows)

    return DeduplicationResult(
        dest_path=dest_path,
        total_rows=len(data_rows),
        unique_rows=len(unique_rows),
        removed_count=len(data_rows) - len(unique_rows),
    )


def check_and_deduplicate_queries(
    queries_csv_path: Path,
    logger: logging.Logger,
    confirm_callback: Callable[[DuplicateQueryReport], bool],
) -> Path:
    """Check queries.csv for duplicate rows. If any are found, ask the caller
    (via confirm_callback) whether to deduplicate. On confirmation, writes a
    deduped copy (queries-copy.csv) to be used later in the process instead
    of the original file; on decline, the original file continues to be
    used. Returns the path that should be used going forward."""
    report = find_duplicate_queries(queries_csv_path)

    if not report.duplicate_queries:
        logger.info(
            "No duplicate queries found in %s (%d rows)",
            queries_csv_path,
            report.total_rows,
        )
        return queries_csv_path

    logger.warning(
        "Found %d duplicate quer%s (%d redundant row(s)) in %s: %s",
        len(report.duplicate_queries),
        "y" if len(report.duplicate_queries) == 1 else "ies",
        report.duplicate_row_count,
        queries_csv_path,
        report.duplicate_queries,
    )

    if not confirm_callback(report):
        logger.info(
            "User declined deduplication; continuing with original file %s",
            queries_csv_path,
        )
        return queries_csv_path

    logger.info("User confirmed deduplication of %s", queries_csv_path)

    dest_path = resolve_queries_copy_path(queries_csv_path)
    result = deduplicate_queries_csv(queries_csv_path, dest_path)
    logger.info(
        "Deduplicated %s -> %s: %d rows -> %d unique rows (%d removed)",
        queries_csv_path,
        result.dest_path,
        result.total_rows,
        result.unique_rows,
        result.removed_count,
    )
    return result.dest_path


@dataclass(frozen=True)
class SearchResultRow:
    id: int
    domain: str
    query: str


def extract_domain(url: str) -> Optional[str]:
    """Return the lowercased hostname of a URL (e.g. "shop.example.com"),
    or None if the URL has no parseable hostname. Subdomains are kept as-is
    and "www." is not stripped."""
    hostname = urlparse(url).hostname
    return hostname.lower() if hostname else None


def read_queries(csv_path: Path) -> list[str]:
    _, data_rows = _read_csv_rows(csv_path)
    queries: list[str] = []
    for row in data_rows:
        if not row:
            continue
        query = row[0].strip()
        if query:
            queries.append(query)
    return queries


def compute_pagination_plan(per_query: int) -> list[tuple[int, int]]:
    """Return (count, offset) pairs needed to fetch up to per_query results,
    respecting Brave's limits (count 1-20, offset 0-9, i.e. 200 max/query).
    Brave's `offset` is measured in pages of `count` (skip = offset * count),
    so `count` is kept constant at BRAVE_MAX_COUNT across every page here;
    callers are responsible for truncating the fetched results down to
    per_query afterwards. Silently caps at the max; callers that want to
    warn about capping check per_query themselves."""
    target = min(per_query, schemas.BRAVE_MAX_RESULTS_PER_QUERY)
    pages = math.ceil(target / schemas.BRAVE_MAX_COUNT)
    return [(schemas.BRAVE_MAX_COUNT, offset) for offset in range(pages)]


def fetch_brave_results(
    query: str,
    per_query: int,
    api_key: str,
    logger: logging.Logger,
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[str]:
    if per_query > schemas.BRAVE_MAX_RESULTS_PER_QUERY:
        logger.warning(
            "per_query=%d for query=%r exceeds Brave's max of %d results/query "
            "(count<=%d x offset<=%d); capping to %d",
            per_query,
            query,
            schemas.BRAVE_MAX_RESULTS_PER_QUERY,
            schemas.BRAVE_MAX_COUNT,
            schemas.BRAVE_MAX_OFFSET,
            schemas.BRAVE_MAX_RESULTS_PER_QUERY,
        )

    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    urls: list[str] = []

    for count, offset in compute_pagination_plan(per_query):
        sleep_fn(schemas.REQUEST_DELAY_SECONDS)
        logger.info("Fetching query=%r count=%d offset=%d", query, count, offset)
        try:
            response = http_get(
                schemas.BRAVE_SEARCH_URL,
                headers=headers,
                params={"q": query, "count": count, "offset": offset},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error(
                "Brave request/parse failed for query=%r count=%d offset=%d: %s",
                query,
                count,
                offset,
                exc,
            )
            break

        web = payload.get("web") if isinstance(payload, dict) else None
        page_results = (web or {}).get("results", [])
        urls.extend(item["url"] for item in page_results if "url" in item)

        if len(page_results) < count:
            logger.info(
                "query=%r returned %d/%d results at offset=%d; stopping pagination",
                query,
                len(page_results),
                count,
                offset,
            )
            break

    return urls[:per_query]


def load_blacklist(blacklist_path: Path) -> set[str]:
    """Parse blacklist.txt into a set of lowercased domains. Blank lines and
    lines starting with '#' (comments/section headers) are ignored."""
    domains: set[str] = set()
    for line in blacklist_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        domains.add(stripped.lower())
    return domains


def is_blacklisted(domain: str, blacklist: set[str]) -> bool:
    """Return True if domain exactly matches a blacklist entry, or is a
    subdomain of one (e.g. "shop.amazon.pl" matches blacklisted "amazon.pl")."""
    return any(domain == entry or domain.endswith(f".{entry}") for entry in blacklist)


def filter_blacklisted_rows(
    rows: list[SearchResultRow], blacklist: set[str]
) -> list[SearchResultRow]:
    """Drop rows whose domain is blacklisted, renumbering the remaining rows'
    IDs so they stay contiguous (1..N) in the output."""
    kept = [row for row in rows if not is_blacklisted(row.domain, blacklist)]
    return [
        SearchResultRow(id=new_id, domain=row.domain, query=row.query)
        for new_id, row in enumerate(kept, start=1)
    ]


def resolve_results_dir(output_dir: Path) -> Path:
    return output_dir / schemas.RESULTS_DIRNAME


def write_results_xlsx(rows: list[SearchResultRow], output_dir: Path) -> Path:
    results_dir = resolve_results_dir(output_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = results_dir / f"{timestamp}.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ID", "domain", "query"])
    for row in rows:
        sheet.append([row.id, row.domain, row.query])
    workbook.save(str(dest_path))

    return dest_path


@dataclass(frozen=True)
class SearchCollectionResult:
    rows: list[SearchResultRow]
    total_urls: int
    unparseable_count: int


def collect_search_rows(
    queries: list[str],
    per_query: int,
    api_key: str,
    logger: logging.Logger,
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> SearchCollectionResult:
    """Search each query via Brave, extract a domain from each result URL, and
    dedupe domains across all queries (first query to surface a domain wins).
    Returns the kept rows plus URL/parsing stats for the caller's summary log."""
    rows: list[SearchResultRow] = []
    seen_domains: set[str] = set()
    next_id = 1
    total_urls = 0
    unparseable_count = 0
    for query in queries:
        logger.info("Searching query=%r", query)
        urls = fetch_brave_results(
            query, per_query, api_key, logger, http_get=http_get, sleep_fn=sleep_fn
        )
        logger.info("query=%r returned %d result(s)", query, len(urls))
        total_urls += len(urls)
        for url in urls:
            domain = extract_domain(url)
            if domain is None:
                unparseable_count += 1
                logger.warning(
                    "Could not extract a domain from url=%r (query=%r); skipping",
                    url,
                    query,
                )
                continue
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            rows.append(SearchResultRow(id=next_id, domain=domain, query=query))
            next_id += 1

    return SearchCollectionResult(
        rows=rows, total_urls=total_urls, unparseable_count=unparseable_count
    )


def run_prospect_search(
    queries_csv_path: Path,
    per_query: int,
    api_key: str,
    logger: logging.Logger,
    output_dir: Optional[Path] = None,
    blacklist_path: Optional[Path] = None,
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Path:
    resolved_output_dir = output_dir if output_dir is not None else schemas.OUTPUT_DIR
    resolved_blacklist_path = (
        blacklist_path if blacklist_path is not None else schemas.BLACKLIST_PATH
    )

    queries = read_queries(queries_csv_path)
    logger.info(
        "Starting prospect search for %d quer%s (per_query=%d)",
        len(queries),
        "y" if len(queries) == 1 else "ies",
        per_query,
    )
    if not queries:
        logger.warning("%s contains no queries; nothing to search", queries_csv_path)

    collection = collect_search_rows(
        queries, per_query, api_key, logger, http_get=http_get, sleep_fn=sleep_fn
    )
    rows = collection.rows
    duplicate_count = collection.total_urls - len(rows) - collection.unparseable_count

    blacklist = load_blacklist(resolved_blacklist_path)
    unique_domain_count = len(rows)
    rows = filter_blacklisted_rows(rows, blacklist)
    blacklisted_count = unique_domain_count - len(rows)
    if blacklisted_count:
        logger.info(
            "Filtered %d blacklisted domain(s) using %s",
            blacklisted_count,
            resolved_blacklist_path,
        )

    dest_path = write_results_xlsx(rows, resolved_output_dir)
    logger.info(
        "Wrote %d unique domain row(s) to %s (%d URL(s) fetched, %d duplicate "
        "domain(s), %d unparseable URL(s), and %d blacklisted domain(s) dropped)",
        len(rows),
        dest_path,
        collection.total_urls,
        duplicate_count,
        collection.unparseable_count,
        blacklisted_count,
    )
    return dest_path
