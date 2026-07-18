from celery import shared_task
from .services import instagram_send, instagram_sender_action, process_pending_customer_reply, resolve_instagram_event


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_instagram_webhook(payload):
    jobs = resolve_instagram_event(payload)
    for job in jobs:
        process_delayed_instagram_reply.apply_async(args=[job["conversation_id"], job["message_id"], job["recipient_id"]], countdown=5)
    return jobs


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_delayed_instagram_reply(conversation_id, expected_message_id, recipient_id):
    try:
        instagram_sender_action(recipient_id, "typing_on")
    except Exception as exc:
        print(f"INSTAGRAM_TYPING_ON_FAILED recipient={recipient_id} error={exc}", flush=True)
    reply = process_pending_customer_reply(conversation_id, expected_message_id)
    if not reply:
        return None
    try:
        instagram_send(recipient_id, reply.text)
    finally:
        try:
            instagram_sender_action(recipient_id, "typing_off")
        except Exception as exc:
            print(f"INSTAGRAM_TYPING_OFF_FAILED recipient={recipient_id} error={exc}", flush=True)
    return reply.id
