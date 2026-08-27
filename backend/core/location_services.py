# -*- coding: utf-8 -*-
"""Yetkazib berish manzilini xaritadan olish.

Mijoz yetkazib berishni tanlasa AI unga havola beradi: front/{lead_id}?t=<kod>.
Mijoz xaritada nuqtani belgilaydi, frontend bizning API ga long/lat yuboradi.
Kod leadning o'zida turadi — kodsiz yoki noto'g'ri kod bilan kelgan so'rov
qabul qilinmaydi, lead topilmasa so'rov shunchaki o'tkazib yuboriladi.

Manzil kelgach ikki ish bo'ladi: operatorlar guruhidagi lead xabariga javob
qilib joylashuv yuboriladi, va suhbatga mijoz xabari sifatida yozilib AI o'z
navbatida javob beradi — shunda "manzilingizni oldik" dan keyin nima so'rash
kerakligini suhbat holatiga qarab o'zi hal qiladi.
"""
import math
import secrets
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from .models import Lead, Message
from .platform_services import telegram_api_with_token

TOKEN_BYTES = 5
# Mijoz belgini bir necha metr surib qayta bossa bu yangi manzil emas. Shu
# masofadan yaqin nuqta guruhga qayta yuborilmaydi.
SAME_POINT_METERS = 30


def location_state(lead):
    return dict((lead.details or {}).get("location") or {})


def save_location_state(lead, **changes):
    details = dict(lead.details or {})
    state = dict(details.get("location") or {})
    state.update(changes)
    details["location"] = state
    lead.details = details
    lead.save(update_fields=["details", "updated_at"])
    return state


def ensure_token(lead):
    """Leadning maxfiy kodi. Bir marta yaratiladi va o'zgarmaydi."""
    state = location_state(lead)
    token = (state.get("token") or "").strip()
    if token:
        return token
    token = secrets.token_hex(TOKEN_BYTES)
    save_location_state(lead, token=token)
    return token


def token_matches(lead, token):
    stored = (location_state(lead).get("token") or "").strip()
    return bool(stored) and secrets.compare_digest(stored, str(token or "").strip())


def location_link(lead):
    """Mijozga beriladigan havola. Sozlanmagan bo'lsa bo'sh qaytadi."""
    template = (settings.DELIVERY_LOCATION_URL or "").strip()
    if not template:
        return ""
    # Kod hozir faqat hex, lekin shakli o'zgarsa "+", "/", "=" havolani buzadi.
    return template.format(lead_id=lead.id, token=quote(ensure_token(lead), safe=""))


def point_moved(state, latitude, longitude):
    """Yangi nuqta avvalgisidan sezilarli uzoqdami.

    Avval nuqta bo'lmagan bo'lsa har qanday nuqta yangi hisoblanadi.
    """
    try:
        old_lat = float(state.get("latitude"))
        old_lon = float(state.get("longitude"))
        new_lat = float(latitude)
        new_lon = float(longitude)
    except (TypeError, ValueError):
        return True
    metres_per_degree = 111320.0
    north = (new_lat - old_lat) * metres_per_degree
    east = (new_lon - old_lon) * metres_per_degree * math.cos(math.radians(old_lat))
    return math.hypot(north, east) > SAME_POINT_METERS


