from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from apps.enrichment import schemas as enrichment_schemas
from apps.enrichment import services as enrichment_services
from apps.enrichment import tasks as enrichment_tasks
from apps.pipeline import services
from apps.prospects import schemas as prospects_schemas
from apps.prospects import services as prospects_services
from apps.prospects import tasks as prospects_tasks
from cli import app as root_app

runner = CliRunner()


@pytest.fixture
def valid_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    queries_csv = data_dir / "queries.csv"
    queries_csv.write_text("query\nplumbers in Chicago\n")

    blacklist = data_dir / "blacklist.txt"
    blacklist.write_text("spam.com\n")

    about_csv = data_dir / "about-us-urls.csv"
    about_csv.write_text("page_name,url_slug\nO nas,/o-nas\n")

    prefixes_csv = data_dir / "email_prefixes_only.csv"
    prefixes_csv.write_text("prefix\ninfo\nkontakt\n")

    logs_dir = tmp_path / "logs"

    monkeypatch.setattr(prospects_schemas, "QUERIES_CSV_PATH", queries_csv)
    monkeypatch.setattr(prospects_schemas, "OUTPUT_DIR", data_dir)
    monkeypatch.setattr(prospects_schemas, "BLACKLIST_PATH", blacklist)
    monkeypatch.setattr(prospects_schemas, "DEFAULT_LOG_DIR", logs_dir)
    monkeypatch.setattr(
        prospects_schemas,
        "get_settings",
        lambda: SimpleNamespace(brave_api_key="test-brave-api-key"),
    )
    monkeypatch.setattr(enrichment_schemas, "ABOUT_US_CSV_PATH", about_csv)
    monkeypatch.setattr(enrichment_schemas, "EMAIL_PREFIXES_CSV_PATH", prefixes_csv)
    monkeypatch.setattr(enrichment_schemas, "DEFAULT_LOG_DIR", logs_dir)

    return {
        "data_dir": data_dir,
        "queries_csv": queries_csv,
        "blacklist": blacklist,
        "about_csv": about_csv,
        "prefixes_csv": prefixes_csv,
        "logs_dir": logs_dir,
    }


# --- enqueue_pipeline ---


def test_enqueue_pipeline_chains_search_then_scrape(valid_env, monkeypatch):
    signatures = []

    class FakeSignature:
        def __init__(self, task, args):
            self.task = task
            self.args = args

    def fake_search_s(*args):
        sig = FakeSignature("search", args)
        signatures.append(sig)
        return sig

    def fake_scrape_s(*args):
        sig = FakeSignature("scrape", args)
        signatures.append(sig)
        return sig

    captured_chain_args = {}

    def fake_chain(*sigs):
        captured_chain_args["sigs"] = sigs
        return SimpleNamespace(delay=lambda: SimpleNamespace(id="fake-chain-id"))

    monkeypatch.setattr(prospects_tasks.run_prospect_search_task, "s", fake_search_s)
    monkeypatch.setattr(enrichment_tasks.scrape_contacts_task, "s", fake_scrape_s)
    monkeypatch.setattr(services, "chain", fake_chain)

    search_config = prospects_services.validate_search_inputs(
        per_query=5, contact_email="test@example.com", log_file=None
    )
    enrich_config = enrichment_services.validate_scrape_inputs_for_chain(
        contact_email="test@example.com", log_file=None
    )

    result = services.enqueue_pipeline(
        search_config=search_config,
        queries_csv_path=valid_env["queries_csv"],
        enrich_config=enrich_config,
    )

    assert result.id == "fake-chain-id"
    search_sig, scrape_sig = captured_chain_args["sigs"]
    assert search_sig.task == "search"
    assert search_sig.args == (
        str(valid_env["queries_csv"]),
        5,
        str(search_config.log_file),
        str(search_config.output_dir),
        str(search_config.blacklist_path),
    )
    assert scrape_sig.task == "scrape"
    assert scrape_sig.args == (
        "test@example.com",
        str(enrich_config.log_file),
        str(valid_env["about_csv"]),
        str(valid_env["prefixes_csv"]),
    )


# --- CLI smoke tests ---


def test_cli_pipeline_run_success(valid_env, monkeypatch):
    monkeypatch.setattr(
        services,
        "enqueue_pipeline",
        lambda **kwargs: SimpleNamespace(id="fake-task-id"),
    )
    result = runner.invoke(
        root_app,
        [
            "--per-query",
            "5",
            "--contact-email",
            "test@example.com",
        ],
        input="y\n",
    )
    assert result.exit_code == 0
    assert "Validation successful" in result.output
    assert "Pipeline enqueued as background task fake-task-id" in result.output


def test_cli_pipeline_run_validation_failure(valid_env):
    result = runner.invoke(
        root_app,
        [
            "--per-query",
            "0",
            "--contact-email",
            "test@example.com",
        ],
    )
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_cli_pipeline_run_prompts_before_scraping_and_can_be_declined(
    valid_env, monkeypatch
):
    monkeypatch.setattr(
        services,
        "enqueue_pipeline",
        lambda **kwargs: pytest.fail("should not be enqueued when declined"),
    )
    result = runner.invoke(
        root_app,
        [
            "--per-query",
            "5",
            "--contact-email",
            "test@example.com",
        ],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Proceed with search and contact scraping?" in result.output
    assert "Aborted." in result.output


def test_cli_pipeline_run_prompts_before_scraping_and_can_be_confirmed(
    valid_env, monkeypatch
):
    monkeypatch.setattr(
        services,
        "enqueue_pipeline",
        lambda **kwargs: SimpleNamespace(id="fake-task-id"),
    )
    result = runner.invoke(
        root_app,
        [
            "--per-query",
            "5",
            "--contact-email",
            "test@example.com",
        ],
        input="y\n",
    )
    assert result.exit_code == 0
    assert "Pipeline enqueued as background task fake-task-id" in result.output
