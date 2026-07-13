import logging
import re
from types import SimpleNamespace

import pytest

from apps.prospects import schemas, services
from core.exceptions import ValidationFailedError


@pytest.fixture
def valid_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    queries_csv = data_dir / "queries.csv"
    queries_csv.write_text("query\nplumbers in Chicago\n")

    blacklist = data_dir / "blacklist.txt"
    blacklist.write_text("spam.com\n")

    logs_dir = tmp_path / "logs"

    monkeypatch.setattr(schemas, "QUERIES_CSV_PATH", queries_csv)
    monkeypatch.setattr(schemas, "OUTPUT_DIR", data_dir)
    monkeypatch.setattr(schemas, "BLACKLIST_PATH", blacklist)
    monkeypatch.setattr(schemas, "DEFAULT_LOG_DIR", logs_dir)
    monkeypatch.setattr(
        schemas,
        "get_settings",
        lambda: SimpleNamespace(brave_api_key="test-brave-api-key"),
    )

    return {
        "data_dir": data_dir,
        "queries_csv": queries_csv,
        "blacklist": blacklist,
        "logs_dir": logs_dir,
    }


def run_validation(
    tmp_path, per_query=10, contact_email="test@example.com", log_file=None
):
    if log_file is None:
        log_file = tmp_path / "custom.log"
    return services.validate_search_inputs(
        per_query=per_query, contact_email=contact_email, log_file=log_file
    )


# --- queries.csv ---


def test_queries_csv_missing_raises(valid_env, tmp_path):
    valid_env["queries_csv"].unlink()
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path)
    assert any("queries.csv" in message for message in exc_info.value.errors)


def test_queries_csv_present_passes(valid_env, tmp_path):
    config = run_validation(tmp_path)
    assert config.queries_csv_path == valid_env["queries_csv"]


# --- output directory ---


def test_output_dir_missing_raises(valid_env, tmp_path, monkeypatch):
    monkeypatch.setattr(schemas, "OUTPUT_DIR", tmp_path / "does-not-exist")
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path)
    assert any("Output directory" in message for message in exc_info.value.errors)


def test_output_dir_present_passes(valid_env, tmp_path):
    config = run_validation(tmp_path)
    assert config.output_dir == valid_env["data_dir"]


# --- per-query ---


@pytest.mark.parametrize("bad_value", [0, -5])
def test_per_query_not_positive_raises(valid_env, tmp_path, bad_value):
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path, per_query=bad_value)
    assert any("--per-query" in message for message in exc_info.value.errors)


def test_per_query_positive_passes(valid_env, tmp_path):
    config = run_validation(tmp_path, per_query=1)
    assert config.per_query == 1


# --- contact email ---


def test_contact_email_invalid_format_raises(valid_env, tmp_path):
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path, contact_email="not-an-email")
    assert any("email" in message.lower() for message in exc_info.value.errors)


def test_contact_email_valid_passes(valid_env, tmp_path):
    config = run_validation(tmp_path, contact_email="user@example.com")
    assert config.contact_email == "user@example.com"


# --- blacklist.txt ---


def test_blacklist_missing_raises(valid_env, tmp_path, monkeypatch):
    monkeypatch.setattr(schemas, "BLACKLIST_PATH", tmp_path / "no-blacklist.txt")
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path)
    assert any(
        "blacklist.txt not found" in message for message in exc_info.value.errors
    )


def test_blacklist_empty_raises(valid_env, tmp_path):
    valid_env["blacklist"].write_text("\n   \n")
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path)
    assert any("blacklist.txt is empty" in message for message in exc_info.value.errors)


def test_blacklist_with_content_passes(valid_env, tmp_path):
    config = run_validation(tmp_path)
    assert config.blacklist_path == valid_env["blacklist"]


# --- log file ---


def test_log_file_wrong_extension_raises(valid_env, tmp_path):
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path, log_file=tmp_path / "out.txt")
    assert any(".log" in message for message in exc_info.value.errors)


def test_log_file_parent_missing_raises(valid_env, tmp_path):
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path, log_file=tmp_path / "missing_dir" / "run.log")
    assert any(
        "directory does not exist" in message for message in exc_info.value.errors
    )


def test_log_file_valid_custom_passes(valid_env, tmp_path):
    custom_log = tmp_path / "custom.log"
    config = run_validation(tmp_path, log_file=custom_log)
    assert config.log_file == custom_log


def test_log_file_default_creates_dir_and_timestamped_name(valid_env, tmp_path):
    assert not valid_env["logs_dir"].exists()
    config = services.validate_search_inputs(
        per_query=10, contact_email="test@example.com", log_file=None
    )
    assert valid_env["logs_dir"].exists()
    assert config.log_file.parent == valid_env["logs_dir"]
    assert re.fullmatch(r"\d{8}_\d{6}\.log", config.log_file.name)


