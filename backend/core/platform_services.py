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


def instagram_active_stories():
    _, access_token = instagram_credentials()
    if not access_token:
        return []
    account_id = instagram_user_id(access_token)
    if not account_id:
        return []
    response = requests.get(
        f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account_id}/stories",
        params={"access_token": access_token, "fields": "id,media_type,media_url,permalink,timestamp"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def instagram_recent_media():
    _, access_token = instagram_credentials()
    if not access_token:
        return []
    account_id = instagram_user_id(access_token)
    if not account_id:
        return []
    url = f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account_id}/media"
    params = {"access_token": access_token, "fields": "id,caption,media_type,media_url,permalink,timestamp,thumbnail_url", "limit": 100}
    rows = []
    for _ in range(5):
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = {}
        if not url:
            break
    return rows


def find_active_story_by_permalink(permalink):
    normalized = normalize_instagram_permalink(permalink)
    if not normalized:
        return None
    for story in instagram_active_stories():
        if normalize_instagram_permalink(story.get("permalink")) == normalized:
            return story
    return None


def find_media_by_permalink(permalink):
    normalized = normalize_instagram_permalink(permalink)
    if not normalized:
        return None
    for media in instagram_recent_media():
        if normalize_instagram_permalink(media.get("permalink")) == normalized:
            return media
    return None


def find_media_by_id(media_id):
    if not media_id:
        return None
    for media in instagram_recent_media():
        if str(media.get("id", "")) == str(media_id):
            return media
    return None

def instagram_send(recipient_id, text):
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    access_token = integration.instagram_access_token or settings.INSTAGRAM_ACCESS_TOKEN
    account_id = integration.instagram_account_id or settings.INSTAGRAM_ACCOUNT_ID or integration.instagram_business_id
    if not access_token or not account_id:
        return {"mocked": True}
    url = f"https://graph.instagram.com/{settings.INSTAGRAM_API_VERSION}/{account_id}/messages"
    response = requests.post(url, params={"access_token": access_token}, json={"recipient": {"id": recipient_id}, "message": {"text": text}}, timeout=20)
    response.raise_for_status()
    return response.json()


def instagram_send_image(recipient_id, image_url):
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    access_token = integration.instagram_access_token or settings.INSTAGRAM_ACCESS_TOKEN
    account_id = integration.instagram_account_id or settings.INSTAGRAM_ACCOUNT_ID or integration.instagram_business_id
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


def instagram_sender_action(recipient_id, action):
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    access_token = integration.instagram_access_token or settings.INSTAGRAM_ACCESS_TOKEN
    account_id = integration.instagram_account_id or settings.INSTAGRAM_ACCOUNT_ID or integration.instagram_business_id
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


def telegram_send(chat_id, text):
    return telegram_api("sendMessage", {"chat_id": chat_id, "text": text})


def telegram_send_image(chat_id, image_url):
    return telegram_api("sendPhoto", {"chat_id": chat_id, "photo": image_url})


def telegram_sender_action(chat_id, action="typing"):
    return telegram_api("sendChatAction", {"chat_id": chat_id, "action": action})


def send_lead_recall(lead_id):
    with transaction.atomic():
        lead = Lead.objects.select_for_update().select_related("customer", "branch").filter(id=lead_id).first()
        if not lead or lead.status == "lost" or lead.recall_sent_at or not lead.recall_at or lead.recall_at > timezone.now():
            return None
        title = f"Recall: Lead #{lead.id}"
        body = f"{lead.customer} buyurtmasi 1 soat ichida yuborilishi kerak. Telefon: {lead.customer.phone or lead.customer.masked_phone}. So‘rov: {lead.request_uz or lead.request_ru}"
        notification = Notification.objects.create(
            branch=lead.branch,
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
