# -*- coding: utf-8 -*-
"""Buyurtma sanasiga eslatma va operator chaqirig'i.

Mijoz "ertaga" yoki aniq sanani aytsa, o'sha kun ertalab soat 9:00 ga eslatma
qo'yiladi va eslatma guruhiga qisqa karta yuboriladi.

Guruh id si oddiy guruhdan superguruhga o'tganda o'zgaradi (-NNN dan -100NNN ga).
Telegram bunda migrate_to_chat_id ni qaytaradi — yangi id o'sha zahoti eslab
qolinadi va xabar qaytadan yuboriladi.
"""
from datetime import datetime, time

from django.conf import settings
from django.utils import timezone

from .models import IntegrationSettings
from .platform_services import telegram_api_with_token

RECALL_HOUR = 9
MIGRATED_KEY = "recall_group_chat_id"


def recall_bot():
    """Eslatma operator boti orqali ketadi — guruhda o'sha bot admin."""
    return settings.AI_OPERATOR_HANDOFF_BOT_TOKEN


def recall_group_id():
    """Eslatma guruhi. Superguruhga o'tgan bo'lsa eslab qolingan id ustun turadi."""
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    remembered = str((integration.extra or {}).get(MIGRATED_KEY) or "").strip()
    return remembered or str(settings.AI_RECALL_GROUP_ID or "").strip()


def remember_group_id(chat_id):
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    extra = dict(integration.extra or {})
    if str(extra.get(MIGRATED_KEY) or "") == str(chat_id):
        return
    extra[MIGRATED_KEY] = str(chat_id)
    integration.extra = extra
    integration.save(update_fields=["extra", "updated_at"])
    print(f"RECALL_GROUP_MIGRATED chat_id={chat_id}", flush=True)


def send_to_group(token, chat_id, payload, method="sendMessage"):
    """Guruhga yuboradi, superguruhga o'tgan bo'lsa yangi id bilan qayta uradi."""
    if not token or not chat_id:
        return {"ok": False, "detail": "recall_group_not_configured"}
    body = dict(payload, chat_id=chat_id)
    try:
        return telegram_api_with_token(token, method, body)
    except Exception as error:
        migrated = migrate_target(error)
        if not migrated:
            print(f"RECALL_SEND_FAILED chat={chat_id} error={error}", flush=True)
            return {"ok": False, "detail": "send_failed"}
    remember_group_id(migrated)
    try:
        return telegram_api_with_token(token, method, dict(payload, chat_id=migrated))
    except Exception as error:
        print(f"RECALL_SEND_FAILED_AFTER_MIGRATION chat={migrated} error={error}", flush=True)
        return {"ok": False, "detail": "send_failed"}


def migrate_target(error):
    """Xatolik javobidan yangi superguruh id sini oladi."""
    response = getattr(error, "response", None)
    if response is None:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    return ((data or {}).get("parameters") or {}).get("migrate_to_chat_id")


def recall_moment(desired_date):
    """So'ralgan sananing ertalab soat 9:00 i, mahalliy vaqtda."""
    if not desired_date:
        return None
    naive = datetime.combine(desired_date, time(hour=RECALL_HOUR))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def schedule_from_desired_date(lead):
    """Mijoz aytgan sanaga eslatma qo'yadi.

    Operator qo'lda boshqa vaqt qo'ygan bo'lsa tegilmaydi. Sana bugundan oldin
    bo'lsa ham eslatma qo'yiladi — o'tib ketgani beat navbatida darhol ketadi.
    """
    if not lead.desired_date or lead.recall_at or lead.recall_sent_at:
        return None
    moment = recall_moment(lead.desired_date)
    if not moment:
        return None
    lead.recall_at = moment
    lead.save(update_fields=["recall_at", "updated_at"])
    return moment


def recall_card(lead):
    """Eslatma guruhiga ketadigan qisqa karta."""
    customer = lead.customer
    conversation = lead.conversation
    lines = [f"⏰ Bugunga buyurtma — Lead #{lead.id}", ""]
    lines.append(f"👤 {customer.name or 'Ism yozilmagan'}")
    lines.append(f"📞 {customer.phone or 'raqam berilmagan'}")
    if customer.instagram_username:
        lines.append(f"📷 @{customer.instagram_username}")
    flower = recall_flower_line(lead)
    if flower:
        lines.append(f"💐 {flower}")
    when = []
    if lead.desired_date:
        when.append(lead.desired_date.strftime("%d.%m.%Y"))
    if lead.desired_time:
        when.append(lead.desired_time)
    if when:
        lines.append(f"📅 Kerak: {' · '.join(when)}")
    if conversation:
        first = conversation.messages.filter(sender="customer").order_by("created_at", "id").first()
        if first:
            lines.append(f"✍️ Yozgan: {timezone.localtime(first.created_at):%d.%m.%Y %H:%M}")
    return "\n".join(lines)


def recall_flower_line(lead):
    """Mijoz so'ragan gul: katalogdan tanlagani yoki o'z so'zi."""
    from .services import lead_catalog_lines

    rows = lead_catalog_lines(lead)
    if rows:
        return " · ".join(row["text"] for row in rows[:3])
    details = lead.details or {}
    wanted = " · ".join(value for value in [details.get("flowers_text"), details.get("size_text")] if value)
    return wanted or (lead.request_uz or "")[:160]


def chat_button(lead):
    from .services import operator_chat_url

    conversation = lead.conversation
    if not conversation:
        return None
    return {"inline_keyboard": [[{"text": "CRM chatni ochish", "url": operator_chat_url(conversation)}]]}


def send_recall_card(lead):
    """Eslatma kartasini eslatma guruhiga yuboradi."""
    payload = {"text": recall_card(lead)}
    keyboard = chat_button(lead)
    if keyboard:
        payload["reply_markup"] = keyboard
    return send_to_group(recall_bot(), recall_group_id(), payload)
