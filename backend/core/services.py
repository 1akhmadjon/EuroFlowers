import json
import re
import requests
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from openai import OpenAI
from .models import AISettings, AuditLog, BusinessSettings, CatalogItem, Conversation, Customer, InstagramWebhookEvent, IntegrationSettings, Lead, Message, Notification, Packaging, SocialPost, StockBatch, StockMovement


def normalize_instagram_permalink(value):
    return (value or "").split("?")[0].rstrip("/")


def media_id_from_url(value):
    if not value:
        return ""
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    return (query.get("asset_id") or [""])[0]


def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 9:
        digits = "998" + digits
    return "+" + digits if digits else ""


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


def mark_catalog_sold(item, user):
    with transaction.atomic():
        item.status = "sold"
        item.sold_at = timezone.now()
        item.save(update_fields=["status", "sold_at", "updated_at"])
        notification = Notification.objects.create(
            branch=item.branch,
            notification_type="stock_pending",
            title_uz=f"{item.name_uz}: sklad chiqimi kutilmoqda",
            title_ru=f"{item.name_ru}: ожидается списание со склада",
            body_uz="Kompozitsiya sotildi. Tarkibdagi gullarni sklad hisobidan chiqaring.",
            body_ru="Композиция продана. Спишите цветы из состава со склада.",
            reference_type="catalog_item",
            reference_id=item.id,
        )
        AuditLog.objects.create(user=user, action="catalog_sold", entity_type="CatalogItem", entity_id=str(item.id), after={"status": "sold", "notification": notification.id})
    return item


def deduct_catalog_stock(item, user):
    with transaction.atomic():
        if item.stock_deducted_at:
            raise ValueError("Sklad chiqimi avval bajarilgan")
        rows = list(item.composition.select_related("stock_batch").select_for_update())
        shortages = [row for row in rows if row.stock_batch.remaining_stems < row.quantity_stems]
        if shortages:
            names = ", ".join(row.stock_batch.batch_number for row in shortages)
            raise ValueError(f"Yetarli qoldiq yo‘q: {names}")
        for row in rows:
            batch = row.stock_batch
            batch.remaining_stems -= row.quantity_stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="out",
                quantity_stems=-row.quantity_stems,
                quantity_bunches=-row.quantity_bunches,
                reference_type="catalog_item",
                reference_id=item.id,
                reason=f"{item.name_uz} sotildi",
                performed_by=user,
            )
        item.stock_deducted_at = timezone.now()
        item.save(update_fields=["stock_deducted_at", "updated_at"])
        Notification.objects.filter(reference_type="catalog_item", reference_id=item.id, notification_type="stock_pending").update(is_read=True)
        AuditLog.objects.create(user=user, action="catalog_stock_deducted", entity_type="CatalogItem", entity_id=str(item.id), after={"rows": len(rows)})
    return item


def apply_stock_movement(batch, movement_type, quantity_stems, reason, user):
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
        delta = abs(quantity_stems) if movement_type in ["in", "transfer_in"] else -abs(quantity_stems)
        if batch.remaining_stems + delta < 0:
            raise ValueError("Skladda yetarli gul yo‘q")
        before = batch.remaining_stems
        batch.remaining_stems += delta
        batch.save(update_fields=["remaining_stems", "updated_at"])
        movement = StockMovement.objects.create(
            batch=batch,
            movement_type=movement_type,
            quantity_stems=delta,
            quantity_bunches=Decimal(delta) / Decimal(batch.stems_per_bunch),
            reason=reason,
            performed_by=user,
        )
        AuditLog.objects.create(user=user, action="stock_movement", entity_type="StockBatch", entity_id=str(batch.id), before={"remaining_stems": before}, after={"remaining_stems": batch.remaining_stems, "movement": movement.id})
        if batch.remaining_stems <= batch.minimum_sale_stems:
            Notification.objects.create(
                branch=batch.branch,
                notification_type="low_stock",
                title_uz=f"{batch.variant.flower.name_uz} qoldig‘i kamaydi",
                title_ru=f"Остаток {batch.variant.flower.name_ru} заканчивается",
                body_uz=f"{batch.batch_number} partiyada {batch.remaining_stems} dona qoldi.",
                body_ru=f"В партии {batch.batch_number} осталось {batch.remaining_stems} шт.",
                reference_type="stock_batch",
                reference_id=batch.id,
            )
    return movement


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