# --- log file written on validation failure ---


def test_log_file_created_on_validation_failure_with_invalid_values(
    valid_env, tmp_path
):
    log_file = tmp_path / "failure.log"
    with pytest.raises(ValidationFailedError):
        run_validation(
            tmp_path,
            per_query=0,
            contact_email="not-an-email",
            log_file=log_file,
        )

    assert log_file.exists()
    contents = log_file.read_text()
    assert "per_query" in contents
    assert "invalid_value=0" in contents
    assert "contact_email" in contents
    assert "not-an-email" in contents


def test_log_file_falls_back_to_default_dir_when_target_unwritable(valid_env, tmp_path):
    bad_log_file = tmp_path / "missing_dir" / "run.log"
    with pytest.raises(ValidationFailedError):
        run_validation(tmp_path, per_query=0, log_file=bad_log_file)

    assert not bad_log_file.parent.exists()
    fallback_logs = list(valid_env["logs_dir"].glob("*.log"))
    assert len(fallback_logs) == 1
    contents = fallback_logs[0].read_text()
    assert "per_query" in contents
    assert "invalid_value=0" in contents


# --- aggregate errors ---


def test_multiple_errors_reported_together(valid_env, tmp_path):
    with pytest.raises(ValidationFailedError) as exc_info:
        run_validation(tmp_path, per_query=0, contact_email="not-an-email")
    assert len(exc_info.value.errors) >= 2


# --- queries.csv duplicate detection ---


def test_find_duplicate_queries_none_found(valid_env):
    report = services.find_duplicate_queries(valid_env["queries_csv"])
    assert report.duplicate_queries == []
    assert report.duplicate_row_count == 0
    assert report.total_rows == 1


def test_find_duplicate_queries_detects_duplicates(valid_env):
    valid_env["queries_csv"].write_text(
        "google_search_query\n"
        "plumbers in Chicago\n"
        "  Plumbers in Chicago  \n"
        "electricians in Boston\n"
        "electricians in Boston\n"
    )
    report = services.find_duplicate_queries(valid_env["queries_csv"])
    assert report.total_rows == 4
    assert report.duplicate_row_count == 2
    assert report.duplicate_queries == ["plumbers in Chicago", "electricians in Boston"]


# --- queries.csv deduplication ---


def test_deduplicate_queries_csv_writes_unique_rows(valid_env, tmp_path):
    valid_env["queries_csv"].write_text(
        "google_search_query\n"
        "plumbers in Chicago\n"
        "PLUMBERS IN CHICAGO\n"
        "electricians in Boston\n"
    )
    dest = valid_env["data_dir"] / "queries-copy.csv"

    result = services.deduplicate_queries_csv(valid_env["queries_csv"], dest)

    assert result.dest_path == dest
    assert result.total_rows == 3
    assert result.unique_rows == 2
    assert result.removed_count == 1
    assert dest.read_text() == (
        "google_search_query\nplumbers in Chicago\nelectricians in Boston\n"
    )


def test_resolve_queries_copy_path(valid_env):
    dest = services.resolve_queries_copy_path(valid_env["queries_csv"])
    assert dest == valid_env["data_dir"] / "queries-copy.csv"


# --- check_and_deduplicate_queries orchestration ---


def test_check_and_deduplicate_no_duplicates_skips_prompt(valid_env):
    logger = logging.getLogger("test-dedup-none")
    confirm_callback = lambda report: pytest.fail("should not be called")  # noqa: E731

    result_path = services.check_and_deduplicate_queries(
        valid_env["queries_csv"], logger, confirm_callback
    )
    assert result_path == valid_env["queries_csv"]


def test_check_and_deduplicate_confirmed_writes_copy(valid_env):
    valid_env["queries_csv"].write_text(
        "google_search_query\nplumbers in Chicago\nplumbers in Chicago\n"
    )
    logger = logging.getLogger("test-dedup-confirmed")

    result_path = services.check_and_deduplicate_queries(
        valid_env["queries_csv"], logger, lambda report: True
    )

    expected_copy = valid_env["data_dir"] / "queries-copy.csv"
    assert result_path == expected_copy
    assert expected_copy.exists()


def test_check_and_deduplicate_declined_keeps_original(valid_env):
    valid_env["queries_csv"].write_text(
        "google_search_query\nplumbers in Chicago\nplumbers in Chicago\n"
    )
    logger = logging.getLogger("test-dedup-declined")

    result_path = services.check_and_deduplicate_queries(
        valid_env["queries_csv"], logger, lambda report: False
    )

    assert result_path == valid_env["queries_csv"]
    assert not (valid_env["data_dir"] / "queries-copy.csv").exists()
