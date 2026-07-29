import json
import re
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone
from openai import OpenAI
from .models import AISettings, BusinessSettings, CatalogItem, Conversation, FlowerVariant, Lead, LeadCatalogUsage, LeadStockUsage, Message, Notification, Packaging, StockBatch
from .platform_services import instagram_send_image, openai_api_key, telegram_send_image


SHOP_ADDRESS = "Bobur ko‘chasi 10"
SHOP_LOCATION_LINK = "https://yandex.uz/maps/-/CTfQ6TMD"
SHOP_ORIENTIR = "Next Mall dan o'tgandan keyin o‘ng qo‘lda do‘konimiz"
SHOP_WORKING_HOURS = "24/7"
SHOP_PHONE = "+998 88 009 33 30"
AI_REPLY_WAIT_SECONDS = 7
AI_FOLLOW_UP_DELAY_SECONDS = 30 * 60


def normalize_phone(value):
    if "*" in (value or ""):
        return ""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return ""


def valid_customer_name(value):
    text = (value or "").strip()
    return bool(text) and compact_match_text(text) not in {"", "unknown", "nomalum", "noma lum"}


def detect_customer_reply_script(text):
    value = text or ""
    compact = compact_match_text(value)
    has_cyrillic = bool(re.search(r"[а-яёқўғҳ]", value.lower()))
    if not has_cyrillic:
        return "uz_latin"
    uz_markers = {
        "канча", "канчадан", "нечпул", "канака", "канакаси", "силада", "сизларда", "борми",
        "бовотти", "кере", "керак", "керакми", "килиб", "бер", "беринг", "пушти", "кизил",
        "қизил", "кок", "кўк", "ок", "оқ", "манга", "менга", "шуни", "бита", "битта",
        "дона", "сават", "гулла", "гуллар", "бизда", "нарх", "нархи", "сум", "сўм",
    }
    if any(marker in compact for marker in uz_markers):
        return "uz_cyril"
    ru_markers = {
        "сколько", "стоит", "продается", "продаете", "есть", "какие", "почему", "дорого",
        "дешево", "доставка", "нужно", "цвет", "штук", "можно", "заказать",
    }
    if any(marker in compact for marker in ru_markers):
        return "ru"
    return "uz_cyril"


def language_control_message(reply_script):
    if reply_script == "uz_cyril":
        return (
            "LANGUAGE_CONTROL:\n"
            "The latest customer message is Uzbek written in Cyrillic script.\n"
            "Reply fully in Uzbek Cyrillic script only.\n"
            "Do not reply in Russian.\n"
            "Use detected_language = uz."
        )
    if reply_script == "ru":
        return (
            "LANGUAGE_CONTROL:\n"
            "The latest customer message is Russian.\n"
            "Reply fully in Russian only.\n"
            "Use detected_language = ru."
        )
    return (
        "LANGUAGE_CONTROL:\n"
        "The latest customer message is Uzbek Latin.\n"
        "Reply fully in Uzbek Latin only.\n"
        "Use detected_language = uz."
    )


def customer_message_is_greeting(text):
    compact = compact_match_text(text)
    greeting_markers = {
        "salom", "assalomu", "assalomalaykum", "assalomualaykum", "assalomu alaykum",
        "ассалому", "ассаломуалайкум", "ассалому алайкум", "салом", "здравствуйте", "привет",
    }
    return any(marker in compact for marker in greeting_markers)


def greeting_control_message(reply_script):
    if reply_script == "uz_cyril":
        return (
            "GREETING_CONTROL:\n"
            "This is the first AI reply in the session and the customer greeted in Uzbek Cyrillic before asking a question.\n"
            "Start the reply with an Uzbek Cyrillic greeting, then answer the customer's business question in the same message.\n"
            "Do not send greeting alone."
        )
    if reply_script == "ru":
        return (
            "GREETING_CONTROL:\n"
            "This is the first AI reply in the session and the customer greeted before asking a question.\n"
            "Start the reply with a Russian greeting, then answer the customer's business question in the same message.\n"
            "Do not send greeting alone."
        )
    return (
        "GREETING_CONTROL:\n"
        "This is the first AI reply in the session and the customer greeted before asking a question.\n"
        "Start the reply with an Uzbek Latin greeting, then answer the customer's business question in the same message.\n"
        "Do not send greeting alone."
    )


def catalog_composition_summary(item):
    rows = []
    for row in item.composition.select_related("stock_batch__variant__flower"):
        batch = row.stock_batch
        name = f"{batch.variant.flower.name_uz} {batch.variant.name_uz} {batch.variant.color_uz}".strip()
        rows.append({"name_uz": name, "quantity_stems": row.quantity_stems, "quantity_bunches": str(row.quantity_bunches)})
    return rows


def available_catalog_queryset():
    return CatalogItem.objects.filter(status="available", quantity_sold__lt=F("quantity_total"))


def stock_availability(batch):
    if batch.remaining_stems <= 0:
        return "qolmagan"
    if batch.remaining_stems <= batch.minimum_sale_stems:
        return "oz qoldi"
    return "bor"


def flower_variant_display_name(variant, language):
    flower_name = variant.flower.name_uz
    variant_name = variant.name_uz
    color = variant.color_uz
    flower_compact = compact_match_text(flower_name)
    variant_compact = compact_match_text(variant_name)
    if variant_name and flower_compact and variant_compact.startswith(flower_compact):
        parts = [variant_name]
    else:
        parts = [flower_name, variant_name]
    current_compact = compact_match_text(" ".join(part for part in parts if part))
    if color and compact_match_text(color) not in current_compact:
        parts.append(color)
    return " ".join(part for part in parts if part).strip()


def uz_latin_to_cyril(text):
    value = text or ""
    replacements = [
        ("g‘", "ғ"),
        ("G‘", "Ғ"),
        ("o‘", "ў"),
        ("O‘", "Ў"),
        ("g'", "ғ"),
        ("G'", "Ғ"),
        ("o'", "ў"),
        ("O'", "Ў"),
        ("sh", "ш"),
        ("Sh", "Ш"),
        ("SH", "Ш"),
        ("ch", "ч"),
        ("Ch", "Ч"),
        ("CH", "Ч"),
        ("yo", "ё"),
        ("Yo", "Ё"),
        ("YO", "Ё"),
        ("yu", "ю"),
        ("Yu", "Ю"),
        ("YU", "Ю"),
        ("ya", "я"),
        ("Ya", "Я"),
        ("YA", "Я"),
        ("ts", "ц"),
        ("Ts", "Ц"),
        ("TS", "Ц"),
    ]
    for source, target in replacements:
        value = value.replace(source, target)
    table = str.maketrans({
        "a": "а",
        "b": "б",
        "d": "д",
        "e": "е",
        "f": "ф",
        "g": "г",
        "h": "ҳ",
        "i": "и",
        "j": "ж",
        "k": "к",
        "l": "л",
        "m": "м",
        "n": "н",
        "o": "о",
        "p": "п",
        "q": "қ",
        "r": "р",
        "s": "с",
        "t": "т",
        "u": "у",
        "v": "в",
        "x": "х",
        "y": "й",
        "z": "з",
        "A": "А",
        "B": "Б",
        "D": "Д",
        "E": "Е",
        "F": "Ф",
        "G": "Г",
        "H": "Ҳ",
        "I": "И",
        "J": "Ж",
        "K": "К",
        "L": "Л",
        "M": "М",
        "N": "Н",
        "O": "О",
        "P": "П",
        "Q": "Қ",
        "R": "Р",
        "S": "С",
        "T": "Т",
        "U": "У",
        "V": "В",
        "X": "Х",
        "Y": "Й",
        "Z": "З",
    })
    return value.translate(table)


def stock_image_url(batch):
    return batch.image_url or batch.variant.image_url or batch.variant.flower.image_url or ""


