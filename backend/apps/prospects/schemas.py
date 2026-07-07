from pathlib import Path

from pydantic import BaseModel, EmailStr, Field, field_validator

QUERIES_CSV_PATH = Path("/backend/data/queries.csv")
OUTPUT_DIR = Path("/backend/data")
BLACKLIST_PATH = Path("/backend/data/blacklist.txt")
DEFAULT_LOG_DIR = Path("/backend/logs")


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

    @field_validator("log_file")
    @classmethod
    def validate_log_file(cls, v: Path) -> Path:
        if v.suffix != ".log":
            raise ValueError(f"--log-file must end in .log, got: {v}")
        if not v.parent.exists():
            raise ValueError(f"--log-file directory does not exist: {v.parent}")
        return v