def ai_reply(conversation):
    customer = conversation.customer
    branch = conversation.branch
    stock = StockBatch.objects.filter(branch=branch, is_active=True, remaining_stems__gt=0).select_related("variant__flower")
    catalog = CatalogItem.objects.filter(branch=branch, status="available")[:30]
    baskets = Packaging.objects.filter(branch=branch, packaging_type="basket", is_active=True, quantity__gt=0)
    history = [{"role": "user" if m.sender == "customer" else "assistant", "content": m.text} for m in conversation.messages.exclude(sender="system").order_by("created_at")[:60]]
    business_settings, _ = BusinessSettings.objects.get_or_create(pk=1)
    ai_settings, _ = AISettings.objects.get_or_create(pk=1)
    context = {
        "customer": {"name": customer.name, "phone": customer.masked_phone, "has_phone": bool(customer.phone), "language": customer.language},
        "stock": [{
            "batch_id": row.id,
            "flower_uz": row.variant.flower.name_uz,
            "flower_ru": row.variant.flower.name_ru,
            "variant_uz": row.variant.name_uz,
            "variant_ru": row.variant.name_ru,
            "color_uz": row.variant.color_uz,
            "color_ru": row.variant.color_ru,
            "height_cm": row.height_cm,
            "remaining_stems": row.remaining_stems,
            "stems_per_bunch": row.stems_per_bunch,
            "minimum_sale_stems": row.minimum_sale_stems,
            "price_per_stem": str(row.sale_price_per_stem),
            "price_per_bunch": str(row.sale_price_per_bunch),
        } for row in stock],
        "catalog": [{"id": row.id, "name_uz": row.name_uz, "name_ru": row.name_ru, "type": row.arrangement_type, "price": str(row.price)} for row in catalog],
        "baskets": [{"id": row.id, "name_uz": row.name_uz, "name_ru": row.name_ru, "min": row.capacity_min_stems, "max": row.capacity_max_stems, "price": str(row.sale_price)} for row in baskets],
        "post": None,
        "rules": {
            "florist_fee": str(business_settings.default_florist_fee),
            "price_is_estimate": True,
            "min_sale_reminder_uz": business_settings.min_sale_reminder_uz,
            "min_sale_reminder_ru": business_settings.min_sale_reminder_ru,
            "approximate_price_wording_uz": business_settings.approximate_price_wording_uz,
            "approximate_price_wording_ru": business_settings.approximate_price_wording_ru,
            "handoff_rules_uz": business_settings.handoff_rules_uz,
            "handoff_rules_ru": business_settings.handoff_rules_ru,
            "working_hours": business_settings.working_hours,
        },
    }
    if conversation.social_post:
        post = conversation.social_post
        context["post"] = {"title_uz": post.title_uz, "title_ru": post.title_ru, "description_uz": post.description_uz, "description_ru": post.description_ru, "price": str(post.price or ""), "flower_count": post.flower_count}
    instructions = ai_settings.system_prompt + " Javobni JSON qaytaring: reply matni, detected_language uz yoki ru, customer_name, phone, lead_ready boolean, lead_request, arrangement_type bouquet/basket/stems/catalog yoki bo‘sh, estimated_price raqam yoki null, handoff boolean."
    api_key = openai_api_key()
    if not api_key:
        return {"reply": "Hozir operatorimiz sizga yordam beradi. Ismingiz va telefon raqamingizni qoldiring, iltimos.", "detected_language": customer.language, "lead_ready": False, "handoff": True}
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=ai_settings.openai_model or settings.OPENAI_MODEL,
        instructions=instructions + "\nKONTEKST:\n" + json.dumps(context, ensure_ascii=False),
        input=history,
        text={"format": {"type": "json_schema", "name": "sales_reply", "strict": True, "schema": {
            "type": "object",
            "properties": {
                "reply": {"type": "string"},
                "detected_language": {"type": "string", "enum": ["uz", "ru"]},
                "customer_name": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
                "lead_ready": {"type": "boolean"},
                "lead_request": {"type": ["string", "null"]},
                "arrangement_type": {"type": ["string", "null"], "enum": ["bouquet", "basket", "stems", "catalog", None]},
                "estimated_price": {"type": ["number", "null"]},
                "handoff": {"type": "boolean"}
            },
            "required": ["reply", "detected_language", "customer_name", "phone", "lead_ready", "lead_request", "arrangement_type", "estimated_price", "handoff"],
            "additionalProperties": False
        }}},
    )
    return json.loads(response.output_text)


