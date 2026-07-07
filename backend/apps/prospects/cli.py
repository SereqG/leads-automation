import logging
from pathlib import Path
from typing import Optional

import typer

from core.exceptions import ValidationFailedError
from core.logging import LOGGER_NAME

from . import services

app = typer.Typer(help="Prospect discovery commands")


def _confirm_deduplication(report: services.DuplicateQueryReport) -> bool:
    count = len(report.duplicate_queries)
    typer.secho(
        f"Found {count} duplicate quer{'y' if count == 1 else 'ies'} in queries.csv:",
        fg=typer.colors.YELLOW,
    )
    for query in report.duplicate_queries:
        typer.echo(f"  - {query}")
    return typer.confirm("Deduplicate queries.csv now?")


@app.command("search")
def search(
    per_query: int = typer.Option(
        ...,
        "--per-query",
        prompt="Max results to fetch per query",
        help="Max results to fetch per query",
    ),
    contact_email: str = typer.Option(
        ...,
        "--contact-email",
        prompt="Contact email for this search run",
        help="Contact email for this search run",
    ),
    log_file: Optional[Path] = typer.Option(
        None, "--log-file", help="Override the default log file path"
    ),
) -> None:
    try:
        config = services.validate_search_inputs(
            per_query=per_query, contact_email=contact_email, log_file=log_file
        )
    except ValidationFailedError as exc:
        for message in exc.errors:
            typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(
        f"Validation successful. Logging to {config.log_file}", fg=typer.colors.GREEN
    )

    logger = logging.getLogger(LOGGER_NAME)
    queries_path = services.check_and_deduplicate_queries(
        config.queries_csv_path, logger, _confirm_deduplication
    )
    if queries_path != config.queries_csv_path:
        typer.secho(
            f"Using deduplicated queries file: {queries_path}", fg=typer.colors.GREEN
        )
