import logging
from pathlib import Path
from typing import Optional

LOGGER_NAME = "leadgen"
DEFAULT_LOG_DIR = Path("/backend/logs")


def configure_logging(
    log_file: Optional[Path], level: int = logging.INFO
) -> logging.Logger:
    logger_name = f"{LOGGER_NAME}:{log_file}" if log_file is not None else LOGGER_NAME
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
