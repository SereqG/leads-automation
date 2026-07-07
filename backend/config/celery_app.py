from celery import Celery

from config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "leadgen",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
