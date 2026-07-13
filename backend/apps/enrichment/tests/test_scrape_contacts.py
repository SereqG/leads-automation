import logging
from urllib.robotparser import RobotFileParser

import pytest
import requests
from openpyxl import Workbook, load_workbook

from apps.enrichment import schemas, services, tasks
from core.exceptions import ValidationFailedError

LOGGER = logging.getLogger("test-enrichment")

# Allowlist used across email-extraction/scraping tests.
ALLOWED_PREFIXES = frozenset({"info", "hello", "kontakt", "office", "sales"})


class FakeResponse:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers if headers is not None else {"Content-Type": "text/html"}


# --- read_about_us_slugs ---


def test_read_about_us_slugs_dedupes_preserving_order(tmp_path):
    csv_path = tmp_path / "about.csv"
    csv_path.write_text(
        "page_name,url_slug\n"
        "O nas,/o-nas\n"
        "BOK,/bok\n"
        "Biuro,/bok\n"  # duplicate slug
        "FAQ,/faq\n"
        "Blank,\n"  # empty slug -> skipped
    )
    assert services.read_about_us_slugs(csv_path) == ["/o-nas", "/bok", "/faq"]


# --- read_email_prefixes ---


def test_read_email_prefixes_lowercases_strips_and_dedupes(tmp_path):
    csv_path = tmp_path / "email_prefixes.csv"
    csv_path.write_text(
        "prefix\n"
        "info\n"
        "  Office  \n"  # surrounding whitespace + mixed case
        "INFO\n"  # duplicate after lowercasing
        "\n"  # blank -> skipped
        "kontakt\n"
    )
    assert services.read_email_prefixes(csv_path) == frozenset(
        {"info", "office", "kontakt"}
    )


# --- build_candidate_urls ---


def test_build_candidate_urls_homepage_first():
    assert services.build_candidate_urls("example.com", ["/o-nas", "/kontakt"]) == [
        "https://example.com/",
        "https://example.com/o-nas",
        "https://example.com/kontakt",
    ]


# --- extract_contact_email ---


def test_extract_contact_email_prefers_mailto_over_plain_text():
    html = 'plain info@plain.com <a href="mailto:hello@shop.pl?subject=Hi">write</a>'
    assert services.extract_contact_email(html, ALLOWED_PREFIXES) == "hello@shop.pl"


def test_extract_contact_email_plain_text_normalized_and_trimmed():
    # The trailing sentence period is stripped and the address is normalised.
    assert (
        services.extract_contact_email(
            "Reach us at Info@Example.COM.", ALLOWED_PREFIXES
        )
        == "info@example.com"
    )


def test_extract_contact_email_rejects_asset_filenames():
    assert (
        services.extract_contact_email('<img src="sprite@2x.png">', ALLOWED_PREFIXES)
        is None
    )


def test_extract_contact_email_none_when_absent():
    assert (
        services.extract_contact_email("no email here, just words", ALLOWED_PREFIXES)
        is None
    )


def test_extract_contact_email_keeps_allowlisted_prefixes():
    # Exact prefix and prefix-plus-separator variants are all kept.
    assert (
        services.extract_contact_email("Write to info@company.pl", ALLOWED_PREFIXES)
        == "info@company.pl"
    )
    assert (
        services.extract_contact_email(
            "Write to info.sales@company.pl", ALLOWED_PREFIXES
        )
        == "info.sales@company.pl"
    )
    assert (
        services.extract_contact_email("Write to info-pl@company.pl", ALLOWED_PREFIXES)
        == "info-pl@company.pl"
    )


def test_extract_contact_email_drops_non_allowlisted_prefixes():
    # Personal addresses and prefixes without a separator boundary are dropped.
    assert (
        services.extract_contact_email(
            "Write to name.surname@company.pl", ALLOWED_PREFIXES
        )
        is None
    )
    assert (
        services.extract_contact_email(
            "Write to informacje@company.pl", ALLOWED_PREFIXES
        )
        is None
    )


def test_extract_contact_email_prefix_match_is_case_insensitive():
    assert (
        services.extract_contact_email("Write to INFO@Company.PL", ALLOWED_PREFIXES)
        == "info@company.pl"
    )


