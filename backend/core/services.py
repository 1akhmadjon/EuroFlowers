import json
import re
import requests
from html import escape
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from openai import OpenAI
from .models import AISettings, AuditLog, Branch, BusinessSettings, CatalogItem, Conversation, Customer, InstagramWebhookEvent, IntegrationSettings, Lead, LeadCatalogUsage, LeadPackagingUsage, LeadStockUsage, Message, Notification, Packaging, PackagingMovement, SocialPost, StockBatch, StockMovement


def normalize_instagram_permalink(value):
    return (value or "").split("?")[0].rstrip("/")


def media_id_from_url(value):
    if not value:
        return ""
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    return (query.get("asset_id") or [""])[0]


def normalize_phone(value):
    if "*" in (value or ""):
        return ""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return ""


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


def ensure_catalog_stock_available(item, quantity=None):
    qty = quantity if quantity is not None else item.quantity_total
    rows = list(item.composition.select_related("stock_batch"))
    shortages = []
    for row in rows:
        needed = row.quantity_stems * qty
        if row.stock_batch.remaining_stems < needed:
            shortages.append(f"{row.stock_batch.batch_number}: kerak {needed}, bor {row.stock_batch.remaining_stems}")
    if shortages:
        raise ValueError("Katalog uchun yetarli qoldiq yo‘q: " + "; ".join(shortages))


def mark_catalog_sold(item, user, quantity=1):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        quantity = int(quantity or 1)
        if quantity < 1:
            raise ValueError("Sotilgan son 1 dan kam bo‘lmasligi kerak")
        if item.quantity_sold + quantity > item.quantity_total:
            raise ValueError("Sotilgan son katalogdagi umumiy sondan oshib ketdi")
        item.quantity_sold += quantity
        if item.quantity_sold >= item.quantity_total:
            item.status = "sold"
            item.sold_at = timezone.now()
        elif item.status == "draft":
            item.status = "available"
        item.save(update_fields=["quantity_sold", "status", "sold_at", "updated_at"])
        notification = Notification.objects.create(
            branch=item.branch,
            notification_type="stock_pending",
            title_uz=f"{item.name_uz}: sklad chiqimi kutilmoqda",
            title_ru=f"{item.name_ru}: ожидается списание со склада",
            body_uz=f"{quantity} ta kompozitsiya sotildi. Tarkibdagi gullarni sklad hisobidan chiqaring.",
            body_ru=f"Продано композиций: {quantity}. Спишите цветы из состава со склада.",
            reference_type="catalog_item",
            reference_id=item.id,
        )
        AuditLog.objects.create(user=user, action="catalog_sold", entity_type="CatalogItem", entity_id=str(item.id), after={"status": item.status, "quantity": quantity, "quantity_sold": item.quantity_sold, "notification": notification.id})
    return item


