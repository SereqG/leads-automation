from pathlib import Path

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.logging import DEFAULT_LOG_DIR as DEFAULT_LOG_DIR

ABOUT_US_CSV_PATH = Path("/backend/data/about-us-urls.csv")
# Allowlist of generic mailbox prefixes (info, kontakt, biuro, ...): a scraped
# email is kept only if its local-part matches one of these; personal addresses
# like name.surname@ are discarded and never written to the results workbook.
EMAIL_PREFIXES_CSV_PATH = Path("/backend/data/email_prefixes_only.csv")

# Politeness: wait at least this long between every outbound request. Raised to
# the host's robots.txt Crawl-delay when that is larger.
SCRAPE_DELAY_SECONDS = 3
# Per-request connect/read timeout for page and robots.txt fetches.
PAGE_FETCH_TIMEOUT = 15
# requests' auto-follow is disabled (allow_redirects=False) so every hop can
# be re-validated (public-IP + robots.txt); this caps how many same-host hops
# we'll manually follow before giving up.
MAX_REDIRECTS = 5

# How often (in processed rows) the results workbook is checkpointed to disk
# during a long contact-scrape run, plus a final save once the loop ends —
# saving every single row rewrites the whole file each time (O(n^2) bytes
# written for N rows).
CHECKPOINT_EVERY_ROWS = 25

# Hard cap on a fetched page's body size (checked via len(response.text) as a
# close-enough proxy for bytes — exact byte-accuracy isn't critical for this
# email-regex-scanning use case) to guard against a hostile/broken site
# spiking memory.
MAX_PAGE_BYTES = 5 * 1024 * 1024

# Identify the crawler and give site owners a way to reach us (the contact email
# collected for the run) — standard courtesy for polite scraping.
USER_AGENT_TEMPLATE = "LeadGenBot/1.0 (+mailto:{email})"

# Plain-text email matches ending in one of these are almost always asset
# filenames (e.g. "sprite@2x.png"), not real addresses; drop them.
ASSET_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".css", ".js"}
)

# A local-part matches an allowlisted prefix when it equals the prefix or starts
# with the prefix immediately followed by one of these separators — so "info"
# matches info@, info.sales@ and info-pl@, but not informacje@.
EMAIL_PREFIX_SEPARATORS = (".", "-", "_")

# Columns appended to the results workbook.
CONTACT_URL_HEADER = "contact_url"
CONTACT_EMAIL_HEADER = "contact_email"
# Polish for "contact form" — kept verbatim (not translated/snake_cased like
# the other two headers) per explicit user request.
CONTACT_FORM_HEADER = "formularz kontaktowy"
# Red fill (ARGB) applied to cells with no contact url/email (cell value is
# left empty rather than a literal "None" string — see _write_contact_cell).
NONE_FILL_COLOR = "FFFF0000"


def _validate_about_us_csv_path(v: Path) -> Path:
    if not v.is_file():
        raise ValueError(f"about-us-urls.csv not found at {v}")
    return v


def _validate_email_prefixes_csv_path(v: Path) -> Path:
    if not v.is_file():
        raise ValueError(f"email_prefixes_only.csv not found at {v}")
    return v


def _validate_log_file(v: Path) -> Path:
    if v.suffix != ".log":
        raise ValueError(f"--log-file must end in .log, got: {v}")
    if not v.parent.exists():
        raise ValueError(f"--log-file directory does not exist: {v.parent}")
    return v


class ScrapeContactsChainConfig(BaseModel):
    """Contact-scrape inputs minus results_xlsx_path: contact scraping always
    runs chained after a prospect search whose output xlsx doesn't exist yet
    at validation time (the chain supplies that path at runtime, once the
    search stage has actually produced it)."""

    contact_email: EmailStr
    log_file: Path

    about_us_csv_path: Path = Field(
        default_factory=lambda: ABOUT_US_CSV_PATH, validate_default=True
    )

    email_prefixes_csv_path: Path = Field(
        default_factory=lambda: EMAIL_PREFIXES_CSV_PATH, validate_default=True
    )

    @field_validator("about_us_csv_path")
    @classmethod
    def validate_about_us_csv_path(cls, v: Path) -> Path:
        return _validate_about_us_csv_path(v)

    @field_validator("email_prefixes_csv_path")
    @classmethod
    def validate_email_prefixes_csv_path(cls, v: Path) -> Path:
        return _validate_email_prefixes_csv_path(v)

    @field_validator("log_file")
    @classmethod
    def validate_log_file(cls, v: Path) -> Path:
        return _validate_log_file(v)
