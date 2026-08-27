from celery import shared_task
from math import ceil
import threading
import time
from .platform_services import instagram_send, instagram_sender_action, send_due_lead_recalls, send_lead_recall, telegram_send, telegram_sender_action
from .services import AI_FOLLOW_UP_DELAY_SECONDS, INSTAGRAM_AI_REPLY_WAIT_SECONDS, ai_reply_wait_seconds_remaining, process_pending_customer_reply, process_stalled_conversation_follow_up, should_start_ai_reply
from .webhook_services import resolve_instagram_event, resolve_telegram_update
from .backup_services import send_backup_to_telegram
from .models import AICatalogItem
from .vision_services import ensure_catalog_fingerprint, refresh_stale_fingerprints



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
        # account_id ni ham uzatamiz: mijoz ketma-ket yozganda vazifa qayta
        # rejalashtiriladi va u tushib qolsa javob boshqa akkauntdan ketardi.
        process_delayed_instagram_reply.apply_async(args=[conversation_id, expected_message_id, recipient_id, account_id], countdown=ceil(remaining))
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


def deliver_ai_reply(conversation, reply):
    """AI javobini mijozga yetkazadi — Instagram yoki Telegram.

    Yuborilgan xabarning Instagram id si javobga yozilishi SHART. Yozilmasa
    Instagram qaytargan echo o'zimizning xabarimiz ekani tanilmaydi, u
    "operator javob yozdi" bo'lib tushadi va AI o'zini o'n besh daqiqaga
    to'xtatib qo'yadi — mijozning keyingi xabari javobsiz qoladi.
    """
    from .services import conversation_instagram_account_id, remember_sent_instagram_message

    external_id = conversation.customer.instagram_user_id or ""
    try:
        if external_id.startswith("telegram:"):
            telegram_send(external_id.split(":", 1)[1], reply.text)
            return
        if not external_id:
            return
        response = instagram_send(external_id, reply.text, conversation_instagram_account_id(conversation))
    except Exception as error:
        print(f"LOCATION_REPLY_SEND_FAILED conversation={conversation.id} error={error}", flush=True)
        return
    message_id = (response or {}).get("message_id") or (response or {}).get("mid")
    if message_id:
        remember_sent_instagram_message({"message_id": message_id})
        reply.instagram_message_id = message_id
        reply.save(update_fields=["instagram_message_id", "updated_at"])


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_location_reply(conversation_id, message_id):
    """Manzil kelgach AI o'z navbatida javob beradi.

    Manzil suhbatga mijoz xabari bo'lib yozilgani uchun odatdagi javob yo'li
    ishlaydi — alohida matn yozilmaydi, AI suhbat holatiga qarab o'zi hal qiladi.
    """
    from .models import Conversation
    from .services import create_ai_reply_for_conversation

    if not should_start_ai_reply(conversation_id, message_id):
        return None
    conversation = Conversation.objects.filter(id=conversation_id).first()
    if not conversation:
        return None
    reply = create_ai_reply_for_conversation(conversation)
    if not reply:
        return None
    deliver_ai_reply(conversation, reply)
    return reply.id


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


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def build_ai_catalog_fingerprint(catalog_item_id):
    """Bitta katalog rasmi tahlil qilinib bazaga yoziladi.

    Admin katalog qo'shganda yoki rasmini almashtirganda chaqiriladi. Shu ish bir marta
    qilingani uchun mijoz rasm yuborganda katalogning hamma rasmini qayta tahlil qilish
    kerak bo'lmaydi.
    """
    item = AICatalogItem.objects.filter(id=catalog_item_id).first()
    if not item:
        return {"ok": False, "detail": "not_found"}
    fingerprint = ensure_catalog_fingerprint(item)
    return {"ok": bool(fingerprint), "catalog_id": catalog_item_id}


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def refresh_ai_catalog_fingerprints(limit=50):
    """Fingerprinti yo'q yoki eskirgan katalog mahsulotlarini yangilaydi."""
    updated = refresh_stale_fingerprints(AICatalogItem.objects.filter(is_active=True), limit=limit)
    return {"ok": True, "updated": updated}
