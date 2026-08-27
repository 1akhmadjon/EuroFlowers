# -*- coding: utf-8 -*-
"""Mijozning to'lov turi, chek rasmi va operatorning tasdig'i.

Oqim: AI mijozdan to'lov turini so'raydi → karta bo'lsa rekvizit beradi va chek
so'raydi → chek kelganda operatorlar guruhidagi lead xabari yangilanadi va unga
«tasdiqlash / rad etish» tugmalari qo'shiladi → operator bosgan tugma mijozga
javob bo'lib qaytadi.

Guruhdagi xabar tahrirlanadi, o'chirilmaydi. Tahrir o'tmasa xabarga javob
qilib yuboriladi — ma'lumot yo'qolmasligi tahrirdan muhimroq.
"""
from django.conf import settings
from django.utils import timezone

from .models import BusinessSettings, Lead, Message
from .platform_services import telegram_api_with_token

PAYMENT_LABELS = {"cash": "💵 Naqd", "card": "💳 Karta"}
RECEIPT_WAITING = "waiting"
RECEIPT_CONFIRMED = "confirmed"
RECEIPT_REJECTED = "rejected"

CUSTOMER_CONFIRMED_UZ = (
    "To'lovingiz tasdiqlandi. Operatorlarimiz siz bilan tez orada bog'lanadi."
)
CUSTOMER_REJECTED_UZ = (
    "Hurmatli mijoz, iltimos qaytadan haqiqiy to'lov chekini yuboring. "
    "To'lovingiz tasdiqlanmadi."
)


def operator_bot():
    return settings.AI_OPERATOR_HANDOFF_BOT_TOKEN, settings.AI_OPERATOR_HANDOFF_GROUP_ID


def payment_card(business=None):
    """Karta rekvizitlari. To'ldirilmagan bo'lsa bo'sh qaytadi."""
    business = business or BusinessSettings.objects.get_or_create(pk=1)[0]
    number = (business.payment_card_number or "").strip()
    holder = (business.payment_card_holder or "").strip()
    return {"number": number, "holder": holder} if number else {}


def payment_state(lead):
    return dict((lead.details or {}).get("payment") or {})


def save_payment_state(lead, **changes):
    details = dict(lead.details or {})
    state = dict(details.get("payment") or {})
    state.update(changes)
    details["payment"] = state
    lead.details = details
    lead.save(update_fields=["details", "updated_at"])
    return state


def remember_operator_message(lead, telegram_result, body="", keyboard=None):
    """Guruhda yuborilgan lead xabarining id sini leadga yozib qo'yadi.

    Keyin shu id bo'yicha xabar tahrirlanadi. Id topilmasa hech narsa yozilmaydi
    va tahrir o'rniga oddiy yangi xabar yuboriladi.

    Xabarning asl matni va tugmasi ham saqlanadi: to'lov holati qo'shilganda
    lead ma'lumotlari o'rniga faqat to'lov qatorlari yozilib qolmasin.
    """
    result = (telegram_result or {}).get("result")
    message_id = None
    if isinstance(result, dict):
        message_id = result.get("message_id")
    elif isinstance(result, list):
        ids = [row.get("message_id") for row in result if isinstance(row, dict) and row.get("message_id")]
        message_id = ids[-1] if ids else None
    if not message_id:
        return None
    changes = {"operator_message_id": message_id}
    if body:
        changes["operator_body"] = body[:3000]
    if keyboard:
        changes["operator_keyboard"] = keyboard
    save_payment_state(lead, **changes)
    return message_id


def operator_keyboard(lead):
    return {"inline_keyboard": [[
        {"text": "✅ To'lovni tasdiqlash", "callback_data": f"pay:ok:{lead.id}"},
        {"text": "❌ To'lovni rad etish", "callback_data": f"pay:no:{lead.id}"},
    ]]}


def payment_summary_lines(lead):
    """Lead xabariga qo'shiladigan to'lov qatorlari."""
    state = payment_state(lead)
    lines = []
    label = PAYMENT_LABELS.get(state.get("type"))
    if label:
        lines.append(f"{label} to'lov")
    status = state.get("receipt_status")
    if status == RECEIPT_WAITING:
        lines.append("To'landi ✅ chekni tekshirish kerak")
    elif status == RECEIPT_CONFIRMED:
        lines.append("To'lov tasdiqlandi ✅")
    elif status == RECEIPT_REJECTED:
        lines.append("To'lov rad etildi ❌ mijozdan yangi chek so'raldi")
    return lines


