from celery import shared_task
import threading
import time
from .services import instagram_send, instagram_sender_action, process_pending_customer_reply, resolve_instagram_event, resolve_telegram_update, send_due_lead_recalls, send_instagram_context_image, send_lead_recall, telegram_send, telegram_sender_action


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


def keep_typing(send_action, stop_event, error_label, interval=4):
    while not stop_event.is_set():
        try:
            send_action()
        except Exception as exc:
            print(f"{error_label} error={exc}", flush=True)
        stop_event.wait(interval)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_instagram_webhook(payload):
    jobs = resolve_instagram_event(payload)
    for job in jobs:
        process_delayed_instagram_reply.apply_async(args=[job["conversation_id"], job["message_id"], job["recipient_id"]], countdown=2)
    return jobs


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_delayed_instagram_reply(conversation_id, expected_message_id, recipient_id):
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(lambda: instagram_sender_action(recipient_id, "typing_on"), stop_typing, f"INSTAGRAM_TYPING_ON_FAILED recipient={recipient_id}"), daemon=True)
    typing_thread.start()
    try:
        reply = process_pending_customer_reply(conversation_id, expected_message_id)
        if not reply:
            return None
        try:
            send_instagram_context_image(recipient_id, reply.conversation, reply)
        except Exception as exc:
            print(f"INSTAGRAM_CONTEXT_IMAGE_SEND_FAILED conversation={conversation_id} recipient={recipient_id} error={exc}", flush=True)
        for text in split_location_reply(reply.text):
            instagram_send(recipient_id, text)
        return reply.id
    finally:
        stop_typing.set()
        typing_thread.join(timeout=1)
        try:
            instagram_sender_action(recipient_id, "typing_off")
        except Exception as exc:
            print(f"INSTAGRAM_TYPING_OFF_FAILED recipient={recipient_id} error={exc}", flush=True)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_telegram_webhook(payload):
    jobs = resolve_telegram_update(payload)
    for job in jobs:
        process_delayed_telegram_reply.apply_async(args=[job["conversation_id"], job["message_id"], job["chat_id"]], countdown=2)
    return jobs


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_delayed_telegram_reply(conversation_id, expected_message_id, chat_id):
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(lambda: telegram_sender_action(chat_id, "typing"), stop_typing, f"TELEGRAM_TYPING_FAILED chat={chat_id}"), daemon=True)
    typing_thread.start()
    try:
        reply = process_pending_customer_reply(conversation_id, expected_message_id)
        if not reply:
            return None
        for text in split_location_reply(reply.text):
            telegram_send(chat_id, text)
        return reply.id
    finally:
        stop_typing.set()
        typing_thread.join(timeout=1)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_lead_recall(lead_id):
    return bool(send_lead_recall(lead_id))


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_due_lead_recalls():
    return send_due_lead_recalls()
