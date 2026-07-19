from celery import shared_task
from .services import instagram_send, instagram_sender_action, process_pending_customer_reply, resolve_instagram_event


LOCATION_LINKS = ["https://yandex.uz/maps/-/CTVJzD4O", "https://yandex.uz/maps/-/CTVJfPoq"]


def split_location_reply(text):
    if not all(link in text for link in LOCATION_LINKS):
        return [text]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = []
    second = []
    current = first
    trailing = []
    for line in lines:
        if line.lower().startswith("manzillar"):
            continue
        if line.startswith("2."):
            current = second
        if line.startswith("Qaysi "):
            trailing.append(line)
            continue
        current.append(line)
    messages = ["\n".join(part) for part in [first, second] if part]
    if trailing and messages:
        messages[-1] = messages[-1] + "\n\n" + "\n".join(trailing)
    return messages if len(messages) > 1 else [text]


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
        try:
            instagram_sender_action(recipient_id, "typing_off")
        except Exception as exc:
            print(f"INSTAGRAM_TYPING_OFF_FAILED recipient={recipient_id} error={exc}", flush=True)
        return None
    try:
        for text in split_location_reply(reply.text):
            instagram_send(recipient_id, text)
    finally:
        try:
            instagram_sender_action(recipient_id, "typing_off")
        except Exception as exc:
            print(f"INSTAGRAM_TYPING_OFF_FAILED recipient={recipient_id} error={exc}", flush=True)
    return reply.id
