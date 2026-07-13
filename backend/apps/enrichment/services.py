import csv
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.robotparser import RobotFileParser

import requests
from email_validator import EmailNotValidError, validate_email
from openpyxl import load_workbook
from openpyxl.styles import PatternFill

from core import validation as core_validation
from core.logging import configure_logging

from . import schemas

# Emails embedded as plain text (as opposed to mailto: links).
_TEXT_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# mailto: links — stop at the first character that ends the address / starts
# query params, so "mailto:foo@bar.com?subject=Hi" yields "foo@bar.com".
_MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.IGNORECASE)


def validate_scrape_inputs_for_chain(
    contact_email: str, log_file: Optional[Path]
) -> schemas.ScrapeContactsChainConfig:
    """Validate contact-scrape inputs except the results workbook path, which
    isn't known until the chained prospect-search stage produces it."""
    bootstrap_logger = configure_logging(log_file=None)
    bootstrap_logger.info("Validating contact-scrape inputs...")

    resolved_log_file = core_validation.resolve_log_path(
        log_file, schemas.DEFAULT_LOG_DIR
    )

    config = core_validation.validate_config(
        schemas.ScrapeContactsChainConfig,
        {
            "contact_email": contact_email,
            "log_file": resolved_log_file,
        },
        resolved_log_file,
        schemas.DEFAULT_LOG_DIR,
        "contact-scrape inputs",
    )

    logger = configure_logging(log_file=config.log_file)
    logger.info(
        "Validation passed: contact_email=%s log_file=%s about_us_csv_path=%s "
        "email_prefixes_csv_path=%s",
        config.contact_email,
        config.log_file,
        config.about_us_csv_path,
        config.email_prefixes_csv_path,
    )
    return config


def build_user_agent(contact_email: str) -> str:
    return schemas.USER_AGENT_TEMPLATE.format(email=contact_email)