def operator_message_body(lead):
    """Lead xabarining asl matni, ostiga to'lov qatorlari qo'shilgan holda."""
    state = payment_state(lead)
    lines = payment_summary_lines(lead)
    base = (state.get("operator_body") or "").strip()
    if not base:
        return f"🌸 Lead #{lead.id} — to'lov\n" + "\n".join(lines)
    return base + "\n\n" + "\n".join(lines)


def update_operator_message(lead):
    """Guruhdagi lead xabarining matniga to'lov holatini qo'shadi.

    Xabar ALMASHTIRILMAYDI: asl lead matni joyida qoladi, ostiga to'lov
    qatorlari qo'shiladi. Avval faqat to'lov qatorlari yozilardi va operator
    ism, raqam, mahsulot va manzilni yo'qotardi.

    Tugma ham o'zgarmaydi — "CRM chatni ochish" joyida qoladi. To'lovni
    tasdiqlash tugmalari bu xabarga QO'YILMAYDI, ular chek xabarida turadi.
    """
    token, chat_id = operator_bot()
    if not token or not chat_id:
        return {"ok": False, "detail": "operator_group_not_configured"}
    lines = payment_summary_lines(lead)
    if not lines:
        return {"ok": False, "detail": "nothing_to_add"}
    state = payment_state(lead)
    message_id = state.get("operator_message_id")
    keyboard = state.get("operator_keyboard")
    body = operator_message_body(lead)
    if message_id:
        for method, field in (("editMessageCaption", "caption"), ("editMessageText", "text")):
            payload = {"chat_id": chat_id, "message_id": message_id, field: body}
            if keyboard:
                payload["reply_markup"] = keyboard
            try:
                return telegram_api_with_token(token, method, payload)
            except Exception as error:
                print(f"PAYMENT_EDIT_FAILED lead={lead.id} method={method} error={error}", flush=True)
    payload = {"chat_id": chat_id, "text": body}
    if message_id:
        payload["reply_to_message_id"] = message_id
    if keyboard:
        payload["reply_markup"] = keyboard
    try:
        return telegram_api_with_token(token, "sendMessage", payload)
    except Exception as error:
        print(f"PAYMENT_NOTE_FAILED lead={lead.id} error={error}", flush=True)
        return {"ok": False, "detail": "send_failed"}


def send_receipt_to_operators(lead, receipt_url, repeated=False):
    """Chek rasmini guruhga, lead xabariga javob qilib yuboradi."""
    token, chat_id = operator_bot()
    if not token or not chat_id:
        return {"ok": False, "detail": "operator_group_not_configured"}
    message_id = payment_state(lead).get("operator_message_id")
    title = "♻️ Chek qayta yuborildi" if repeated else "🧾 To'lov cheki keldi"
    caption = f"{title}\nLead #{lead.id}\nTo'landi ✅ chekni tekshirish kerak"
    payload = {"chat_id": chat_id, "photo": receipt_url, "caption": caption,
               "reply_markup": operator_keyboard(lead)}
    if message_id:
        payload["reply_to_message_id"] = message_id
    try:
        return telegram_api_with_token(token, "sendPhoto", payload)
    except Exception as error:
        print(f"PAYMENT_RECEIPT_SEND_FAILED lead={lead.id} error={error}", flush=True)
        return {"ok": False, "detail": "send_failed"}


def set_payment_type(lead, payment_type):
    """Mijoz to'lov turini aytdi."""
    if payment_type not in PAYMENT_LABELS:
        return {"ok": False, "detail": "unknown_payment_type"}
    save_payment_state(lead, type=payment_type, type_set_at=timezone.now().isoformat())
    update_operator_message(lead)
    result = {"ok": True, "payment_type": payment_type}
    if payment_type == "card":
        card = payment_card()
        result["card"] = card
        result["instruction_uz"] = (
            "Karta raqamini va egasining ismini mijozga ayt, keyin to'lov chekining rasmini "
            "yuborishini so'ra. Bitta javobda ikkalasi ham bo'lsin."
            if card else
            "Karta rekvizitlari tizimda yo'q. Raqamni o'zingdan yozma — mijozni "
            "business.operator_telegram dagi Telegram akkauntga yo'naltir."
        )
    else:
        result["instruction_uz"] = (
            "Naqd to'lov tanlandi. Chek so'rama, qisqa tasdiqla va operatorlar "
            "bog'lanishini ayt."
        )
    return result


