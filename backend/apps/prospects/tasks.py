from pathlib import Path

from config.celery_app import celery_app
from config.settings import get_settings
from core.logging import configure_logging

from . import services


@celery_app.task(name="prospects.run_search")
def run_prospect_search_task(
    queries_csv_path: str,
    per_query: int,
    log_file: str,
    output_dir: str,
    blacklist_path: str,
) -> str:
    logger = configure_logging(log_file=Path(log_file))
    api_key = get_settings().brave_api_key
    dest_path = services.run_prospect_search(
        queries_csv_path=Path(queries_csv_path),
        per_query=per_query,
        api_key=api_key,
        logger=logger,
        output_dir=Path(output_dir),
        blacklist_path=Path(blacklist_path),
    )
    return str(dest_path)