def read_about_us_slugs(csv_path: Path) -> list[str]:
    """Read the url_slug column from about-us-urls.csv, stripping blanks and
    deduplicating while preserving first-seen order (the CSV has repeats such
    as /bok and /faq)."""
    slugs: list[str] = []
    seen: set[str] = set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        for record in csv.DictReader(f):
            slug = (record.get("url_slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            slugs.append(slug)
    return slugs


def read_email_prefixes(csv_path: Path) -> frozenset[str]:
    """Read the prefix column from email_prefixes_only.csv into a lower-cased,
    deduplicated allowlist of generic mailbox prefixes (info, kontakt, ...)."""
    prefixes: set[str] = set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        for record in csv.DictReader(f):
            prefix = (record.get("prefix") or "").strip().lower()
            if prefix:
                prefixes.add(prefix)
    return frozenset(prefixes)


def build_candidate_urls(host: str, slugs: list[str]) -> list[str]:
    """Homepage first (footers frequently carry the contact email), then each
    about-us subpage."""
    urls = [f"https://{host}/"]
    urls.extend(f"https://{host}{slug}" for slug in slugs)
    return urls


def load_robots(
    host: str,
    user_agent: str,
    logger: logging.Logger,
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> RobotFileParser:
    """Fetch and parse a host's robots.txt through the shared requests stack
    (so it honours the OS trust store and our User-Agent). Mirrors urllib's
    status handling: 401/403 -> disallow all, other 4xx/5xx or errors ->
    allow all (nothing to respect)."""
    rp = RobotFileParser()
    robots_url = f"https://{host}/robots.txt"
    sleep_fn(schemas.SCRAPE_DELAY_SECONDS)
    try:
        response = http_get(
            robots_url,
            headers={"User-Agent": user_agent},
            timeout=schemas.PAGE_FETCH_TIMEOUT,
        )
    except requests.RequestException as exc:
        rp.allow_all = True
        logger.warning(
            "Could not fetch robots.txt for %s (%s); assuming crawling allowed",
            host,
            exc,
        )
        return rp

    if response.status_code == 200:
        rp.parse(response.text.splitlines())
        logger.info("Loaded robots.txt for %s", host)
    elif response.status_code in (401, 403):
        rp.disallow_all = True
        logger.info(
            "robots.txt for %s returned %d; treating as disallow-all",
            host,
            response.status_code,
        )
    else:
        rp.allow_all = True
        logger.info(
            "No usable robots.txt for %s (status %d); assuming crawling allowed",
            host,
            response.status_code,
        )
    return rp


def resolve_crawl_delay(rp: RobotFileParser, user_agent: str) -> float:
    """Effective per-request delay: our baseline, raised to the host's
    Crawl-delay directive when that is larger."""
    crawl_delay = rp.crawl_delay(user_agent)
    if crawl_delay is None:
        return float(schemas.SCRAPE_DELAY_SECONDS)
    return max(float(schemas.SCRAPE_DELAY_SECONDS), float(crawl_delay))


def fetch_page(
    url: str,
    user_agent: str,
    logger: logging.Logger,
    http_get: Callable[..., requests.Response] = requests.get,
    timeout: int = schemas.PAGE_FETCH_TIMEOUT,
) -> Optional[str]:
    """Return HTML text for url, or None (content unavailable — move on) on a
    request error, a non-200 status, or non-HTML content."""
    try:
        response = http_get(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.info("No content from %s (%s)", url, exc)
        return None

    if response.status_code != 200:
        logger.info("No content from %s (status %d)", url, response.status_code)
        return None

    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type and "text" not in content_type:
        logger.info("Skipping non-HTML content at %s (%r)", url, content_type)
        return None

    return response.text


def _is_asset_filename(candidate: str) -> bool:
    lowered = candidate.lower()
    return any(lowered.endswith(ext) for ext in schemas.ASSET_EXTENSIONS)


def _prefix_allowed(local_part: str, allowed_prefixes: frozenset[str]) -> bool:
    """Whether an email's local-part matches the allowlist: equal to a prefix, or
    a prefix followed immediately by a separator (info@, info.sales@, info-pl@)."""
    lp = local_part.lower()
    for prefix in allowed_prefixes:
        if lp == prefix or any(
            lp.startswith(prefix + sep) for sep in schemas.EMAIL_PREFIX_SEPARATORS
        ):
            return True
    return False


def extract_contact_email(html: str, allowed_prefixes: frozenset[str]) -> Optional[str]:
    """Return the first valid email in the page whose local-part matches the
    prefix allowlist. mailto: links are preferred over plain-text matches; asset
    filenames like 'sprite@2x.png' are ignored, as are addresses whose prefix is
    not on the allowlist (e.g. personal name.surname@ addresses)."""
    candidates = _MAILTO_RE.findall(html) + _TEXT_EMAIL_RE.findall(html)
    for candidate in candidates:
        candidate = candidate.strip().rstrip(".")
        if _is_asset_filename(candidate):
            continue
        try:
            validated = validate_email(candidate, check_deliverability=False)
        except EmailNotValidError:
            continue
        local_part = validated.normalized.rsplit("@", 1)[0]
        if not _prefix_allowed(local_part, allowed_prefixes):
            continue
        return validated.normalized
    return None


def scrape_domain_contact(
    host: str,
    slugs: list[str],
    rp: RobotFileParser,
    user_agent: str,
    delay: float,
    logger: logging.Logger,
    allowed_prefixes: frozenset[str],
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[Optional[str], Optional[str]]:
    """Try each candidate page for host in order; return (url, email) for the
    first page that yields an allowlisted email, or (None, None) if none do."""
    for url in build_candidate_urls(host, slugs):
        if not rp.can_fetch(user_agent, url):
            logger.info("robots.txt disallows %s; skipping", url)
            continue
        sleep_fn(delay)
        logger.info("Fetching %s", url)
        html = fetch_page(url, user_agent, logger, http_get=http_get)
        if html is None:
            continue
        email = extract_contact_email(html, allowed_prefixes)
        if email:
            logger.info("Found contact email %s at %s", email, url)
            return url, email
        logger.info("Content at %s has no email; trying next page", url)
    return None, None


def _find_column(header_row: list, name: str) -> int:
    """1-based index of the named column in the workbook header."""
    for index, value in enumerate(header_row, start=1):
        if value == name:
            return index
    raise ValueError(f"Results workbook has no {name!r} column")


def _write_contact_cell(sheet, row: int, column: int, value: Optional[str]) -> None:
    cell = sheet.cell(row=row, column=column)
    if value is None:
        cell.value = schemas.NONE_CELL_TEXT
        cell.fill = PatternFill(fill_type="solid", fgColor=schemas.NONE_FILL_COLOR)
    else:
        cell.value = value


@dataclass(frozen=True)
class ContactScrapeStats:
    processed: int
    found: int


def populate_contact_columns(
    workbook,
    sheet,
    domain_col: int,
    url_col: int,
    email_col: int,
    xlsx_path: Path,
    slugs: list[str],
    user_agent: str,
    logger: logging.Logger,
    allowed_prefixes: frozenset[str],
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ContactScrapeStats:
    """Scrape a contact url/email for each domain row in sheet and write it into
    url_col/email_col, caching robots.txt per host. Saves the workbook after
    every row so a long run's progress survives an interruption. Returns
    processed/found counts for the caller's summary log."""
    robots_cache: dict[str, tuple[RobotFileParser, float]] = {}
    processed = 0
    found = 0
    for row in range(2, sheet.max_row + 1):
        domain = sheet.cell(row=row, column=domain_col).value
        if domain is None or not str(domain).strip():
            continue
        host = str(domain).strip()

        if host not in robots_cache:
            rp = load_robots(host, user_agent, logger, http_get, sleep_fn)
            robots_cache[host] = (rp, resolve_crawl_delay(rp, user_agent))
        rp, delay = robots_cache[host]

        logger.info("Scraping contact for domain=%s", host)
        contact_url, contact_email = scrape_domain_contact(
            host,
            slugs,
            rp,
            user_agent,
            delay,
            logger,
            allowed_prefixes,
            http_get,
            sleep_fn,
        )
        if contact_email is None:
            logger.info("No contact email found for domain=%s", host)

        _write_contact_cell(sheet, row, url_col, contact_url)
        _write_contact_cell(sheet, row, email_col, contact_email)

        processed += 1
        if contact_email is not None:
            found += 1
        workbook.save(str(xlsx_path))

    return ContactScrapeStats(processed=processed, found=found)


def augment_workbook_with_contacts(
    xlsx_path: Path,
    slugs: list[str],
    user_agent: str,
    logger: logging.Logger,
    allowed_prefixes: frozenset[str],
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Path:
    """Add contact_url / contact_email columns to the results workbook, one row
    per domain, colouring cells red where nothing was found. Saves after every
    row so a long run's progress survives an interruption."""
    workbook = load_workbook(xlsx_path)
    sheet = workbook.active

    header_row = [
        sheet.cell(row=1, column=col).value for col in range(1, sheet.max_column + 1)
    ]
    domain_col = _find_column(header_row, "domain")
    url_col = len(header_row) + 1
    email_col = len(header_row) + 2
    sheet.cell(row=1, column=url_col, value=schemas.CONTACT_URL_HEADER)
    sheet.cell(row=1, column=email_col, value=schemas.CONTACT_EMAIL_HEADER)

    stats = populate_contact_columns(
        workbook,
        sheet,
        domain_col,
        url_col,
        email_col,
        xlsx_path,
        slugs,
        user_agent,
        logger,
        allowed_prefixes,
        http_get,
        sleep_fn,
    )

    logger.info(
        "Contact scraping complete: %d domain(s) processed, %d with email, "
        "%d without (marked red) in %s",
        stats.processed,
        stats.found,
        stats.processed - stats.found,
        xlsx_path,
    )
    return xlsx_path


def scrape_contacts(
    results_xlsx_path: Path,
    contact_email: str,
    about_us_csv_path: Path,
    email_prefixes_csv_path: Path,
    logger: logging.Logger,
    http_get: Callable[..., requests.Response] = requests.get,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Path:
    """Entry point: read the about-us slug list and the email-prefix allowlist,
    then augment the results workbook with a contact url/email for each domain,
    keeping only emails whose local-part matches an allowlisted prefix."""
    slugs = read_about_us_slugs(about_us_csv_path)
    allowed_prefixes = read_email_prefixes(email_prefixes_csv_path)
    if not allowed_prefixes:
        logger.warning(
            "Email-prefix allowlist %s is empty; no contact emails will be kept",
            email_prefixes_csv_path,
        )
    user_agent = build_user_agent(contact_email)
    logger.info(
        "Starting contact scrape for %s using %d candidate slug(s) and %d "
        "allowlisted prefix(es); user_agent=%s",
        results_xlsx_path,
        len(slugs),
        len(allowed_prefixes),
        user_agent,
    )
    return augment_workbook_with_contacts(
        results_xlsx_path,
        slugs,
        user_agent,
        logger,
        allowed_prefixes,
        http_get=http_get,
        sleep_fn=sleep_fn,
    )
