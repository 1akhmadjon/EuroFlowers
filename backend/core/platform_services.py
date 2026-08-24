import requests
from urllib.parse import parse_qs, urlparse
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import IntegrationSettings, Lead, Notification, SocialPost


def normalize_instagram_permalink(value):
    return (value or "").split("?")[0].rstrip("/")


def media_id_from_url(value):
    if not value:
        return ""
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    return (query.get("asset_id") or [""])[0]

def instagram_credentials():
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    return integration, integration.instagram_access_token or settings.INSTAGRAM_ACCESS_TOKEN


def instagram_account_token_pairs():
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    pairs = {}
    primary_account_id = integration.instagram_account_id or settings.INSTAGRAM_ACCOUNT_ID or integration.instagram_business_id
    primary_token = integration.instagram_access_token or settings.INSTAGRAM_ACCESS_TOKEN
    if primary_account_id and primary_token:
        pairs[str(primary_account_id)] = primary_token
    for row in settings.INSTAGRAM_ACCOUNT_ACCESS_TOKENS:
        if ":" not in row:
            continue
        account_id, token = row.split(":", 1)
        account_id = account_id.strip()
        token = token.strip()
        if account_id and token:
            pairs[account_id] = token
    return pairs


def instagram_credentials_for_account(account_id=None):
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    primary_account_id = integration.instagram_account_id or settings.INSTAGRAM_ACCOUNT_ID or integration.instagram_business_id
    if account_id:
        account_id = str(account_id)
        token = instagram_account_token_pairs().get(account_id, "")
        return account_id, token
    return primary_account_id, integration.instagram_access_token or settings.INSTAGRAM_ACCESS_TOKEN


def openai_api_key():
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    return (integration.extra or {}).get("openai_api_key") or settings.OPENAI_API_KEY


def instagram_user_id(access_token):
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    if integration.instagram_account_id:
        return integration.instagram_account_id
    response = requests.get(f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/me", params={"access_token": access_token, "fields": "id,username"}, timeout=20)
    response.raise_for_status()
    data = response.json()
    account_id = data.get("id", "")
    if account_id:
        integration.instagram_account_id = account_id
        integration.save(update_fields=["instagram_account_id", "updated_at"])
    return account_id


def instagram_lookup_accounts(account_id=None):
    """Story va postni qaysi akkauntlardan qidirish kerak.

    Tizimga bitta emas, bir nechta Instagram akkaunt ulanadi. Mijozning xabari
    qaysi akkauntga kelganini bilsak faqat o'shanikini so'raymiz; bilmasak
    (masalan operator postga link qo'yayotganda) hammasidan qidiramiz. Faqat
    asosiy akkauntdan qidirish boshqa akkauntning storysini "yo'q" qilib
    ko'rsatadi va o'sha story hech qachon postga bog'lanmay qoladi.
    """
    pairs = instagram_account_token_pairs()
    if account_id and str(account_id) in pairs:
        return [(str(account_id), pairs[str(account_id)])]
    if pairs:
        return list(pairs.items())
    _, access_token = instagram_credentials()
    if not access_token:
        return []
    resolved = instagram_user_id(access_token)
    return [(resolved, access_token)] if resolved else []


def instagram_active_stories(account_id=None):
    rows = []
    for account, access_token in instagram_lookup_accounts(account_id):
        try:
            response = requests.get(
                f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account}/stories",
                params={"access_token": access_token, "fields": "id,media_type,media_url,permalink,timestamp"},
                timeout=20,
            )
            response.raise_for_status()
            rows.extend(response.json().get("data", []))
        except Exception as error:
            # Bitta akkauntning tokeni eskirgani qolganlarini qidirishga to'sqinlik qilmaydi.
            print(f"INSTAGRAM_ACTIVE_STORIES_FAILED account={account} error={error}", flush=True)
    return rows


def instagram_recent_media(account_id=None):
    rows = []
    for account, access_token in instagram_lookup_accounts(account_id):
        url = f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account}/media"
        params = {"access_token": access_token, "fields": "id,caption,media_type,media_url,permalink,timestamp,thumbnail_url", "limit": 100}
        try:
            for _ in range(5):
                response = requests.get(url, params=params, timeout=20)
                response.raise_for_status()
                data = response.json()
                rows.extend(data.get("data", []))
                url = data.get("paging", {}).get("next")
                params = {}
                if not url:
                    break
        except Exception as error:
            print(f"INSTAGRAM_RECENT_MEDIA_FAILED account={account} error={error}", flush=True)
    return rows


