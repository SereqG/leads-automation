from celery import Celery

from config.settings import get_settings
from core.network import enable_system_trust_store

enable_system_trust_store()

settings = get_settings()

celery_app = Celery(
    "leadgen",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.autodiscover_tasks(["apps.prospects", "apps.enrichment"])