def ingest_customer_message(conversation, message_text, instagram_message_id=""):
    if instagram_message_id and Message.objects.filter(instagram_message_id=instagram_message_id, conversation=conversation).exists():
        return None
    message = Message.objects.create(conversation=conversation, sender="customer", text=message_text, instagram_message_id=instagram_message_id)
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=["last_message_at", "updated_at"])
    return message


def create_ai_reply_for_conversation(conversation):
    if conversation.status == "closed":
        return None
    if conversation.ai_paused_until and conversation.ai_paused_until > timezone.now():
        return None
    if conversation.ai_paused_until:
        conversation.ai_paused_until = None
        conversation.ai_pause_reason = ""
        conversation.save(update_fields=["ai_paused_until", "ai_pause_reason", "updated_at"])
    if conversation.status != "ai":
        conversation.status = "ai"
        conversation.save(update_fields=["status", "updated_at"])
    result = ai_reply(conversation)
    customer = conversation.customer
    changed = []
    if result.get("customer_name") and not customer.name:
        customer.name = result["customer_name"][:160]
        changed.append("name")
    phone = normalize_phone(result.get("phone"))
    if phone:
        customer.phone = phone
        changed.append("phone")
    if result.get("detected_language") in ["uz", "ru"]:
        customer.language = result["detected_language"]
        changed.append("language")
    if changed:
        customer.save(update_fields=list(set(changed)) + ["updated_at"])
    reply = Message.objects.create(conversation=conversation, sender="ai", text=result["reply"], metadata=result)
    if result.get("lead_ready") and result.get("lead_request"):
        request_text = result["lead_request"]
        request_language = result.get("detected_language", "uz")
        lead = Lead.objects.create(
            customer=customer,
            branch=conversation.branch,
            conversation=conversation,
            social_post=conversation.social_post,
            request_uz=request_text if request_language == "uz" else "",
            request_ru=request_text if request_language == "ru" else "",
            arrangement_type=result.get("arrangement_type") or "",
            estimated_price=result.get("estimated_price"),
        )
        Notification.objects.create(branch=conversation.branch, notification_type="lead", title_uz=f"Yangi lead: {customer}", title_ru=f"Новый лид: {customer}", body_uz=request_text, body_ru=request_text, reference_type="lead", reference_id=lead.id)
    if result.get("handoff"):
        Notification.objects.create(branch=conversation.branch, notification_type="handoff", title_uz=f"Operator aloqasi kerak: {customer}", title_ru=f"Нужна связь оператора: {customer}", body_uz=result.get("lead_request") or result.get("reply", ""), body_ru=result.get("lead_request") or result.get("reply", ""), reference_type="conversation", reference_id=conversation.id)
    return reply


def process_customer_message(conversation, message_text, instagram_message_id=""):
    message = ingest_customer_message(conversation, message_text, instagram_message_id)
    if not message:
        return None
    return create_ai_reply_for_conversation(conversation)


def process_pending_customer_reply(conversation_id, expected_message_id):
    conversation = Conversation.objects.select_related("customer", "branch", "social_post").filter(id=conversation_id).first()
    if not conversation:
        return None
    latest = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    if not latest or latest.id != expected_message_id:
        return None
    return create_ai_reply_for_conversation(conversation)


