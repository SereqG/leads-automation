import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ValidationFailedError
from core.logging import configure_logging

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def resolve_log_path(log_file: Optional[Path], default_log_dir: Path) -> Path:
    if log_file is not None:
        return log_file
    default_log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return default_log_dir / f"{timestamp}.log"


def configure_failure_logger(
    preferred_log_file: Path, default_log_dir: Path
) -> tuple[Path, logging.Logger]:
    """Attach a file handler for the failure log, falling back to the default
    log directory if the requested path itself can't be opened."""
    try:
        return preferred_log_file, configure_logging(log_file=preferred_log_file)
    except OSError:
        fallback_log_file = resolve_log_path(None, default_log_dir)
        return fallback_log_file, configure_logging(log_file=fallback_log_file)


def validate_config(
    model_cls: type[ConfigT],
    kwargs: dict,
    resolved_log_file: Path,
    default_log_dir: Path,
    context_label: str,
) -> ConfigT:
    """Construct model_cls(**kwargs); on failure, log each invalid field to the
    resolved log file (falling back to default_log_dir if that path itself
    can't be opened) and raise ValidationFailedError."""
    try:
        return model_cls(**kwargs)
    except PydanticValidationError as exc:
        errors = exc.errors()
        messages = [error["msg"] for error in errors]

        failure_log_file, failure_logger = configure_failure_logger(
            resolved_log_file, default_log_dir
        )
        if failure_log_file != resolved_log_file:
            failure_logger.warning(
                "Could not write to requested log file %s; writing to %s instead",
                resolved_log_file,
                failure_log_file,
            )
        failure_logger.error("Validation failed for %s:", context_label)
        for error in errors:
            field = ".".join(str(part) for part in error["loc"])
            failure_logger.error(
                "  field=%s invalid_value=%r reason=%s",
                field,
                error.get("input"),
                error["msg"],
            )
        raise ValidationFailedError(messages) from exc