def test_extract_contact_email_skips_personal_and_returns_allowlisted():
    # A page with both a personal and a generic address keeps the generic one.
    html = "Jan: jan.kowalski@company.pl or general info@company.pl"
    assert services.extract_contact_email(html, ALLOWED_PREFIXES) == "info@company.pl"


# --- fetch_page ---


def test_fetch_page_returns_text_for_html():
    resp = FakeResponse(
        text="<html>ok</html>", headers={"Content-Type": "text/html; charset=utf-8"}
    )
    assert (
        services.fetch_page(
            "https://x.com/", "ua", LOGGER, http_get=lambda *a, **k: resp
        )
        == "<html>ok</html>"
    )


def test_fetch_page_none_on_non_200():
    resp = FakeResponse(status_code=404)
    assert (
        services.fetch_page(
            "https://x.com/x", "ua", LOGGER, http_get=lambda *a, **k: resp
        )
        is None
    )


def test_fetch_page_none_on_non_html_content():
    resp = FakeResponse(text="%PDF", headers={"Content-Type": "application/pdf"})
    assert (
        services.fetch_page(
            "https://x.com/f", "ua", LOGGER, http_get=lambda *a, **k: resp
        )
        is None
    )


def test_fetch_page_none_on_request_exception():
    def boom(*args, **kwargs):
        raise requests.ConnectionError("boom")

    assert services.fetch_page("https://x.com/", "ua", LOGGER, http_get=boom) is None


# --- load_robots ---


def test_load_robots_parses_disallow_rules():
    resp = FakeResponse(
        text="User-agent: *\nDisallow: /private\n",
        headers={"Content-Type": "text/plain"},
    )
    rp = services.load_robots(
        "example.com",
        "ua",
        LOGGER,
        http_get=lambda *a, **k: resp,
        sleep_fn=lambda s: None,
    )
    assert rp.can_fetch("ua", "https://example.com/private/x") is False
    assert rp.can_fetch("ua", "https://example.com/o-nas") is True


def test_load_robots_allows_all_on_404():
    resp = FakeResponse(status_code=404)
    rp = services.load_robots(
        "example.com",
        "ua",
        LOGGER,
        http_get=lambda *a, **k: resp,
        sleep_fn=lambda s: None,
    )
    assert rp.can_fetch("ua", "https://example.com/anything") is True


def test_load_robots_disallows_all_on_403():
    resp = FakeResponse(status_code=403)
    rp = services.load_robots(
        "example.com",
        "ua",
        LOGGER,
        http_get=lambda *a, **k: resp,
        sleep_fn=lambda s: None,
    )
    assert rp.can_fetch("ua", "https://example.com/anything") is False


def test_load_robots_allows_all_on_request_exception():
    def boom(*args, **kwargs):
        raise requests.ConnectionError("boom")

    rp = services.load_robots(
        "example.com", "ua", LOGGER, http_get=boom, sleep_fn=lambda s: None
    )
    assert rp.can_fetch("ua", "https://example.com/anything") is True


# --- scrape_domain_contact ---


def test_scrape_domain_contact_skips_disallowed_and_finds_next():
    rp = RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /o-nas"])

    fetched = []

    def http_get(url, headers, timeout):
        fetched.append(url)
        if url == "https://example.com/":
            return FakeResponse(text="homepage, no email")
        if url == "https://example.com/kontakt":
            return FakeResponse(text='<a href="mailto:kontakt@example.com">mail</a>')
        return FakeResponse(status_code=404)

    url, email = services.scrape_domain_contact(
        "example.com",
        ["/o-nas", "/kontakt"],
        rp,
        "ua",
        3,
        LOGGER,
        ALLOWED_PREFIXES,
        http_get=http_get,
        sleep_fn=lambda s: None,
    )

    assert (url, email) == ("https://example.com/kontakt", "kontakt@example.com")
    # /o-nas is disallowed by robots.txt and must never be requested.
    assert "https://example.com/o-nas" not in fetched
    assert fetched == ["https://example.com/", "https://example.com/kontakt"]


def test_scrape_domain_contact_returns_none_when_nothing_found():
    rp = RobotFileParser()
    rp.allow_all = True

    def http_get(url, headers, timeout):
        return FakeResponse(status_code=404)

    assert services.scrape_domain_contact(
        "example.com",
        ["/o-nas"],
        rp,
        "ua",
        3,
        LOGGER,
        ALLOWED_PREFIXES,
        http_get=http_get,
        sleep_fn=lambda s: None,
    ) == (None, None)