def stock_batch_ai_row(batch):
    image_url = stock_image_url(batch)
    display_name_uz = flower_variant_display_name(batch.variant, "uz")
    return {
        "batch_id": batch.id,
        "display_name_uz": display_name_uz,
        "display_name_uz_cyril": uz_latin_to_cyril(display_name_uz),
        "flower_uz": batch.variant.flower.name_uz,
        "flower_uz_cyril": uz_latin_to_cyril(batch.variant.flower.name_uz),
        "variant_uz": batch.variant.name_uz,
        "variant_uz_cyril": uz_latin_to_cyril(batch.variant.name_uz),
        "color_uz": batch.variant.color_uz,
        "color_uz_cyril": uz_latin_to_cyril(batch.variant.color_uz),
        "description_uz": batch.variant.description_uz,
        "description_uz_cyril": uz_latin_to_cyril(batch.variant.description_uz),
        "description_ru": batch.variant.description_ru,
        "height_cm": batch.height_cm,
        "height_from_cm": batch.height_from_cm,
        "height_to_cm": batch.height_to_cm,
        "height_label": batch.height_label,
        "availability": stock_availability(batch),
        "remaining_stems": batch.remaining_stems,
        "stems_per_bunch": batch.stems_per_bunch,
        "price_per_stem": str(batch.sale_price_per_stem),
        "price_per_bunch": str(batch.sale_price_per_bunch),
        "has_image": bool(image_url),
        "image_url": image_url,
    }


def variant_without_stock_ai_row(variant):
    image_url = variant.image_url or variant.flower.image_url or ""
    display_name_uz = flower_variant_display_name(variant, "uz")
    return {
        "batch_id": None,
        "display_name_uz": display_name_uz,
        "display_name_uz_cyril": uz_latin_to_cyril(display_name_uz),
        "flower_uz": variant.flower.name_uz,
        "flower_uz_cyril": uz_latin_to_cyril(variant.flower.name_uz),
        "variant_uz": variant.name_uz,
        "variant_uz_cyril": uz_latin_to_cyril(variant.name_uz),
        "color_uz": variant.color_uz,
        "color_uz_cyril": uz_latin_to_cyril(variant.color_uz),
        "description_uz": variant.description_uz,
        "description_uz_cyril": uz_latin_to_cyril(variant.description_uz),
        "description_ru": variant.description_ru,
        "height_cm": None,
        "height_from_cm": None,
        "height_to_cm": None,
        "height_label": "",
        "availability": "qolmagan",
        "remaining_stems": 0,
        "stems_per_bunch": variant.default_stems_per_bunch,
        "price_per_stem": "",
        "price_per_bunch": "",
        "has_image": bool(image_url),
        "image_url": image_url,
    }


def flower_variant_search_haystack(variant):
    return compact_match_text(" ".join([
        variant.flower.name_uz,
        variant.name_uz,
        variant.color_uz,
        variant.description_uz,
        variant.description_ru,
    ]))


def ai_search_terms(value):
    text = compact_match_text(value)
    replacements = {
        "кок": "moviy",
        "кўк": "moviy",
        "синий": "moviy",
        "голубой": "moviy",
        "moviy": "blue",
        "kok": "moviy",
        "ko k": "moviy",
        "гортензия": "gortenziya",
        "гортензи": "gortenziya",
        "гидрангея": "gortenziya",
        "атиргул": "atirgul",
        "роза": "atirgul",
        "розы": "atirgul",
        "пион": "pion",
        "пиони": "pion",
        "пушти": "pushti",
        "розовый": "pushti",
        "ок": "oq",
        "оқ": "oq",
        "белый": "oq",
        "қизил": "qizil",
        "кизил": "qizil",
        "красный": "qizil",
    }
    expanded = text
    for source, target in replacements.items():
        expanded = expanded.replace(source, target)
    stopwords = {"narxi", "narx", "nechpul", "qancha", "болади", "боларкан", "нархи", "нечпул", "dona", "tasi", "та", "дона", "buket", "savat", "yasash", "yasalgani", "qilamiz", "bilan", "uchun", "bor", "bormi", "mavjud", "price", "and", "the"}
    return [term for term in dict.fromkeys((text + " " + expanded).split()) if len(term) >= 3 and term not in stopwords]


def ai_color_search_terms(terms):
    return {term for term in terms if term in {"moviy", "blue", "pushti", "qizil", "oq", "yashil", "sariq", "binafsha"}}


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


def ai_catalog_rows(query="", limit=24, arrangement_type=""):
    query = (query or "").strip()
    queryset = available_catalog_queryset().select_related("social_post").prefetch_related("composition__stock_batch__variant__flower").order_by("-created_at")
    if arrangement_type in ["bouquet", "basket", "box"]:
        queryset = queryset.filter(arrangement_type=arrangement_type)
    generic_query_terms = {"vitrina", "katalog", "catalog", "tayyor", "mahsulot", "gulla", "buketlar", "savatlar"}
    normalized_query = compact_match_text(query)
    is_generic_query = bool(normalized_query) and any(term in normalized_query for term in generic_query_terms)
    if query and not is_generic_query:
        queryset = queryset.filter(Q(name_uz__icontains=query) | Q(description_uz__icontains=query) | Q(description_ru__icontains=query))
    rows = []
    for row in queryset[:limit]:
        image_url = row.image_url or (row.social_post.image_url if row.social_post_id else "")
        rows.append({
            "name_uz": row.name_uz,
            "type": row.arrangement_type,
            "description_uz": row.description_uz,
            "description_ru": row.description_ru,
            "price": str(row.price),
            "has_image": bool(image_url),
            "image_url": image_url,
            "social_post_type": row.social_post.post_type if row.social_post_id else "",
            "social_post_permalink": row.social_post.permalink if row.social_post_id else "",
            "composition": catalog_composition_summary(row),
        })
    return rows


def ai_stock_rows(query="", limit=24):
    query = (query or "").strip()
    if generic_stock_query(query):
        query = ""
    stock_batches = StockBatch.objects.filter(is_active=True, remaining_stems__gt=0).select_related("variant__flower").order_by("received_at", "id")
    queryset = (
        FlowerVariant.objects
        .filter(is_active=True)
        .select_related("flower")
        .prefetch_related(Prefetch("batches", queryset=stock_batches, to_attr="ai_stock_batches"))
        .order_by("flower__name_uz", "color_uz", "name_uz")
    )
    if query:
        terms = ai_search_terms(query)
        color_terms = ai_color_search_terms(terms)
        ranked = []
        for variant in queryset:
            haystack = flower_variant_search_haystack(variant)
            if color_terms and not any(term in haystack for term in color_terms):
                continue
            score = sum(1 for term in terms if term in haystack)
            if score:
                ranked.append((score, variant))
        queryset = [variant for _, variant in sorted(ranked, key=lambda row: (-row[0], row[1].flower.name_uz, row[1].color_uz, row[1].name_uz))]
    rows = []
    for variant in queryset:
        batches = getattr(variant, "ai_stock_batches", [])
        if batches:
            rows.append(stock_batch_ai_row(batches[0]))
        if len(rows) >= limit:
            break
    return rows[:limit]


def ai_basket_rows(limit=20):
    return [{
        "id": row.id,
        "name_uz": row.name_uz,
        "min": row.capacity_min_stems,
        "max": row.capacity_max_stems,
        "price": str(row.sale_price),
    } for row in Packaging.objects.filter(packaging_type="basket", is_active=True, quantity__gt=0).order_by("sale_price")[:limit]]


