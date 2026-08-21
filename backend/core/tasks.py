from celery import shared_task
from math import ceil
import threading
import time
from .platform_services import instagram_send, instagram_sender_action, send_due_lead_recalls, send_lead_recall, telegram_send, telegram_sender_action
from .services import AI_FOLLOW_UP_DELAY_SECONDS, INSTAGRAM_AI_REPLY_WAIT_SECONDS, ai_reply_wait_seconds_remaining, process_pending_customer_reply, process_stalled_conversation_follow_up, should_start_ai_reply
from .webhook_services import resolve_instagram_event, resolve_telegram_update
from .backup_services import send_backup_to_telegram



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
        if should_start_ai_reply(job["conversation_id"], job["message_id"]):
            try:
                instagram_sender_action(job["recipient_id"], "typing_on", job.get("account_id"))
            except Exception as exc:
                print(f"INSTAGRAM_TYPING_ON_FAILED recipient={job['recipient_id']} error={exc}", flush=True)
        process_delayed_instagram_reply.apply_async(args=[job["conversation_id"], job["message_id"], job["recipient_id"], job.get("account_id")], countdown=INSTAGRAM_AI_REPLY_WAIT_SECONDS)
    return jobs


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_delayed_instagram_reply(conversation_id, expected_message_id, recipient_id, account_id=None):
    if not should_start_ai_reply(conversation_id, expected_message_id):
        return None
    remaining = ai_reply_wait_seconds_remaining(conversation_id, expected_message_id, INSTAGRAM_AI_REPLY_WAIT_SECONDS)
    if remaining is None:
        return None
    if remaining > 0:
        process_delayed_instagram_reply.apply_async(args=[conversation_id, expected_message_id, recipient_id], countdown=ceil(remaining))
        return None
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(lambda: instagram_sender_action(recipient_id, "typing_on", account_id), stop_typing, f"INSTAGRAM_TYPING_ON_FAILED recipient={recipient_id}"), daemon=True)
    typing_thread.start()
    try:
        reply = process_pending_customer_reply(conversation_id, expected_message_id)
        if not reply:
            return None
        response = instagram_send(recipient_id, reply.text, account_id)
        message_id = (response or {}).get("message_id") or (response or {}).get("mid")
        if message_id:
            reply.instagram_message_id = message_id
            reply.save(update_fields=["instagram_message_id", "updated_at"])
        process_conversation_follow_up.apply_async(args=[conversation_id, reply.id], countdown=AI_FOLLOW_UP_DELAY_SECONDS)
        return reply.id
    finally:
        stop_typing.set()
        typing_thread.join(timeout=1)
        try:
            instagram_sender_action(recipient_id, "typing_off", account_id)
        except Exception as exc:
            print(f"INSTAGRAM_TYPING_OFF_FAILED recipient={recipient_id} error={exc}", flush=True)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_telegram_webhook(payload):
    jobs = resolve_telegram_update(payload)
    for job in jobs:
        process_delayed_telegram_reply.apply_async(args=[job["conversation_id"], job["message_id"], job["chat_id"]], countdown=7)
    return jobs


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=4)
def process_delayed_telegram_reply(conversation_id, expected_message_id, chat_id):
    if not should_start_ai_reply(conversation_id, expected_message_id):
        return None
    remaining = ai_reply_wait_seconds_remaining(conversation_id, expected_message_id)
    if remaining is None:
        return None
    if remaining > 0:
        process_delayed_telegram_reply.apply_async(args=[conversation_id, expected_message_id, chat_id], countdown=ceil(remaining))
        return None
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(lambda: telegram_sender_action(chat_id, "typing"), stop_typing, f"TELEGRAM_TYPING_FAILED chat={chat_id}"), daemon=True)
    typing_thread.start()
    try:
        reply = process_pending_customer_reply(conversation_id, expected_message_id)
        if not reply:
            return None
        telegram_send(chat_id, reply.text)
        process_conversation_follow_up.apply_async(args=[conversation_id, reply.id], countdown=AI_FOLLOW_UP_DELAY_SECONDS)
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


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def send_telegram_backup(triggered_by="auto"):
    return send_backup_to_telegram(triggered_by)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def process_conversation_follow_up(conversation_id, expected_ai_message_id):
    follow_up = process_stalled_conversation_follow_up(conversation_id, expected_ai_message_id)
    if not follow_up:
        return None
    customer = follow_up.conversation.customer
    if customer.instagram_user_id.startswith("telegram:"):
        telegram_send(customer.instagram_user_id.split(":", 1)[1], follow_up.text)
    elif customer.instagram_user_id:
        latest_customer_message = follow_up.conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
        account_id = (latest_customer_message.metadata or {}).get("instagram_account_id") if latest_customer_message else None
        response = instagram_send(customer.instagram_user_id, follow_up.text, account_id)
        message_id = (response or {}).get("message_id") or (response or {}).get("mid")
        if message_id:
            follow_up.instagram_message_id = message_id
            follow_up.save(update_fields=["instagram_message_id", "updated_at"])
    return follow_up.id
