import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ValidationFailedError
from core.logging import configure_logging

from . import schemas

QUERIES_COPY_FILENAME = "queries-copy.csv"


def resolve_log_path(log_file: Optional[Path]) -> Path:
    if log_file is not None:
        return log_file
    schemas.DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return schemas.DEFAULT_LOG_DIR / f"{timestamp}.log"


def _configure_failure_logger(preferred_log_file: Path) -> tuple[Path, logging.Logger]:
    """Attach a file handler for the failure log, falling back to the default
    log directory if the requested path itself can't be opened (e.g. its
    parent directory is missing — one of the possible validation failures)."""
    try:
        return preferred_log_file, configure_logging(log_file=preferred_log_file)
    except OSError:
        fallback_log_file = resolve_log_path(None)
        return fallback_log_file, configure_logging(log_file=fallback_log_file)


def validate_search_inputs(
    per_query: int, contact_email: str, log_file: Optional[Path]
) -> schemas.ProspectSearchConfig:
    bootstrap_logger = configure_logging(log_file=None)
    bootstrap_logger.info("Validating prospect search inputs...")

    resolved_log_file = resolve_log_path(log_file)

    try:
        config = schemas.ProspectSearchConfig(
            per_query=per_query,
            contact_email=contact_email,
            log_file=resolved_log_file,
        )
    except PydanticValidationError as exc:
        errors = exc.errors()
        messages = [error["msg"] for error in errors]

        failure_log_file, failure_logger = _configure_failure_logger(resolved_log_file)
        if failure_log_file != resolved_log_file:
            failure_logger.warning(
                "Could not write to requested log file %s; writing to %s instead",
                resolved_log_file,
                failure_log_file,
            )
        failure_logger.error("Validation failed for prospect search inputs:")
        for error in errors:
            field = ".".join(str(part) for part in error["loc"])
            failure_logger.error(
                "  field=%s invalid_value=%r reason=%s",
                field,
                error.get("input"),
                error["msg"],
            )
        raise ValidationFailedError(messages) from exc

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