def find_active_story_by_permalink(permalink, account_id=None):
    normalized = normalize_instagram_permalink(permalink)
    if not normalized:
        return None
    for story in instagram_active_stories(account_id):
        if normalize_instagram_permalink(story.get("permalink")) == normalized:
            return story
    return None


def find_active_story_by_media_url(media_url, account_id=None):
    normalized = normalize_instagram_permalink(media_url)
    asset_id = media_id_from_url(media_url)
    if not normalized and not asset_id:
        return None
    for story in instagram_active_stories(account_id):
        story_media_url = story.get("media_url", "")
        if asset_id and str(story.get("id", "")) == asset_id:
            return story
        if asset_id and media_id_from_url(story_media_url) == asset_id:
            return story
        if normalized and normalize_instagram_permalink(story_media_url) == normalized:
            return story
    return None


def find_media_by_permalink(permalink, account_id=None):
    normalized = normalize_instagram_permalink(permalink)
    if not normalized:
        return None
    for media in instagram_recent_media(account_id):
        if normalize_instagram_permalink(media.get("permalink")) == normalized:
            return media
    return None


def find_media_by_id(media_id, account_id=None):
    if not media_id:
        return None
    for media in instagram_recent_media(account_id):
        if str(media.get("id", "")) == str(media_id):
            return media
    return None

def instagram_send(recipient_id, text, account_id=None):
    account_id, access_token = instagram_credentials_for_account(account_id)
    if not access_token or not account_id:
        return {"mocked": True}
    url = f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account_id}/messages"
    response = requests.post(url, params={"access_token": access_token}, json={"recipient": {"id": recipient_id}, "message": {"text": text}}, timeout=20)
    response.raise_for_status()
    return response.json()


def instagram_send_image(recipient_id, image_url, account_id=None):
    account_id, access_token = instagram_credentials_for_account(account_id)
    if not access_token or not account_id:
        return {"mocked": True}
    url = f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account_id}/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": image_url, "is_reusable": True},
            }
        },
    }
    response = requests.post(url, params={"access_token": access_token}, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def instagram_send_carousel(recipient_id, elements, account_id=None):
    """Bir nechta rasmni bitta xabarda karusel qilib yuboradi.

    elements: [{"title": ..., "subtitle": ..., "image_url": ...}]
    Instagram generic template bitta xabarda ko'pi bilan 10 ta element ko'taradi.
    """
    account_id, access_token = instagram_credentials_for_account(account_id)
    if not access_token or not account_id:
        return {"mocked": True}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "generic",
                    "elements": [
                        {
                            "title": (row.get("title") or "")[:80],
                            "subtitle": (row.get("subtitle") or "")[:80],
                            "image_url": row.get("image_url") or "",
                        }
                        for row in elements
                    ],
                },
            }
        },
    }
    url = f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account_id}/messages"
    response = requests.post(url, params={"access_token": access_token}, json=payload, timeout=40)
    if response.status_code >= 400:
        # Instagram sababni faqat javob tanasida yozadi, status kodda emas.
        print(f"INSTAGRAM_CAROUSEL_REJECTED account={account_id} status={response.status_code} body={response.text[:600]}", flush=True)
    response.raise_for_status()
    return response.json()


def instagram_sender_action(recipient_id, action, account_id=None):
    account_id, access_token = instagram_credentials_for_account(account_id)
    if not access_token or not account_id:
        return {"mocked": True}
    url = f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account_id}/messages"
    response = requests.post(url, params={"access_token": access_token}, json={"recipient": {"id": recipient_id}, "sender_action": action}, timeout=20)
    response.raise_for_status()
    return response.json()


def telegram_bot_token():
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    return integration.telegram_bot_token


def telegram_file_url(file_id):
    token = telegram_bot_token()
    if not token or not file_id:
        return ""
    response = requests.post(f"https://api.telegram.org/bot{token}/getFile", json={"file_id": file_id}, timeout=20)
    response.raise_for_status()
    file_path = response.json().get("result", {}).get("file_path", "")
    if not file_path:
        return ""
    return f"https://api.telegram.org/file/bot{token}/{file_path}"


def telegram_api(method, payload):
    token = telegram_bot_token()
    if not token:
        return {"mocked": True}
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


