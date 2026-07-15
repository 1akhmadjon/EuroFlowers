from celery import shared_task
from .services import resolve_instagram_event


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_instagram_webhook(payload):
    return resolve_instagram_event(payload)