def send_location_to_group(lead, updated=False):
    """Operatorlar guruhidagi lead xabariga javob qilib joylashuv yuboradi."""
    from .payment_services import payment_state

    token = settings.AI_OPERATOR_HANDOFF_BOT_TOKEN
    chat_id = settings.AI_OPERATOR_HANDOFF_GROUP_ID
    state = location_state(lead)
    if not token or not chat_id:
        return {"ok": False, "detail": "operator_group_not_configured"}
    if state.get("latitude") is None or state.get("longitude") is None:
        return {"ok": False, "detail": "no_coordinates"}
    payload = {
        "chat_id": chat_id,
        "latitude": float(state["latitude"]),
        "longitude": float(state["longitude"]),
    }
    reply_to = payment_state(lead).get("operator_message_id")
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    if settings.AI_OPERATOR_HANDOFF_THREAD_ID:
        payload["message_thread_id"] = settings.AI_OPERATOR_HANDOFF_THREAD_ID
    try:
        sent = telegram_api_with_token(token, "sendLocation", payload)
    except Exception as error:
        print(f"LOCATION_GROUP_SEND_FAILED lead={lead.id} error={error}", flush=True)
        return {"ok": False, "detail": "send_failed"}
    caption = location_caption(lead, updated=updated)
    if caption:
        note = {"chat_id": chat_id, "text": caption}
        if reply_to:
            note["reply_to_message_id"] = reply_to
        if settings.AI_OPERATOR_HANDOFF_THREAD_ID:
            note["message_thread_id"] = settings.AI_OPERATOR_HANDOFF_THREAD_ID
        try:
            telegram_api_with_token(token, "sendMessage", note)
        except Exception as error:
            print(f"LOCATION_GROUP_NOTE_FAILED lead={lead.id} error={error}", flush=True)
    return {"ok": bool(sent.get("ok")), "detail": ""}


def location_caption(lead, updated=False):
    state = location_state(lead)
    title = "📍 Manzil yangilandi" if updated else "📍 Yetkazib berish manzili"
    lines = [f"{title} — Lead #{lead.id}"]
    address = (state.get("address") or lead.delivery_address or "").strip()
    if address:
        lines.append(address)
    return "\n".join(lines)


def record_customer_location_message(lead, address=""):
    """Manzilni suhbatga mijoz xabari sifatida yozadi.

    Shunda AI navbati odatdagidek ishlaydi: yangi mijoz xabari bor, javobda
    "manzilingizni oldik" deb yozadi va suhbat holatiga qarab keyingi savolni
    beradi. Alohida qoida yozish kerak emas.
    """
    conversation = lead.conversation
    if not conversation:
        return None
    state = location_state(lead)
    text = "Mijoz yetkazib berish manzilini xaritada belgiladi."
    if address:
        text += f"\nManzil: {address}"
    if state.get("latitude") is not None and state.get("longitude") is not None:
        text += f"\nKoordinata: {state['latitude']}, {state['longitude']}"
    return Message.objects.create(
        conversation=conversation, sender="customer", text=text,
        metadata={"delivery_location": {k: v for k, v in state.items() if k != "token"}},
    )


def accept_location(lead_id, token, latitude, longitude, address=""):
    """Frontenddan kelgan manzil. Lead yo'q bo'lsa o'tkazib yuboriladi."""
    lead = Lead.objects.filter(id=lead_id).select_related("conversation__customer").first()
    if not lead:
        return {"status": "skipped", "detail": "lead_not_found"}
    if not token_matches(lead, token):
        return {"status": "rejected", "detail": "bad_token"}
    # Havola bir necha marta ishlaydi va frontend bloklamaydi. Har bosishda
    # guruhga joylashuv yuborib, AI ga "manzilingizni oldik" deb qayta yozdirsak
    # operator ham mijoz ham takror xabarlarga ko'miladi. Shuning uchun birinchi
    # manzil to'liq oqimni yuritadi, keyingilari faqat nuqta ko'chganda guruhga
    # tuzatish bo'lib boradi.
    previous = location_state(lead)
    had_point = previous.get("latitude") is not None and previous.get("longitude") is not None
    moved = point_moved(previous, latitude, longitude)
    save_location_state(
        lead,
        latitude=str(latitude),
        longitude=str(longitude),
        address=(address or "").strip()[:255],
        received_at=timezone.now().isoformat(),
    )
    text_address = (address or "").strip()
    if text_address and not lead.delivery_address:
        lead.delivery_address = text_address[:255]
        lead.save(update_fields=["delivery_address", "updated_at"])
    if had_point and not moved:
        return {"status": "ok", "lead_id": lead.id, "detail": "same_point"}
    send_location_to_group(lead, updated=had_point)
    if had_point:
        return {"status": "ok", "lead_id": lead.id, "detail": "updated"}
    message = record_customer_location_message(lead, text_address)
    if message:
        from .tasks import process_location_reply

        process_location_reply.delay(lead.conversation_id, message.id)
    return {"status": "ok", "lead_id": lead.id}