def ai_flower_variant_rows(query="", limit=24):
    queryset = FlowerVariant.objects.filter(is_active=True).select_related("flower").order_by("flower__name_uz", "color_uz", "name_uz")
    if query:
        terms = ai_search_terms(query)
        color_terms = ai_color_search_terms(terms)
        ranked = []
        for variant in queryset:
            haystack = flower_variant_search_haystack(variant)
            if color_terms and not any(term in haystack for term in color_terms):
                continue
            score = sum(1 for term in terms if term in haystack)
            if score:
                ranked.append((score, variant))
        queryset = [variant for _, variant in sorted(ranked, key=lambda row: (-row[0], row[1].flower.name_uz, row[1].color_uz))]
    rows = []
    for variant in queryset[:limit]:
        stock_rows = StockBatch.objects.filter(variant=variant, is_active=True, remaining_stems__gt=0).order_by("received_at", "id")[:1]
        rows.append({
            "variant_id": variant.id,
            "display_name_uz": flower_variant_display_name(variant, "uz"),
            "flower_uz": variant.flower.name_uz,
            "variant_uz": variant.name_uz,
            "color_uz": variant.color_uz,
            "description_uz": variant.description_uz,
            "description_ru": variant.description_ru,
            "active_stock": [{
                "batch_id": batch.id,
                "display_name_uz": flower_variant_display_name(batch.variant, "uz"),
                "display_name_uz_cyril": uz_latin_to_cyril(flower_variant_display_name(batch.variant, "uz")),
                "height_label": batch.height_label,
                "availability": stock_availability(batch),
                "remaining_stems": batch.remaining_stems,
                "stems_per_bunch": batch.stems_per_bunch,
                "price_per_stem": str(batch.sale_price_per_stem),
                "price_per_bunch": str(batch.sale_price_per_bunch),
            } for batch in stock_rows],
        })
    return rows


def ai_post_context(conversation):
    if not conversation.social_post_id:
        return None
    post = conversation.social_post
    post_catalog = available_catalog_queryset().filter(social_post=post).prefetch_related("composition__stock_batch__variant__flower")
    return {
        "type": post.post_type,
        "title_uz": post.title_uz,
        "title_ru": post.title_ru,
        "description_uz": post.description_uz,
        "description_ru": post.description_ru,
        "price": str(post.price or ""),
        "catalog": [{
            "name_uz": row.name_uz,
            "type": row.arrangement_type,
            "description_uz": row.description_uz,
            "description_ru": row.description_ru,
            "height_cm": row.height_cm,
            "diameter_cm": row.diameter_cm,
            "price": str(row.price),
            "has_image": bool(row.image_url or post.image_url),
            "image_url": row.image_url or post.image_url,
            "composition": catalog_composition_summary(row),
        } for row in post_catalog],
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


def calculate_custom_arrangement_price(stock_items):
    business_settings, _ = BusinessSettings.objects.get_or_create(pk=1)
    florist_fee = Decimal(str(business_settings.default_florist_fee or 0))
    lines = []
    flower_subtotal = Decimal("0")
    errors = []
    for row in stock_items or []:
        batch = StockBatch.objects.filter(id=row.get("batch_id"), is_active=True).select_related("variant__flower").first()
        quantity_stems = int(row.get("quantity_stems") or 0)
        if not batch:
            errors.append({"batch_id": row.get("batch_id"), "detail": "stock_not_found"})
            continue
        if quantity_stems <= 0:
            errors.append({"batch_id": batch.id, "detail": "quantity_stems_required"})
            continue
        if quantity_stems > batch.remaining_stems:
            errors.append({
                "batch_id": batch.id,
                "stock_name": flower_variant_display_name(batch.variant, "uz"),
                "detail": "not_enough_stock",
                "requested_stems": quantity_stems,
                "remaining_stems": batch.remaining_stems,
            })
            continue
        unit_price = Decimal(str(batch.sale_price_per_stem or 0))
        subtotal = (Decimal(quantity_stems) * unit_price).quantize(Decimal("1"))
        flower_subtotal += subtotal
        lines.append({
            "batch_id": batch.id,
            "stock_name": flower_variant_display_name(batch.variant, "uz"),
            "quantity_stems": quantity_stems,
            "price_per_stem": str(unit_price.quantize(Decimal("1"))),
            "subtotal": str(subtotal),
            "display_line_uz": f"{quantity_stems} ta {flower_variant_display_name(batch.variant, 'uz')} {money_uz(subtotal)} so'm",
        })
    total = (flower_subtotal + florist_fee).quantize(Decimal("1"))
    return {
        "ok": not errors and bool(lines),
        "lines": lines,
        "errors": errors,
        "flower_subtotal": str(flower_subtotal.quantize(Decimal("1"))),
        "florist_fee": str(florist_fee.quantize(Decimal("1"))),
        "total": str(total),
        "display_summary_uz": {
            "flower_subtotal": f"Gullar jami {money_uz(flower_subtotal)} so'm",
            "florist_fee": f"Florist haqi taxminan {money_uz(florist_fee)} so'm",
            "total": f"Jami taxminan {money_uz(total)} so'm",
        },
    }


def ai_tool_definitions():
    lead_catalog_item_schema = {
        "type": "object",
        "properties": {
            "catalog_name": {"type": "string"},
            "quantity": {"type": "integer"},
        },
        "required": ["catalog_name", "quantity"],
        "additionalProperties": False,
    }
    lead_stock_item_schema = {
        "type": "object",
        "properties": {
            "batch_id": {"type": "integer"},
            "quantity_stems": {"type": "integer"},
            "quantity_bunches": {"type": "number"},
        },
        "required": ["batch_id", "quantity_stems", "quantity_bunches"],
        "additionalProperties": False,
    }
    return [
        {
            "type": "function",
            "name": "client_leads_get",
            "description": "Shu mijozning avvalgi leadlarini olish.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": ["limit"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "client_lead_create",
            "description": "Mijoz aniq buyurtma qilmoqchi bo'lsa va ism-telefon olingan bo'lsa lead yaratish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "request_text": {"type": "string"},
                    "arrangement_type": {"type": ["string", "null"], "enum": ["bouquet", "basket", "catalog", None]},
                    "estimated_price": {"type": ["number", "null"]},
                    "catalog_items": {"type": "array", "items": lead_catalog_item_schema},
                    "stock_items": {"type": "array", "items": lead_stock_item_schema},
                    "note": {"type": ["string", "null"]},
                },
                "required": ["customer_name", "phone", "request_text", "arrangement_type", "estimated_price", "catalog_items", "stock_items", "note"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "client_lead_edit",
            "description": "Shu mijozning mavjud leadini tahrirlash.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer"},
                    "customer_name": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "request_text": {"type": ["string", "null"]},
                    "status": {"type": ["string", "null"]},
                    "arrangement_type": {"type": ["string", "null"], "enum": ["bouquet", "basket", "catalog", None]},
                    "estimated_price": {"type": ["number", "null"]},
                    "catalog_items": {"type": ["array", "null"], "items": lead_catalog_item_schema},
                    "stock_items": {"type": ["array", "null"], "items": lead_stock_item_schema},
                    "note": {"type": ["string", "null"]},
                },
                "required": ["lead_id", "customer_name", "phone", "request_text", "status", "arrangement_type", "estimated_price", "catalog_items", "stock_items", "note"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "get_catalog",
            "description": "Hozir sotuvdagi katalogdagi tayyor buket/savat/kompozitsiyalarni olish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "arrangement_type": {"type": ["string", "null"], "enum": ["bouquet", "basket", "box", None]},
                },
                "required": ["query", "arrangement_type"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "get_stock",
            "description": "Skladdagi gul variantlari va narxlarini olish. Bu tool faqat gullar uchun, savat/qadoq/materiallarni qaytarmaydi.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "get_flower_variant_info",
            "description": "Gul turi/navi/rangi haqida izoh va mavjud stock ma'lumotlarini olish.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "calculate_custom_arrangement_price",
            "description": "Custom buket yoki savat narxini aniq hisoblash. AI narxni o'zi hisoblamaydi, shu tool qaytargan total va display_summary_uz dan foydalanadi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_items": {"type": "array", "items": lead_stock_item_schema},
                },
                "required": ["stock_items"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "send_catalog_image",
            "description": "Katalogdagi aniq buket/savat rasmini mijozga yuborish.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "send_catalog_images",
            "description": "Katalogdagi bir nechta aniq buket/savat rasmlarini mijozga yuborish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["queries"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "send_stock_image",
            "description": "Skladdagi aniq gul turi/navi/rangi rasmini mijozga yuborish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "batch_id": {"type": ["integer", "null"]},
                },
                "required": ["query", "batch_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "send_stock_images",
            "description": "Skladdagi bir nechta gul turi/navi/rangi rasmlarini mijozga yuborish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["queries"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


def send_catalog_item_image(conversation, item):
    customer = conversation.customer
    image_url = item.image_url or (item.social_post.image_url if item.social_post_id else "")
    if not image_url:
        return {"ok": False, "detail": "image_not_found", "catalog_id": item.id, "catalog_name": item.name_uz}
    sent = None
    if customer.instagram_user_id.startswith("telegram:"):
        sent = telegram_send_image(customer.instagram_user_id.split(":", 1)[1], image_url)
    elif customer.instagram_user_id:
        sent = instagram_send_image(customer.instagram_user_id, image_url)
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"image_tool_result": {"catalog_id": item.id, "catalog_name": item.name_uz, "image_url": image_url, "sent": sent}})
    return {"ok": True, "image_sent": True, "catalog_id": item.id, "catalog_name": item.name_uz, "image_url": image_url}


def _stock_batch_for_ai(query="", batch_id=None):
    queryset = StockBatch.objects.filter(is_active=True, remaining_stems__gt=0).select_related("variant__flower").order_by("received_at", "id")
    if batch_id:
        return queryset.filter(id=batch_id).first()
    query = (query or "").strip()
    if not query:
        return queryset.first()
    rows = ai_stock_rows(query, limit=20)
    ids = [row["batch_id"] for row in rows if row.get("batch_id") and row.get("has_image")]
    if ids:
        return queryset.filter(id=ids[0]).first()
    ids = [row["batch_id"] for row in rows if row.get("batch_id")]
    if ids:
        return queryset.filter(id=ids[0]).first()
    terms = ai_search_terms(query)
    ranked = []
    for batch in queryset:
        haystack = flower_variant_search_haystack(batch.variant)
        score = sum(1 for term in terms if term in haystack)
        if score:
            ranked.append((score, batch))
    if ranked:
        return sorted(ranked, key=lambda row: (-row[0], row[1].received_at, row[1].id))[0][1]
    return None


def send_stock_batch_image(conversation, batch):
    customer = conversation.customer
    image_url = stock_image_url(batch)
    if not image_url:
        return {"ok": False, "detail": "image_not_found", "batch_id": batch.id, "stock_name": flower_variant_display_name(batch.variant, "uz")}
    sent = None
    if customer.instagram_user_id.startswith("telegram:"):
        sent = telegram_send_image(customer.instagram_user_id.split(":", 1)[1], image_url)
    elif customer.instagram_user_id:
        sent = instagram_send_image(customer.instagram_user_id, image_url)
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"image_tool_result": {"stock_batch_id": batch.id, "stock_name": flower_variant_display_name(batch.variant, "uz"), "image_url": image_url, "sent": sent}})
    return {"ok": True, "image_sent": True, "batch_id": batch.id, "stock_name": flower_variant_display_name(batch.variant, "uz"), "image_url": image_url}