def flatten_interesting_payload(value, prefix=""):
    matches = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            key_lower = key.lower()
            if any(part in key_lower for part in ["story", "url", "link", "permalink", "media", "referral", "reply_to", "source"]):
                matches[path] = child
            matches.update(flatten_interesting_payload(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.update(flatten_interesting_payload(child, f"{prefix}[{index}]"))
    return matches


def first_string_from_keys(data, keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def nested_get(data, path):
    value = data
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) else ""


def first_story_attachment(message):
    for attachment in message.get("attachments", []) or []:
        payload = attachment.get("payload", {}) or {}
        if attachment.get("type") == "ig_story" or payload.get("story_media_id") or payload.get("story_media_url"):
            return payload
    return {}


def first_media_attachment(message):
    for attachment in message.get("attachments", []) or []:
        payload = attachment.get("payload", {}) or {}
        if attachment.get("type") == "ig_story" or payload.get("story_media_id"):
            continue
        media_url = first_string_from_keys(payload, ["url", "media_url", "permalink", "link", "share_url"])
        media_id = first_string_from_keys(payload, ["ig_post_media_id", "ig_reel_media_id", "reel_video_id", "reel_media_id", "media_id", "media_share_id", "media_product_id", "id", "source_id", "target_id"]) or media_id_from_url(media_url)
        if media_id or media_url:
            return {"id": media_id, "url": media_url, "type": attachment.get("type", ""), "payload": payload}
    return {}


def save_instagram_webhook_event(payload, entry, event):
    message = event.get("message", {}) or {}
    referral = event.get("referral") or message.get("referral") or {}
    reply_to = message.get("reply_to") or {}
    story_attachment = first_story_attachment(message)
    media_attachment = first_media_attachment(message)
    extracted = flatten_interesting_payload(event)
    media_id = first_string_from_keys(referral, ["media_id", "source_id", "id"]) or first_string_from_keys(reply_to, ["media_id", "story_id", "id"]) or nested_get(reply_to, ["story", "id"]) or first_string_from_keys(story_attachment, ["story_media_id", "media_id", "id"]) or media_attachment.get("id", "")
    story_id = first_string_from_keys(reply_to, ["story_id", "id"]) or first_string_from_keys(referral, ["story_id"]) or nested_get(reply_to, ["story", "id"]) or first_string_from_keys(story_attachment, ["story_media_id", "story_id", "id"])
    story_url = first_string_from_keys(referral, ["source_url", "url", "link", "permalink"]) or first_string_from_keys(reply_to, ["url", "link", "permalink"]) or nested_get(reply_to, ["story", "url"]) or first_string_from_keys(story_attachment, ["story_media_url", "url", "media_url"]) or media_attachment.get("url", "")
    media_id = media_id or media_id_from_url(story_url)
    if story_attachment or reply_to:
        story_id = story_id or media_id_from_url(story_url)
    event_type = "story_send" if story_attachment and not reply_to else "story_reply" if reply_to or "story" in json.dumps(extracted, ensure_ascii=False).lower() else "media_send" if media_attachment else "message"
    try:
        saved = InstagramWebhookEvent.objects.create(
            event_type=event_type,
            sender_id=str(event.get("sender", {}).get("id", "")),
            recipient_id=str(event.get("recipient", {}).get("id", "")),
            message_id=str(message.get("mid", "")),
            text=str(message.get("text", "")),
            media_id=str(media_id or ""),
            story_id=str(story_id or ""),
            story_url=str(story_url or ""),
            postback_referral=referral or {},
            extracted=extracted,
            raw_payload={"entry": entry, "event": event, "payload": payload},
        )
        print(f"INSTAGRAM_WEBHOOK_EVENT id={saved.id} type={saved.event_type} sender={saved.sender_id} mid={saved.message_id} media_id={saved.media_id} story_id={saved.story_id} story_url={saved.story_url} extracted_keys={list(extracted.keys())}", flush=True)
        return saved
    except Exception as exc:
        print(f"INSTAGRAM_WEBHOOK_EVENT_SAVE_FAILED error={exc} event={json.dumps(event, ensure_ascii=False)}", flush=True)
        return None


def link_story_post_from_event(webhook_event, branch=None):
    if not webhook_event or webhook_event.event_type not in ["story_reply", "story_send"]:
        return None
    story_id = webhook_event.story_id or webhook_event.media_id
    if not story_id:
        return None
    exact = SocialPost.objects.filter(Q(media_id=story_id) | Q(webhook_story_id=story_id)).first()
    if exact:
        updates = []
        if not exact.webhook_story_id:
            exact.webhook_story_id = story_id
            updates.append("webhook_story_id")
        if webhook_event.story_url and exact.webhook_story_url != webhook_event.story_url:
            exact.webhook_story_url = webhook_event.story_url
            updates.append("webhook_story_url")
        if updates:
            exact.save(update_fields=updates + ["updated_at"])
        return exact
    queryset = SocialPost.objects.filter(post_type="story", is_active=True, webhook_story_id="")
    if branch:
        queryset = queryset.filter(branch=branch)
    candidates = list(queryset.order_by("-created_at")[:2])
    if len(candidates) != 1:
        return None
    post = candidates[0]
    post.webhook_story_id = story_id
    post.webhook_story_url = webhook_event.story_url
    post.save(update_fields=["webhook_story_id", "webhook_story_url", "updated_at"])
    print(f"INSTAGRAM_STORY_LINKED social_post_id={post.id} story_id={story_id} webhook_event_id={webhook_event.id}", flush=True)
    return post


def link_media_post_from_event(webhook_event):
    if not webhook_event or webhook_event.event_type != "media_send":
        return None
    media_id = webhook_event.media_id
    if media_id:
        exact = SocialPost.objects.filter(media_id=media_id).first()
        if exact:
            return exact
        media = find_media_by_id(media_id)
        if media:
            exact = SocialPost.objects.filter(permalink=media.get("permalink", "")).first()
            if exact:
                if exact.media_id != media_id:
                    exact.media_id = media_id
                    exact.save(update_fields=["media_id", "updated_at"])
                return exact
    if webhook_event.story_url:
        normalized = normalize_instagram_permalink(webhook_event.story_url)
        exact = SocialPost.objects.filter(permalink__startswith=normalized).first()
        if exact and media_id and exact.media_id != media_id and not SocialPost.objects.filter(media_id=media_id).exclude(pk=exact.pk).exists():
            exact.media_id = media_id
            exact.save(update_fields=["media_id", "updated_at"])
        return exact
    return None


def resolve_instagram_event(payload):
    entries = payload.get("entry", [])
    results = []
    for entry in entries:
        for event in entry.get("messaging", []):
            webhook_event = save_instagram_webhook_event(payload, entry, event)
            sender_id = event.get("sender", {}).get("id")
            integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
            own_ids = {value for value in [integration.instagram_account_id, integration.instagram_business_id, settings.INSTAGRAM_ACCOUNT_ID] if value}
            message = event.get("message", {})
            text = message.get("text")
            story_attachment = first_story_attachment(message)
            media_attachment = first_media_attachment(message)
            story_text = "Mijoz Instagram storyni directga yubordi." if story_attachment else ""
            media_text = "Mijoz Instagram post/reelni directga yubordi." if media_attachment else ""
            message_text = text or story_text or media_text
            if not sender_id or sender_id in own_ids or not message_text or message.get("is_echo"):
                continue
            branch = getattr(SocialPost.objects.filter(is_active=True).first(), "branch", None)
            if not branch:
                continue
            referral = event.get("referral") or message.get("referral") or {}
            media_id = referral.get("media_id") or referral.get("source_id") or (webhook_event.story_id if webhook_event else "") or (webhook_event.media_id if webhook_event else "")
            post = SocialPost.objects.filter(Q(media_id=media_id) | Q(webhook_story_id=media_id)).first() if media_id else None
            if not post:
                post = link_story_post_from_event(webhook_event, branch)
            if not post:
                post = link_media_post_from_event(webhook_event)
            customer, _ = Customer.objects.get_or_create(instagram_user_id=sender_id, defaults={"branch": branch})
            conversation = Conversation.objects.filter(customer=customer, status__in=["ai", "operator"]).first()
            if not conversation:
                conversation = Conversation.objects.create(customer=customer, branch=customer.branch or branch, social_post=post)
            elif post and conversation.social_post_id != post.id:
                conversation.social_post = post
                conversation.save(update_fields=["social_post", "updated_at"])
            saved_message = ingest_customer_message(conversation, message_text, message.get("mid", ""))
            if saved_message:
                results.append({"conversation_id": conversation.id, "message_id": saved_message.id, "recipient_id": sender_id})
    return results