def deduct_catalog_stock(item, user, quantity=None):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        pending = item.quantity_sold - item.quantity_stock_deducted
        if quantity is None:
            quantity = pending
        quantity = int(quantity or 0)
        if quantity < 1:
            raise ValueError("Skladdan kamaytirish uchun sotilgan, lekin yechilmagan son yo‘q")
        if quantity > pending:
            raise ValueError("Skladdan kamaytirish soni sotilgan, lekin yechilmagan sondan oshib ketdi")
        rows = list(item.composition.select_related("stock_batch").select_for_update())
        shortages = [row for row in rows if row.stock_batch.remaining_stems < row.quantity_stems * quantity]
        if shortages:
            names = ", ".join(row.stock_batch.batch_number for row in shortages)
            raise ValueError(f"Yetarli qoldiq yo‘q: {names}")
        for row in rows:
            batch = row.stock_batch
            stems = row.quantity_stems * quantity
            bunches = row.quantity_bunches * quantity
            batch.remaining_stems -= stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="out",
                quantity_stems=-stems,
                quantity_bunches=-bunches,
                reference_type="catalog_item",
                reference_id=item.id,
                reason=f"{item.name_uz} sotildi: {quantity} ta",
                performed_by=user,
            )
        item.quantity_stock_deducted += quantity
        if item.quantity_stock_deducted >= item.quantity_sold:
            item.stock_deducted_at = timezone.now()
            Notification.objects.filter(reference_type="catalog_item", reference_id=item.id, notification_type="stock_pending").update(is_read=True)
        item.save(update_fields=["quantity_stock_deducted", "stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="catalog_stock_deducted", entity_type="CatalogItem", entity_id=str(item.id), after={"rows": len(rows), "quantity": quantity, "quantity_stock_deducted": item.quantity_stock_deducted})
    return item


def deduct_lead_stock(lead, user):
    with transaction.atomic():
        lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if lead.stock_deducted_at:
            return lead
        stock_rows = list(lead.stock_usage.select_related("stock_batch").select_for_update())
        packaging_rows = list(lead.packaging_usage.select_related("packaging").select_for_update())
        catalog_rows = list(lead.catalog_usage.select_related("catalog_item").select_for_update())
        catalog_quantities = {}
        for row in catalog_rows:
            catalog_quantities[row.catalog_item_id] = catalog_quantities.get(row.catalog_item_id, 0) + row.quantity
        catalog_composition_rows = []
        catalog_shortages = []
        catalog_items = {}
        for catalog_item_id, quantity in catalog_quantities.items():
            item = CatalogItem.objects.select_for_update().get(pk=catalog_item_id)
            catalog_items[catalog_item_id] = item
            if item.quantity_sold + quantity > item.quantity_total:
                catalog_shortages.append(f"{item.name_uz}: katalogda {item.quantity_total - item.quantity_sold} ta qoldi")
                continue
            for composition in item.composition.select_related("stock_batch").select_for_update():
                needed = composition.quantity_stems * quantity
                if composition.stock_batch.remaining_stems < needed:
                    catalog_shortages.append(f"{item.name_uz} / {composition.stock_batch.batch_number}: kerak {needed}, bor {composition.stock_batch.remaining_stems}")
                catalog_composition_rows.append((quantity, item, composition, needed, composition.quantity_bunches * quantity))
        stock_shortages = [row for row in stock_rows if row.stock_batch.remaining_stems < row.quantity_stems]
        packaging_shortages = [row for row in packaging_rows if row.packaging.quantity < row.quantity]
        if stock_shortages or packaging_shortages or catalog_shortages:
            parts = [row.stock_batch.batch_number for row in stock_shortages] + [row.packaging.name_uz for row in packaging_shortages] + catalog_shortages
            raise ValueError("Lead uchun yetarli sklad qoldig‘i yo‘q: " + ", ".join(parts))
        for quantity, item, composition, stems, bunches in catalog_composition_rows:
            batch = composition.stock_batch
            batch.remaining_stems -= stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="out",
                quantity_stems=-stems,
                quantity_bunches=-bunches,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: {lead.customer} / {item.name_uz}: {quantity} ta",
                performed_by=user,
            )
        for catalog_item_id, quantity in catalog_quantities.items():
            item = catalog_items[catalog_item_id]
            item.quantity_sold += quantity
            item.quantity_stock_deducted += quantity
            if item.quantity_sold >= item.quantity_total:
                item.status = "sold"
                item.sold_at = timezone.now()
            item.stock_deducted_at = timezone.now()
            item.save(update_fields=["quantity_sold", "quantity_stock_deducted", "status", "sold_at", "stock_deducted_at", "updated_at"])
        for row in stock_rows:
            batch = row.stock_batch
            batch.remaining_stems -= row.quantity_stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="out",
                quantity_stems=-row.quantity_stems,
                quantity_bunches=-row.quantity_bunches,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: {lead.customer}",
                performed_by=user,
            )
        for row in packaging_rows:
            packaging = row.packaging
            packaging.quantity -= row.quantity
            packaging.save(update_fields=["quantity", "updated_at"])
            PackagingMovement.objects.create(
                packaging=packaging,
                movement_type="out",
                quantity=-row.quantity,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: {lead.customer}",
                performed_by=user,
            )
        lead.stock_deducted_at = timezone.now()
        lead.save(update_fields=["stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="lead_stock_deducted", entity_type="Lead", entity_id=str(lead.id), after={"stock_rows": len(stock_rows), "packaging_rows": len(packaging_rows), "catalog_rows": len(catalog_rows)})
    return lead


def restore_lead_stock(lead, user):
    with transaction.atomic():
        lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if not lead.stock_deducted_at:
            return lead
        stock_rows = list(lead.stock_usage.select_related("stock_batch").select_for_update())
        packaging_rows = list(lead.packaging_usage.select_related("packaging").select_for_update())
        catalog_rows = list(lead.catalog_usage.select_related("catalog_item").select_for_update())
        catalog_quantities = {}
        for row in catalog_rows:
            catalog_quantities[row.catalog_item_id] = catalog_quantities.get(row.catalog_item_id, 0) + row.quantity
        for catalog_item_id, quantity in catalog_quantities.items():
            item = CatalogItem.objects.select_for_update().get(pk=catalog_item_id)
            for composition in item.composition.select_related("stock_batch").select_for_update():
                batch = composition.stock_batch
                stems = composition.quantity_stems * quantity
                bunches = composition.quantity_bunches * quantity
                batch.remaining_stems += stems
                batch.save(update_fields=["remaining_stems", "updated_at"])
                StockMovement.objects.create(
                    batch=batch,
                    movement_type="adjustment",
                    quantity_stems=stems,
                    quantity_bunches=bunches,
                    reference_type="lead",
                    reference_id=lead.id,
                    reason=f"Lead #{lead.id}: status qaytdi / {item.name_uz}: {quantity} ta",
                    performed_by=user,
                )
            item.quantity_sold = max(item.quantity_sold - quantity, 0)
            item.quantity_stock_deducted = max(item.quantity_stock_deducted - quantity, 0)
            if item.quantity_sold < item.quantity_total and item.status == "sold":
                item.status = "available"
                item.sold_at = None
            item.stock_deducted_at = timezone.now() if item.quantity_stock_deducted and item.quantity_stock_deducted >= item.quantity_sold else None
            item.save(update_fields=["quantity_sold", "quantity_stock_deducted", "status", "sold_at", "stock_deducted_at", "updated_at"])
        for row in stock_rows:
            batch = row.stock_batch
            batch.remaining_stems += row.quantity_stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="adjustment",
                quantity_stems=row.quantity_stems,
                quantity_bunches=row.quantity_bunches,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: status qaytdi / {lead.customer}",
                performed_by=user,
            )
        for row in packaging_rows:
            packaging = row.packaging
            packaging.quantity += row.quantity
            packaging.save(update_fields=["quantity", "updated_at"])
            PackagingMovement.objects.create(
                packaging=packaging,
                movement_type="adjustment",
                quantity=row.quantity,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: status qaytdi / {lead.customer}",
                performed_by=user,
            )
        lead.stock_deducted_at = None
        lead.save(update_fields=["stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="lead_stock_restored", entity_type="Lead", entity_id=str(lead.id), after={"stock_rows": len(stock_rows), "packaging_rows": len(packaging_rows), "catalog_rows": len(catalog_rows)})
    return lead


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


def apply_packaging_movement(packaging, movement_type, quantity, reason, user):
    with transaction.atomic():
        packaging = Packaging.objects.select_for_update().get(pk=packaging.pk)
        delta = quantity if movement_type == "adjustment" else abs(quantity) if movement_type in ["in", "transfer_in"] else -abs(quantity)
        if packaging.quantity + delta < 0:
            raise ValueError("Skladda yetarli qadoqlash/savat yo‘q")
        before = packaging.quantity
        packaging.quantity += delta
        packaging.save(update_fields=["quantity", "updated_at"])
        movement = PackagingMovement.objects.create(
            packaging=packaging,
            movement_type=movement_type,
            quantity=delta,
            reason=reason,
            performed_by=user,
        )
        AuditLog.objects.create(user=user, action="packaging_movement", entity_type="Packaging", entity_id=str(packaging.id), before={"quantity": before}, after={"quantity": packaging.quantity, "movement": movement.id})
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


def catalog_image_for_conversation(conversation, reply=None):
    catalog_ids = []
    if reply and reply.metadata:
        catalog_ids = [row.get("catalog_id") for row in reply.metadata.get("catalog_items", []) if row.get("catalog_id") and int(row.get("quantity") or 0) > 0]
    if catalog_ids:
        catalog = CatalogItem.objects.filter(id__in=catalog_ids, status="available").exclude(image_url="").order_by("id").first()
        if catalog and catalog.image_url.startswith("https://"):
            return {"source": f"catalog:{catalog.id}", "image_url": catalog.image_url}
    if not conversation.social_post_id:
        return None
    catalog = CatalogItem.objects.filter(social_post=conversation.social_post, status="available").exclude(image_url="").order_by("-created_at").first()
    image_url = catalog.image_url if catalog else conversation.social_post.image_url
    if not image_url or not image_url.startswith("https://"):
        return None
    source = f"catalog:{catalog.id}" if catalog else f"social_post:{conversation.social_post_id}"
    return {"source": source, "image_url": image_url}


def send_instagram_context_image(recipient_id, conversation, reply=None):
    image = catalog_image_for_conversation(conversation, reply)
    if not image:
        return None
    marker = f"instagram_image_sent:reply:{getattr(reply, 'id', 0)}:{image['source']}:{image['image_url']}"
    if Message.objects.filter(conversation=conversation, sender="system", metadata__media_image_key=marker).exists():
        return None
    result = instagram_send_image(recipient_id, image["image_url"])
    Message.objects.create(conversation=conversation, sender="system", text="Instagram image sent", metadata={"media_image_key": marker, "image_url": image["image_url"], "result": result})
    return result


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


def telegram_send_rich(chat_id, rich_message):
    return telegram_api("sendRichMessage", {"chat_id": chat_id, "rich_message": rich_message})


def telegram_send_image(chat_id, image_url):
    return telegram_api("sendPhoto", {"chat_id": chat_id, "photo": image_url})


def send_telegram_context_image(chat_id, conversation, reply=None):
    image = catalog_image_for_conversation(conversation, reply)
    if not image:
        return None
    marker = f"telegram_image_sent:reply:{getattr(reply, 'id', 0)}:{image['source']}:{image['image_url']}"
    if Message.objects.filter(conversation=conversation, sender="system", metadata__media_image_key=marker).exists():
        return None
    result = telegram_send_image(chat_id, image["image_url"])
    Message.objects.create(conversation=conversation, sender="system", text="Telegram image sent", metadata={"media_image_key": marker, "image_url": image["image_url"], "result": result})
    return result


def send_catalog_image_for_conversation(conversation, query):
    item = catalog_item_from_text(conversation, query)
    if not item or not item.image_url.startswith("https://"):
        return {"ok": False, "image_sent": False, "catalog_name": str(query or ""), "detail": "Rasm topilmadi"}
    latest_customer_id = conversation.messages.filter(sender="customer").order_by("-created_at").values_list("id", flat=True).first() or 0
    marker = f"ai_tool_image_sent:customer:{latest_customer_id}:catalog:{item.id}:{item.image_url}"
    existing = Message.objects.filter(conversation=conversation, sender="system", metadata__media_image_key=marker).first()
    if existing:
        return {"ok": True, "image_sent": True, "already_sent": True, "catalog_name": item.name_uz}
    recipient = conversation.customer.instagram_user_id or ""
    if recipient.startswith("telegram:"):
        result = telegram_send_image(recipient.split(":", 1)[1], item.image_url)
        text = "Telegram image sent"
    elif recipient:
        result = instagram_send_image(recipient, item.image_url)
        text = "Instagram image sent"
    else:
        result = {"mocked": True}
        text = "Catalog image sent"
    Message.objects.create(conversation=conversation, sender="system", text=text, metadata={"media_image_key": marker, "image_url": item.image_url, "catalog_id": item.id, "catalog_name": item.name_uz, "result": result})
    return {"ok": True, "image_sent": True, "already_sent": False, "catalog_name": item.name_uz}


def telegram_sender_action(chat_id, action="typing"):
    return telegram_api("sendChatAction", {"chat_id": chat_id, "action": action})


def clean_catalog_item_name(value):
    return re.sub(r"\s+\bid\s*\d+\b", "", (value or "").strip(), flags=re.IGNORECASE).strip()


def clean_catalog_price(value):
    price = normalize_ai_reply_text((value or "").strip())
    if not re.search(r"(so[‘'ʻ`]m|som|сум)", price, flags=re.IGNORECASE):
        price = f"{price} so‘m"
    return price


def clean_catalog_listing_text(text):
    cleaned = re.sub(r"(?im)^[^\S\n]*[-]?[^\S\n]*Tarkibi\s*:.*?(?=(?:\d+[).]\s*)|\n|$)", "", text or "")
    cleaned = re.sub(r"\s+(?=\d+[).]\s*)", "\n", cleaned)
    lines = []
    for line in cleaned.splitlines():
        match = re.match(r"^(?P<prefix>\s*(?:(?:\d+[).])|[-*])\s*)(?P<name>.+?)\s+[-—]\s+(?P<price>\d[\d\s]*)(?:\s*(?:so[‘'ʻ`]m|som|сум))?(?:\b|$).*$", line, flags=re.IGNORECASE)
        if match:
            line = f"{match.group('prefix')}{clean_catalog_item_name(match.group('name'))} - {clean_catalog_price(match.group('price'))}"
        lines.append(line)
    return "\n".join(lines)


def telegram_catalog_rows_from_text(text):
    rows = []
    cleaned = clean_catalog_listing_text(text)
    for line in cleaned.splitlines():
        match = re.match(r"^\s*(?:(?:\d+[).])|[-*])\s*(?P<name>.+?)\s+[-—]\s+(?P<price>\d[\d\s]*(?:\s*(?:so[‘'ʻ`]m|som|сум))?)\s*$", line, flags=re.IGNORECASE)
        if not match:
            continue
        rows.append({
            "name": clean_catalog_item_name(match.group("name")),
            "price": clean_catalog_price(match.group("price")),
        })
    return rows


def telegram_catalog_rich_message(text):
    rows = telegram_catalog_rows_from_text(text)
    if len(rows) < 2:
        return None
    cleaned = clean_catalog_listing_text(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    first_row_index = next((index for index, line in enumerate(lines) if re.match(r"^(?:(?:\d+[).])|[-*])\s*", line)), 0)
    intro = " ".join(lines[:first_row_index]).strip()
    outro = "Qaysi biri sizga ma'qul bo‘lsa tanlang, rasmlari bilan ko‘rsataman."
    html = ""
    if intro:
        html += f"<p>{escape(intro)}</p>"
    html += "<table bordered striped><caption>Tayyor katalog</caption><tr><th>Gul</th><th>Narx</th></tr>"
    for row in rows[:20]:
        html += f"<tr><td>{escape(row['name'])}</td><td>{escape(row['price'])}</td></tr>"
    html += "</table>"
    html += f"<p>{escape(outro)}</p>"
    return {"html": html, "skip_entity_detection": True}


def telegram_send_catalog_rich_if_possible(chat_id, text):
    rich_message = telegram_catalog_rich_message(text)
    if not rich_message:
        return None
    return telegram_send_rich(chat_id, rich_message)


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


def catalog_composition_summary(item):
    rows = []
    for row in item.composition.select_related("stock_batch__variant__flower"):
        batch = row.stock_batch
        name = f"{batch.variant.flower.name_uz} {batch.variant.name_uz} {batch.variant.color_uz}".strip()
        rows.append({"name_uz": name, "quantity_stems": row.quantity_stems, "quantity_bunches": str(row.quantity_bunches)})
    return rows


def recent_customer_orders(customer):
    orders = []
    for lead in customer.leads.select_related("social_post").prefetch_related("catalog_usage__catalog_item", "stock_usage__stock_batch__variant__flower", "packaging_usage__packaging").order_by("-created_at")[:3]:
        catalog_items = [{"name_uz": row.catalog_item.name_uz, "quantity": row.quantity, "type": row.catalog_item.arrangement_type, "price": str(row.catalog_item.price)} for row in lead.catalog_usage.all()]
        stock_items = [{
            "flower_uz": row.stock_batch.variant.flower.name_uz,
            "variant_uz": row.stock_batch.variant.name_uz,
            "color_uz": row.stock_batch.variant.color_uz,
            "quantity_stems": row.quantity_stems,
            "quantity_bunches": str(row.quantity_bunches),
        } for row in lead.stock_usage.all()]
        packaging_items = [{"name_uz": row.packaging.name_uz, "quantity": row.quantity, "type": row.packaging.packaging_type} for row in lead.packaging_usage.all()]
        orders.append({
            "lead_id": lead.id,
            "created_at": lead.created_at.isoformat(),
            "status": lead.status,
            "arrangement_type": lead.arrangement_type,
            "estimated_price": str(lead.estimated_price or ""),
            "request_uz": lead.request_uz,
            "catalog_items": catalog_items,
            "stock_items": stock_items,
            "packaging_items": packaging_items,
        })
    return orders


def ai_catalog_rows(query="", limit=24):
    queryset = CatalogItem.objects.filter(status="available").select_related("social_post").order_by("-created_at")
    if query:
        queryset = queryset.filter(Q(name_uz__icontains=query) | Q(name_ru__icontains=query) | Q(description_uz__icontains=query) | Q(description_ru__icontains=query))
    rows = []
    for row in queryset[:limit]:
        rows.append({
            "name_uz": row.name_uz,
            "name_ru": row.name_ru,
            "type": row.arrangement_type,
            "price": str(row.price),
            "has_image": bool(row.image_url or (row.social_post.image_url if row.social_post_id else "")),
        })
    return rows


def ai_stock_rows(query="", limit=24):
    queryset = StockBatch.objects.filter(is_active=True, remaining_stems__gt=0).select_related("variant__flower").order_by("variant__flower__name_uz", "variant__color_uz", "-remaining_stems")
    if query:
        queryset = queryset.filter(Q(variant__flower__name_uz__icontains=query) | Q(variant__flower__name_ru__icontains=query) | Q(variant__name_uz__icontains=query) | Q(variant__name_ru__icontains=query) | Q(variant__color_uz__icontains=query) | Q(variant__color_ru__icontains=query))
    rows = []
    for row in queryset[:limit]:
        rows.append({
            "batch_id": row.id,
            "flower_uz": row.variant.flower.name_uz,
            "flower_ru": row.variant.flower.name_ru,
            "variant_uz": row.variant.name_uz,
            "variant_ru": row.variant.name_ru,
            "color_uz": row.variant.color_uz,
            "color_ru": row.variant.color_ru,
            "height_cm": row.height_cm,
            "availability": "bor" if row.remaining_stems > row.minimum_sale_stems else "oz qoldi",
            "stems_per_bunch": row.stems_per_bunch,
            "minimum_sale_stems": row.minimum_sale_stems,
            "price_per_stem": str(row.sale_price_per_stem),
            "price_per_bunch": str(row.sale_price_per_bunch),
        })
    return rows


def ai_basket_rows(limit=20):
    return [{
        "id": row.id,
        "name_uz": row.name_uz,
        "name_ru": row.name_ru,
        "min": row.capacity_min_stems,
        "max": row.capacity_max_stems,
        "price": str(row.sale_price),
    } for row in Packaging.objects.filter(packaging_type="basket", is_active=True, quantity__gt=0).order_by("sale_price")[:limit]]


def ai_post_context(conversation):
    if not conversation.social_post_id:
        return None
    post = conversation.social_post
    post_catalog = CatalogItem.objects.filter(social_post=post, status="available")
    return {
        "type": post.post_type,
        "title_uz": post.title_uz,
        "title_ru": post.title_ru,
        "description_uz": post.description_uz,
        "description_ru": post.description_ru,
        "price": str(post.price or ""),
        "catalog": [{"name_uz": row.name_uz, "name_ru": row.name_ru, "type": row.arrangement_type, "price": str(row.price), "has_image": bool(row.image_url)} for row in post_catalog],
    }


def mini_app_custom_quote_ai(request_text, arrangement_type):
    business_settings, _ = BusinessSettings.objects.get_or_create(pk=1)
    ai_settings, _ = AISettings.objects.get_or_create(pk=1)
    api_key = openai_api_key()
    florist_fee = business_settings.default_florist_fee
    if not api_key:
        return {
            "lines": [{"type": "custom_text", "request_text": request_text}],
            "packaging": None,
            "florist_fee": str(florist_fee),
            "estimated_price": str(florist_fee),
            "price_is_estimate": True,
            "ai_note": "Taxminiy narxni operator aniqlashtirib beradi.",
        }
    context = {
        "request_text": request_text,
        "arrangement_type": arrangement_type,
        "florist_fee": str(florist_fee),
        "stock": ai_stock_rows("", limit=60),
        "baskets": ai_basket_rows() if arrangement_type == "basket" else [],
        "rule": "Mijozga stock ro‘yxatini ko‘rsatma. Faqat taxminiy umumiy narx qaytar. Florist haqini narxga qo‘sh.",
    }
    schema = {
        "type": "object",
        "properties": {
            "estimated_price": {"type": "number"},
            "ai_note": {"type": "string"},
        },
        "required": ["estimated_price", "ai_note"],
        "additionalProperties": False,
    }
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=ai_settings.openai_model or settings.OPENAI_MODEL,
        instructions="EuroFlowers mini app uchun custom buket/savat taxminiy narxini hisobla. Narx taxminiy, florist haqi qo‘shilgan bo‘lsin. Javob faqat JSON.",
        input=json.dumps(context, ensure_ascii=False),
        max_output_tokens=700,
        reasoning={"effort": "minimal"},
        text={"format": {"type": "json_schema", "name": "mini_app_quote", "strict": True, "schema": schema}},
    )
    data = json.loads(response.output_text)
    estimated_price = Decimal(str(data["estimated_price"])).quantize(Decimal("1"))
    if estimated_price < florist_fee:
        estimated_price = florist_fee
    return {
        "lines": [{"type": "custom_text", "request_text": request_text}],
        "packaging": None,
        "florist_fee": str(florist_fee),
        "estimated_price": str(estimated_price),
        "price_is_estimate": True,
        "ai_note": data.get("ai_note") or "Taxminiy narx, operator aniq ma'lumot beradi.",
    }


def ai_tool_definitions():
    empty_parameters = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    return [
        {"type": "function", "name": "get_catalog", "description": "Bugungi tayyor katalogdagi available buket/savat/kompozitsiyalarni olish. Umumiy 'qanaqa gullar bor', 'tayyor gullar bormi', 'katalog' so‘rovlarida doim shu tool chaqiriladi.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Ixtiyoriy qidiruv matni, masalan pion yoki qizil atirgul"}}, "required": ["query"], "additionalProperties": False}, "strict": True},
        {"type": "function", "name": "search_stock", "description": "Skladdagi gullarni qidirish. Faqat mijoz custom buket/savat yasatmoqchi/yig‘dirmoqchi ekanini aniq aytsa yoki 'yasatishga qanaqa gullar bor' deb so‘rasa chaqiriladi. Umumiy 'qanaqa gullar bor' so‘rovida chaqirilmaydi.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Gul/rang/tur qidiruvi, bo‘sh string custom yasatish uchun asosiy gullar"}}, "required": ["query"], "additionalProperties": False}, "strict": True},
        {"type": "function", "name": "get_baskets", "description": "Faqat mijoz custom savat yasatmoqchi/yig‘dirmoqchi bo‘lsa mos savat variantlarini olish.", "parameters": empty_parameters, "strict": True},
        {"type": "function", "name": "get_recent_orders", "description": "Mijoz oldingi buyurtmalarini so‘raganda olish.", "parameters": empty_parameters, "strict": True},
        {"type": "function", "name": "get_post_context", "description": "Conversation story/post/reel bilan bog‘langan bo‘lsa, o‘sha media ma’lumotini olish.", "parameters": empty_parameters, "strict": True},
        {"type": "function", "name": "send_catalog_image", "description": "Mijoz aniq katalogdagi buket/savat rasmini so‘raganda shu katalog item rasmini Instagram/Telegram chatga yuborish. Rasm so‘ralganda final javobdan oldin doim shu tool chaqiriladi. Catalog id ishlatilmaydi, katalog nomi yoki mijoz yozgan nom bilan chaqiriladi.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Rasmi yuboriladigan katalog nomi, masalan Pushti atirgul buketi"}}, "required": ["query"], "additionalProperties": False}, "strict": True},
    ]


