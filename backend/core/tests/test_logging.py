import logging

from core.logging import configure_logging


def _file_handlers(logger: logging.Logger) -> list[logging.FileHandler]:
    return [h for h in logger.handlers if isinstance(h, logging.FileHandler)]


def test_different_log_files_get_independent_loggers(tmp_path):
    log_file_a = tmp_path / "a.log"
    log_file_b = tmp_path / "b.log"

    logger_a = configure_logging(log_file=log_file_a)
    logger_b = configure_logging(log_file=log_file_b)

    assert logger_a is not logger_b

    handlers_a = _file_handlers(logger_a)
    handlers_b = _file_handlers(logger_b)
    assert len(handlers_a) == 1
    assert len(handlers_b) == 1
    assert handlers_a[0].baseFilename == str(log_file_a)
    assert handlers_b[0].baseFilename == str(log_file_b)

    # Configuring b must not have clobbered a's handlers.
    assert _file_handlers(logger_a)[0].baseFilename == str(log_file_a)


def test_reconfiguring_same_log_file_does_not_leak_handlers(tmp_path):
    log_file = tmp_path / "same.log"

    logger_first = configure_logging(log_file=log_file)
    assert logger_first is logging.getLogger(f"leadgen:{log_file}")
    assert len(logger_first.handlers) == 2  # console + file

    old_file_handler = _file_handlers(logger_first)[0]
    assert old_file_handler.stream is not None

    logger_second = configure_logging(log_file=log_file)

    # Same log_file -> same underlying Logger object, handlers replaced not appended.
    assert logger_second is logger_first
    assert len(logger_second.handlers) == 2

    # The old FileHandler must have been closed (stream released) before removal.
    assert old_file_handler.stream is None

    new_file_handler = _file_handlers(logger_second)[0]
    assert new_file_handler is not old_file_handler
    assert new_file_handler.baseFilename == str(log_file)
    assert new_file_handler.stream is not None


def test_configure_logging_without_log_file_is_console_only():
    logger = configure_logging(log_file=None)

    assert _file_handlers(logger) == []
    assert any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )
