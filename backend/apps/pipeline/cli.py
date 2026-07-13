import logging
from pathlib import Path
from typing import Optional

import typer

from apps.enrichment import services as enrichment_services
from apps.prospects import services as prospects_services
from core.exceptions import ValidationFailedError
from core.logging import LOGGER_NAME

from . import services

app = typer.Typer(help="End-to-end discovery + enrichment pipeline commands")


def _confirm_deduplication(report: prospects_services.DuplicateQueryReport) -> bool:
    count = len(report.duplicate_queries)
    typer.secho(
        f"Found {count} duplicate quer{'y' if count == 1 else 'ies'} in queries.csv:",
        fg=typer.colors.YELLOW,
    )
    for query in report.duplicate_queries:
        typer.echo(f"  - {query}")
    return typer.confirm("Deduplicate queries.csv now?")


@app.command("run")
def run(
    per_query: int = typer.Option(
        ...,
        "--per-query",
        prompt="Max results to fetch per query",
        help="Max results to fetch per query",
    ),
    contact_email: str = typer.Option(
        ...,
        "--contact-email",
        prompt="Contact email for this run",
        help="Contact email for this search run and the scraper's User-Agent",
    ),
    search_log_file: Optional[Path] = typer.Option(
        None, "--search-log-file", help="Override the search stage's log file path"
    ),
    enrich_log_file: Optional[Path] = typer.Option(
        None, "--enrich-log-file", help="Override the enrichment stage's log file path"
    ),
) -> None:
    try:
        search_config = prospects_services.validate_search_inputs(
            per_query=per_query, contact_email=contact_email, log_file=search_log_file
        )
        enrich_config = enrichment_services.validate_scrape_inputs_for_chain(
            contact_email=contact_email, log_file=enrich_log_file
        )
    except ValidationFailedError as exc:
        for message in exc.errors:
            typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(
        f"Validation successful. Search log: {search_config.log_file}; "
        f"enrichment log: {enrich_config.log_file}",
        fg=typer.colors.GREEN,
    )

    logger = logging.getLogger(LOGGER_NAME)
    queries_path = prospects_services.check_and_deduplicate_queries(
        search_config.queries_csv_path, logger, _confirm_deduplication
    )
    if queries_path != search_config.queries_csv_path:
        typer.secho(
            f"Using deduplicated queries file: {queries_path}", fg=typer.colors.GREEN
        )

    typer.secho(
        "This will run a prospect search, then automatically scrape contact "
        "pages (homepage + about-us subpages) from every domain it finds, "
        "respecting robots.txt with a delay between requests.",
        fg=typer.colors.YELLOW,
    )
    if not typer.confirm("Proceed with search and contact scraping?"):
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    async_result = services.enqueue_pipeline(
        search_config=search_config,
        queries_csv_path=queries_path,
        enrich_config=enrich_config,
    )
    typer.secho(
        f"Pipeline enqueued as background task {async_result.id}.",
        fg=typer.colors.GREEN,
    )
    typer.echo(
        f"Results will be written under "
        f"{prospects_services.resolve_results_dir(search_config.output_dir)} once "
        f"the search stage completes; contact_url/contact_email columns will be "
        f"added once the enrichment stage completes. Progress is logged to "
        f"{search_config.log_file} and {enrich_config.log_file}."
    )
