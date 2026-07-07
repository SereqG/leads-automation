from pathlib import Path
from typing import Optional

import typer

from core.exceptions import ValidationFailedError

from . import services

app = typer.Typer(help="Prospect discovery commands")


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
