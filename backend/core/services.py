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


def instagram_catalog_image_for_conversation(conversation, reply=None):
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
    image = instagram_catalog_image_for_conversation(conversation, reply)
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


def telegram_api(method, payload):
    token = telegram_bot_token()
    if not token:
        return {"mocked": True}
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


def telegram_send(chat_id, text):
    return telegram_api("sendMessage", {"chat_id": chat_id, "text": text})


def telegram_sender_action(chat_id, action="typing"):
    return telegram_api("sendChatAction", {"chat_id": chat_id, "action": action})


def catalog_composition_summary(item):
    rows = []
    for row in item.composition.select_related("stock_batch__variant__flower"):
        batch = row.stock_batch
        name = f"{batch.variant.flower.name_uz} {batch.variant.name_uz} {batch.variant.color_uz}".strip()
        rows.append({"name_uz": name, "quantity_stems": row.quantity_stems, "quantity_bunches": str(row.quantity_bunches)})
    return rows


def ai_reply(conversation):
    customer = conversation.customer
    branch = conversation.branch
    stock = StockBatch.objects.filter(branch=branch, is_active=True, remaining_stems__gt=0).select_related("variant__flower")
    catalog = CatalogItem.objects.filter(branch=branch, status="available").select_related("social_post").prefetch_related("composition__stock_batch__variant__flower")[:30]
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
        " Qat'iy qoida: mijoz o‘zbek tilida, hatto kirill yozuvida yozsa ham javobni o‘zbek lotinida yoz, ruscha so‘z aralashtirma. Faqat mijoz aniq rus tilida yozsa rus tilida javob ber."
        " Format qoidasi: javobda hech bir qatorni probel bilan boshlama. Bullet ishlatsang har qator to‘g‘ridan-to‘g‘ri '•' bilan boshlansin. '  Narx:' kabi oldida space bor qator yozma. Instagram uchun text plain bo‘lsin, markdown ishlatma."
        " Gul variantlarini taklif qilganda sarlavha bilan yoz: masalan 'Bizda bor Gortenziyalar:' yoki 'Hozir mavjud atirgullar:'. Keyin variantlarni bullet bilan ber."
        " Dona narxida 'taxminan' so‘zini ishlatma: 'Dona narxi: 105 000 so‘m' deb yoz. Mijoz so‘ragan miqdor yoki buket/savat jami narxida 'Jami taxminan: ... so‘m' deb yozish mumkin. 'taxminan' so‘zini bitta javobda ko‘pi bilan 1 marta ishlat."
        " Gul variantini taklif qilganda dona narxini ham yoz: masalan 'Bizda bor Gortenziyalar:\\n• Premium Blue — moviy, 50 cm\\nDona narxi: 105 000 so‘m\\n10 dona jami taxminan: 1 050 000 so‘m'."
        " Agar mijoz story/post/reelni sent qilib yoki reply qilib 'shu', 'shundan kerak', 'narxi qancha' desa, 'Sizga qanday gul yoki buket kerak edi?' demagin. 'Bugungi tayyor variantlardan' deb boshlama. Story bo‘lsa 'Siz yozgan storydagi gul:', post bo‘lsa 'Siz yuborgan postdagi gul:', reel bo‘lsa 'Siz yuborgan reeldagi gul:' deb yoz."
        " 'Qabul qilamizmi?', 'davom ettiraymi?' kabi g‘alati yoki noaniq savollar yozma. Tayyor buket/savatni taklif qilganda oxirida tabiiy savol ber: 'Shu buketdan buyurtma qilmoqchimisiz?' yoki 'Shu savatdan nechta kerak bo‘ladi?'"
        " Story/post/reel/katalogdagi tayyor gul haqida javob berganda katalog item ichidagi nechta dona gul ketganini yoki post flower_countni mijoz so‘ramasa yozma. Faqat nomi, buket/savat turi, narxi va katalogda nechta borligini ayt."
        " Agar mijoz tayyor katalog buketiga nechta gul ketganini so‘rasa, catalog composition ma'lumotidan javob ber. Composition mavjud bo‘lsa 'katalogda ko‘rsatilmagan' demagin."
        " Mijoz 'qanaqa tayyor gullar bor', 'katalog bormi', 'tayyor buketlar' desa rasm yuborishni so‘rama va har bir rasmni alohida tavsiflama. Catalog kontekstdagi barcha available gullarni nomi, turi, narxi, qoldiq soni bilan qisqa ro‘yxat qil. Oxirida 'Qaysi biri qiziq bo‘lsa, tanlang, rasmini ko‘rsataman' degan mazmunda bitta savol ber."
        " Mijoz katalog ro‘yxatidan birini tanlasa yoki story/post/reeldagi tayyor gulni olmoqchi bo‘lsa, catalog_items arrayga catalog id va quantity yoz. Bir nechta tayyor buket/savat olsa ham hammasini catalog_itemsga yoz."
        " Mijoz bir nechta tayyor katalog gullarni ko‘rib chiqqan bo‘lsa va oxirida aniq qaysini olishi noma'lum bo‘lsa, ism/telefon so‘rama. Avval 'Sizga qaysi biri yoqdi, qaysi guldan buyurtma qilamiz?' deb aniqlashtir."
        " Mijoz custom yig‘dirsa, stock_items arrayga batch_id, quantity_stems va quantity_bunches yoz. Bir nechta buket/savat/gul bo‘lsa lead_request ichida har birining soni va tarkibi alohida aniq yozilsin."
        " Mijoz hali 'olaman', 'rasmiylashtiring', 'zakaz qilaman', 'shu kerak' demagan bo‘lsa ism yoki telefon so‘rama va lead_ready=false qaytar. Avval ehtiyoj turini aniqlashtir: 'Sizga buket qilib beraylikmi, savatga yig‘amizmi yoki gulning o‘zini olmoqchimisiz?' kabi bitta chiroyli savol ber."
        " CRM lead yaratishda arrangement_type aniq bo‘lsin: buket bo‘lsa bouquet, savat bo‘lsa basket, gulning o‘zi/donalab bo‘lsa stems, tayyor katalog guli bo‘lsa catalog. Tur aniq bo‘lmasa lead yaratma."
        " Mijoz buket yoki savat tanlasa, javobda florist xizmatini alohida ayt: 'Florist xizmati 50 000 so‘mdan boshlanadi, gul hajmi va bezagiga qarab o‘zgaradi.'"
        " Story, reel, post yoki katalogdagi tayyor buket/kompozitsiya haqida so‘ralsa florist xizmatini alohida aytma va narxga qo‘shma, chunki ular tayyor yasalgan sotuvdagi gullar."
        " Lead yaratish uchun JSON estimated_price qiymatida florist xizmatini alohida qo‘shib yuborma; tizim operator sotildi qilganda florist_fee maydonida yuritadi."
    )
    instructions = ai_settings.system_prompt + sales_rules + " Javobni JSON qaytaring: reply matni, detected_language uz yoki ru, customer_name, phone, lead_ready boolean, lead_request, arrangement_type bouquet/basket/stems/catalog yoki bo‘sh, estimated_price raqam yoki null, handoff boolean, catalog_items array, stock_items array."
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
                "handoff": {"type": "boolean"},
                "catalog_items": {"type": "array", "items": {"type": "object", "properties": {"catalog_id": {"type": "integer"}, "quantity": {"type": "integer"}}, "required": ["catalog_id", "quantity"], "additionalProperties": False}},
                "stock_items": {"type": "array", "items": {"type": "object", "properties": {"batch_id": {"type": "integer"}, "quantity_stems": {"type": "integer"}, "quantity_bunches": {"type": "number"}}, "required": ["batch_id", "quantity_stems", "quantity_bunches"], "additionalProperties": False}}
            },
            "required": ["reply", "detected_language", "customer_name", "phone", "lead_ready", "lead_request", "arrangement_type", "estimated_price", "handoff", "catalog_items", "stock_items"],
            "additionalProperties": False
        }}},
    )
    result = json.loads(response.output_text)
    result.setdefault("catalog_items", [])
    result.setdefault("stock_items", [])
    return result


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
    exact = SocialPost.objects.filter(Q(media_id=story_id) | Q(webhook_story_id=story_id), is_active=True).first()
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
            message_text = text or story_text or media_text
            if not sender_id or sender_id in own_ids or not message_text or message.get("is_echo"):
                continue
            branch = getattr(SocialPost.objects.filter(is_active=True).first(), "branch", None)
            if not branch:
                continue
            referral = event.get("referral") or message.get("referral") or {}
            media_id = referral.get("media_id") or referral.get("source_id") or (webhook_event.story_id if webhook_event else "") or (webhook_event.media_id if webhook_event else "")
            post = SocialPost.objects.filter(Q(media_id=media_id) | Q(webhook_story_id=media_id), is_active=True).first() if media_id else None
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


def resolve_telegram_update(payload):
    message = payload.get("message") or payload.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    if not chat_id or not user_id or not text:
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
    saved_message = ingest_customer_message(conversation, text, f"telegram:{chat_id}:{message_id}")
    if not saved_message:
        return []
    return [{"conversation_id": conversation.id, "message_id": saved_message.id, "chat_id": chat_id}]