def telegram_api_with_token(token, method, payload):
    if not token:
        return {"skipped": True, "reason": "token yo‘q"}
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def telegram_send(chat_id, text):
    return telegram_api("sendMessage", {"chat_id": chat_id, "text": text})


def telegram_send_with(token, chat_id, text, reply_markup=None, message_thread_id=""):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    return telegram_api_with_token(token, "sendMessage", payload)


def telegram_send_image(chat_id, image_url, caption=""):
    payload = {"chat_id": chat_id, "photo": image_url}
    if caption:
        payload["caption"] = caption[:1024]
    return telegram_api("sendPhoto", payload)


def telegram_send_media_group(chat_id, media):
    """Bir nechta rasmni bitta xabarda albom qilib yuboradi.

    media: [{"image_url": ..., "caption": ...}]
    Telegram media group bitta xabarda ko'pi bilan 10 ta rasm ko'taradi.
    """
    payload = {
        "chat_id": chat_id,
        "media": [
            {"type": "photo", "media": row.get("image_url") or "", "caption": (row.get("caption") or "")[:1024]}
            for row in media
        ],
    }
    return telegram_api("sendMediaGroup", payload)


def telegram_send_media_group_with(token, chat_id, media, message_thread_id=""):
    payload = {
        "chat_id": chat_id,
        "media": [
            {"type": row.get("type") or "photo", "media": row.get("url") or "", "caption": (row.get("caption") or "")[:1024]}
            for row in media
        ],
    }
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    return telegram_api_with_token(token, "sendMediaGroup", payload)


def telegram_send_rich_message_with(token, chat_id, rich_message, reply_markup=None, message_thread_id=""):
    payload = {"chat_id": chat_id, "rich_message": rich_message}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    return telegram_api_with_token(token, "sendRichMessage", payload)


def telegram_send_photo_with(token, chat_id, photo, caption="", parse_mode="Markdown"):
    """Alohida bot orqali rasm yuboradi.

    Sotuv xabari uchun boshqa bot va guruh ishlatiladi, shuning uchun token
    tashqaridan beriladi. Rasm URL bo'lsa link yuboriladi, bayt bo'lsa fayl.
    """
    if not token or not chat_id:
        return {"skipped": True, "reason": "token yoki chat_id yo‘q"}
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    data = {"chat_id": str(chat_id), "caption": caption[:1024], "parse_mode": parse_mode}
    if isinstance(photo, (bytes, bytearray)):
        response = requests.post(url, data=data, files={"photo": ("sale.jpg", photo)}, timeout=30)
    else:
        data["photo"] = photo
        response = requests.post(url, data=data, timeout=30)
    response.raise_for_status()
    return response.json()


def telegram_sender_action(chat_id, action="typing"):
    return telegram_api("sendChatAction", {"chat_id": chat_id, "action": action})


def send_lead_recall(lead_id):
    with transaction.atomic():
        lead = Lead.objects.select_for_update().select_related("customer").filter(id=lead_id).first()
        if not lead or lead.status == "lost" or lead.recall_sent_at or not lead.recall_at or lead.recall_at > timezone.now():
            return None
        title = f"Recall: Lead #{lead.id}"
        body = f"{lead.customer} buyurtmasi 1 soat ichida yuborilishi kerak. Telefon: {lead.customer.phone or lead.customer.masked_phone}. So‘rov: {lead.request_uz or lead.request_ru}"
        notification = Notification.objects.create(
            notification_type="lead",
            title_uz=title,
            title_ru=title,
            body_uz=body,
            body_ru=body,
            reference_type="lead",
            reference_id=lead.id,
        )
        lead.recall_sent_at = timezone.now()
        lead.save(update_fields=["recall_sent_at", "updated_at"])
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    group_chat_id = integration.telegram_group_chat_id or settings.TELEGRAM_GROUP_CHAT_ID
    if group_chat_id:
        try:
            telegram_send(group_chat_id, f"{title}\n{body}")
        except Exception as exc:
            print(f"LEAD_RECALL_TELEGRAM_FAILED lead={lead_id} error={exc}", flush=True)
    return notification


def send_due_lead_recalls():
    due_ids = list(Lead.objects.filter(recall_at__lte=timezone.now(), recall_sent_at__isnull=True).exclude(status="lost").values_list("id", flat=True)[:100])
    sent = 0
    for lead_id in due_ids:
        if send_lead_recall(lead_id):
            sent += 1
    return sent
