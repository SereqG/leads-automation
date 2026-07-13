from pathlib import Path

from celery import chain
from celery.result import AsyncResult

from apps.enrichment import schemas as enrichment_schemas
from apps.enrichment import tasks as enrichment_tasks
from apps.prospects import schemas as prospects_schemas
from apps.prospects import tasks as prospects_tasks


def enqueue_pipeline(
    search_config: prospects_schemas.ProspectSearchConfig,
    queries_csv_path: Path,
    enrich_config: enrichment_schemas.ScrapeContactsChainConfig,
) -> AsyncResult:
    """Enqueue a prospect search followed by contact scraping of its output,
    as a single Celery chain: the search task's return value (the results
    xlsx path) is passed straight into the scrape task as its first
    argument once the search task completes."""
    workflow = chain(
        prospects_tasks.run_prospect_search_task.s(
            str(queries_csv_path),
            search_config.per_query,
            str(search_config.log_file),
            str(search_config.output_dir),
            str(search_config.blacklist_path),
        ),
        enrichment_tasks.scrape_contacts_task.s(
            str(enrich_config.contact_email),
            str(enrich_config.log_file),
            str(enrich_config.about_us_csv_path),
            str(enrich_config.email_prefixes_csv_path),
        ),
    )
    return workflow.delay()