def execute_ai_tool(name, arguments, conversation):
    customer = conversation.customer
    if name == "client_leads_get":
        limit = max(1, min(int(arguments.get("limit") or 5), 20))
        return {"leads": recent_customer_orders(customer)[:limit]}
    if name == "get_catalog":
        return {"catalog": ai_catalog_rows(arguments.get("query") or "", limit=80, arrangement_type=arguments.get("arrangement_type") or "")}
    if name == "get_stock":
        return {"stock": ai_stock_rows(arguments.get("query") or "", limit=100)}
    if name == "get_flower_variant_info":
        return {"variants": ai_flower_variant_rows(arguments.get("query") or "", limit=60)}
    if name == "calculate_custom_arrangement_price":
        return calculate_custom_arrangement_price(arguments.get("stock_items") or [])
    if name == "send_catalog_images":
        results = []
        seen = set()
        for query in arguments.get("queries") or []:
            item = _catalog_item_for_ai(query)
            if not item or item.id in seen:
                continue
            seen.add(item.id)
            results.append(send_catalog_item_image(conversation, item))
        return {"ok": bool(results), "images": results}
    if name == "send_catalog_image":
        query = arguments.get("query") or ""
        item = _catalog_item_for_ai(query)
        if not item:
            item = available_catalog_queryset().filter(Q(name_uz__icontains=query)).select_related("social_post").first()
        if not item:
            return {"ok": False, "detail": "catalog_not_found"}
        return send_catalog_item_image(conversation, item)
    if name == "send_stock_images":
        results = []
        seen = set()
        for query in arguments.get("queries") or []:
            batch = _stock_batch_for_ai(query=query)
            if not batch or batch.id in seen:
                continue
            seen.add(batch.id)
            results.append(send_stock_batch_image(conversation, batch))
        return {"ok": bool(results), "images": results}
    if name == "send_stock_image":
        batch = _stock_batch_for_ai(query=arguments.get("query") or "", batch_id=arguments.get("batch_id"))
        if not batch:
            return {"ok": False, "detail": "stock_not_found"}
        return send_stock_batch_image(conversation, batch)
    if name not in {"client_lead_create", "client_lead_edit"}:
        return {"ok": False, "detail": "unknown_tool"}
    name_value = (arguments.get("customer_name") or "").strip()
    phone_value = normalize_phone(arguments.get("phone") or "")
    customer_changed = []
    if valid_customer_name(name_value) and not valid_customer_name(customer.name):
        customer.name = name_value[:160]
        customer_changed.append("name")
    if phone_value:
        customer.phone = phone_value
        customer_changed.append("phone")
    if customer_changed:
        customer.save(update_fields=list(set(customer_changed)) + ["updated_at"])
    request_text = (arguments.get("request_text") or "").strip()
    estimated_price = arguments.get("estimated_price")
    arrangement_type = arguments.get("arrangement_type") or ""
    details = {
        "catalog_items": arguments.get("catalog_items") or [],
        "stock_items": arguments.get("stock_items") or [],
        "note": arguments.get("note") or "",
        "created_by": "ai_tool",
    }
    if name == "client_lead_edit":
        lead = Lead.objects.filter(id=arguments.get("lead_id"), customer=customer).first()
        if not lead:
            return {"ok": False, "detail": "lead_not_found"}
        fields = []
        if not request_text:
            latest_customer_message = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
            if latest_customer_message and pickup_requested(latest_customer_message.text):
                request_text = append_lead_request_text(lead.request_uz, "Mijoz kelib olib ketadi.")
        if request_text:
            lead.request_uz = request_text
            fields.append("request_uz")
        if arguments.get("status"):
            lead.status = arguments["status"][:40]
            fields.append("status")
        if arrangement_type:
            lead.arrangement_type = arrangement_type
            fields.append("arrangement_type")
        if estimated_price is not None:
            lead.estimated_price = Decimal(str(estimated_price))
            fields.append("estimated_price")
        if arguments.get("catalog_items") is not None or arguments.get("stock_items") is not None or arguments.get("note"):
            lead.details = details
            fields.append("details")
        if fields:
            lead.save(update_fields=list(set(fields)) + ["updated_at"])
        if arguments.get("catalog_items") is not None:
            lead.catalog_usage.all().delete()
            for row in arguments.get("catalog_items") or []:
                item = _catalog_item_for_ai(row.get("catalog_name"))
                quantity = int(row.get("quantity") or 1)
                if item and quantity > 0:
                    LeadCatalogUsage.objects.create(lead=lead, catalog_item=item, quantity=quantity)
        if arguments.get("stock_items") is not None:
            lead.stock_usage.all().delete()
            for row in arguments.get("stock_items") or []:
                batch = StockBatch.objects.filter(id=row.get("batch_id"), is_active=True).first()
                quantity_stems = int(row.get("quantity_stems") or 0)
                if batch and quantity_stems > 0:
                    LeadStockUsage.objects.create(lead=lead, stock_batch=batch, quantity_stems=quantity_stems, quantity_bunches=Decimal(str(row.get("quantity_bunches") or 0)))
        return {"ok": True, "lead_id": lead.id}
    if not request_text:
        return {"ok": False, "detail": "request_text_required"}
    if not valid_customer_name(customer.name):
        return {"ok": False, "detail": "customer_name_required"}
    if not customer.phone:
        return {"ok": False, "detail": "phone_required"}
    lead = Lead.objects.create(
        customer=customer,
        conversation=conversation,
        social_post=conversation.social_post,
        request_uz=request_text,
        arrangement_type=arrangement_type,
        estimated_price=Decimal(str(estimated_price)) if estimated_price is not None else None,
        details=details,
        source="telegram" if customer.instagram_user_id.startswith("telegram:") else "instagram",
    )
    for row in arguments.get("catalog_items") or []:
        item = _catalog_item_for_ai(row.get("catalog_name"))
        quantity = int(row.get("quantity") or 1)
        if item and quantity > 0:
            LeadCatalogUsage.objects.create(lead=lead, catalog_item=item, quantity=quantity)
    for row in arguments.get("stock_items") or []:
        batch = StockBatch.objects.filter(id=row.get("batch_id"), is_active=True).first()
        quantity_stems = int(row.get("quantity_stems") or 0)
        if batch and quantity_stems > 0:
            LeadStockUsage.objects.create(lead=lead, stock_batch=batch, quantity_stems=quantity_stems, quantity_bunches=Decimal(str(row.get("quantity_bunches") or 0)))
    Notification.objects.create(notification_type="lead", title_uz=f"Yangi lead: {customer}", title_ru=f"Новый лид: {customer}", body_uz=request_text, body_ru=request_text, reference_type="lead", reference_id=lead.id)
    return {"ok": True, "lead_id": lead.id}


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
            "arrangement_type": {"type": ["string", "null"], "enum": ["bouquet", "basket", "catalog", None]},
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