def execute_ai_tool(name, arguments, conversation):
    if name == "get_catalog":
        rows = ai_catalog_rows(arguments.get("query", ""))
        return {"items": rows, "count": len(rows)}
    if name == "search_stock":
        return {"items": ai_stock_rows(arguments.get("query", ""))}
    if name == "get_baskets":
        return {"items": ai_basket_rows()}
    if name == "get_recent_orders":
        return {"items": recent_customer_orders(conversation.customer)}
    if name == "get_post_context":
        return {"post": ai_post_context(conversation)}
    if name == "send_catalog_image":
        return send_catalog_image_for_conversation(conversation, arguments.get("query"))
    return {"error": f"Unknown tool: {name}"}


def ai_response_schema():
    return {
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
            "handoff": {"type": "boolean"},
            "catalog_items": {
                "type": "array",
                "items": {"type": "object", "properties": {"catalog_name": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["catalog_name", "quantity"], "additionalProperties": False},
            },
            "stock_items": {
                "type": "array",
                "items": {"type": "object", "properties": {"batch_id": {"type": "integer"}, "quantity_stems": {"type": "integer"}, "quantity_bunches": {"type": "number"}}, "required": ["batch_id", "quantity_stems", "quantity_bunches"], "additionalProperties": False},
            },
        },
        "required": ["reply", "detected_language", "customer_name", "phone", "lead_ready", "lead_request", "arrangement_type", "estimated_price", "handoff", "catalog_items", "stock_items"],
        "additionalProperties": False,
    }