def register_receipt(lead, receipt_url):
    """Mijoz chek rasmini yubordi."""
    state = payment_state(lead)
    repeated = state.get("receipt_status") == RECEIPT_REJECTED
    receipts = list(state.get("receipts") or [])
    if receipt_url and receipt_url not in receipts:
        receipts.append(receipt_url)
    save_payment_state(
        lead,
        type=state.get("type") or "card",
        receipts=receipts,
        receipt_status=RECEIPT_WAITING,
        receipt_at=timezone.now().isoformat(),
    )
    sent = send_receipt_to_operators(lead, receipt_url, repeated=repeated)
    # Lead xabari faqat matn bilan yangilanadi. Tugmalar yuqoridagi chek
    # xabarida — operator chekni ko'rib turib bosadi.
    update_operator_message(lead)
    return {
        "ok": bool(sent.get("ok")),
        "repeated": repeated,
        "instruction_uz": (
            "Chek qabul qilindi. Mijozga qisqa ayt: chekni oldik, tekshirib "
            "tasdiqlaymiz. Boshqa hech narsa so'rama."
        ),
    }


def decide_payment(lead_id, approved):
    """Operator tugmani bosdi. Guruh xabari yangilanadi, mijozga javob ketadi."""
    lead = Lead.objects.filter(id=lead_id).select_related("conversation__customer").first()
    if not lead:
        return {"ok": False, "detail": "lead_not_found"}
    save_payment_state(
        lead,
        receipt_status=RECEIPT_CONFIRMED if approved else RECEIPT_REJECTED,
        decided_at=timezone.now().isoformat(),
    )
    update_operator_message(lead)
    text = CUSTOMER_CONFIRMED_UZ if approved else CUSTOMER_REJECTED_UZ
    delivered = notify_customer(lead, text)
    return {"ok": True, "approved": approved, "customer_notified": delivered}


def notify_customer(lead, text):
    """Mijozga to'lov qarori haqida xabar yuboradi va suhbatga yozib qo'yadi.

    Yuborilgan xabarning Instagram id si saqlanadi: aks holda echo qaytganda
    o'zimizning xabarimiz "operator javob yozdi" bo'lib tushadi va suhbat
    operatorga o'tib ketadi.
    """
    from .services import conversation_instagram_account_id, latin_to_cyrillic, remember_sent_instagram_message
    from .platform_services import instagram_send, telegram_send

    conversation = lead.conversation
    if not conversation:
        return False
    customer = conversation.customer
    body = text
    if (customer.language or "") == "uz_cyril":
        body = latin_to_cyrillic(text)
    response = None
    try:
        if customer.instagram_user_id.startswith("telegram:"):
            telegram_send(customer.instagram_user_id.split(":", 1)[1], body)
        elif customer.instagram_user_id:
            response = instagram_send(customer.instagram_user_id, body, conversation_instagram_account_id(conversation))
        else:
            return False
    except Exception as error:
        print(f"PAYMENT_CUSTOMER_NOTIFY_FAILED lead={lead.id} error={error}", flush=True)
        return False
    message_id = (response or {}).get("message_id") or (response or {}).get("mid") or ""
    if message_id:
        remember_sent_instagram_message({"message_id": message_id})
    Message.objects.create(conversation=conversation, sender="ai", text=body,
                           instagram_message_id=message_id,
                           metadata={"payment_decision": payment_state(lead)})
    return True


def handle_callback(update):
    """Operator bosgan inline tugmani qayta ishlaydi."""
    query = (update or {}).get("callback_query") or {}
    data = (query.get("data") or "").strip()
    token, _ = operator_bot()
    if not data.startswith("pay:"):
        return {"ok": False, "detail": "not_a_payment_callback"}
    parts = data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return {"ok": False, "detail": "bad_callback_data"}
    approved = parts[1] == "ok"
    result = decide_payment(int(parts[2]), approved)
    if query.get("id") and token:
        note = "Tasdiqlandi ✅" if approved else "Rad etildi ❌"
        try:
            telegram_api_with_token(token, "answerCallbackQuery",
                                    {"callback_query_id": query["id"], "text": note})
        except Exception as error:
            print(f"PAYMENT_CALLBACK_ANSWER_FAILED error={error}", flush=True)
    message = query.get("message") or {}
    if message.get("message_id") and token:
        # Tugmalar olib tashlanadi, xabarning o'zi joyida qoladi.
        try:
            telegram_api_with_token(token, "editMessageReplyMarkup", {
                "chat_id": (message.get("chat") or {}).get("id"),
                "message_id": message["message_id"],
                "reply_markup": {"inline_keyboard": []},
            })
        except Exception as error:
            print(f"PAYMENT_CALLBACK_MARKUP_FAILED error={error}", flush=True)
    return result