def ai_follow_up_schema():
    return {
        "type": "object",
        "properties": {
            "send_follow_up": {"type": "boolean"},
            "message": {"type": ["string", "null"]},
            "reason": {"type": "string"},
        },
        "required": ["send_follow_up", "message", "reason"],
        "additionalProperties": False,
    }


def ai_reply(conversation):
    customer = conversation.customer
    history_messages = list(conversation.messages.exclude(sender="system").order_by("created_at", "id"))
    fresh_session = bool(len(history_messages) > 1 and history_messages[-1].created_at - history_messages[-2].created_at >= timedelta(hours=24))
    history = []
    for message in history_messages:
        content = message.text
        if message.metadata:
            content = json.dumps({"text": message.text, "metadata": message.metadata}, ensure_ascii=False, default=str)
        history.append({"role": "user" if message.sender == "customer" else "assistant", "content": content})
    ai_replies_count = sum(1 for message in history_messages if message.sender == "ai")
    has_ai_reply_in_session = ai_replies_count > 0
    latest_ai_index = max((index for index, message in enumerate(history_messages) if message.sender == "ai"), default=-1)
    pending_customer_messages = [message.text for message in history_messages[latest_ai_index + 1:] if message.sender == "customer"]
    last_customer_message = next((message.text for message in reversed(history_messages) if message.sender == "customer"), "")
    reply_script = detect_customer_reply_script(last_customer_message)
    must_greet = not has_ai_reply_in_session and any(customer_message_is_greeting(text) for text in pending_customer_messages)
    business_settings, _ = BusinessSettings.objects.get_or_create(pk=1)
    ai_settings, _ = AISettings.objects.get_or_create(pk=1)
    context = {
        "customer": {
            "name": customer.name if valid_customer_name(customer.name) else "",
            "phone": customer.masked_phone,
            "has_phone": bool(customer.phone),
            "language": customer.language,
            "instagram_username": customer.instagram_username,
        },
        "conversation": {
            "id": conversation.id,
            "source": "telegram" if customer.instagram_user_id.startswith("telegram:") else "instagram",
            "fresh_session": fresh_session,
            "has_ai_reply_in_session": has_ai_reply_in_session,
            "ai_replies_count": ai_replies_count,
            "last_customer_message": last_customer_message,
            "pending_customer_messages": pending_customer_messages,
            "latest_reply_script": reply_script,
            "language_control": language_control_message(reply_script),
            "conversation_start_requires_greeting": must_greet,
            "social_post": ai_post_context(conversation),
        },
        "business": {
            "florist_fee": str(business_settings.default_florist_fee),
            "working_hours": SHOP_WORKING_HOURS,
            "shop_address": SHOP_ADDRESS,
            "shop_location_link": SHOP_LOCATION_LINK,
            "shop_orientir": SHOP_ORIENTIR,
            "shop_phone": SHOP_PHONE,
        },
    }
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key)
    model_input = [
        {"role": "user", "content": "REAL_CONTEXT_JSON:\n" + json.dumps(context, ensure_ascii=False)},
        {"role": "user", "content": language_control_message(reply_script)},
    ]
    if must_greet:
        model_input.append({"role": "user", "content": greeting_control_message(reply_script)})
    model_input += history
    response_kwargs = {
        "model": ai_settings.openai_model or settings.OPENAI_MODEL,
        "instructions": ai_settings.system_prompt,
        "input": model_input,
        "max_output_tokens": 2000,
        "reasoning": {"effort": "minimal"},
        "tools": ai_tool_definitions(),
        "parallel_tool_calls": False,
        "text": {"format": {"type": "json_schema", "name": "sales_reply", "strict": True, "schema": ai_response_schema()}},
    }
    response = client.responses.create(**response_kwargs)
    tool_results = []
    for _ in range(8):
        calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") == "function_call":
                calls.append(item)
        if not calls:
            break
        tool_outputs = []
        for call in calls:
            try:
                arguments = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            output = execute_ai_tool(call.name, arguments, conversation)
            tool_results.append({"name": call.name, "arguments": arguments, "output": output})
            tool_outputs.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(output, ensure_ascii=False, default=str),
            })
        response = client.responses.create(
            model=ai_settings.openai_model or settings.OPENAI_MODEL,
            instructions=ai_settings.system_prompt,
            previous_response_id=response.id,
            input=tool_outputs,
            max_output_tokens=2000,
            reasoning={"effort": "minimal"},
            tools=ai_tool_definitions(),
            parallel_tool_calls=False,
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
    if tool_results:
        result["tool_results"] = tool_results
    if reply_script in ["uz_cyril", "uz_latin"]:
        result["detected_language"] = "uz"
    elif reply_script == "ru":
        result["detected_language"] = "ru"
    created_leads = [row["output"].get("lead_id") for row in tool_results if row.get("name") == "client_lead_create" and row.get("output", {}).get("ok")]
    if created_leads:
        result["lead_created_id"] = created_leads[-1]
        result["lead_ready"] = False
    return result


def ai_follow_up_decision(conversation, expected_ai_message):
    customer = conversation.customer
    history_messages = list(conversation.messages.exclude(sender="system").order_by("created_at", "id"))
    latest_customer_message = next((message.text for message in reversed(history_messages) if message.sender == "customer"), "")
    reply_script = detect_customer_reply_script(latest_customer_message)
    history = []
    for message in history_messages:
        content = message.text
        if message.metadata:
            content = json.dumps({"text": message.text, "metadata": message.metadata}, ensure_ascii=False, default=str)
        history.append({"role": "user" if message.sender == "customer" else "assistant", "content": content})
    context = {
        "customer": {
            "name": customer.name if valid_customer_name(customer.name) else "",
            "phone": customer.masked_phone,
            "has_phone": bool(customer.phone),
            "language": customer.language,
        },
        "conversation": {
            "id": conversation.id,
            "source": "telegram" if customer.instagram_user_id.startswith("telegram:") else "instagram",
            "latest_customer_message": latest_customer_message,
            "latest_reply_script": reply_script,
            "language_control": language_control_message(reply_script),
            "expected_ai_message_id": expected_ai_message.id,
            "expected_ai_message": expected_ai_message.text,
            "expected_ai_metadata": expected_ai_message.metadata or {},
            "leads_count": conversation.leads.count(),
        },
    }
    api_key = openai_api_key()
    if not api_key:
        return {"send_follow_up": False, "message": None, "reason": "openai_api_key_missing"}
    ai_settings, _ = AISettings.objects.get_or_create(pk=1)
    instructions = (
        "Sen EUROFLOWERS PREMIUM uchun follow up qarorini chiqarasan.\n"
        "Chat oxirgi AI javobidan keyin 30 minut jim turgan bo‘lsa, faqat kerakli holatda bitta tabiiy follow up yoz.\n"
        "Shablon yozma, conversationni to‘liq tahlil qil.\n"
        "Agar lead yaratilgan bo‘lsa, ism va raqam olingan bo‘lsa, mijoz rad etsa, boshqa joydan olaman desa, yoqmadi desa, hop yaxshi rahmat yoki shunga o‘xshash yopuvchi gap yozgan bo‘lsa send_follow_up false qil.\n"
        "Agar AI katalog, rasm, narx yoki custom buket hisobini ko‘rsatganidan keyin mijoz jim qolgan bo‘lsa, budjetiga mos variantni operatorlar ko‘rsatishi mumkinligini tabiiy aytib ism va raqamini so‘ra.\n"
        "Agar mijoz faqat salom yoki bitta noaniq xabar yozib jim qolgan bo‘lsa, qanday gul kerakligini, vitrinadan ko‘rishini yoki o‘zi yig‘dirmoqchiligini qisqa so‘ra.\n"
        "Mijoz qaysi tilda va qaysi yozuvda yozgan bo‘lsa, message ham aynan o‘sha tilda va yozuvda bo‘lsin.\n"
        "Savollarni ko‘paytirma, bitta qisqa premium ohangdagi follow up yoz.\n"
        "Javob faqat JSON schema bo‘yicha bo‘lsin."
    )
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=ai_settings.openai_model or settings.OPENAI_MODEL,
        instructions=instructions,
        input=[
            {"role": "user", "content": "REAL_CONTEXT_JSON:\n" + json.dumps(context, ensure_ascii=False, default=str)},
            {"role": "user", "content": language_control_message(reply_script)},
            *history,
        ],
        max_output_tokens=700,
        reasoning={"effort": "minimal"},
        text={"format": {"type": "json_schema", "name": "follow_up_decision", "strict": True, "schema": ai_follow_up_schema()}},
    )
    try:
        data = json.loads(response.output_text)
    except json.JSONDecodeError:
        print(f"OPENAI_FOLLOW_UP_JSON_DECODE_FAILED conversation={conversation.id} output={response.output_text!r}", flush=True)
        return {"send_follow_up": False, "message": None, "reason": "json_decode_failed"}
    if reply_script in ["uz_cyril", "uz_latin"] and data.get("send_follow_up"):
        customer.language = "uz"
        customer.save(update_fields=["language", "updated_at"])
    elif reply_script == "ru" and data.get("send_follow_up"):
        customer.language = "ru"
        customer.save(update_fields=["language", "updated_at"])
    return data