def normalize_ai_reply_text(text):
    normalized = re.sub(r"(?<=\d),(?=\d{3}\b)", " ", text or "")
    normalized = normalized.replace("—", "-").replace("–", "-")
    normalized = re.sub(r"(?m)^\s*•\s*", "", normalized)
    normalized = re.sub(r"\(([^()]{1,80})\)", r"\1", normalized)
    normalized = re.sub(r"\s*\(?\bID\s*:\s*\d+\)?", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*\(?\bcatalog[_ ]?id\s*:\s*\d+\)?", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bcatalog\s+id\s*\d+\b", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[^.?!\n]*(id\s*yuboring|\d+\s*deb yozing)[^.?!\n]*[.?!]?", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"(?im)^\s*[-]?\s*(Tarkibi|Kompozitsiya|Mavjudligi)\s*:.*$", "", normalized)
    normalized = re.sub(r"(?im)^\s*\d+(?:\.\d+)?\s*bunch(?:lik)?\.?$", "", normalized)
    normalized = re.sub(r"\bUZS\b", "so‘m", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[^.?!\n]*(so‘ramaymiz|so'ramaymiz|taqiqlanadi)[^.?!\n]*[.?!]?", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"[^.?!\n]*(yetkazish/sana/vaqt|sana/vaqt|yetkazish vaqti)[^.?!\n]*[.?!]?", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"\bxom\s+(pion|pioni|gul|guli)\b", r"\1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bolmaysizmi\b", "yuborasizmi", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\btaqsimlandisini\b", "taqsimlanishini", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bQani,\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*yoki alohida 10 dona novdali atirgul sifatida yetkazib beraylikmi", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"Coral Charm katalogi tayyor gul bo‘lsa, katalogdan tekshirib chiqay[.?!]?\s*", "Coral Charm dan custom buket qilib tayyorlab beramiz. ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*katalogdan ko‘rsatib beraymi\??", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[^.?!\n]*stokdan topolmadim[^.?!\n]*[.?!]?", "Bu gul bo‘yicha aniq ma'lumotni operatorimiz tekshirib beradi.", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"[^.?!\n]*Pochka odatda[^.?!\n]*[.?!]?", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"(Buyurtma qabul qilindi:[^.?!]+[.?!]?)\s*Rahmat,\s*buyurtmangiz qabul qilindi,?\s*", r"\1\nRahmat, ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.replace("hozirtayyor", "hozir tayyor").replace("Hozirtayyor", "Hozir tayyor")


def is_accidental_message(text):
    lowered = (text or "").lower()
    patterns = ["uzur adash", "uzr adash", "adashib yoz", "xato yoz", "notogri yoz", "noto‘g‘ri yoz", "e'tibor bermang", "etibor bermang"]
    return any(pattern in lowered for pattern in patterns)


def remove_image_offer_after_selection(text):
    if not text:
        return ""
    image_words = ["rasmni yuboraymi", "rasmini yuboraymi", "rasm yuboraymi", "rasmini ko‘rsataymi", "rasmini ko'rsataymi", "rasmni ko‘rsataymi", "rasmni ko'rsataymi", "rasmni ko‘rsatish", "rasmni ko'rsatish", "rasm ko‘rsatish", "rasm ko'rsatish"]
    cleaned = text
    for word in image_words:
        cleaned = re.sub(rf"(^|[.?!]\s+|\n)[^.?!\n]*{re.escape(word)}[^.?!\n]*[.?!]?", lambda match: match.group(1).strip() if match.group(1).strip() else "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[^.?!\n]*\brasm\w*[^.?!\n]*(telefon|raqam|manzil|yetkaz)[^.?!\n]*[.?!]?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or text


def remove_premature_catalog_contact_request(text):
    if not text:
        return ""
    lowered = text.lower()
    catalog_markers = ["katalog", "tayyor buket", "tayyor savat", "tayyor gullar", "tayyor variant", "mavjud variant"]
    if not any(marker in lowered for marker in catalog_markers):
        cleaned = re.sub(r"\n*\s*Qaysi biri yoqdi, rasmini ko‘rsataman\??", "", text, flags=re.IGNORECASE).rstrip()
        cleaned = re.sub(r"(Tasdiqlaganingizdan keyin|Tasdiqlasangiz keyin)\s*$", "", cleaned, flags=re.IGNORECASE).rstrip()
        return cleaned
    markers = ["telefon", "raqam", "manzil", "yetkaz"]
    cleaned = text
    for separator in [" Yoki ", "\nYoki ", " yoki ", "\nyoki "]:
        head, sep, tail = cleaned.rpartition(separator)
        if sep and any(marker in tail.lower() for marker in markers):
            cleaned = head.rstrip()
            break
    lines = cleaned.splitlines()
    if lines:
        last = lines[-1]
        lowered_last = last.lower()
        marker_positions = [lowered_last.find(marker) for marker in markers if marker in lowered_last]
        if marker_positions:
            cut_at = min(marker_positions)
            lines[-1] = last[:cut_at].rstrip(" .")
    while lines and any(marker in lines[-1].lower() for marker in markers):
        lines.pop()
    cleaned = "\n".join(lines).rstrip()
    cleaned_lines = cleaned.splitlines()
    if cleaned != text and (not cleaned_lines or "?" not in cleaned_lines[-1]):
        cleaned = cleaned.rstrip() + "\n\nQaysi biri yoqdi, rasmini ko‘rsataman?"
    return cleaned


def shorten_ai_reply_text(text, max_sentences=4, max_chars=420):
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 6:
        lines = lines[:6]
    shortened = "\n".join(lines)
    has_list = any(line.startswith(("1)", "1.", "•")) for line in lines)
    sentences = re.split(r"(?<=[.?!])\s+", shortened)
    if len(sentences) > max_sentences and not has_list:
        shortened = " ".join(sentences[:max_sentences]).strip()
    if not has_list and len(shortened) > max_chars:
        kept = []
        total = 0
        for sentence in sentences:
            next_total = total + len(sentence) + (1 if kept else 0)
            if next_total > max_chars:
                break
            kept.append(sentence)
            total = next_total
        if kept:
            shortened = " ".join(kept).strip()
        else:
            cut = shortened[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-")
            shortened = cut + "."
    return shortened


def ai_reply(conversation):
    customer = conversation.customer
    visible_messages = list(conversation.messages.exclude(sender="system").order_by("-created_at", "-id")[:100])
    fresh_session = bool(len(visible_messages) > 1 and visible_messages[0].created_at - visible_messages[1].created_at >= timedelta(hours=24))
    history_messages = list(reversed(visible_messages))
    history = [{"role": "user" if m.sender == "customer" else "assistant", "content": m.text} for m in history_messages]
    ai_replies_count = sum(1 for message in history_messages if message.sender == "ai")
    has_ai_reply_in_session = ai_replies_count > 0
    last_customer_message = next((message.text for message in reversed(history_messages) if message.sender == "customer"), "")
    if is_accidental_message(last_customer_message):
        reply_text = "Hechqisi yo‘q. Davom etamizmi?" if has_ai_reply_in_session else "Hechqisi yo‘q. Sizga qanday yordam bera olaman?"
        return {
            "reply": reply_text,
            "detected_language": customer.language or "uz",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": None,
            "estimated_price": None,
            "handoff": False,
            "catalog_items": [],
            "stock_items": [],
        }
    business_settings, _ = BusinessSettings.objects.get_or_create(pk=1)
    ai_settings, _ = AISettings.objects.get_or_create(pk=1)
    context = {
        "customer": {"name": customer.name, "phone": customer.masked_phone, "has_phone": bool(customer.phone), "language": customer.language},
        "conversation": {"fresh_session": fresh_session, "has_ai_reply_in_session": has_ai_reply_in_session, "ai_replies_count": ai_replies_count, "last_customer_message": last_customer_message},
        "has_post_context": bool(conversation.social_post_id),
        "rules": {
            "florist_fee": str(business_settings.default_florist_fee),
            "working_hours": business_settings.working_hours,
        },
    }
    sales_rules = (
        " Function calling qoidasi: javobni o‘zing yozasan, lekin real ma'lumot kerak bo‘lsa avval function tool chaqir. Salom, rahmat, umumiy savol yoki oddiy aniqlashtirish uchun tool chaqirma."
        " Umumiy 'qanaqa gullar bor', 'qanday gullar bor', 'gullar bormi', 'nimalar bor', 'tayyor gullar bormi' kabi so‘rovlar tayyor katalog so‘rovi hisoblanadi: get_catalog chaqir, search_stock chaqirma."
        " Katalog/tayyor buket so‘ralsa get_catalog chaqir. Aniq bitta katalog gulining rasmi yoki ma'lumoti so‘ralsa get_catalog query bilan chaqir va final catalog_items ichida faqat o‘sha katalog nomi quantity=1 bo‘lsin."
        " Custom yasatish/yig‘dirish konteksti faqat mijoz 'yasatmoqchiman', 'yig‘dirmoqchiman', 'yasab berasizlarmi', 'buket yasatishga', 'savat yasatishga', 'savatga yig‘ib' kabi aniq aytsa boshlanadi. Shundagina search_stock chaqir."
        " Custom yasatishga qanaqa gullar bor deb so‘ralsa search_stock chaqir, lekin stock narxlarini yozma. Faqat gul nomi, rangi va bo‘yini qisqa sanab, qaysi guldan yig‘ib beraylik deb so‘ra."
        " Savat custom kerak bo‘lsa get_baskets chaqir; mijoz savat desa mos savat variantini tanlashni so‘ra yoki gul miqdoriga qarab bitta mos savatni taxminan tavsiya qil. Mijoz buket desa savat variantlarini sanama."
        " Post/story/reel context kerak bo‘lsa faqat has_post_context=true bo‘lganda get_post_context chaqir."
        " Katalog ro‘yxati so‘ralganda final catalog_items bo‘sh bo‘lsin, rasm yuborilmaydi. Mijoz aniq tanlaganda yoki rasm so‘raganda catalog_items quantity=1 bo‘lsin."
        " Katalog ro‘yxatida hech qachon qoldiq soni, nechta borligi yoki 'mavjud: 3 dona' kabi matn yozma. Faqat nomi va narxini yoz."
        " Katalog ro‘yxatida tarkib, gul navi, rang, qaysi guldan qancha ketgani yoki '50 ta' kabi sonlarni yozma. Bularni faqat mijoz aniq 'tarkibi nima' yoki 'qaysi guldan qancha ketgan' deb so‘rasa ayt."
        " Katalog ro‘yxatini faqat raqamlangan formatda yoz: '1. Pion buketi - 800 000 so‘m'. Defisli bullet bilan '- Pion buketi' deb boshlama."
        " Katalog ro‘yxatida har bir qatorda narx yonida albatta 'so‘m' yoz: '1. Pion buketi - 800 000 so‘m'. 'Narxlar so‘mda' deb tepada umumiy yozib, qatorda so‘mni tashlab ketma."
        " Katalog ro‘yxati bosqichida ism, telefon, raqam, manzil, sana, vaqt yoki yetkazishni so‘rash taqiqlanadi. Bu bosqichdagi oxirgi savol aynan shu mazmunda bo‘lsin: 'Qaysi biri sizga ma'qul bo‘lsa tanlang, rasmlari bilan ko‘rsataman.' 'Yoki boshqa variant/miqdor kerakmi' deb so‘rama."
        " Custom buket yoki savat suhbatida bir javobda faqat bitta narsani aniqlashtir: avval gul/rang, keyin buketmi yoki savat, keyin miqdor, keyin ism/telefon. Bitta xabarda 3-5 ta savol bermagin."
        " Custom buket/savat uchun narx aytilsa faqat taxminiy deb ayt. Florist haqi 50 000 so‘mdan boshlanishini va obyomga qarab o‘zgarishini ayt. Aniq narxni operator hisoblab berishini yoz."
        " Custom savat hisobida gullar narxi, mos savat va florist haqini taxminiy qo‘shib ayt; keyin 'Aniq narxni operatorimiz hisoblab beradi. Ismingiz va telefon raqamingizni yozib yuborasizmi?' deb so‘ra."
        " Mijoz 'nimaga ism raqam kerak' yoki shunga o‘xshash so‘rasa javob aynan shu mazmunda bo‘lsin: 'Ism va raqamingiz operatorimiz sizga aloqaga chiqib, aniq ma'lumotlarni berishi uchun kerak.'"
        " Mijoz '10 ta atirgul olmoqchiman' kabi yozsa, 'individual', 'paket', 'bog‘lam' kabi keraksiz variantlar o‘ylab topma. Qisqa javob ber: rangini aniqlashtir yoki buket qilib yig‘ib beraylikmi deb so‘ra."
        " Gulni alohida novda sifatida yetkazib berishni taklif qilma; mijoz custom gul so‘rasa buket yoki savat qilib yig‘ishni taklif qil."
        " Mijoz '3ta pochka dan', '3 pochka dan', '3 pochka' desa bu umumiy 3 pochka degani. Mijoz bir nechta buket demaguncha 'har bir buket 3 pochka mi yoki jami 3 pochka mi' deb qayta so‘rama."
        " Coral Charm, pion, atirgul kabi stock gul nomlarini mijoz custom yasatish kontekstida aytsa, katalogga o‘tkazma. Katalog faqat mijoz tayyor buket/katalog/story/post/reel so‘raganda ishlatiladi."
        " Tool natijasida gul topilmasa yoki noaniq bo‘lsa, uzun variantlar sanama. Qisqa yoz: 'Bu gul bo‘yicha aniq ma'lumotni operatorimiz tekshirib beradi. Ismingiz va telefon raqamingizni yozib yuborasizmi?'"
        " Mijoz aniq gul, miqdor, telefon va manzilni yuborgan bo‘lsa, keyingi savol faqat ism bo‘lsin. Mijoz ismini yozsa lead_ready=true qaytar."
        " 'Flarisla', 'floristla', 'floristlar', 'florisla' kabi yozuvlar florist xizmatini bildiradi, gul nomi emas. Bunday savolga 'Ha, floristlarimiz chiroyli qilib yig‘ib beradi' deb qisqa javob ber."
        " Ichki cheklovlarni mijozga yozma: 'so‘ramaymiz', 'taqiqlanadi', 'hozir so‘ramaymiz' kabi iboralarni ishlatma."
        " Mijoz manzil va telefonni yozgan, lekin ismi yo‘q bo‘lsa, faqat ismini so‘ra. Telefonni qayta so‘rama."
        " Yetkazish, sana, vaqt va manzilni mijoz buyurtmani aniq tasdiqlamaguncha so‘rama. Custom jarayonda avval gul/rang/miqdor/buket-savatni aniqlashtir."
        " 'olmaysizmi?' kabi g‘alati savol yozma. 'Yozib yuborasizmi?' yoki 'Tasdiqlaysizmi?' deb yoz."
        " Mijoz custom yasatiladigan gul rasmini so‘rasa va aniq tayyor katalog item tanlanmagan bo‘lsa, qisqa yoz: 'Aynan siz so‘ragan custom buket hali tayyor rasmda yo‘q. Xohlasangiz katalogdagi o‘xshash variant rasmini ko‘rsataman.'"
        " Buyurtma qabul qilinganda uzun invoice yozma. 2-3 qatorda rahmat, buyurtma qisqacha, operator/jamoa bog‘lanishini ayt."
        " Mijozga hech qachon ichki id, ID, catalog_id, batch_id yoki qavs ichidagi raqamli ID yozma. 'catalog id 24', 'id yuboring', '24 deb yozing' kabi gaplar mutlaqo taqiqlanadi."
        " Javobda '—', '•' va qavs belgilarini ishlatma. Ro‘yxat kerak bo‘lsa '1. Gul nomi - Narx: 800 000 so‘m' formatida yoz."
        " Mijoz 'uzur adashib yozdim', 'xato yozdim', 'e'tibor bermang' desa tool chaqirma, qisqa javob ber: 'Hechqisi yo‘q. Davom etamizmi?'"
        " Rasm yuborish faqat send_catalog_image tool orqali qilinadi. Final catalog_items rasm yuborish uchun emas, tanlangan katalogni metadata/lead uchun belgilashga ishlatiladi."
        " Mijoz katalogdagi buket/savat rasmini so‘rasa avval get_catalog orqali aniq item nomini top, keyin send_catalog_image(query=katalog nomi) toolini chaqir. Final javobda 'rasmni yuboraman', 'rasmni yuboraymi', 'rasmini ko‘rsataymi' dema, faqat 'Rasmini yubordim' deb yoz."
        " Chat ichida oldin AI javobi bo‘lsa salomlashma. 'Assalomu', 'Salom', 'Va alaykum' bilan boshlama."
        " Har javobda 'Shu buketdan buyurtma qilmoqchimisiz?' deb so‘rayverma. Rasm/ma'lumot bosqichida 'Yana boshqasini ham ko‘rsataymi?' yetarli."
        " 'Siz yozgan postdagi/storydagi/reeldagi gul' faqat get_post_context natijasida real post bo‘lsa yoziladi. Oddiy katalog tanlovida 'Katalogdagi gul' deb yoz."
        " Agar mijoz faqat salomlashsa, javob aynan shu mazmunda bo‘lsin: 'Assalomu aleykum, EuroFlowers Premium gul do‘koni AI menejeriman. Sizga qanday gul kerak edi?' Katalog, post, story, reel, tayyor variantlar ro‘yxati yoki ichki qoida matnini qo‘shma."
        " Narxlarni vergul bilan emas, probel bilan yoz: 800 000 so‘m."
    )
    instructions = ai_settings.system_prompt + sales_rules + " Final javobni JSON qaytar: reply, detected_language, customer_name, phone, lead_ready, lead_request, arrangement_type, estimated_price, handoff, catalog_items, stock_items. catalog_items ichida catalog_name va quantity yoziladi, hech qachon catalog_id yozilmaydi."
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key)
    response_kwargs = {
        "model": ai_settings.openai_model or settings.OPENAI_MODEL,
        "instructions": instructions + "\nKONTEKST:\n" + json.dumps(context, ensure_ascii=False),
        "input": history,
        "max_output_tokens": 2000,
        "max_tool_calls": 4,
        "tools": ai_tool_definitions(),
        "reasoning": {"effort": "minimal"},
        "text": {"format": {"type": "json_schema", "name": "sales_reply", "strict": True, "schema": ai_response_schema()}},
    }
    response = client.responses.create(**response_kwargs)
    image_tool_results = []
    for _ in range(4):
        function_calls = [item for item in response.output if getattr(item, "type", "") == "function_call"]
        if not function_calls:
            break
        tool_outputs = []
        for call in function_calls:
            arguments = json.loads(call.arguments or "{}")
            output = execute_ai_tool(call.name, arguments, conversation)
            if call.name == "send_catalog_image":
                image_tool_results.append(output)
            tool_outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(output, ensure_ascii=False)})
        response = client.responses.create(
            model=ai_settings.openai_model or settings.OPENAI_MODEL,
            previous_response_id=response.id,
            input=tool_outputs,
            max_output_tokens=2000,
            max_tool_calls=4,
            tools=ai_tool_definitions(),
            reasoning={"effort": "minimal"},
            text={"format": {"type": "json_schema", "name": "sales_reply", "strict": True, "schema": ai_response_schema()}},
        )
    try:
        result = json.loads(response.output_text)
    except json.JSONDecodeError:
        print(f"OPENAI_JSON_DECODE_FAILED conversation={conversation.id} output={response.output_text!r}", flush=True)
        response_kwargs["max_output_tokens"] = 4000
        response = client.responses.create(**response_kwargs)
        result = json.loads(response.output_text)
    result.setdefault("catalog_items", [])
    result.setdefault("stock_items", [])
    if image_tool_results:
        result["image_tool_results"] = image_tool_results
    result["reply"] = clean_catalog_listing_text(normalize_ai_reply_text(result.get("reply", "")))
    if image_tool_results:
        result["reply"] = re.sub(r"rasmni yuboraman|rasmini yuboraman|rasm yuboraman", "Rasmini yubordim", result["reply"], flags=re.IGNORECASE)
    if not result.get("lead_ready") and len(result.get("catalog_items") or []) != 1:
        result["catalog_items"] = []
    if result.get("catalog_items"):
        result["reply"] = remove_image_offer_after_selection(result["reply"])
    if not result.get("catalog_items"):
        result["reply"] = remove_premature_catalog_contact_request(result["reply"])
    result["reply"] = shorten_ai_reply_text(result["reply"])
    return result


def ingest_customer_message(conversation, message_text, instagram_message_id="", metadata=None):
    if instagram_message_id and Message.objects.filter(instagram_message_id=instagram_message_id, conversation=conversation).exists():
        return None
    message = Message.objects.create(conversation=conversation, sender="customer", text=message_text, instagram_message_id=instagram_message_id, metadata=metadata or {})
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=["last_message_at", "updated_at"])
    return message


def recent_catalog_item_for_conversation(conversation):
    for message in conversation.messages.exclude(metadata={}).order_by("-created_at")[:20]:
        for row in (message.metadata or {}).get("catalog_items") or []:
            catalog_id = row.get("catalog_id")
            quantity = int(row.get("quantity") or 0)
            if catalog_id and quantity > 0:
                item = CatalogItem.objects.filter(id=catalog_id).first()
                if item:
                    return item
        media_key = (message.metadata or {}).get("media_image_key") or ""
        match = re.search(r":catalog:(\d+):", media_key)
        if match:
            item = CatalogItem.objects.filter(id=match.group(1)).first()
            if item:
                return item
    return None


def compact_match_text(value):
    return re.sub(r"[^a-zа-я0-9]+", " ", (value or "").lower()).strip()


def catalog_item_from_text(conversation, *values):
    texts = [compact_match_text(value) for value in values if value]
    for text in texts:
        for item in CatalogItem.objects.filter(status="available"):
            name = compact_match_text(item.name_uz)
            if name and name in text:
                return item
            tokens = [token for token in name.split() if token not in {"buketi", "buket", "guldasta", "kompozitsiya"}]
            if len(tokens) >= 2 and all(token in text for token in tokens[:2]):
                return item
    return None


def recent_customer_texts(conversation, limit=6):
    return list(conversation.messages.filter(sender="customer").order_by("-created_at").values_list("text", flat=True)[:limit])


def has_recent_order_intent(conversation):
    text = compact_match_text(" ".join(recent_customer_texts(conversation)))
    patterns = ["olaman", "zakaz", "buyurtma", "qvor", "qilvor", "qilib bering", "qiliber", "rasmiylashtir", "shu kerak", "shuni kerak"]
    return any(pattern in text for pattern in patterns)


def is_catalog_browsing_intent(conversation):
    latest = compact_match_text(recent_customer_texts(conversation, limit=1)[0] if recent_customer_texts(conversation, limit=1) else "")
    patterns = ["korsat", "ko rsat", "koʻrsat", "ko rsating", "rasm", "qanaqa", "qanday", "katalog", "bor", "narxi", "qancha"]
    return any(pattern in latest for pattern in patterns)


def normalize_lead_catalog_items(result, conversation):
    inferred = catalog_item_from_text(conversation, result.get("reply"), recent_customer_texts(conversation, limit=1)[0] if recent_customer_texts(conversation, limit=1) else "", result.get("lead_request"))
    if inferred:
        result["catalog_items"] = [{"catalog_id": inferred.id, "quantity": 1}]
        result["estimated_price"] = float(inferred.price)
        return result["catalog_items"]
    rows = []
    for row in result.get("catalog_items") or []:
        catalog_id = row.get("catalog_id")
        item = CatalogItem.objects.filter(id=catalog_id).first() if catalog_id else None
        if not item and row.get("catalog_name"):
            item = catalog_item_from_text(conversation, row.get("catalog_name"))
        if item:
            rows.append({"catalog_id": item.id, "quantity": max(1, int(row.get("quantity") or 1))})
    if rows:
        result["catalog_items"] = rows
        return rows
    fallback = recent_catalog_item_for_conversation(conversation)
    if fallback:
        rows = [{"catalog_id": fallback.id, "quantity": 1}]
        result["catalog_items"] = rows
        if not result.get("estimated_price"):
            result["estimated_price"] = float(fallback.price)
    return rows


def fallback_lead_request(result, conversation):
    catalog_rows = []
    for row in result.get("catalog_items") or []:
        item = CatalogItem.objects.filter(id=row.get("catalog_id")).first()
        if item:
            catalog_rows.append(f"{item.name_uz} - {int(row.get('quantity') or 1)} dona")
    if catalog_rows:
        return "; ".join(catalog_rows)
    stock_rows = []
    for row in result.get("stock_items") or []:
        batch = StockBatch.objects.select_related("variant__flower").filter(id=row.get("batch_id"), branch=conversation.branch).first()
        quantity = int(row.get("quantity_stems") or 0)
        if batch and quantity > 0:
            stock_rows.append(f"{batch.variant.flower.name_uz} {batch.variant.name_uz} - {quantity} dona")
    if stock_rows:
        return "; ".join(stock_rows)
    return conversation.messages.filter(sender="customer").order_by("-created_at").values_list("text", flat=True).first() or result.get("reply") or "Instagram buyurtma"


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
    request_text_value = (result.get("lead_request") or "").lower()
    direct_stems_request = result.get("arrangement_type") == "stems" and any(word in request_text_value for word in ["dona", "pochka", "gulni dona", "gulni pochka", "gulni o‘zi", "gulni o'zi"])
    if direct_stems_request:
        result["estimated_price"] = None
        result["stock_items"] = []
    if result.get("catalog_items"):
        normalize_lead_catalog_items(result, conversation)
    if result.get("lead_ready") and is_catalog_browsing_intent(conversation) and not has_recent_order_intent(conversation):
        result["lead_ready"] = False
        result["handoff"] = False
        result["lead_request"] = None
    if result.get("lead_ready") and not customer.name:
        result["lead_ready"] = False
        result["reply"] = "Buyurtmani rasmiylashtirish uchun ismingizni yozib yuborasizmi?"
    elif result.get("lead_ready") and not customer.phone:
        result["lead_ready"] = False
        result["phone"] = None
        result["reply"] = "Telefon raqamingizni to‘liq yuborasizmi?\nMasalan: 90 123 45 67"
    if result.get("lead_ready"):
        normalize_lead_catalog_items(result, conversation)
        request_catalog = catalog_item_from_text(conversation, result.get("lead_request"))
        selected_catalog_id = (result.get("catalog_items") or [{}])[0].get("catalog_id")
        if selected_catalog_id and (not request_catalog or request_catalog.id != selected_catalog_id):
            result["lead_request"] = None
        if not result.get("lead_request"):
            result["lead_request"] = fallback_lead_request(result, conversation)
    reply = Message.objects.create(conversation=conversation, sender="ai", text=result["reply"], metadata=result)
    if result.get("lead_ready") and result.get("lead_request"):
        request_text = result["lead_request"]
        request_language = result.get("detected_language", "uz")
        details = {
            "catalog_items": result.get("catalog_items") or [],
            "stock_items": result.get("stock_items") or [],
        }
        lead = Lead.objects.create(
            customer=customer,
            branch=conversation.branch,
            conversation=conversation,
            social_post=conversation.social_post,
            request_uz=request_text if request_language == "uz" else "",
            request_ru=request_text if request_language == "ru" else "",
            arrangement_type=result.get("arrangement_type") or "",
            estimated_price=result.get("estimated_price"),
            details=details,
        )
        for row in result.get("catalog_items") or []:
            catalog_item = CatalogItem.objects.filter(id=row.get("catalog_id")).first()
            quantity = int(row.get("quantity") or 1)
            if catalog_item and quantity > 0:
                LeadCatalogUsage.objects.create(lead=lead, catalog_item=catalog_item, quantity=quantity)
        for row in result.get("stock_items") or []:
            batch = StockBatch.objects.filter(id=row.get("batch_id"), branch=conversation.branch).first()
            quantity_stems = int(row.get("quantity_stems") or 0)
            if batch and quantity_stems > 0:
                LeadStockUsage.objects.create(lead=lead, stock_batch=batch, quantity_stems=quantity_stems, quantity_bunches=Decimal(str(row.get("quantity_bunches") or 0)))
        Notification.objects.create(branch=conversation.branch, notification_type="lead", title_uz=f"Yangi lead: {customer}", title_ru=f"Новый лид: {customer}", body_uz=request_text, body_ru=request_text, reference_type="lead", reference_id=lead.id)
    if result.get("handoff"):
        Notification.objects.create(branch=conversation.branch, notification_type="handoff", title_uz=f"Operator aloqasi kerak: {customer}", title_ru=f"Нужна связь оператора: {customer}", body_uz=result.get("lead_request") or result.get("reply", ""), body_ru=result.get("lead_request") or result.get("reply", ""), reference_type="conversation", reference_id=conversation.id)
    return reply


def process_customer_message(conversation, message_text, instagram_message_id=""):
    message = ingest_customer_message(conversation, message_text, instagram_message_id)
    if not message:
        return None
    return create_ai_reply_for_conversation(conversation)


def should_start_ai_reply(conversation_id, expected_message_id):
    conversation = Conversation.objects.filter(id=conversation_id).first()
    if not conversation:
        return False
    if conversation.status == "closed":
        return False
    if conversation.ai_paused_until and conversation.ai_paused_until > timezone.now():
        return False
    latest = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    if not latest or latest.id != expected_message_id:
        return False
    if conversation.ai_replied_to_message_id == latest.id:
        return False
    return True


def process_pending_customer_reply(conversation_id, expected_message_id):
    stale_started_at = timezone.now() - timedelta(seconds=120)
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().filter(id=conversation_id).first()
        if not conversation:
            return None
        latest = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
        if not latest or latest.id != expected_message_id:
            return None
        if conversation.ai_replied_to_message_id == latest.id:
            return None
        if conversation.ai_reply_started_for_message_id == latest.id and conversation.ai_reply_started_at and conversation.ai_reply_started_at > stale_started_at:
            return None
        conversation.ai_reply_started_for_message = latest
        conversation.ai_reply_started_at = timezone.now()
        conversation.save(update_fields=["ai_reply_started_for_message", "ai_reply_started_at", "updated_at"])
    try:
        conversation = Conversation.objects.select_related("customer", "branch", "social_post").get(id=conversation_id)
        reply = create_ai_reply_for_conversation(conversation)
    except Exception:
        Conversation.objects.filter(id=conversation_id, ai_reply_started_for_message_id=expected_message_id).update(ai_reply_started_for_message=None, ai_reply_started_at=None)
        raise
    if reply:
        Conversation.objects.filter(id=conversation_id, ai_reply_started_for_message_id=expected_message_id).update(ai_replied_to_message_id=expected_message_id, ai_reply_started_for_message=None, ai_reply_started_at=None)
    else:
        Conversation.objects.filter(id=conversation_id, ai_reply_started_for_message_id=expected_message_id).update(ai_reply_started_for_message=None, ai_reply_started_at=None)
    return reply


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


def urls_from_value(value):
    urls = []
    if isinstance(value, str):
        urls.extend(re.findall(r"https?://[^\s\"'<>]+", value))
    elif isinstance(value, dict):
        for child in value.values():
            urls.extend(urls_from_value(child))
    elif isinstance(value, list):
        for child in value:
            urls.extend(urls_from_value(child))
    return urls


def attachment_kind(source, attachment_type, url):
    text = f"{source} {attachment_type} {url}".lower()
    if "voice" in text or "audio" in text:
        return "voice"
    if "story" in text:
        return "story"
    if "reel" in text:
        return "reel"
    if "post" in text or "media" in text or "instagram.com/p/" in text:
        return "post"
    return "media"


def attachment_label(kind):
    return {
        "story": "Story link",
        "post": "Post link",
        "reel": "Reel link",
        "voice": "Voice message",
        "media": "Media link",
    }.get(kind, "Media link")


def unique_attachment_rows(rows):
    result = []
    seen = set()
    for row in rows:
        url = row.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(row)
    return result


def append_attachment_links(text, attachments):
    base = (text or "").strip()
    lines = [base] if base else []
    for row in attachments:
        url = row.get("url")
        if url:
            lines.append(f"{attachment_label(row.get('kind'))}: {url}")
    return "\n".join(lines)


def first_string_from_keys(data, keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def social_post_media_query(media_id):
    return Q(media_id=media_id) | Q(story_share_id=media_id) | Q(webhook_story_id=media_id) | Q(webhook_story_id__contains=media_id)


def append_story_webhook_id(post, story_id):
    if not story_id:
        return False
    values = [value.strip() for value in (post.webhook_story_id or "").splitlines() if value.strip()]
    if story_id in values:
        return False
    values.append(story_id)
    post.webhook_story_id = "\n".join(values)
    return True


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


def instagram_message_metadata(event, webhook_event=None):
    message = event.get("message", {}) or {}
    rows = []
    for attachment in message.get("attachments", []) or []:
        attachment_type = attachment.get("type", "")
        payload = attachment.get("payload", {}) or {}
        for url in urls_from_value(payload):
            rows.append({"kind": attachment_kind("instagram_attachment", attachment_type, url), "type": attachment_type, "url": url, "source": "instagram_attachment"})
    for source, value in [
        ("instagram_message", message),
        ("instagram_referral", event.get("referral") or message.get("referral") or {}),
        ("instagram_reply_to", message.get("reply_to") or {}),
    ]:
        for url in urls_from_value(value):
            rows.append({"kind": attachment_kind(source, "", url), "type": "", "url": url, "source": source})
    if webhook_event and webhook_event.story_url:
        rows.append({"kind": attachment_kind("instagram_webhook_event", webhook_event.event_type, webhook_event.story_url), "type": webhook_event.event_type, "url": webhook_event.story_url, "source": "instagram_webhook_event"})
    return {"attachments": unique_attachment_rows(rows)}


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
    exact = SocialPost.objects.filter(social_post_media_query(story_id), is_active=True).first()
    if exact:
        updates = []
        if append_story_webhook_id(exact, story_id):
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
        exact = SocialPost.objects.filter(media_id=media_id, is_active=True).first()
        if exact:
            return exact
        media = find_media_by_id(media_id)
        if media:
            exact = SocialPost.objects.filter(permalink=media.get("permalink", ""), is_active=True).first()
            if exact:
                if exact.media_id != media_id:
                    exact.media_id = media_id
                    exact.save(update_fields=["media_id", "updated_at"])
                return exact
    if webhook_event.story_url:
        normalized = normalize_instagram_permalink(webhook_event.story_url)
        exact = SocialPost.objects.filter(permalink__startswith=normalized, is_active=True).first()
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
            message_metadata = instagram_message_metadata(event, webhook_event)
            message_text = append_attachment_links(text or story_text or media_text, message_metadata.get("attachments", []))
            if not sender_id or sender_id in own_ids or not message_text or message.get("is_echo"):
                continue
            branch = getattr(SocialPost.objects.filter(is_active=True).first(), "branch", None)
            if not branch:
                continue
            referral = event.get("referral") or message.get("referral") or {}
            media_id = referral.get("media_id") or referral.get("source_id") or (webhook_event.story_id if webhook_event else "") or (webhook_event.media_id if webhook_event else "")
            post = SocialPost.objects.filter(social_post_media_query(media_id), is_active=True).first() if media_id else None
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
                conversation.branch = post.branch
                conversation.save(update_fields=["social_post", "branch", "updated_at"])
            elif (story_attachment or media_attachment or message_metadata.get("attachments")) and not post and conversation.social_post_id:
                conversation.social_post = None
                conversation.save(update_fields=["social_post", "updated_at"])
            if (story_attachment or media_attachment or message_metadata.get("attachments")) and not post:
                message_text = append_attachment_links(f"{message_text}\nTizim izohi: yuborilgan Instagram media bazadagi story/post/reel katalogiga bog‘lanmagan.", [])
            saved_message = ingest_customer_message(conversation, message_text, message.get("mid", ""), message_metadata)
            if saved_message:
                results.append({"conversation_id": conversation.id, "message_id": saved_message.id, "recipient_id": sender_id})
    return results


def telegram_media_file_id(message):
    if message.get("voice"):
        return "voice", message["voice"].get("file_id", "")
    if message.get("audio"):
        return "audio", message["audio"].get("file_id", "")
    if message.get("video"):
        return "video", message["video"].get("file_id", "")
    if message.get("video_note"):
        return "video_note", message["video_note"].get("file_id", "")
    if message.get("document"):
        return "document", message["document"].get("file_id", "")
    if message.get("photo"):
        photo = sorted(message["photo"], key=lambda row: row.get("file_size", 0))[-1]
        return "photo", photo.get("file_id", "")
    return "", ""


def telegram_message_metadata(message):
    rows = []
    for url in urls_from_value(message):
        rows.append({"kind": attachment_kind("telegram_message", "", url), "type": "", "url": url, "source": "telegram_message"})
    media_type, file_id = telegram_media_file_id(message)
    if file_id:
        row = {"kind": attachment_kind("telegram_file", media_type, ""), "type": media_type, "file_id": file_id, "source": "telegram_file"}
        try:
            file_url = telegram_file_url(file_id)
        except Exception as exc:
            print(f"TELEGRAM_FILE_URL_FAILED file_id={file_id} error={exc}", flush=True)
            file_url = ""
        if file_url:
            row["url"] = file_url
            rows.append(row)
        else:
            rows.append(row)
    return {"attachments": unique_attachment_rows(rows)}


def resolve_telegram_update(payload):
    message = payload.get("message") or payload.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    metadata = telegram_message_metadata(message)
    message_text = append_attachment_links(text, metadata.get("attachments", []))
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    if not chat_id or not user_id or not message_text:
        return []
    branch = getattr(SocialPost.objects.filter(is_active=True).first(), "branch", None) or Branch.objects.filter(is_active=True).first() or Branch.objects.first()
    if not branch:
        return []
    external_id = f"telegram:{user_id}"
    defaults = {"branch": branch, "language": "uz"}
    full_name = " ".join(part for part in [user.get("first_name", ""), user.get("last_name", "")] if part).strip()
    if full_name:
        defaults["name"] = full_name[:160]
    customer, created = Customer.objects.get_or_create(instagram_user_id=external_id, defaults=defaults)
    if not created and full_name and not customer.name:
        customer.name = full_name[:160]
        customer.save(update_fields=["name", "updated_at"])
    conversation = Conversation.objects.filter(customer=customer, status__in=["ai", "operator"]).first()
    if not conversation:
        conversation = Conversation.objects.create(customer=customer, branch=customer.branch or branch)
    message_id = message.get("message_id", "")
    saved_message = ingest_customer_message(conversation, message_text, f"telegram:{chat_id}:{message_id}", metadata)
    if not saved_message:
        return []
    return [{"conversation_id": conversation.id, "message_id": saved_message.id, "chat_id": chat_id}]
