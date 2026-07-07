import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ValidationFailedError
from core.logging import configure_logging

from . import schemas


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