def ingest_customer_message(conversation, message_text, instagram_message_id="", metadata=None):
    if instagram_message_id and Message.objects.filter(instagram_message_id=instagram_message_id, conversation=conversation).exists():
        return None
    message = Message.objects.create(conversation=conversation, sender="customer", text=message_text, instagram_message_id=instagram_message_id, metadata=metadata or {})
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=["last_message_at", "updated_at"])
    return message


def ai_reply_wait_seconds_remaining(conversation_id, expected_message_id):
    conversation = Conversation.objects.filter(id=conversation_id).first()
    if not conversation:
        return None
    latest = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    if not latest or latest.id != expected_message_id:
        return None
    elapsed = (timezone.now() - latest.created_at).total_seconds()
    return max(0, AI_REPLY_WAIT_SECONDS - elapsed)


def recent_catalog_item_for_conversation(conversation):
    for message in conversation.messages.exclude(metadata={}).order_by("-created_at")[:20]:
        for row in (message.metadata or {}).get("catalog_items") or []:
            catalog_id = row.get("catalog_id")
            quantity = int(row.get("quantity") or 0)
            if catalog_id and quantity > 0:
                item = available_catalog_queryset().filter(id=catalog_id).first()
                if item:
                    return item
        media_key = (message.metadata or {}).get("media_image_key") or ""
        match = re.search(r":catalog:(\d+):", media_key)
        if match:
            item = available_catalog_queryset().filter(id=match.group(1)).first()
            if item:
                return item
    return None


def compact_match_text(value):
    return re.sub(r"[^a-zа-я0-9]+", " ", (value or "").lower()).strip()


def generic_stock_query(query):
    normalized = compact_match_text(query)
    if not normalized:
        return True
    generic_terms = {
        "bor",
        "borlar",
        "current",
        "есть",
        "gulla",
        "gullar",
        "gul",
        "имеющиеся",
        "mavjud",
        "qanaqa",
        "qanday",
        "склад",
        "складе",
        "sklad",
        "skladda",
        "skladimizda",
        "текущие",
        "цвет",
        "цветы",
        "yasash",
        "yasatish",
        "yegdirish",
        "yigdirish",
        "yigish",
    }
    return set(normalized.split()).issubset(generic_terms)


def _catalog_text_aliases(text):
    aliases = {text}
    replacements = {
        "пиони": "pion",
        "пион": "pion",
        "пионл": "pion",
        "атиргул": "atirgul",
        "атиргулл": "atirgul",
        "роза": "atirgul",
        "розы": "atirgul",
        "пушти": "pushti",
        "кизил": "qizil",
        "қизил": "qizil",
        "гортензия": "gortenziya",
        "гортензи": "gortenziya",
    }
    expanded = text
    for source, target in replacements.items():
        expanded = expanded.replace(source, target)
    aliases.add(expanded)
    return aliases


def _catalog_item_for_ai(*values):
    texts = []
    for value in values:
        if value:
            texts.extend(_catalog_text_aliases(compact_match_text(value)))
    available_items = list(available_catalog_queryset())
    for text in texts:
        for item in available_items:
            name = compact_match_text(item.name_uz)
            if name and name in text:
                return item
            tokens = [token for token in name.split() if token not in {"buketi", "buket", "guldasta", "kompozitsiya"}]
            if len(tokens) >= 2 and all(token in text for token in tokens[:2]):
                return item
            if len(tokens) == 1 and len(tokens[0]) >= 4 and tokens[0] in text:
                matches = [row for row in available_items if tokens[0] in compact_match_text(row.name_uz).split()]
                if len(matches) == 1:
                    return item
    return None


def pickup_requested(text):
    compact = compact_match_text(text)
    phrases = [
        "borib olaman",
        "kelib olaman",
        "olib ketaman",
        "ozim olib ketaman",
        "o zim olib ketaman",
    ]
    return any(phrase in compact for phrase in phrases)


def location_requested(text):
    compact = compact_match_text(text)
    phrases = [
        "адрес",
        "lokatsiya",
        "location",
        "manzil",
        "qayerda",
        "qayoda",
        "tashang",
    ]
    return any(phrase in compact for phrase in phrases)


def shop_location_reply(include_final_thanks=False):
    parts = [
        f"Manzilimiz {SHOP_ADDRESS}",
        SHOP_ORIENTIR,
        "",
        SHOP_LOCATION_LINK,
        f"Telefon {SHOP_PHONE}",
        f"Ish vaqti {SHOP_WORKING_HOURS}",
    ]
    if include_final_thanks:
        parts.extend(["", "Rahmat, tez orada operatorlarimiz buyurtmangizni tasdiqlash uchun aloqaga chiqishadi."])
    return "\n".join(parts)


def append_lead_request_text(current, addition):
    current = (current or "").strip()
    addition = (addition or "").strip()
    if not addition:
        return current
    if addition.lower() in current.lower():
        return current
    return f"{current}\n{addition}".strip() if current else addition


def money_uz(value):
    try:
        amount = int(Decimal(str(value)))
    except Exception:
        return str(value or "")
    return f"{amount:,}".replace(",", " ")


def catalog_type_label(value):
    return {"basket": "savat", "bouquet": "buket", "box": "quti"}.get(value or "", "gul")


