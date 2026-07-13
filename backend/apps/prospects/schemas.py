from pathlib import Path

from pydantic import BaseModel, EmailStr, Field, field_validator

from config.settings import get_settings
from core.logging import DEFAULT_LOG_DIR as DEFAULT_LOG_DIR

QUERIES_CSV_PATH = Path("/backend/data/queries.csv")
OUTPUT_DIR = Path("/backend/data")
BLACKLIST_PATH = Path("/backend/data/blacklist.txt")

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_MAX_COUNT = 20
BRAVE_MAX_OFFSET = 9
BRAVE_MAX_RESULTS_PER_QUERY = BRAVE_MAX_COUNT * (BRAVE_MAX_OFFSET + 1)
REQUEST_DELAY_SECONDS = 5
RESULTS_DIRNAME = "results"


class ProspectSearchConfig(BaseModel):
    per_query: int
    contact_email: EmailStr
    log_file: Path

    queries_csv_path: Path = Field(
        default_factory=lambda: QUERIES_CSV_PATH, validate_default=True
    )
    output_dir: Path = Field(default_factory=lambda: OUTPUT_DIR, validate_default=True)
    blacklist_path: Path = Field(
        default_factory=lambda: BLACKLIST_PATH, validate_default=True
    )
    brave_api_key: str = Field(
        default_factory=lambda: get_settings().brave_api_key, validate_default=True
    )

    @field_validator("per_query")
    @classmethod
    def validate_per_query(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("--per-query must be a positive integer")
        return v

    @field_validator("queries_csv_path")
    @classmethod
    def validate_queries_csv_path(cls, v: Path) -> Path:
        if not v.is_file():
            raise ValueError(f"queries.csv not found at {v}")
        return v

    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls, v: Path) -> Path:
        if not v.is_dir():
            raise ValueError(f"Output directory does not exist: {v}")
        return v

    @field_validator("blacklist_path")
    @classmethod
    def validate_blacklist_path(cls, v: Path) -> Path:
        if not v.is_file():
            raise ValueError(f"blacklist.txt not found at {v}")
        non_blank_lines = [line for line in v.read_text().splitlines() if line.strip()]
        if not non_blank_lines:
            raise ValueError(f"blacklist.txt is empty: {v}")
        return v

    @field_validator("brave_api_key")
    @classmethod
    def validate_brave_api_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "BRAVE_API_KEY environment variable is not set (add it to .env)"
            )
        return v

    @field_validator("log_file")
    @classmethod
    def validate_log_file(cls, v: Path) -> Path:
        if v.suffix != ".log":
            raise ValueError(f"--log-file must end in .log, got: {v}")
        if not v.parent.exists():
            raise ValueError(f"--log-file directory does not exist: {v.parent}")
        return v
