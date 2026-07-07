import re

import pytest
from typer.testing import CliRunner

from apps.prospects import schemas, services
from cli import app as root_app
from core.exceptions import ValidationFailedError

runner = CliRunner()


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


# --- CLI smoke tests ---


def test_cli_search_success(valid_env):
    result = runner.invoke(
        root_app,
        [
            "prospects",
            "search",
            "--per-query",
            "5",
            "--contact-email",
            "test@example.com",
        ],
    )
    assert result.exit_code == 0
    assert "Validation successful" in result.output


def test_cli_search_failure(valid_env):
    result = runner.invoke(
        root_app,
        [
            "prospects",
            "search",
            "--per-query",
            "0",
            "--contact-email",
            "test@example.com",
        ],
    )
    assert result.exit_code == 1
    assert "Error:" in result.output