def ai_reply_asks_for_catalog_image(text):
    compact = compact_match_text(text)
    phrases = [
        "rasmni yuboraymi",
        "rasmini yuboraymi",
        "rasmni korsataman",
        "rasmini korsataman",
        "rasmlarini korsataman",
        "rasmni ko rsataman",
        "rasmini ko rsataman",
        "rasmlarini ko rsataman",
        "qaysini tanlaysiz",
    ]
    return any(phrase in compact for phrase in phrases)


def customer_asks_for_image(text):
    compact = compact_match_text(text)
    phrases = [
        "rasm",
        "rasmi",
        "rasmini",
        "rasmlar",
        "korsat",
        "ko rsat",
        "yubor",
        "qani",
    ]
    return any(phrase in compact for phrase in phrases)


def ai_reply_has_stock_results(result):
    for tool_result in result.get("tool_results") or []:
        if tool_result.get("name") == "get_stock" and tool_result.get("output", {}).get("stock"):
            return True
    return False


def clean_stock_image_offer(text):
    replacements = [
        " yoki rasmni ko‘rmokchimisiz?",
        " yoki rasmni ko‘rmoqchimisiz?",
        " yoki rasmini ko‘rmokchimisiz?",
        " yoki rasmini ko‘rmoqchimisiz?",
        " yoki rasmni ko'rmokchimisiz?",
        " yoki rasmni ko'rmoqchimisiz?",
        " yoki rasmini ko'rmokchimisiz?",
        " yoki rasmini ko'rmoqchimisiz?",
        " yoki rasmni ko rmokchimisiz?",
        " yoki rasmni ko rmoqchimisiz?",
        " yoki rasmini ko rmokchimisiz?",
        " yoki rasmini ko rmoqchimisiz?",
    ]
    cleaned = text or ""
    for phrase in replacements:
        cleaned = cleaned.replace(phrase, "?")
    return cleaned


def stock_rows_from_ai_result(result):
    rows = []
    for tool_result in result.get("tool_results") or []:
        if tool_result.get("name") != "get_stock":
            continue
        rows.extend(tool_result.get("output", {}).get("stock") or [])
    unique = []
    seen = set()
    for row in rows:
        batch_id = row.get("batch_id")
        if not batch_id or batch_id in seen:
            continue
        seen.add(batch_id)
        unique.append(row)
    return unique


def reply_has_stock_false_negative(text):
    compact = compact_match_text(text)
    phrases = [
        "vitrinada tayyor gullar ro yxati bo sh",
        "vitrinada yo q",
        "yo q ekan",
        "yok ekan",
        "bo sh ekan",
        "готовых цветов нет",
        "в витрине нет",
        "нет доступных цветов",
        "рўйхати бўш",
        "йўқ экан",
        "бўш экан",
    ]
    return any(phrase in compact for phrase in phrases)


def latest_message_asks_stock_list(text):
    compact = compact_match_text(text)
    if not compact:
        return False
    stock_words = ["gullar", "gulla", "gul", "цветы", "цвет", "гулла", "гул"]
    availability_words = ["bor", "bormi", "есть", "бор", "борми", "qanaqa", "qanday", "какие", "канака", "қанақа"]
    return any(word in compact for word in stock_words) and any(word in compact for word in availability_words)


def format_stock_rows_reply(rows, latest_customer_text):
    script = detect_customer_reply_script(latest_customer_text)
    if script == "ru":
        lines = ["Сейчас в складе есть такие цветы", ""]
        for index, row in enumerate(rows, start=1):
            name = row.get("display_name_uz_cyril") or row.get("display_name_uz") or ""
            lines.append(f"{index} {name} — за штуку {money_uz(row.get('price_per_stem'))} сум")
        lines.extend(["", "Из какого цветка соберём букет или корзину?"])
        return "\n".join(lines)
    if script == "uz_cyril":
        lines = ["Ҳозир складимизда қуйидаги гуллар бор", ""]
        for index, row in enumerate(rows, start=1):
            name = row.get("display_name_uz_cyril") or uz_latin_to_cyril(row.get("display_name_uz") or "")
            lines.append(f"{index} {name} — дона {money_uz(row.get('price_per_stem'))} сўм")
        lines.extend(["", "Қайси биридан букет ёки сават ясаймиз?"])
        return "\n".join(lines)
    lines = ["Skladimizda hozir quyidagi gullar bor", ""]
    for index, row in enumerate(rows, start=1):
        name = row.get("display_name_uz") or ""
        lines.append(f"{index} {name} — dona {money_uz(row.get('price_per_stem'))} so'm")
    lines.extend(["", "Qaysi biridan buket yoki savat yasaymiz?"])
    return "\n".join(lines)


def catalog_image_already_sent(conversation, item):
    if not item:
        return False
    for message in conversation.messages.exclude(metadata={}).order_by("-created_at", "-id")[:20]:
        image_result = (message.metadata or {}).get("image_tool_result") or {}
        if str(image_result.get("catalog_id") or "") == str(item.id) and image_result.get("sent") is not None:
            return True
    return False


def catalog_items_from_ai_result(result):
    items = []
    seen = set()
    for row in result.get("catalog_items") or []:
        item = _catalog_item_for_ai(row.get("catalog_name"))
        if item and item.id not in seen:
            seen.add(item.id)
            items.append(item)
    return items


def stock_image_already_sent(conversation, batch):
    if not batch:
        return False
    for message in conversation.messages.exclude(metadata={}).order_by("-created_at", "-id")[:20]:
        image_result = (message.metadata or {}).get("image_tool_result") or {}
        if str(image_result.get("stock_batch_id") or "") == str(batch.id) and image_result.get("sent") is not None:
            return True
    return False


def stock_batch_from_recent_ai_context(conversation):
    queryset = StockBatch.objects.filter(is_active=True, remaining_stems__gt=0).select_related("variant__flower")
    for message in conversation.messages.exclude(metadata={}).order_by("-created_at", "-id")[:20]:
        metadata = message.metadata or {}
        for row in metadata.get("stock_items") or []:
            batch = queryset.filter(id=row.get("batch_id")).first()
            if batch and stock_image_url(batch):
                return batch
        for tool_result in metadata.get("tool_results") or []:
            if tool_result.get("name") != "get_stock":
                continue
            for row in tool_result.get("output", {}).get("stock") or []:
                batch = queryset.filter(id=row.get("batch_id")).first()
                if batch and stock_image_url(batch):
                    return batch
    return None


def single_catalog_item_from_ai_result(result, conversation):
    for tool_result in result.get("tool_results") or []:
        if tool_result.get("name") != "get_catalog":
            continue
        catalog = tool_result.get("output", {}).get("catalog") or []
        if len(catalog) == 1:
            item = _catalog_item_for_ai(catalog[0].get("name_uz"))
            if item:
                return item, "catalog_filter"
    catalog_rows = result.get("catalog_items") or []
    if len(catalog_rows) == 1:
        item = _catalog_item_for_ai(catalog_rows[0].get("catalog_name"))
        if item:
            return item, "selected_item"
    if conversation.social_post_id:
        post_items = list(available_catalog_queryset().filter(social_post=conversation.social_post)[:2])
        if len(post_items) == 1:
            return post_items[0], "social_post"
    return None, ""


