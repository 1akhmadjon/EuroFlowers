import json
import re
import requests
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
        catalog_ids = [row.get("catalog_id") for row in reply.metadata.get("catalog_items", []) if row.get("catalog_id")]
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
    marker = f"instagram_image_sent:{image['source']}:{image['image_url']}"
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


def telegram_send_image(chat_id, image_url):
    return telegram_api("sendPhoto", {"chat_id": chat_id, "photo": image_url})


def send_telegram_context_image(chat_id, conversation, reply=None):
    image = catalog_image_for_conversation(conversation, reply)
    if not image:
        return None
    marker = f"telegram_image_sent:{image['source']}:{image['image_url']}"
    if Message.objects.filter(conversation=conversation, sender="system", metadata__media_image_key=marker).exists():
        return None
    result = telegram_send_image(chat_id, image["image_url"])
    Message.objects.create(conversation=conversation, sender="system", text="Telegram image sent", metadata={"media_image_key": marker, "image_url": image["image_url"], "result": result})
    return result


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


def ai_reply(conversation):
    customer = conversation.customer
    branch = conversation.branch
    stock = StockBatch.objects.filter(branch=branch, is_active=True, remaining_stems__gt=0).select_related("variant__flower").order_by("variant__flower__name_uz", "variant__color_uz", "-remaining_stems")[:120]
    catalog = CatalogItem.objects.filter(branch=branch, status="available").select_related("social_post").prefetch_related("composition__stock_batch__variant__flower").order_by("-created_at")[:24]
    baskets = Packaging.objects.filter(branch=branch, packaging_type="basket", is_active=True, quantity__gt=0).order_by("sale_price")[:20]
    visible_messages = list(conversation.messages.exclude(sender="system").order_by("-created_at", "-id")[:24])
    session_messages = []
    fresh_session = False
    for index, message in enumerate(visible_messages):
        if index and session_messages[-1].created_at - message.created_at >= timedelta(hours=24):
            fresh_session = True
            break
        session_messages.append(message)
    history_messages = list(reversed(session_messages))
    history = [{"role": "user" if m.sender == "customer" else "assistant", "content": m.text} for m in history_messages]
    ai_replies_count = sum(1 for message in history_messages if message.sender == "ai")
    has_ai_reply_in_session = ai_replies_count > 0
    last_customer_message = next((message.text for message in reversed(history_messages) if message.sender == "customer"), "")
    business_settings, _ = BusinessSettings.objects.get_or_create(pk=1)
    ai_settings, _ = AISettings.objects.get_or_create(pk=1)
    context = {
        "customer": {"name": customer.name, "phone": customer.masked_phone, "has_phone": bool(customer.phone), "language": customer.language},
        "conversation": {"fresh_session": fresh_session, "has_ai_reply_in_session": has_ai_reply_in_session, "ai_replies_count": ai_replies_count, "last_customer_message": last_customer_message},
        "recent_orders": recent_customer_orders(customer),
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
        "catalog": [{
            "id": row.id,
            "name_uz": row.name_uz,
            "name_ru": row.name_ru,
            "type": row.arrangement_type,
            "price": str(row.price),
            "quantity_available": max(row.quantity_total - row.quantity_sold, 0),
            "has_image": bool(row.image_url or (row.social_post.image_url if row.social_post_id else "")),
            "composition": catalog_composition_summary(row),
        } for row in catalog],
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
        post_catalog = CatalogItem.objects.filter(social_post=post, status="available").prefetch_related("composition__stock_batch__variant__flower")
        context["post"] = {
            "type": post.post_type,
            "title_uz": post.title_uz,
            "title_ru": post.title_ru,
            "description_uz": post.description_uz,
            "description_ru": post.description_ru,
            "price": str(post.price or ""),
            "catalog": [{"id": row.id, "name_uz": row.name_uz, "name_ru": row.name_ru, "type": row.arrangement_type, "price": str(row.price), "quantity_available": max(row.quantity_total - row.quantity_sold, 0), "composition": catalog_composition_summary(row)} for row in post_catalog],
        }
    sales_rules = (
        " Qat'iy til qoidasi: mijoz o‘zbek lotinida yozsa o‘zbek lotinida javob ber. Mijoz o‘zbek kirill harflarida yozsa o‘zbek kirill harflarida javob ber. Mijoz aniq rus tilida yozsa rus tilida javob ber. Mijoz ingliz tilida yozsa ham ingliz tilida javob berma; o‘zbek lotinida qisqa javob ber: 'Men faqat o‘zbek va rus tillarida yordam bera olaman 🌸 O‘zbek tilida davom etamizmi?' Inglizcha gap va so‘zlarni aralashtirma."
        " Gulga aloqasi yo‘q savollarga umuman javob berma: kod yozish, dasturlash, siyosat, din, boshqa biznes, umumiy maslahat, hazil yoki boshqa mavzularda savol kelsa, savolni bajarma. Qisqa javob ber: 'Men faqat EuroFlowers gullari, buket va savatlar bo‘yicha yordam bera olaman 🌸 Sizga buket yoki savat kerakmi?' Kod, retsept, matn, reja yoki boshqa ishni yozib berma."
        " Salomlashish qoidasi: conversation.has_ai_reply_in_session=false bo‘lsa va mijoz salomlashsa yoki yangi session boshlansa, bir marta salomlash. conversation.has_ai_reply_in_session=true bo‘lsa hech qachon 'Assalomu alaykum', 'Assalomu aleykum', 'Va alaykum assalom', 'Salom' deb boshlama; bevosita mijozning oxirgi savoliga javob ber."
        " Format qoidasi: javobda hech bir qatorni probel bilan boshlama. Bullet ishlatsang har qator to‘g‘ridan-to‘g‘ri '•' bilan boshlansin. '  Narx:' kabi oldida space bor qator yozma. Instagram uchun text plain bo‘lsin, markdown ishlatma."
        " Gul variantlarini taklif qilganda sarlavha bilan yoz: masalan 'Bizda bor Gortenziyalar:' yoki 'Hozir mavjud atirgullar:'. Keyin variantlarni bullet bilan ber."
        " EuroFlowersda gulning o‘zi dona yoki pochka holida odatda sotilmaydi. Mijoz dona, nechta dona gul, pochka, 1 pochka, 3 pochka, gulni o‘zini olish, faqat atirgul kerak kabi so‘rasa, dona/pochka narxini aytma va hisoblama. Javob mazmuni shunday bo‘lsin: 'Bizda gulning o‘zi dona yoki pochka holida ko‘p hollarda sotilmaydi. Gulni buket qilib yoki savatga yasatib olishingiz mumkin. Ismingiz va telefon raqamingizni qoldirsangiz, operatorimiz aniq ma'lumot berib aloqaga chiqadi.'"
        " Mijoz dona yoki pochka holida gul olmoqchi bo‘lsa, lead uchun ism va telefonni yig‘. Lead_request ichida aniq xulosa yoz: 'Mijoz gulni dona/pochka holida olmoqchi, operator aniqlashtirishi kerak.' Arrangement_type stems bo‘lsin, estimated_price null bo‘lsin, stock_items bo‘sh bo‘lsin."
        " Story/post/reel/katalogdagi tayyor buket, savat yoki kompozitsiya narxi aniq hisoblanadi. Bunday tayyor gullarda hech qachon 'taxminan', 'taxminiy', 'taxminan narx' demagin. 'Narx: 800 000 so‘m' deb yoz."
        " 'Taxminan' so‘zini faqat mijoz gulni yangidan buket/savat qilib yeg‘dirayotganda yoki custom hisob-kitobda ishlat: 'Jami taxminan: ... so‘m'."
        " Gul variantini taklif qilganda dona yoki pochka narxini yozma. Faqat buket yoki savat qilib yig‘dirish mumkinligini ayt va qaysi format kerakligini so‘ra."
        " Mijoz 'yasab berasizmi', 'yasabam beraslami', 'yasab beraslami', 'yasatmoqchiman', 'yasatmoxchiman', 'o‘zim yasatmoqchiman', 'ozim yasatmoxchiman', 'yig‘diraman', 'yigdirmoqchiman' desa bu custom buket/savat yig‘dirish niyati. Buni gulning o‘zini dona/pochka holida sotib olish deb tushunma. Bunday holatda katalogdagi tayyor buketlarni taklif qilma; 'Ha, albatta, xohishingizga qarab buket yoki savat qilib yasab beramiz' mazmunida javob berib, qaysi guldan, qanday rangda va buketmi yoki savatmi ekanini bitta savol bilan aniqlashtir."
        " Mijoz 'tayyor buket kerakmas', 'tayyor kerak emas', 'o‘zim yasatmoqchiman', 'ozim yasatmoxchiman' desa katalog ro‘yxatini qayta yuborma. Faqat custom yig‘ish oqimida davom et."
        " Mijoz 'skladda qanaqa gul bor', 'qanaqa gulla bor sklada', 'gul turlari bormi' desa bu custom yig‘ish uchun mavjud gul turlarini so‘rayapti. Katalogdagi tayyor buketlarni emas, stock kontekstdagi gul turlarini qisqa ro‘yxat qilib ber va qaysi guldan buket yoki savat kerakligini so‘ra."
        " Mijoz 'tayyor buketlayam sotaslami yoki savatga yasalgan tayyor gulla', 'tayyor buket bormi', 'tayyor savat bormi', 'tayyor gulla bormi' desa bu ha/yo‘q savol emas, katalog so‘rovi. Javob: 'Ha, tayyor buket va savatdagi kompozitsiyalarimiz bor' mazmunida boshlansin va catalog kontekstdagi mavjud variantlarni nomi, turi, narxi bilan ro‘yxat qil. Bunday savolga 'Sizga buketmi yoki savatmi kerak?' deb qayta savol berma."
        " Mijoz 'ha' deb javob bersa, oldingi AI savolini historydan tushun. Agar oldingi savol katalog variantlari haqida bo‘lsa, variantlarni ko‘rsat yoki tanlashni so‘ra; hech qachon aynan bir xil savolni takrorlama."
        " Agar mijoz story/post/reelni sent qilib yoki reply qilib 'shu', 'shundan kerak', 'narxi qancha' desa, 'Sizga qanday gul yoki buket kerak edi?' demagin. 'Bugungi tayyor variantlardan' deb boshlama. Story bo‘lsa 'Siz yozgan storydagi gul:', post bo‘lsa 'Siz yuborgan postdagi gul:', reel bo‘lsa 'Siz yuborgan reeldagi gul:' deb yoz."
        " Agar mijoz yuborgan story/post/reel linki tizim izohida bazadan topilmadi deb kelsa yoki conversation.post bo‘sh bo‘lsa, oldingi post/reel/story yoki boshqa katalog gulini ishlatma. Javob ber: 'Bu yuborgan media bo‘yicha tizimda aniq ma'lumot topilmadi. Iltimos, qaysi gul ekanini yozib yuboring yoki ism-raqamingizni qoldiring, operatorimiz aniqlashtirib bog‘lanadi.'"
        " Agar conversation contextida post mavjud bo‘lsa va mijoz 'bo‘yi nechchi', 'narxi qancha', 'bormi', 'qoldimi', 'shu gul', 'shu buket' kabi noaniq savol bersa, albatta o‘sha post/story/reeldagi katalog gulini nazarda tutyapti deb qabul qil. Bunday holatda 'Qaysi gulni nazarda tutyapsiz?' deb so‘rama."
        " Mijoz story/post/reeldagi gul bo‘yini so‘rasa, catalog height_cm va compositiondagi gul bo‘yidan javob ber. Ma'lumot contextda bo‘lsa umumiy gul turini aniqlashtirishga qaytma."
        " Javobda arrangement_type enum qiymatlarini inglizcha yozma: 'bouquet' emas 'buket', 'basket' emas 'savat', 'stems' emas 'gulning o‘zi' deb yoz. Lekin mijozga 'gulning o‘zi sotiladi' degan ma'noda yozma, chunki gulning o‘zi dona/pochka holida odatda sotilmaydi."
        " 'Qabul qilamizmi?', 'davom ettiraymi?' kabi g‘alati yoki noaniq savollar yozma. Tayyor buket/savatni taklif qilganda oxirida tabiiy savol ber: 'Shu buketdan buyurtma qilmoqchimisiz?' yoki 'Shu savatdan nechta kerak bo‘ladi?'"
        " 'Operator bilan muqobil yechim qilamizmi?', 'muqobil yechim qilamizmi?', 'operator bilan hal qilamizmi?' kabi g‘alati iboralarni yozma. Operator kerak bo‘lsa tabiiy yoz: 'Ismingiz va telefon raqamingizni qoldirsangiz, operatorimiz aniq ma'lumot berib aloqaga chiqadi.'"
        " 'Sizga buketmi yoki savatdagi tayyor kompozitsiyami kerak?', 'Ajoyib! Sizga buketmi yoki savatdagi tayyor kompozitsiya kerakligini aniqlasak?', 'Ajoyib, buketga qaror qilganingiz uchun rahmat', 'Aniq narxni operator tasdiqlaydi' iboralarini yozma."
        " Story/post/reel/katalogdagi tayyor gul haqida javob berganda katalog item ichidagi nechta dona gul ketganini yoki post flower_countni mijoz so‘ramasa yozma. Faqat nomi, buket/savat turi, narxi va katalogda nechta borligini ayt."
        " Agar mijoz tayyor katalog buketiga nechta gul ketganini so‘rasa, catalog composition ma'lumotidan javob ber. Composition mavjud bo‘lsa 'katalogda ko‘rsatilmagan' demagin."
        " Mijoz arzonlashtirish, skidka, chegirma, savdolashish yoki narxni tushirishni so‘rasa, chegirma va'da qilma va foiz aytma. Javob mazmuni shunday bo‘lsin: 'Hurmatli mijoz, bizning narxlarimiz shahardagi ko‘p gul do‘konlarga nisbatan ancha qulay. Chegirma yoki yakuniy narx masalasini operatorimiz bilan gaplashib ko‘rsangiz bo‘ladi 😊' Keyin ism va telefon raqamini so‘rab, lead_ready uchun kerakli ma'lumotlarni yig‘."
        " recent_orders faqat mijozning eski buyurtmalari haqida ma'lumot berish uchun. Mijoz 'oldingi zakazim nima edi', 'oxirgi nima olgandim' desa shu ro‘yxatdan javob ber. Eski buyurtmani yangi lead deb yaratma, mijoz 'yana shundan olaman' yoki yangi buyurtmani aniq tasdiqlamaguncha lead_ready=false bo‘lsin."
        " Agar conversation.fresh_session=true bo‘lsa va conversation.has_ai_reply_in_session=false bo‘lsa, mijoz ertasi kuni yoki uzoq tanaffusdan keyin yozgan bo‘ladi: salomlashuvdan boshlagin, oldingi suhbatdagi savollarni yoki takliflarni mijoz o‘zi eslatmasa eslatma. recent_ordersni ham faqat mijoz o‘zi oldingi zakaz haqida so‘rasa ishlat."
        " Mijoz 'qanaqa tayyor gullar bor', 'katalog bormi', 'tayyor buketlar' desa rasm yuborishni so‘rama va har bir rasmni alohida tavsiflama. Catalog kontekstdagi barcha available gullarni nomi, turi, narxi, qoldiq soni bilan qisqa ro‘yxat qil. Oxirida 'Qaysi biri qiziq bo‘lsa, tanlang, rasmini ko‘rsataman' degan mazmunda bitta savol ber."
        " Mijoz katalog ro‘yxatidan birini tanlasa yoki story/post/reeldagi tayyor gulni olmoqchi bo‘lsa, catalog_items arrayga catalog id va quantity yoz. Bir nechta tayyor buket/savat olsa ham hammasini catalog_itemsga yoz."
        " Mijoz bir nechta tayyor katalog gullarni ko‘rib chiqqan bo‘lsa va oxirida aniq qaysini olishi noma'lum bo‘lsa, ism/telefon so‘rama. Avval 'Sizga qaysi biri yoqdi, qaysi guldan buyurtma qilamiz?' deb aniqlashtir."
        " Mijoz custom buket yoki savat yig‘dirsa, stock_items arrayga batch_id, quantity_stems va quantity_bunches yoz. Bir nechta buket/savat bo‘lsa lead_request ichida har birining soni va tarkibi alohida aniq yozilsin."
        " Mijoz hali 'olaman', 'rasmiylashtiring', 'zakaz qilaman', 'shu kerak' demagan bo‘lsa ism yoki telefon so‘rama va lead_ready=false qaytar. Avval ehtiyoj turini aniqlashtir: 'Sizga buket qilib beraylikmi yoki savatga yig‘amizmi?' kabi bitta chiroyli savol ber. 'Gulning o‘zini olmoqchimisiz?' deb so‘rama."
        " CRM lead yaratishda arrangement_type aniq bo‘lsin: buket bo‘lsa bouquet, savat bo‘lsa basket, gulning o‘zi/donalab bo‘lsa stems, tayyor katalog guli bo‘lsa catalog. Tur aniq bo‘lmasa lead yaratma."
        " Mijoz buket yoki savat tanlasa, javobda florist xizmatini alohida ayt: 'Florist xizmati 50 000 so‘mdan boshlanadi, gul hajmi va bezagiga qarab o‘zgaradi.'"
        " Story, reel, post yoki katalogdagi tayyor buket/kompozitsiya haqida so‘ralsa florist xizmatini alohida aytma va narxga qo‘shma, chunki ular tayyor yasalgan sotuvdagi gullar."
        " Tayyor katalog/post/story/reel narxida 'operator narxni tasdiqlaydi', 'aniq narxni operator tasdiqlaydi' dema. Bu narxlar aniq ko‘rsatiladi. Operator faqat buyurtma tafsilotlari, vaqt, yetkazish va mavjudlikni yakuniy kelishadi."
        " Lead yaratish uchun JSON estimated_price qiymatida florist xizmatini alohida qo‘shib yuborma; tizim operator sotildi qilganda florist_fee maydonida yuritadi."
    )
    final_rules = (
        " ENG MUHIM SO‘NGGI QOIDALAR: Agar conversation.ai_replies_count > 0 bo‘lsa yoki input history ichida assistant/ai xabari bor bo‘lsa, javobni salomlashuv bilan boshlash mutlaqo taqiqlanadi. "
        "Bunday holatda birinchi so‘z 'Ha', 'Albatta', 'Tushunarli', 'Mayli' yoki bevosita javob bo‘lishi mumkin, lekin 'Assalomu', 'Salom', 'Va alaykum' bo‘lmasin. "
        "Mijoz yasab berish/yasatish haqida so‘rasa, bu custom buket yoki savat xizmati; tayyor katalog taklif qilma va gulning o‘zi sotilmaydi qoidasi bilan adashtirma. "
        "Mijoz tayyor buket kerakmas desa katalogni takrorlama. Tayyor katalog narxida operator narxni tasdiqlaydi demagin."
    )
    instructions = ai_settings.system_prompt + sales_rules + final_rules + " Javobni JSON qaytaring: reply matni, detected_language uz yoki ru, customer_name, phone, lead_ready boolean, lead_request, arrangement_type bouquet/basket/stems/catalog yoki bo‘sh, estimated_price raqam yoki null, handoff boolean, catalog_items array, stock_items array."
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key)
    response_kwargs = {
        "model": ai_settings.openai_model or settings.OPENAI_MODEL,
        "instructions": instructions + "\nKONTEKST:\n" + json.dumps(context, ensure_ascii=False),
        "input": history,
        "max_output_tokens": 2000,
        "reasoning": {"effort": "minimal"},
        "text": {"format": {"type": "json_schema", "name": "sales_reply", "strict": True, "schema": {
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
                "catalog_items": {"type": "array", "items": {"type": "object", "properties": {"catalog_id": {"type": "integer"}, "quantity": {"type": "integer"}}, "required": ["catalog_id", "quantity"], "additionalProperties": False}},
                "stock_items": {"type": "array", "items": {"type": "object", "properties": {"batch_id": {"type": "integer"}, "quantity_stems": {"type": "integer"}, "quantity_bunches": {"type": "number"}}, "required": ["batch_id", "quantity_stems", "quantity_bunches"], "additionalProperties": False}}
            },
            "required": ["reply", "detected_language", "customer_name", "phone", "lead_ready", "lead_request", "arrangement_type", "estimated_price", "handoff", "catalog_items", "stock_items"],
            "additionalProperties": False
        }}},
    }
    response = client.responses.create(**response_kwargs)
    try:
        result = json.loads(response.output_text)
    except json.JSONDecodeError:
        print(f"OPENAI_JSON_DECODE_FAILED conversation={conversation.id} output={response.output_text!r}", flush=True)
        response_kwargs["max_output_tokens"] = 4000
        response = client.responses.create(**response_kwargs)
        result = json.loads(response.output_text)
    result.setdefault("catalog_items", [])
    result.setdefault("stock_items", [])
    return result


