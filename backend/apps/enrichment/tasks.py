from pathlib import Path

from config.celery_app import celery_app
from core.logging import configure_logging

from . import services


@celery_app.task(name="enrichment.scrape_contacts")
def scrape_contacts_task(
    results_xlsx_path: str,
    contact_email: str,
    log_file: str,
    about_us_csv_path: str,
    email_prefixes_csv_path: str,
) -> str:
    logger = configure_logging(log_file=Path(log_file))
    dest_path = services.scrape_contacts(
        results_xlsx_path=Path(results_xlsx_path),
        contact_email=contact_email,
        about_us_csv_path=Path(about_us_csv_path),
        email_prefixes_csv_path=Path(email_prefixes_csv_path),
        logger=logger,
    )
    return str(dest_path)