def enforce_catalog_image_flow(result, conversation):
    selected_items = catalog_items_from_ai_result(result)
    if len(selected_items) > 1 and ai_reply_asks_for_catalog_image(result.get("reply", "")):
        outputs = []
        for item in selected_items:
            if catalog_image_already_sent(conversation, item):
                outputs.append({"ok": False, "detail": "image_already_sent", "catalog_id": item.id, "catalog_name": item.name_uz})
            else:
                outputs.append(send_catalog_item_image(conversation, item))
        result.setdefault("tool_results", [])
        result["tool_results"].append({"name": "send_catalog_images", "arguments": {"queries": [item.name_uz for item in selected_items]}, "output": {"ok": True, "images": outputs}})
        return result
    item, source = single_catalog_item_from_ai_result(result, conversation)
    if not item or not ai_reply_asks_for_catalog_image(result.get("reply", "")):
        return result
    tool_result = {"ok": False, "detail": "image_already_sent", "catalog_name": item.name_uz}
    if not catalog_image_already_sent(conversation, item):
        tool_result = send_catalog_item_image(conversation, item)
    result.setdefault("tool_results", [])
    result["tool_results"].append({"name": "send_catalog_image", "arguments": {"query": item.name_uz}, "output": tool_result})
    result["catalog_items"] = [{"catalog_name": item.name_uz, "quantity": 1}]
    result["arrangement_type"] = item.arrangement_type
    result["estimated_price"] = str(item.price)
    type_label = "savat" if item.arrangement_type == "basket" else "buket"
    if source == "catalog_filter":
        result["reply"] = f"Katalogimizda hozir faqat {item.name_uz} {type_label} bor ekan\nNarxi {money_uz(item.price)} so'm\nSizga qachonga kerak edi?"
    else:
        result["reply"] = f"{item.name_uz} {type_label}\nNarxi {money_uz(item.price)} so'm\nSizga qachonga kerak edi?"
    return result


def enforce_stock_image_flow(result, conversation):
    if ai_reply_has_stock_results(result):
        result["reply"] = clean_stock_image_offer(result.get("reply", ""))
    latest_customer_message = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    if not latest_customer_message or not customer_asks_for_image(latest_customer_message.text):
        return result
    already_called = any(tool_result.get("name") in {"send_stock_image", "send_stock_images"} for tool_result in result.get("tool_results") or [])
    if already_called:
        return result
    batch = stock_batch_from_recent_ai_context(conversation)
    if not batch:
        return result
    tool_result = {"ok": False, "detail": "image_already_sent", "batch_id": batch.id, "stock_name": flower_variant_display_name(batch.variant, "uz")}
    if not stock_image_already_sent(conversation, batch):
        tool_result = send_stock_batch_image(conversation, batch)
    result.setdefault("tool_results", [])
    result["tool_results"].append({"name": "send_stock_image", "arguments": {"query": flower_variant_display_name(batch.variant, "uz"), "batch_id": batch.id}, "output": tool_result})
    result["stock_items"] = [{"batch_id": batch.id, "quantity_stems": 0, "quantity_bunches": 0}]
    result["reply"] = f"{flower_variant_display_name(batch.variant, 'uz')} rasmi\nShu guldan nechta dona qilib buket yoki savat yasaymiz?"
    return result


def enforce_stock_list_reply(result, conversation):
    rows = stock_rows_from_ai_result(result)
    if not rows:
        return result
    tool_names = {tool_result.get("name") for tool_result in result.get("tool_results") or []}
    if tool_names & {"calculate_custom_arrangement_price", "send_stock_image", "send_stock_images", "client_lead_create", "client_lead_edit"}:
        return result
    latest_customer_message = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    latest_text = latest_customer_message.text if latest_customer_message else ""
    if reply_has_stock_false_negative(result.get("reply", "")) or latest_message_asks_stock_list(latest_text):
        result["reply"] = format_stock_rows_reply(rows, latest_text)
        result["stock_items"] = [{"batch_id": row["batch_id"], "quantity_stems": 0, "quantity_bunches": 0} for row in rows]
    return result


def enforce_pickup_and_location_flow(result, conversation):
    latest_customer_message = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    if not latest_customer_message:
        return result
    latest_text = latest_customer_message.text or ""
    if location_requested(latest_text) and not pickup_requested(latest_text):
        result["reply"] = shop_location_reply()
        return result
    if not pickup_requested(latest_text):
        return result
    latest_lead = conversation.leads.order_by("-created_at", "-id").first()
    if not latest_lead:
        return result
    already_edited = any(tool_result.get("name") == "client_lead_edit" for tool_result in result.get("tool_results") or [])
    if not already_edited:
        tool_result = execute_ai_tool("client_lead_edit", {
            "lead_id": latest_lead.id,
            "customer_name": None,
            "phone": None,
            "request_text": None,
            "status": None,
            "arrangement_type": None,
            "estimated_price": None,
            "catalog_items": None,
            "stock_items": None,
            "note": None,
        }, conversation)
        result.setdefault("tool_results", [])
        result["tool_results"].append({"name": "client_lead_edit", "arguments": {"lead_id": latest_lead.id, "pickup_auto": True}, "output": tool_result})
    result["reply"] = shop_location_reply(include_final_thanks=True)
    return result


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
    if valid_customer_name(result.get("customer_name")) and not valid_customer_name(customer.name):
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
    if result.get("lead_created_id"):
        result["lead_ready"] = False
    if result.get("lead_ready") and not valid_customer_name(customer.name):
        result["lead_ready"] = False
        result["reply"] = "Buyurtmani rasmiylashtirish uchun ismingizni yozib yuborasizmi?"
    elif result.get("lead_ready") and not customer.phone:
        result["lead_ready"] = False
        result["phone"] = None
        result["reply"] = "Telefon raqamingizni to‘liq yuborasizmi?\nMasalan: 90 123 45 67"
    result = enforce_catalog_image_flow(result, conversation)
    result = enforce_stock_list_reply(result, conversation)
    result = enforce_stock_image_flow(result, conversation)
    result = enforce_pickup_and_location_flow(result, conversation)
    reply = Message.objects.create(conversation=conversation, sender="ai", text=result["reply"], metadata=result)
    if result.get("handoff"):
        Notification.objects.create(notification_type="handoff", title_uz=f"Operator aloqasi kerak: {customer}", title_ru=f"Нужна связь оператора: {customer}", body_uz=result.get("lead_request") or result.get("reply", ""), body_ru=result.get("lead_request") or result.get("reply", ""), reference_type="conversation", reference_id=conversation.id)
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
        conversation = Conversation.objects.select_related("customer", "social_post").get(id=conversation_id)
        reply = create_ai_reply_for_conversation(conversation)
    except Exception:
        Conversation.objects.filter(id=conversation_id, ai_reply_started_for_message_id=expected_message_id).update(ai_reply_started_for_message=None, ai_reply_started_at=None)
        raise
    if reply:
        Conversation.objects.filter(id=conversation_id, ai_reply_started_for_message_id=expected_message_id).update(ai_replied_to_message_id=expected_message_id, ai_reply_started_for_message=None, ai_reply_started_at=None)
    else:
        Conversation.objects.filter(id=conversation_id, ai_reply_started_for_message_id=expected_message_id).update(ai_reply_started_for_message=None, ai_reply_started_at=None)
    return reply


def process_stalled_conversation_follow_up(conversation_id, expected_ai_message_id):
    conversation = Conversation.objects.select_related("customer").filter(id=conversation_id).first()
    if not conversation or conversation.status != "ai":
        return None
    if conversation.ai_paused_until and conversation.ai_paused_until > timezone.now():
        return None
    expected_ai_message = conversation.messages.filter(id=expected_ai_message_id, sender="ai").first()
    if not expected_ai_message:
        return None
    latest_message = conversation.messages.order_by("-created_at", "-id").first()
    if not latest_message or latest_message.id != expected_ai_message.id:
        return None
    if conversation.leads.exists() or expected_ai_message.metadata.get("lead_created_id"):
        return None
    elapsed = (timezone.now() - expected_ai_message.created_at).total_seconds()
    if elapsed < AI_FOLLOW_UP_DELAY_SECONDS:
        return None
    decision = ai_follow_up_decision(conversation, expected_ai_message)
    message_text = (decision.get("message") or "").strip()
    if not decision.get("send_follow_up") or not message_text:
        Message.objects.create(conversation=conversation, sender="system", text="", metadata={"follow_up_cancelled": True, "expected_ai_message_id": expected_ai_message.id, "reason": decision.get("reason") or ""})
        return None
    follow_up = Message.objects.create(conversation=conversation, sender="ai", text=message_text, metadata={"follow_up": True, "expected_ai_message_id": expected_ai_message.id, "reason": decision.get("reason") or ""})
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=["last_message_at", "updated_at"])
    return follow_up