def ingest_customer_message(conversation, message_text, instagram_message_id="", metadata=None):
    if instagram_message_id and Message.objects.filter(instagram_message_id=instagram_message_id, conversation=conversation).exists():
        return None
    message = Message.objects.create(conversation=conversation, sender="customer", text=message_text, instagram_message_id=instagram_message_id, metadata=metadata or {})
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
    request_text_value = (result.get("lead_request") or "").lower()
    direct_stems_request = result.get("arrangement_type") == "stems" and any(word in request_text_value for word in ["dona", "pochka", "gulni dona", "gulni pochka", "gulni o‘zi", "gulni o'zi"])
    if direct_stems_request:
        result["estimated_price"] = None
        result["stock_items"] = []
    if result.get("lead_ready") and not customer.name:
        result["lead_ready"] = False
        result["reply"] = "Buyurtmani rasmiylashtirish uchun ismingizni yozib yuborasizmi?"
    elif result.get("lead_ready") and not customer.phone:
        result["lead_ready"] = False
        result["phone"] = None
        result["reply"] = "Telefon raqamingizni to‘liq yuborasizmi?\nMasalan: 90 123 45 67"
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
            catalog_item = CatalogItem.objects.filter(id=row.get("catalog_id"), branch=conversation.branch).first()
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