def test_scrape_domain_contact_skips_personal_email_and_keeps_looking():
    rp = RobotFileParser()
    rp.allow_all = True

    def http_get(url, headers, timeout):
        if url == "https://example.com/":
            # Only a personal address on the homepage -> filtered out.
            return FakeResponse(text="Jan Kowalski: jan.kowalski@example.com")
        if url == "https://example.com/kontakt":
            return FakeResponse(text='<a href="mailto:kontakt@example.com">mail</a>')
        return FakeResponse(status_code=404)

    url, email = services.scrape_domain_contact(
        "example.com",
        ["/kontakt"],
        rp,
        "ua",
        3,
        LOGGER,
        ALLOWED_PREFIXES,
        http_get=http_get,
        sleep_fn=lambda s: None,
    )

    assert (url, email) == ("https://example.com/kontakt", "kontakt@example.com")


# --- populate_contact_columns ---


def test_populate_contact_columns_writes_cells_and_returns_stats(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ID", "domain"])
    sheet.append([1, "found.com"])
    sheet.append([2, "none.com"])
    sheet.append([3, ""])  # blank domain -> skipped entirely
    path = tmp_path / "results.xlsx"
    workbook.save(path)

    def http_get(url, headers, timeout):
        if url.endswith("/robots.txt"):
            return FakeResponse(status_code=404)  # allow all
        if url == "https://found.com/":
            return FakeResponse(text='<a href="mailto:info@found.com">contact</a>')
        return FakeResponse(status_code=404)  # none.com yields nothing

    stats = services.populate_contact_columns(
        workbook,
        sheet,
        domain_col=2,
        url_col=3,
        email_col=4,
        xlsx_path=path,
        slugs=["/kontakt"],
        user_agent="ua",
        logger=LOGGER,
        allowed_prefixes=ALLOWED_PREFIXES,
        http_get=http_get,
        sleep_fn=lambda s: None,
    )

    assert stats == services.ContactScrapeStats(processed=2, found=1)

    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert rows[0] == (1, "found.com", "https://found.com/", "info@found.com")
    assert rows[1] == (2, "none.com", "None", "None")

    # Row 4 (blank domain) is untouched — no url/email cells written.
    assert sheet.cell(row=4, column=3).value is None
    assert sheet.cell(row=4, column=4).value is None

    # Saved after every processed row: confirmed by re-reading from disk.
    persisted = list(load_workbook(path).active.iter_rows(min_row=2, values_only=True))
    assert persisted[0] == (1, "found.com", "https://found.com/", "info@found.com")


# --- augment_workbook_with_contacts (end to end) ---


def _make_results_workbook(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ID", "domain", "query"])
    sheet.append([1, "found.com", "q1"])
    sheet.append([2, "none.com", "q2"])
    path = tmp_path / "results.xlsx"
    workbook.save(path)
    return path


def test_augment_workbook_adds_columns_and_marks_red(tmp_path):
    path = _make_results_workbook(tmp_path)

    def http_get(url, headers, timeout):
        if url.endswith("/robots.txt"):
            return FakeResponse(status_code=404)  # allow all
        if url == "https://found.com/":
            return FakeResponse(text='<a href="mailto:info@found.com">contact</a>')
        return FakeResponse(status_code=404)  # none.com yields nothing

    sleeps = []
    services.augment_workbook_with_contacts(
        path,
        ["/kontakt"],
        "ua",
        LOGGER,
        ALLOWED_PREFIXES,
        http_get=http_get,
        sleep_fn=sleeps.append,
    )

    sheet = load_workbook(path).active
    header = [cell.value for cell in sheet[1]]
    assert header == ["ID", "domain", "query", "contact_url", "contact_email"]

    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert rows[0] == (1, "found.com", "q1", "https://found.com/", "info@found.com")
    assert rows[1] == (2, "none.com", "q2", "None", "None")

    # found.com cells: no red fill
    assert sheet.cell(row=2, column=4).fill.fill_type is None
    assert sheet.cell(row=2, column=5).fill.fill_type is None
    # none.com cells: red fill
    for column in (4, 5):
        cell = sheet.cell(row=3, column=column)
        assert cell.fill.fill_type == "solid"
        assert cell.fill.fgColor.rgb == "FFFF0000"

    # A polite delay is applied before every request (robots + each page).
    assert len(sleeps) == 5
    assert all(delay == 3 for delay in sleeps)


# --- validate_scrape_inputs_for_chain ---


@pytest.fixture
def chain_valid_env(tmp_path, monkeypatch):
    about_csv = tmp_path / "about-us-urls.csv"
    about_csv.write_text("page_name,url_slug\nO nas,/o-nas\n")
    prefixes_csv = tmp_path / "email_prefixes_only.csv"
    prefixes_csv.write_text("prefix\ninfo\nkontakt\n")
    logs_dir = tmp_path / "logs"

    monkeypatch.setattr(schemas, "ABOUT_US_CSV_PATH", about_csv)
    monkeypatch.setattr(schemas, "EMAIL_PREFIXES_CSV_PATH", prefixes_csv)
    monkeypatch.setattr(schemas, "DEFAULT_LOG_DIR", logs_dir)

    return {
        "about_csv": about_csv,
        "prefixes_csv": prefixes_csv,
        "logs_dir": logs_dir,
    }


def test_validate_scrape_inputs_for_chain_passes(chain_valid_env, tmp_path):
    config = services.validate_scrape_inputs_for_chain(
        contact_email="test@example.com", log_file=tmp_path / "custom.log"
    )
    assert config.contact_email == "test@example.com"
    assert config.about_us_csv_path == chain_valid_env["about_csv"]
    assert config.email_prefixes_csv_path == chain_valid_env["prefixes_csv"]


def test_validate_scrape_inputs_for_chain_invalid_email_raises(
    chain_valid_env, tmp_path
):
    with pytest.raises(ValidationFailedError) as exc_info:
        services.validate_scrape_inputs_for_chain(
            contact_email="not-an-email", log_file=tmp_path / "custom.log"
        )
    assert any("email" in message.lower() for message in exc_info.value.errors)


def test_validate_scrape_inputs_for_chain_missing_about_csv_raises(
    chain_valid_env, tmp_path, monkeypatch
):
    monkeypatch.setattr(schemas, "ABOUT_US_CSV_PATH", tmp_path / "missing.csv")
    with pytest.raises(ValidationFailedError) as exc_info:
        services.validate_scrape_inputs_for_chain(
            contact_email="test@example.com", log_file=tmp_path / "custom.log"
        )
    assert any(
        "about-us-urls.csv not found" in message for message in exc_info.value.errors
    )


def test_validate_scrape_inputs_for_chain_missing_prefixes_csv_raises(
    chain_valid_env, tmp_path, monkeypatch
):
    monkeypatch.setattr(schemas, "EMAIL_PREFIXES_CSV_PATH", tmp_path / "missing.csv")
    with pytest.raises(ValidationFailedError) as exc_info:
        services.validate_scrape_inputs_for_chain(
            contact_email="test@example.com", log_file=tmp_path / "custom.log"
        )
    assert any(
        "email_prefixes_only.csv not found" in message
        for message in exc_info.value.errors
    )


def test_validate_scrape_inputs_for_chain_has_no_xlsx_field(chain_valid_env, tmp_path):
    config = services.validate_scrape_inputs_for_chain(
        contact_email="test@example.com", log_file=tmp_path / "custom.log"
    )
    assert not hasattr(config, "results_xlsx_path")


# --- Celery task ---


def test_scrape_contacts_task_calls_service(tmp_path, monkeypatch):
    captured = {}
    expected_dest = tmp_path / "results.xlsx"

    def fake_scrape_contacts(**kwargs):
        captured.update(kwargs)
        return expected_dest

    monkeypatch.setattr(tasks.services, "scrape_contacts", fake_scrape_contacts)

    log_file = tmp_path / "run.log"
    about_csv = tmp_path / "about.csv"
    prefixes_csv = tmp_path / "email_prefixes.csv"

    result = tasks.scrape_contacts_task(
        str(expected_dest),
        "me@example.com",
        str(log_file),
        str(about_csv),
        str(prefixes_csv),
    )

    assert result == str(expected_dest)
    assert captured["results_xlsx_path"] == expected_dest
    assert captured["contact_email"] == "me@example.com"
    assert captured["about_us_csv_path"] == about_csv
    assert captured["email_prefixes_csv_path"] == prefixes_csv
