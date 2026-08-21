import json
import re
from html import escape
from datetime import date, timedelta
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone
from openai import OpenAI
from .models import AICatalogItem, AISettings, BusinessSettings, CatalogItem, Conversation, FlowerVariant, Lead, LeadStockUsage, Message, Notification, Packaging, StockBatch
from .platform_services import instagram_send_carousel, instagram_send_image, openai_api_key, telegram_send_image, telegram_send_media_group, telegram_send_rich_message_with, telegram_send_with


AI_REPLY_WAIT_SECONDS = 7
INSTAGRAM_AI_REPLY_WAIT_SECONDS = 10
AI_FOLLOW_UP_DELAY_SECONDS = 30 * 60

# Sklad AI ga ko'rsatilmaydi. Bu nomlar tool ro'yxatidan olib tashlangan, model baribir
# chaqirib qolsa execute_ai_tool unga operatorga topshirish yo'riqnomasini qaytaradi.
AI_HIDDEN_STOCK_TOOLS = {"get_stock", "get_flower_variant_info", "calculate_custom_arrangement_price", "send_stock_image", "send_stock_images"}

LEAD_TOPIC_LABELS = {
    "catalog_order": "Katalogdan buyurtma",
    "custom_order": "Yasatma buyurtma",
    "photo_request": "Rasm bo'yicha so'rov",
    "question": "Savol",
    "other": "Boshqa mavzu",
}

ARRANGEMENT_LABELS = [("bouquet", "buket"), ("basket", "savat"), ("stems", "donalab"), ("catalog", "katalog mahsuloti")]

MAX_LEAD_PHOTO_URLS = 5
MAX_CONTEXT_ATTACHMENTS = 6
MAX_OPERATOR_HANDOFF_MEDIA = 10
MAX_AI_CATALOG_MATCH_CANDIDATES = 40
AI_CATALOG_MATCH_CONFIDENCE = Decimal("0.75")

MEDIA_MATCHING_PRIORITY_INSTRUCTION = """
MEDIA MATCHING FIRST:
If REAL_CONTEXT_JSON.conversation.customer_attachments has any customer image, story, post or reel media and the customer asks about that media, call match_ai_catalog_by_media before asking for phone or handing off to an operator.
If match_ai_catalog_by_media returns a confident match, call send_catalog_image with that catalog_id and answer with the item name, price and one next question.
Only if match_ai_catalog_by_media fails or returns no matches, ask for phone and use handoff_media_to_operator.
Never skip media matching for "shu nechpul", "shundan bormi", "rasmdagi", "storydagi", "reeldagi", "tepadan 2chisi", "qizili", or circled/marked flower requests.
"""


def ai_globally_active():
    return AISettings.objects.get_or_create(pk=1)[0].is_active


def ai_allowed_for_conversation(conversation):
    if ai_globally_active():
        return True
    customer = conversation.customer
    username = (customer.instagram_username or "").lower().lstrip("@")
    if username and username in settings.AI_TEST_INSTAGRAM_USERNAMES:
        return True
    external_id = str(customer.instagram_user_id or "")
    return bool(external_id and external_id in settings.AI_TEST_INSTAGRAM_USER_IDS)


def parse_lead_date(value):
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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


def catalog_composition_summary(item):
    rows = []
    for row in item.composition.select_related("stock_batch__variant__flower"):
        if int(row.quantity_stems or 0) <= 0:
            continue
        batch = row.stock_batch
        name = " ".join(part for part in (batch.variant.flower.name_uz, batch.variant.name_uz, batch.variant.color_uz) if part)
        rows.append({"name_uz": name, "quantity_stems": row.quantity_stems, "quantity_bunches": str(row.quantity_bunches)})
    return rows


def available_catalog_queryset():
    """Sotuvda turgan katalog. Sotilgan, chiqitga chiqarilgan va restavratsiyada
    buzilgan donalar hisobdan chiqariladi."""
    return CatalogItem.objects.filter(status="available").annotate(
        used_quantity=F("quantity_sold") + F("quantity_wasted") + F("quantity_reworked")
    ).filter(used_quantity__lt=F("quantity_total"))


def available_ai_catalog_queryset():
    return AICatalogItem.objects.filter(is_active=True, quantity__gt=0)


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


PROTECTED_RE = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.]+|EuroFlowers|Next\s+Mall|Instagram|Telegram|\bAI\b", re.IGNORECASE)

CYRIL_TO_LATIN = {
    "А": "A", "а": "a", "Б": "B", "б": "b", "В": "V", "в": "v", "Г": "G", "г": "g",
    "Д": "D", "д": "d", "Ё": "Yo", "ё": "yo", "Ж": "J", "ж": "j", "З": "Z", "з": "z",
    "И": "I", "и": "i", "Й": "Y", "й": "y", "К": "K", "к": "k", "Л": "L", "л": "l",
    "М": "M", "м": "m", "Н": "N", "н": "n", "О": "O", "о": "o", "П": "P", "п": "p",
    "Р": "R", "р": "r", "С": "S", "с": "s", "Т": "T", "т": "t", "У": "U", "у": "u",
    "Ф": "F", "ф": "f", "Х": "X", "х": "x", "Ц": "Ts", "ц": "ts", "Ч": "Ch", "ч": "ch",
    "Ш": "Sh", "ш": "sh", "Щ": "Shch", "щ": "shch", "Ъ": "’", "ъ": "’", "Ь": "", "ь": "",
    "Э": "E", "э": "e", "Ю": "Yu", "ю": "yu", "Я": "Ya", "я": "ya",
    "Ў": "O‘", "ў": "o‘", "Қ": "Q", "қ": "q", "Ғ": "G‘", "ғ": "g‘", "Ҳ": "H", "ҳ": "h",
    "Ы": "I", "ы": "i", "Ъ".lower(): "’",
}

LATIN_DIGRAPHS = [
    ("Sh", "Ш"), ("SH", "Ш"), ("sh", "ш"),
    ("Ch", "Ч"), ("CH", "Ч"), ("ch", "ч"),
    ("Yo", "Ё"), ("YO", "Ё"), ("yo", "ё"),
    ("Yu", "Ю"), ("YU", "Ю"), ("yu", "ю"),
    ("Ya", "Я"), ("YA", "Я"), ("ya", "я"),
]
APOSTROPHES = "‘’'\u02bb\u02bc\u2018\u2019`\u00b4"
LATIN_APOSTROPHE = (
    [("O" + a, "Ў") for a in APOSTROPHES]
    + [("o" + a, "ў") for a in APOSTROPHES]
    + [("G" + a, "Ғ") for a in APOSTROPHES]
    + [("g" + a, "ғ") for a in APOSTROPHES]
)
LATIN_SINGLE = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ", "i": "и",
    "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "қ",
    "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "x": "х", "y": "й", "z": "з",
    "c": "к", "w": "в",
    "A": "А", "B": "Б", "D": "Д", "E": "Е", "F": "Ф", "G": "Г", "H": "Ҳ", "I": "И",
    "J": "Ж", "K": "К", "L": "Л", "M": "М", "N": "Н", "O": "О", "P": "П", "Q": "Қ",
    "R": "Р", "S": "С", "T": "Т", "U": "У", "V": "В", "X": "Х", "Y": "Й", "Z": "З",
    "C": "К", "W": "В",
    "'": "ъ", "’": "ъ", "‘": "ъ", "\u02bb": "ъ", "\u02bc": "ъ", "\u2018": "ъ", "\u2019": "ъ",
}

WORD_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" + APOSTROPHES


def _split_protected(text):
    parts, last = [], 0
    for m in PROTECTED_RE.finditer(text or ""):
        if m.start() > last:
            parts.append((text[last:m.start()], False))
        parts.append((m.group(0), True))
        last = m.end()
    parts.append((text[last:], False))
    return parts


def _cyril_chunk_to_latin(chunk):
    out, prev = [], ""
    for ch in chunk:
        if ch in ("Е", "е"):
            starts_word = not prev or prev not in WORD_CHARS + "абвгдеёжзийклмнопрстуфхцчшщъьэюяўқғҳАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЬЭЮЯЎҚҒҲ"
            out.append(("Ye" if ch == "Е" else "ye") if starts_word else ("E" if ch == "Е" else "e"))
        else:
            out.append(CYRIL_TO_LATIN.get(ch, ch))
        prev = ch
    return "".join(out)


def cyrillic_to_latin(text):
    return "".join(chunk if keep else _cyril_chunk_to_latin(chunk) for chunk, keep in _split_protected(text or ""))


def _latin_chunk_to_cyril(chunk):
    value = chunk
    for src, dst in LATIN_APOSTROPHE:
        value = value.replace(src, dst)
    value = re.sub(r"\bYe", "Е", value)
    value = re.sub(r"\bye", "е", value)
    value = re.sub(r"\bE(?![" + APOSTROPHES + "])", "Э", value)
    value = re.sub(r"\be(?![" + APOSTROPHES + "])", "э", value)
    for src, dst in LATIN_DIGRAPHS:
        value = value.replace(src, dst)
    return "".join(LATIN_SINGLE.get(ch, ch) for ch in value)


def latin_to_cyrillic(text):
    return "".join(chunk if keep else _latin_chunk_to_cyril(chunk) for chunk, keep in _split_protected(text or ""))


RU_MARKERS = re.compile(r"\b(цветы|цветов|какие|сколько|стоит|есть|адрес|где|здравствуйте|спасибо|доставка|нужен|нужна|хочу|работаете|дорого|букет из|привет|можно|пожалуйста|заказ|цена|день|это|вы|мне|для)\b", re.IGNORECASE)
UZ_CYRIL_MARKERS = re.compile(r"[ўқғҳЎҚҒҲ]|\b(гул|гулла|гуллар|бор|борми|бормиди|керак|кере|канака|қанақа|нечпул|неч|манзил|каерда|қаерда|ассалом|ассалому|раҳмат|рахмат|сават|яса|ясаймиз|ясанг|олиб|беринг|сизда|бизда|ишлайсизми|нархи|дона|сўм|киммат|қиммат|арзон|яхши|ҳам|учун|билан)\b", re.IGNORECASE)


def detect_text_script(text):
    value = text or ""
    if not re.search(r"[А-Яа-яЁёЎўҚқҒғҲҳ]", value):
        return "latin"
    # Rus belgilari aniqroq. Avval ularni tekshiramiz, chunki "букет" kabi so'zlar ikkala tilda bor.
    if RU_MARKERS.search(value):
        return "ru"
    if UZ_CYRIL_MARKERS.search(value):
        return "uz_cyril"
    return "uz_cyril"


def uz_latin_to_cyril(text):
    return latin_to_cyrillic(text)


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
        "stems_per_pochka": batch.stems_per_bunch,
        "price_per_pochka": str(batch.sale_price_per_bunch),
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
        "stems_per_pochka": variant.default_stems_per_bunch,
        "price_per_pochka": "",
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


def haystack_has_term(haystack, term):
    """So'z chegarasi bo'yicha moslik. 'all' so'zi 'podgallan' ichiga tushmasligi uchun."""
    if len(term) < 4:
        return any(word == term for word in haystack.split())
    return any(word == term or word.startswith(term) or term.startswith(word) for word in haystack.split())


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
        if not catalog_items:
            # AI orqali tushgan leadda sklad katalogi bog'lanmaydi, tanlov `details` da turadi.
            catalog_items = [
                {"name_uz": row.get("catalog_name") or "", "quantity": row.get("quantity") or 1, "type": lead.arrangement_type, "price": row.get("price") or ""}
                for row in (lead.details or {}).get("catalog_items") or []
                if isinstance(row, dict) and row.get("catalog_name")
            ]
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


def ai_catalog_ranked_matches(queryset, query):
    """Katalogni so'rovdagi so'zlar bo'yicha saralaydi va eng mos qatorlarni qaytaradi.

    Nom qisqa, mijozning jumlasi uzun bo'ladi. Shuning uchun har bir so'zni alohida
    tekshirib, eng ko'p so'zi mos kelgan mahsulotlarni olamiz. Hech biri mos kelmasa
    bo'sh qaytadi — AI shunda buni yasatma buyurtma deb hisoblaydi.
    """
    terms = ai_search_terms(query)
    if not terms:
        return queryset.none()
    ranked = []
    for item in queryset:
        haystack = compact_match_text(" ".join([item.name, item.volume, item.note]))
        score = sum(1 for term in terms if haystack_has_term(haystack, term))
        if score:
            ranked.append((score, item.id))
    if not ranked:
        return queryset.none()
    best = max(score for score, _ in ranked)
    return queryset.filter(id__in=[item_id for score, item_id in ranked if score == best])


def ai_catalog_rows(query="", limit=24, arrangement_type="", made_from_batch_id=None):
    query = (query or "").strip()
    queryset = available_ai_catalog_queryset().order_by("-created_at", "-id")
    if arrangement_type in ["bouquet", "basket", "box", "other"]:
        queryset = queryset.filter(arrangement_type=arrangement_type)
    if made_from_batch_id:
        queryset = queryset.none()
    generic_query_terms = {"vitrina", "katalog", "catalog", "tayyor", "mahsulot", "gulla", "buketlar", "savatlar"}
    normalized_query = compact_match_text(query)
    is_generic_query = bool(normalized_query) and any(term in normalized_query for term in generic_query_terms)
    if query and not is_generic_query:
        matched = queryset.filter(Q(name__icontains=query) | Q(volume__icontains=query) | Q(note__icontains=query) | Q(instagram_link__icontains=query))
        # `icontains` butun jumlani qidiradi. Mijoz esa "kotta shoxli bambastik gulidan
        # bormi" deb yozadi va hech narsa topilmaydi — o'shanda so'zlar bo'yicha izlaymiz.
        queryset = matched if matched.exists() else ai_catalog_ranked_matches(queryset, query)
    rows = []
    for row in queryset[:limit]:
        rows.append({
            "catalog_id": row.id,
            "name_uz": row.name,
            "type": row.arrangement_type,
            "quantity": row.quantity,
            "volume": row.volume,
            # Operator izohi. Mahsulot tafsiloti ham, kelishilgan narx ham shu yerda
            # bo'ladi — AI o'zi o'qib, qaysi biri mijozga aytilishini hal qiladi.
            "note_uz": row.note,
            "price": str(row.price),
            "has_image": bool(row.image_url),
            "image_url": row.image_url,
            "instagram_link": row.instagram_link,
            "composition": [],
        })
    return rows


def ai_stock_rows(query="", limit=24):
    query = (query or "").strip()
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
            if color_terms and not any(haystack_has_term(haystack, term) for term in color_terms):
                continue
            score = sum(1 for term in terms if haystack_has_term(haystack, term))
            if score:
                ranked.append((score, variant))
        queryset = [variant for _, variant in sorted(ranked, key=lambda row: (-row[0], row[1].flower.name_uz, row[1].color_uz, row[1].name_uz))]
    rows = []
    for variant in queryset:
        for batch in distinct_stock_offers(getattr(variant, "ai_stock_batches", [])):
            rows.append(stock_batch_ai_row(batch))
            if len(rows) >= limit:
                return rows[:limit]
    return rows[:limit]


def distinct_stock_offers(batches):
    """Mijozga aytish uchun bir-biridan farq qiladigan partiyalar.

    Kirimda nav so'ralmaydi, shuning uchun bitta gulda bir nechta partiya
    bo'ladi — bo'yi va narxi har xil. Ularning hammasi kerak, aks holda AI
    guldan faqat bittasini ko'radi. Bo'yi ham, narxi ham bir xil bo'lsa esa
    eng eskisi olinadi: mijozga bir xil taklifni ikki marta aytish shart emas.
    """
    seen = set()
    offers = []
    for batch in batches:
        key = (batch.height_label, str(batch.sale_price_per_stem))
        if key in seen:
            continue
        seen.add(key)
        offers.append(batch)
    return offers


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
            if color_terms and not any(haystack_has_term(haystack, term) for term in color_terms):
                continue
            score = sum(1 for term in terms if haystack_has_term(haystack, term))
            if score:
                ranked.append((score, variant))
        queryset = [variant for _, variant in sorted(ranked, key=lambda row: (-row[0], row[1].flower.name_uz, row[1].color_uz))]
    rows = []
    for variant in queryset[:limit]:
        stock_rows = distinct_stock_offers(StockBatch.objects.filter(variant=variant, is_active=True, remaining_stems__gt=0).order_by("received_at", "id"))
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


def customer_attachment_rows(history_messages):
    """Mijoz yuborgan rasm va media havolalari, eng yangisi oxirida.

    Havolalar Instagram yoki Telegram tomonidan beriladi, biz ularni serverga
    ko'chirmaymiz — leadga aynan shu havola yoziladi.
    """
    rows = []
    for message in history_messages:
        if message.sender != "customer":
            continue
        for attachment in (message.metadata or {}).get("attachments", []) or []:
            url = attachment.get("url")
            if not url or any(row["url"] == url for row in rows):
                continue
            rows.append({"kind": attachment.get("kind") or "media", "url": url})
    return rows[-MAX_CONTEXT_ATTACHMENTS:]


def ai_post_context(conversation):
    if not conversation.social_post_id:
        return None
    post = conversation.social_post
    links = [post.permalink, post.webhook_story_url]
    post_catalog = available_ai_catalog_queryset()
    link_query = Q()
    for link in links:
        if link:
            link_query |= Q(instagram_link__startswith=link) | Q(instagram_link__contains=link)
    post_catalog = post_catalog.filter(link_query) if link_query else post_catalog.none()
    return {
        "type": post.post_type,
        "title_uz": post.title_uz,
        "title_ru": post.title_ru,
        "description_uz": post.description_uz,
        "description_ru": post.description_ru,
        "price": str(post.price or ""),
        "catalog": [{
            "name_uz": row.name,
            "type": row.arrangement_type,
            "note_uz": row.note,
            "height_cm": None,
            "diameter_cm": None,
            "quantity": row.quantity,
            "volume": row.volume,
            "price": str(row.price),
            "has_image": bool(row.image_url),
            "image_url": row.image_url,
            "composition": [],
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
            "ai_note": mini_app_quote_note(florist_fee),
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
        "ai_note": mini_app_quote_note(estimated_price),
    }


def mini_app_quote_note(estimated_price):
    return f"Taxminiy narx {money_uz(estimated_price)} so'm. Operatorlarimiz aloqaga chiqib, sizga batafsil ma'lumot berishadi."


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


def ai_catalog_lead_rows(arguments):
    """AI katalogidan tanlangan mahsulotlar leadga izoh sifatida yoziladi.

    AI ko'radigan katalog — alohida `AICatalogItem` ro'yxati, sklad katalogi emas.
    Shuning uchun `LeadCatalogUsage` ochilmaydi: u sklad mahsulotiga bog'lanadi va
    haqiqiy mahsulotni operator o'zi tanlaydi.
    """
    rows = []
    for row in arguments.get("catalog_items") or []:
        quantity = int(row.get("quantity") or 1)
        if quantity <= 0:
            continue
        item = _catalog_item_for_ai(row.get("catalog_name"))
        rows.append({
            "catalog_name": item.name if item else str(row.get("catalog_name") or "").strip()[:180],
            "quantity": quantity,
            "ai_catalog_item": item.id if item else None,
            "price": str(item.price) if item else "",
        })
    return rows


def lead_request_details(arguments):
    """Operator ko'radigan qo'shimcha izoh maydonlari. AI tool argumentlaridan olinadi."""
    topic = (arguments.get("topic") or "").strip()
    return {
        "topic": topic if topic in LEAD_TOPIC_LABELS else "",
        "flowers_text": (arguments.get("flowers_text") or "").strip()[:400],
        "size_text": (arguments.get("size_text") or "").strip()[:200],
        "photo_urls": customer_photo_urls(arguments.get("photo_urls")),
    }


def customer_photo_urls(values):
    """Mijoz yuborgan rasm havolalari. Rasm serverga saqlanmaydi, faqat havola yoziladi."""
    urls = []
    for value in values or []:
        url = str(value or "").strip()
        if url.startswith("http") and url not in urls:
            urls.append(url[:500])
    return urls[:MAX_LEAD_PHOTO_URLS]


def operator_chat_url(conversation):
    template = settings.FRONTEND_CHAT_URL or "https://euroflowers.cognilabs.org/chat?conversation_id={conversation_id}"
    if "{conversation_id}" in template:
        return template.format(conversation_id=conversation.id)
    separator = "&" if "?" in template else "?"
    return f"{template}{separator}conversation_id={conversation.id}"


def operator_handoff_media_type(url, kind=""):
    text = f"{url} {kind}".lower()
    if any(text.split("?")[0].endswith(ext) for ext in [".mp4", ".mov", ".webm"]) or "video" in text:
        return "video"
    if any(text.split("?")[0].endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]) or "photo" in text or "image" in text:
        return "photo"
    return ""


def operator_handoff_attachment_payload(attachments):
    media = []
    links = []
    for row in attachments:
        url = row.get("url")
        if not url:
            continue
        media_type = operator_handoff_media_type(url, row.get("kind") or row.get("type") or "")
        if media_type and len(media) < MAX_OPERATOR_HANDOFF_MEDIA:
            media.append({"type": media_type, "url": url})
        else:
            links.append(url)
    return media, links


def operator_handoff_message(conversation, summary, phone, attachments, links):
    customer = conversation.customer
    lines = [
        "🌸 EuroFlowers media so‘rovi",
        "",
        f"👤 Mijoz: {customer.name or customer.instagram_username or customer.instagram_user_id}",
        f"📞 Telefon: {phone or customer.phone or 'raqam berilmagan'}",
        f"📍 Platforma: {'Telegram' if customer.instagram_user_id.startswith('telegram:') else 'Instagram'}",
        "",
        "🧠 AI xulosa",
        (summary or "Mijoz yuborgan rasm/reels bo‘yicha operator aniq javob berishi kerak.")[:1500],
    ]
    all_links = [row.get("url") for row in attachments if row.get("url")]
    if all_links:
        lines.extend(["", "🔗 Media havolalar"])
        lines.extend(f"{index}. {url}" for index, url in enumerate(all_links[:10], start=1))
    if links and not all_links:
        lines.extend(["", "🔗 Media havolalar"])
        lines.extend(f"{index}. {url}" for index, url in enumerate(links[:10], start=1))
    return "\n".join(lines)


def operator_handoff_rich_message(conversation, summary, phone, attachments, media, links):
    customer = conversation.customer
    platform = "Telegram" if customer.instagram_user_id.startswith("telegram:") else "Instagram"
    all_links = [row.get("url") for row in attachments if row.get("url")]
    display_links = all_links or links
    media_items = []
    blocks = []
    for index, row in enumerate(media[:MAX_OPERATOR_HANDOFF_MEDIA], start=1):
        media_id = f"handoff_{index}"
        media_type = row.get("type") or "photo"
        if media_type == "video":
            blocks.append(f'<video src="tg://video?id={media_id}"/>')
            media_payload = {"type": "video", "media": row.get("url") or ""}
        else:
            blocks.append(f'<img src="tg://photo?id={media_id}"/>')
            media_payload = {"type": "photo", "media": row.get("url") or ""}
        media_items.append({"id": media_id, "media": media_payload})
    summary_text = (summary or "Mijoz yuborgan media bo'yicha operator aniq javob berishi kerak.")[:1500]
    html_parts = []
    if blocks:
        html_parts.append("<tg-slideshow>")
        html_parts.extend(blocks)
        html_parts.append("</tg-slideshow>")
    customer_name = customer.name or customer.instagram_username or customer.instagram_user_id or "Nomalum"
    html_parts.extend([
        "<h3>🌸 EuroFlowers media so'rovi</h3>",
        f"<p>👤 Mijoz<br/>{escape(customer_name)}</p>",
        f"<p>📞 Telefon<br/>{escape(phone or customer.phone or 'raqam berilmagan')}</p>",
        f"<p>📍 Platforma<br/>{escape(platform)}</p>",
        f"<p>🧠 AI xulosa<br/>{escape(summary_text)}</p>",
    ])
    if display_links:
        html_parts.append("<p>🔗 Media havolalar</p>")
        html_parts.append("<ul>")
        for url in display_links[:10]:
            safe_url = escape(url)
            html_parts.append(f'<li><a href="{safe_url}">{safe_url}</a></li>')
        html_parts.append("</ul>")
    return {"html": "\n".join(html_parts), "media": media_items}


def handoff_media_to_operator(conversation, summary="", phone="", customer_refused_phone=False):
    attachments = customer_attachment_rows(conversation.messages.order_by("created_at", "id"))
    if not attachments:
        return {"ok": False, "detail": "no_customer_attachments"}
    token = settings.AI_OPERATOR_HANDOFF_BOT_TOKEN
    chat_id = settings.AI_OPERATOR_HANDOFF_GROUP_ID
    if not token or not chat_id:
        return {"ok": False, "detail": "operator_group_not_configured"}
    normalized_phone = normalize_phone(phone) or conversation.customer.phone
    if normalized_phone and normalized_phone != conversation.customer.phone:
        conversation.customer.phone = normalized_phone
        conversation.customer.save(update_fields=["phone", "updated_at"])
    media, links = operator_handoff_attachment_payload(attachments)
    media_sent = False
    media_error = ""
    reply_markup = {"inline_keyboard": [[{"text": "CRM chatni ochish", "url": operator_chat_url(conversation)}]]}
    rich_message = operator_handoff_rich_message(conversation, summary, normalized_phone, attachments, media, links)
    try:
        sent = telegram_send_rich_message_with(token, chat_id, rich_message, reply_markup=reply_markup, message_thread_id=settings.AI_OPERATOR_HANDOFF_THREAD_ID)
        media_sent = bool(media)
    except Exception as exc:
        media_error = str(exc)
        print(f"AI_OPERATOR_RICH_HANDOFF_FAILED conversation={conversation.id} error={exc}", flush=True)
        try:
            text = operator_handoff_message(conversation, summary, normalized_phone, attachments, links)
            sent = telegram_send_with(token, chat_id, text, reply_markup=reply_markup, message_thread_id=settings.AI_OPERATOR_HANDOFF_THREAD_ID)
        except Exception as fallback_exc:
            print(f"AI_OPERATOR_HANDOFF_FAILED conversation={conversation.id} error={fallback_exc}", flush=True)
            return {"ok": False, "detail": "telegram_send_failed", "error": str(fallback_exc), "media_sent": media_sent, "media_error": media_error}
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={
        "operator_media_handoff": {
            "summary": summary,
            "phone": normalized_phone,
            "customer_refused_phone": customer_refused_phone,
            "attachments": attachments,
            "media_sent": media_sent,
            "media_error": media_error,
            "chat_url": operator_chat_url(conversation),
            "telegram_result": sent,
        }
    })
    return {"ok": True, "media_sent": media_sent, "attachments_count": len(attachments), "chat_url": operator_chat_url(conversation)}


def latest_customer_media_attachment(conversation, source_url=""):
    attachments = customer_attachment_rows(conversation.messages.order_by("created_at", "id"))
    if source_url:
        for row in reversed(attachments):
            if row.get("url") == source_url:
                return row
        return {"kind": "media", "url": source_url}
    return attachments[-1] if attachments else None


def ai_catalog_match_candidate_rows(limit=MAX_AI_CATALOG_MATCH_CANDIDATES):
    rows = []
    for item in available_ai_catalog_queryset().exclude(image_url="").order_by("-created_at", "-id")[:limit]:
        rows.append({
            "catalog_id": item.id,
            "name": item.name,
            "arrangement_type": item.arrangement_type,
            "quantity": item.quantity,
            "volume": item.volume,
            "price": str(item.price),
            "price_text": f"{money_uz(item.price)} so'm",
            "note": item.note[:700],
            "instagram_link": item.instagram_link,
            "image_url": item.image_url,
        })
    return rows


def media_url_match_key(url):
    text = (url or "").strip()
    if not text:
        return ""
    text = text.split("?", 1)[0].rstrip("/")
    return text.lower()


def vision_compatible_media_url(url, kind=""):
    text = f"{url or ''} {kind or ''}".lower()
    direct_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    clean_url = (url or "").split("?", 1)[0].lower()
    return clean_url.endswith(direct_extensions) or "facebook.com/ads/image" in text or "lookaside.fbsbx.com/ig_messaging_cdn" in text or "photo" in text or "image" in text or "story" in text


def direct_ai_catalog_link_matches(candidates, source_url):
    source_key = media_url_match_key(source_url)
    if not source_key:
        return []
    rows = []
    for row in candidates:
        catalog_key = media_url_match_key(row.get("instagram_link"))
        if not catalog_key:
            continue
        if catalog_key == source_key or catalog_key in source_key or source_key in catalog_key:
            rows.append({
                "catalog_id": row["catalog_id"],
                "confidence": 1,
                "reason": "instagram_link matched",
            })
    return rows


def ai_catalog_match_result_schema():
    return {
        "type": "object",
        "properties": {
            "source_description": {"type": "string"},
            "target_description": {"type": "string"},
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "catalog_id": {"type": "integer"},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["catalog_id", "confidence", "reason"],
                    "additionalProperties": False,
                },
            },
            "no_match_reason": {"type": "string"},
        },
        "required": ["source_description", "target_description", "matches", "no_match_reason"],
        "additionalProperties": False,
    }


def normalize_catalog_match_rows(rows, candidates_by_id):
    normalized = []
    seen = set()
    for row in rows or []:
        try:
            catalog_id = int(row.get("catalog_id"))
        except (TypeError, ValueError):
            continue
        item = candidates_by_id.get(catalog_id)
        if not item or catalog_id in seen:
            continue
        seen.add(catalog_id)
        try:
            confidence = Decimal(str(row.get("confidence") or 0))
        except Exception:
            confidence = Decimal("0")
        if confidence < 0:
            confidence = Decimal("0")
        if confidence > 1:
            confidence = Decimal("1")
        normalized.append({
            "catalog_id": catalog_id,
            "name": item["name"],
            "type": item["arrangement_type"],
            "quantity": item["quantity"],
            "volume": item["volume"],
            "price": item["price"],
            "price_text": item["price_text"],
            "has_image": True,
            "confidence": str(confidence),
            "is_confident": confidence >= AI_CATALOG_MATCH_CONFIDENCE,
            "reason": (row.get("reason") or "")[:500],
            "note_uz": item["note"],
        })
    return sorted(normalized, key=lambda row: Decimal(row["confidence"]), reverse=True)


def first_confident_media_match(tool_results):
    for row in reversed(tool_results or []):
        if row.get("name") != "match_ai_catalog_by_media":
            continue
        for match in (row.get("output") or {}).get("matches") or []:
            if match.get("is_confident"):
                return match
    return None


def media_match_failed(tool_results):
    for row in reversed(tool_results or []):
        if row.get("name") != "match_ai_catalog_by_media":
            continue
        output = row.get("output") or {}
        return not output.get("ok") or not output.get("matches")
    return False


def tool_results_sent_catalog(tool_results, catalog_id):
    for row in tool_results or []:
        if row.get("name") == "send_catalog_image" and (row.get("output") or {}).get("catalog_id") == catalog_id and (row.get("output") or {}).get("ok"):
            return True
    return False


def apply_media_match_safeguard(conversation, result, tool_results):
    match = first_confident_media_match(tool_results)
    if match:
        catalog_id = match.get("catalog_id")
        item = catalog_album_queryset().filter(id=catalog_id).first()
        if item and not tool_results_sent_catalog(tool_results, item.id):
            output = send_catalog_item_image(conversation, item)
            tool_results.append({"name": "send_catalog_image", "arguments": {"query": "", "catalog_id": item.id, "safeguard": True}, "output": output})
        price_text = match.get("price_text") or (f"{money_uz(item.price)} so'm" if item else "")
        name = match.get("name") or (item.name if item else "")
        if name and price_text:
            result["reply"] = f"{name}\nNarxi {price_text}\nSizga qachonga kerak edi?"
        return result
    if media_match_failed(tool_results) and customer_attachment_rows(conversation.messages.order_by("created_at", "id")):
        result["reply"] = "Yuborgan rasmingiz yoki reelsingiz bo'yicha operatorlarimiz sizga aniq javob berishadi. Telefon raqamingizni yozib yuboraolasizmi?"
    return result


def match_ai_catalog_by_media(conversation, source_url="", user_text="", limit=MAX_AI_CATALOG_MATCH_CANDIDATES):
    attachment = latest_customer_media_attachment(conversation, source_url)
    if not attachment or not attachment.get("url"):
        return {"ok": False, "detail": "no_customer_media", "matches": []}
    candidates = ai_catalog_match_candidate_rows(max(1, min(int(limit or MAX_AI_CATALOG_MATCH_CANDIDATES), MAX_AI_CATALOG_MATCH_CANDIDATES)))
    if not candidates:
        return {"ok": False, "detail": "ai_catalog_empty_or_no_images", "source": attachment, "matches": []}
    direct_matches = normalize_catalog_match_rows(direct_ai_catalog_link_matches(candidates, attachment["url"]), {row["catalog_id"]: row for row in candidates})
    if direct_matches:
        result = {
            "ok": True,
            "source": attachment,
            "source_description": "Instagram link AI katalog bilan mos keldi.",
            "target_description": direct_matches[0]["name"],
            "no_match_reason": "",
            "confidence_threshold": str(AI_CATALOG_MATCH_CONFIDENCE),
            "matches": direct_matches[:5],
        }
        Message.objects.create(conversation=conversation, sender="system", text="", metadata={"ai_catalog_media_match": result})
        return result
    if not vision_compatible_media_url(attachment["url"], attachment.get("kind")):
        return {"ok": False, "detail": "media_url_not_image", "source": attachment, "matches": []}
    api_key = openai_api_key()
    if not api_key:
        return {"ok": False, "detail": "openai_api_key_missing", "source": attachment, "matches": []}
    image_detail = (settings.OPENAI_IMAGE_QUALITY or "low").lower()
    if image_detail not in {"low", "high", "auto"}:
        image_detail = "low"
    source_url = attachment["url"]
    text = (user_text or "").strip()
    if not text:
        latest_customer = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
        text = latest_customer.text if latest_customer else ""
    content = [
        {
            "type": "input_text",
            "text": json.dumps({
                "task": "Find which AI catalog flower arrangement best matches the customer's media. Use customer text for pointing instructions like circled item, second from top, color, story/reel/post context. Return only candidates that visually match.",
                "customer_text": text,
                "source": {"kind": attachment.get("kind") or "media", "url": source_url},
                "confidence_rule": "0.75 or higher only when the exact catalog item is very likely the same product. Lower confidence if only flower type/color is similar.",
                "candidates": [{key: value for key, value in row.items() if key != "image_url"} for row in candidates],
            }, ensure_ascii=False),
        },
        {"type": "input_image", "image_url": source_url, "detail": image_detail},
    ]
    for row in candidates:
        content.append({"type": "input_text", "text": f"CATALOG_ID {row['catalog_id']} | {row['name']} | {row['price_text']} | {row['volume']} | {row['note'][:300]}"})
        content.append({"type": "input_image", "image_url": row["image_url"], "detail": image_detail})
    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=settings.OPENAI_VISION_MODEL or settings.OPENAI_MODEL,
            instructions="You are a strict visual matcher for a flower shop catalog. Compare the customer image/video preview with catalog images. Do not guess prices or products. Return JSON only.",
            input=[{"role": "user", "content": content}],
            max_output_tokens=1200,
            reasoning={"effort": "minimal"},
            text={"format": {"type": "json_schema", "name": "ai_catalog_media_match", "strict": True, "schema": ai_catalog_match_result_schema()}},
        )
        raw = json.loads(response.output_text)
    except Exception as error:
        print(f"AI_CATALOG_MEDIA_MATCH_FAILED conversation={conversation.id} error={error}", flush=True)
        return {"ok": False, "detail": "vision_match_failed", "source": attachment, "error": str(error)[:500], "matches": []}
    candidates_by_id = {row["catalog_id"]: row for row in candidates}
    matches = normalize_catalog_match_rows(raw.get("matches"), candidates_by_id)
    result = {
        "ok": bool(matches),
        "source": attachment,
        "source_description": raw.get("source_description") or "",
        "target_description": raw.get("target_description") or "",
        "no_match_reason": raw.get("no_match_reason") or "",
        "confidence_threshold": str(AI_CATALOG_MATCH_CONFIDENCE),
        "matches": matches[:5],
    }
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"ai_catalog_media_match": result})
    return result


def lead_summary_text(lead):
    """Operator suhbat ustida ko'radigan bir qatorlik xulosa."""
    details = lead.details or {}
    parts = [LEAD_TOPIC_LABELS.get(details.get("topic"), "So'rov")]
    if lead.arrangement_type:
        parts.append(dict(ARRANGEMENT_LABELS).get(lead.arrangement_type, lead.arrangement_type))
    if details.get("flowers_text"):
        parts.append(f"gul {details['flowers_text']}")
    if details.get("size_text"):
        parts.append(f"hajmi {details['size_text']}")
    if details.get("photo_urls"):
        parts.append(f"{len(details['photo_urls'])} ta rasm havolasi")
    if lead.request_uz:
        parts.append(lead.request_uz)
    return " · ".join(parts)[:2000]


def save_conversation_ai_summary(conversation, lead):
    """Lead yaratilgach yoki yangilangach suhbat xulosasini yozib qo'yadi."""
    summary = lead_summary_text(lead)
    if summary == conversation.ai_summary:
        return
    conversation.ai_summary = summary
    conversation.save(update_fields=["ai_summary", "updated_at"])


def stock_hidden_result(name):
    """AI dan olib tashlangan sklad tool'lari chaqirilsa qaytariladigan yo'riqnoma."""
    return {
        "ok": False,
        "detail": "stock_not_available_to_ai",
        "tool": name,
        "instruction": "Sklad ma'lumoti AI ga berilmaydi. Mijozga gul ro'yxati, dona narxi yoki sklad rasmi ko'rsatilmaydi. Ism va telefonni ol, qaysi guldan, qanday hajmda va buketmi yoki savatmi ekanini so'ra, keyin client_lead_create chaqir va aniq narxni operator aytishini ayt.",
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
    lead_request_properties = {
        "topic": {"type": ["string", "null"], "enum": list(LEAD_TOPIC_LABELS) + [None], "description": "So'rov turi. custom_order — mijoz o'zi yasatmoqchi, photo_request — mijoz rasm yubordi, question — AI javob berolmaydigan savol."},
        "flowers_text": {"type": ["string", "null"], "description": "Faqat gul nomlari va ranglari, mijozning so'zi bilan. Masalan \"jumila pushti\". Butun jumlani ko'chirma. Mijoz gul aytmagan bo'lsa null."},
        "size_text": {"type": ["string", "null"], "description": "Faqat hajm yoki dona soni. Masalan \"51 dona\", \"katta\". Bilmasa null."},
        "photo_urls": {"type": "array", "items": {"type": "string"}, "description": "Mijoz yuborgan rasm havolalari. Suhbatdagi havolani o'zgartirmasdan ko'chir."},
    }
    lead_request_keys = list(lead_request_properties)
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
            "description": "Ism va telefon olingach so'rovni operatorga topshirish. Katalog buyurtmasi ham, yasatma buyurtma ham, rasm bo'yicha so'rov ham, AI javob berolmagan savol ham shu tool orqali yoziladi. request_text faqat o'zbekcha, mijoz so'ragan narsani aniq yoz.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "request_text": {"type": "string"},
                    "arrangement_type": {"type": ["string", "null"], "enum": ["bouquet", "basket", "catalog", None]},
                    "estimated_price": {"type": ["number", "null"], "description": "Faqat katalog mahsulotining aniq narxi. Yasatma buyurtmada null."},
                    "florist_fee": {"type": ["number", "null"]},
                    "fulfillment": {"type": ["string", "null"], "enum": ["delivery", "pickup", None]},
                    "delivery_address": {"type": ["string", "null"]},
                    "desired_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "desired_time": {"type": ["string", "null"], "description": "HH:MM"},
                    "catalog_items": {"type": "array", "items": lead_catalog_item_schema},
                    "note": {"type": ["string", "null"]},
                    **lead_request_properties,
                },
                "required": ["customer_name", "phone", "request_text", "arrangement_type", "estimated_price", "florist_fee", "fulfillment", "delivery_address", "desired_date", "desired_time", "catalog_items", "note"] + lead_request_keys,
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "client_lead_edit",
            "description": "Shu mijozning mavjud leadini tahrirlash. Mijoz yetkazib berish yoki kelib olishni tanlasa, manzil, sana yoki vaqt aytsa darhol shu tool bilan leadni yangila.",
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
                    "florist_fee": {"type": ["number", "null"]},
                    "fulfillment": {"type": ["string", "null"], "enum": ["delivery", "pickup", None]},
                    "delivery_address": {"type": ["string", "null"]},
                    "desired_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "desired_time": {"type": ["string", "null"], "description": "HH:MM"},
                    "catalog_items": {"type": ["array", "null"], "items": lead_catalog_item_schema},
                    "note": {"type": ["string", "null"]},
                    **lead_request_properties,
                },
                "required": ["lead_id", "customer_name", "phone", "request_text", "status", "arrangement_type", "estimated_price", "florist_fee", "fulfillment", "delivery_address", "desired_date", "desired_time", "catalog_items", "note"] + lead_request_keys,
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "handoff_media_to_operator",
            "description": "Mijoz yuborgan rasm, reels, post yoki media bo‘yicha AI aniq tushunmasa operator guruhiga yuborish. Avval telefon so‘ra; telefon berilsa phone bilan chaqir, telefon berishdan bosh tortsa customer_refused_phone=true qilib raqamsiz chaqir. Media linklari suhbat metadata sidan olinadi, argumentga media URL yozma.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Operator uchun aniq xulosa. Mijoz nimani so‘radi, rasm/reelsda nimani bilmoqchi, qanday javob kerak."},
                    "phone": {"type": ["string", "null"], "description": "Mijoz bergan telefon raqami. Bermasa null."},
                    "customer_refused_phone": {"type": "boolean", "description": "Mijoz telefon berishni rad etsa true."},
                },
                "required": ["summary", "phone", "customer_refused_phone"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "match_ai_catalog_by_media",
            "description": "Majburiy media matching tool. Conversation.customer_attachments bo'lsa va mijoz yuborgan rasm/story/post/reel haqida narx, bor-yo'qlik yoki aynan qaysi gul ekanini so'rasa, telefon so'rash yoki handoff qilishdan oldin doim shu toolni chaqir. Mijoz 'shu nechpul', 'shundan bormi', 'tepadan 2chisi', 'qizili', 'chizilgan joydagi' kabi yozsa shu tool shart. source_url bo'sh bo'lsa oxirgi customer media olinadi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_url": {"type": ["string", "null"], "description": "Suhbatdagi aniq media URL. Bo'sh yoki null bo'lsa oxirgi customer media olinadi."},
                    "user_text": {"type": "string", "description": "Mijozning media bilan bog'liq oxirgi savoli yoki ko'rsatmasi."},
                },
                "required": ["source_url", "user_text"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "get_catalog",
            "description": "AI katalogdagi mijozga ko'rsatiladigan tayyor buket/savat/kompozitsiyalarni olish. Har qatordagi note_uz — operator izohi: mahsulot tafsiloti va ba'zan kelishilgan narx shu yerda turadi.",
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
            "name": "send_catalog_image",
            "description": "AI katalogdagi bitta aniq buket/savat rasmini mijozga yuborish. Butun katalog kerak bo'lsa send_catalog_album ishlat. catalog_id ma'lum bo'lsa uni yubor, aks holda query ga nomini yoz.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "catalog_id": {"type": ["integer", "null"]},
                },
                "required": ["query", "catalog_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "send_catalog_album",
            "description": "AI katalogni mijozga rasm albomi qilib yuborish. Mijoz katalogni, vitrinani, tayyor buketlarni yoki nima borligini so'rasa shu tool chaqiriladi va katalog matn ro'yxati qilib yozilmaydi. catalog_ids bo'sh bo'lsa AI katalogdagi faol mahsulotlar yuboriladi. Rasmlar bitta xabarda albom bo'lib boradi, har rasm ostida tartib raqami, nomi va narxi ko'rinadi. Natijadagi position mijoz ko'rgan raqam bilan bir xil, mijoz keyin birinchisi, 2-chisi desa o'sha position dagi catalog_id olinadi. Har qatorda note_uz — o'sha mahsulotning operator izohi ham keladi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "catalog_ids": {"type": "array", "items": {"type": "integer"}, "description": "Bo'sh massiv = butun katalog. Aks holda get_catalog qaytargan catalog_id lar."},
                },
                "required": ["catalog_ids"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


def conversation_instagram_account_id(conversation):
    message = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    return (message.metadata or {}).get("instagram_account_id") if message else None


def send_image_to_customer(customer, image_url, conversation=None):
    try:
        if customer.instagram_user_id.startswith("telegram:"):
            result = telegram_send_image(customer.instagram_user_id.split(":", 1)[1], image_url)
        elif customer.instagram_user_id:
            result = instagram_send_image(customer.instagram_user_id, image_url, conversation_instagram_account_id(conversation) if conversation else None)
        else:
            return False, "no_platform_id", None
    except Exception as error:
        print(f"IMAGE_SEND_FAILED customer={customer.id} url={image_url} error={error}", flush=True)
        return False, "send_failed", None
    if isinstance(result, dict) and result.get("mocked"):
        return True, "mocked", result
    return True, "sent", result


def send_catalog_item_image(conversation, item):
    customer = conversation.customer
    item_name = getattr(item, "name", getattr(item, "name_uz", ""))
    image_url = catalog_item_image_url(item)
    price_value = getattr(item, "price", "")
    price_text = f"{money_uz(price_value)} so'm" if price_value != "" else ""
    if not image_url:
        return {"ok": False, "detail": "image_not_found", "catalog_id": item.id, "catalog_name": item_name}
    delivered, detail, sent = send_image_to_customer(customer, image_url, conversation)
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"image_tool_result": {"catalog_id": item.id, "catalog_name": item_name, "image_url": image_url, "delivered": delivered, "detail": detail, "sent": sent}})
    if not delivered:
        return {"ok": False, "image_sent": False, "detail": detail, "catalog_id": item.id, "catalog_name": item_name}
    return {"ok": True, "image_sent": True, "catalog_id": item.id, "catalog_name": item_name, "price": str(price_value), "price_text": price_text, "note_uz": getattr(item, "note", ""), "image_url": image_url, "reply_instruction": f"{item_name}\nNarxi {price_text}\nSizga qachonga kerak edi?"}


CATALOG_ALBUM_MAX_PER_MESSAGE = 10


def catalog_item_image_url(item):
    return item.image_url or (item.social_post.image_url if getattr(item, "social_post_id", None) else "")


def catalog_album_queryset():
    return available_ai_catalog_queryset().order_by("-created_at", "-id")


def catalog_album_items(catalog_ids=None, limit=60):
    queryset = catalog_album_queryset()
    if catalog_ids:
        items = {item.id: item for item in queryset.filter(id__in=catalog_ids)}
        return [items[value] for value in catalog_ids if value in items][:limit]
    return [item for item in queryset[:limit]]


def send_catalog_album(conversation, items):
    """Katalog rasmlarini albom qilib yuboradi. Bitta xabarga 10 tadan rasm sig'adi, bu platformaning chegarasi.

    Har rasm ostida tartib raqami, nomi va narxi ko'rinadi. Natijadagi position mijoz
    ko'rgan raqam bilan bir xil, keyin mijoz shu raqamni aytsa AI qaysi mahsulot ekanini biladi.
    """
    customer = conversation.customer
    rows = []
    not_sent = []
    for item in items:
        image_url = catalog_item_image_url(item)
        item_name = getattr(item, "name", getattr(item, "name_uz", ""))
        if not image_url:
            not_sent.append({"catalog_id": item.id, "name": item_name, "detail": "image_not_found"})
            continue
        rows.append({"item": item, "image_url": image_url})
    platform = "telegram" if customer.instagram_user_id.startswith("telegram:") else "instagram"
    chat_id = customer.instagram_user_id.split(":", 1)[1] if platform == "telegram" else customer.instagram_user_id
    if not rows:
        return {"ok": False, "detail": "image_not_found", "items": [], "not_sent": not_sent}
    if not chat_id:
        return {"ok": False, "detail": "no_platform_id", "items": [], "not_sent": not_sent}
    position = 0
    for row in rows:
        position += 1
        row["position"] = position
        row["caption"] = f"{row['position']}. {row['item'].name} — {money_uz(row['item'].price)} so'm"
    sent_items = []
    messages_sent = 0
    album_chunks = 0
    fallback_chunks = 0
    for start in range(0, len(rows), CATALOG_ALBUM_MAX_PER_MESSAGE):
        chunk = rows[start:start + CATALOG_ALBUM_MAX_PER_MESSAGE]
        delivered, detail = send_catalog_album_chunk(customer, platform, chat_id, chunk)
        if delivered:
            messages_sent += 1
            album_chunks += 1
            for row in chunk:
                sent_items.append(catalog_album_row(row, True, detail))
            continue
        fallback_chunks += 1
        for row in chunk:
            single = send_catalog_item_image(conversation, row["item"])
            if single.get("ok"):
                messages_sent += 1
                sent_items.append(catalog_album_row(row, True, "one_by_one"))
            else:
                not_sent.append({"catalog_id": row["item"].id, "name": row["item"].name, "detail": single.get("detail") or "send_failed"})
    if album_chunks and fallback_chunks:
        sent_as = "mixed"
    elif album_chunks:
        sent_as = "album"
    else:
        sent_as = "one_by_one"
    result = {
        "ok": bool(sent_items),
        "sent_as": sent_as,
        "messages_sent": messages_sent,
        "album_max_per_message": CATALOG_ALBUM_MAX_PER_MESSAGE,
        "numbering_visible": bool(sent_items) and fallback_chunks == 0,
        "items": sent_items,
        "not_sent": not_sent,
    }
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"catalog_album_result": result})
    return ai_catalog_album_result(result)


def catalog_album_row(row, delivered, detail):
    item = row["item"]
    return {
        "position": row["position"],
        "catalog_id": item.id,
        "name": item.name,
        "price": str(item.price),
        "type": item.arrangement_type,
        # Albom ketgach mijoz raqam bilan tanlaydi va shu mahsulot haqida so'raydi.
        # Izoh shu yerda tursa AI get_catalog ni qaytadan chaqirmasdan javob bera oladi.
        "note_uz": getattr(item, "note", ""),
        "image_url": row["image_url"],
        "delivered": delivered,
        "detail": detail,
    }


def ai_catalog_album_result(result):
    """AI ga rasm havolasi berilmaydi, u URL ni matn qilib yuborib qo'ymasligi uchun.

    Havolalar suhbat xabarining metadata sida qoladi va API orqali CRM chatiga chiqadi.
    """
    trimmed = dict(result)
    trimmed["items"] = [{key: value for key, value in row.items() if key != "image_url"} for row in result.get("items", [])]
    return trimmed


def send_catalog_album_chunk(customer, platform, chat_id, chunk):
    """Bitta albom xabarini yuboradi. (delivered, detail) qaytaradi, exception ko'tarmaydi."""
    try:
        if platform == "telegram":
            if len(chunk) == 1:
                result = telegram_send_image(chat_id, chunk[0]["image_url"], caption=chunk[0]["caption"])
            else:
                result = telegram_send_media_group(chat_id, [{"image_url": row["image_url"], "caption": row["caption"]} for row in chunk])
        else:
            result = instagram_send_carousel(chat_id, [{"title": f"{row['position']}. {row['item'].name}", "subtitle": f"{money_uz(row['item'].price)} so'm", "image_url": row["image_url"]} for row in chunk])
    except Exception as error:
        print(f"CATALOG_ALBUM_FAILED customer={customer.id} platform={platform} count={len(chunk)} error={error}", flush=True)
        return False, "album_failed"
    if isinstance(result, dict) and result.get("mocked"):
        return True, "mocked"
    if isinstance(result, dict) and result.get("ok") is False:
        print(f"CATALOG_ALBUM_REJECTED customer={customer.id} platform={platform} result={result}", flush=True)
        return False, "album_rejected"
    return True, "album"


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
        score = sum(1 for term in terms if haystack_has_term(haystack, term))
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
    delivered, detail, sent = send_image_to_customer(customer, image_url, conversation)
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"image_tool_result": {"stock_batch_id": batch.id, "stock_name": flower_variant_display_name(batch.variant, "uz"), "image_url": image_url, "delivered": delivered, "detail": detail, "sent": sent}})
    if not delivered:
        return {"ok": False, "image_sent": False, "detail": detail, "batch_id": batch.id, "stock_name": flower_variant_display_name(batch.variant, "uz")}
    return {"ok": True, "image_sent": True, "batch_id": batch.id, "stock_name": flower_variant_display_name(batch.variant, "uz"), "image_url": image_url}


def execute_ai_tool(name, arguments, conversation):
    customer = conversation.customer
    if name in AI_HIDDEN_STOCK_TOOLS:
        return stock_hidden_result(name)
    if name == "client_leads_get":
        limit = max(1, min(int(arguments.get("limit") or 5), 20))
        return {"leads": recent_customer_orders(customer)[:limit]}
    if name == "get_catalog":
        return {"catalog": ai_catalog_rows(arguments.get("query") or "", limit=80, arrangement_type=arguments.get("arrangement_type") or "")}
    if name == "send_catalog_album":
        catalog_ids = [int(value) for value in (arguments.get("catalog_ids") or []) if str(value).isdigit() or isinstance(value, int)]
        items = catalog_album_items(catalog_ids)
        if not items:
            return {"ok": False, "detail": "catalog_empty", "items": [], "not_sent": []}
        return send_catalog_album(conversation, items)
    if name == "send_catalog_image":
        query = arguments.get("query") or ""
        catalog_id = arguments.get("catalog_id")
        item = catalog_album_queryset().filter(id=catalog_id).first() if catalog_id else None
        if not item:
            item = _catalog_item_for_ai(query)
        if not item:
            return {"ok": False, "detail": "catalog_not_found"}
        return send_catalog_item_image(conversation, item)
    if name == "handoff_media_to_operator":
        return handoff_media_to_operator(
            conversation,
            summary=arguments.get("summary") or "",
            phone=arguments.get("phone") or "",
            customer_refused_phone=bool(arguments.get("customer_refused_phone")),
        )
    if name == "match_ai_catalog_by_media":
        return match_ai_catalog_by_media(
            conversation,
            source_url=arguments.get("source_url") or "",
            user_text=arguments.get("user_text") or "",
        )
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
    florist_fee = arguments.get("florist_fee")
    fulfillment = arguments.get("fulfillment") or ""
    delivery_address = (arguments.get("delivery_address") or "").strip()
    desired_date = parse_lead_date(arguments.get("desired_date"))
    desired_time = (arguments.get("desired_time") or "").strip()
    details = {
        "catalog_items": ai_catalog_lead_rows(arguments),
        "stock_items": arguments.get("stock_items") or [],
        "note": arguments.get("note") or "",
        "created_by": "ai_tool",
        **lead_request_details(arguments),
    }
    if name == "client_lead_edit":
        lead = Lead.objects.filter(id=arguments.get("lead_id"), customer=customer).first()
        if not lead:
            return {"ok": False, "detail": "lead_not_found"}
        fields = []
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
        if florist_fee is not None:
            lead.florist_fee = Decimal(str(florist_fee))
            fields.append("florist_fee")
        if fulfillment in {"delivery", "pickup"}:
            lead.fulfillment = fulfillment
            fields.append("fulfillment")
        if delivery_address:
            lead.delivery_address = delivery_address[:255]
            fields.append("delivery_address")
        if desired_date:
            lead.desired_date = desired_date
            fields.append("desired_date")
        if desired_time:
            lead.desired_time = desired_time[:20]
            fields.append("desired_time")
        # Tahrirda faqat yuborilgan maydon yangilanadi. Butun details ni almashtirsak
        # avvalgi izoh, gul turi va rasm havolasi yo'qolib ketardi.
        changed_details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        changed_details.pop("created_by", None)
        if changed_details:
            lead.details = {**(lead.details or {}), **changed_details}
            fields.append("details")
        if fields:
            lead.save(update_fields=list(set(fields)) + ["updated_at"])
        if arguments.get("stock_items") is not None:
            lead.stock_usage.all().delete()
            for row in arguments.get("stock_items") or []:
                batch = StockBatch.objects.filter(id=row.get("batch_id"), is_active=True).first()
                quantity_stems = int(row.get("quantity_stems") or 0)
                if batch and quantity_stems > 0:
                    LeadStockUsage.objects.create(lead=lead, stock_batch=batch, quantity_stems=quantity_stems, quantity_bunches=Decimal(str(row.get("quantity_bunches") or 0)))
        save_conversation_ai_summary(conversation, lead)
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
        florist_fee=Decimal(str(florist_fee)) if florist_fee is not None else Decimal("0"),
        fulfillment=fulfillment if fulfillment in {"delivery", "pickup"} else "",
        delivery_address=delivery_address[:255],
        desired_date=desired_date,
        desired_time=desired_time[:20],
        details=details,
        source="telegram" if customer.instagram_user_id.startswith("telegram:") else "instagram",
    )
    for row in arguments.get("stock_items") or []:
        batch = StockBatch.objects.filter(id=row.get("batch_id"), is_active=True).first()
        quantity_stems = int(row.get("quantity_stems") or 0)
        if batch and quantity_stems > 0:
            LeadStockUsage.objects.create(lead=lead, stock_batch=batch, quantity_stems=quantity_stems, quantity_bunches=Decimal(str(row.get("quantity_bunches") or 0)))
    Notification.objects.create(notification_type="lead", title_uz=f"Yangi lead: {customer}", title_ru=f"Новый лид: {customer}", body_uz=request_text, body_ru=request_text, reference_type="lead", reference_id=lead.id)
    save_conversation_ai_summary(conversation, lead)
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
        },
        "required": ["reply", "detected_language", "customer_name", "phone", "lead_ready", "lead_request", "arrangement_type", "estimated_price", "handoff", "catalog_items"],
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
    latest_customer_text = next((message.text for message in reversed(history_messages) if message.sender == "customer"), "")
    # O'zbek kirill suhbatda model lotinda aniqroq yozadi. Kirill matnni lotinga o'girib beramiz,
    # javobni esa oxirida kirillga qaytaramiz. Rus tiliga tegilmaydi.
    cyrillic_mode = detect_text_script(latest_customer_text) == "uz_cyril"
    history = []
    for message in history_messages:
        content = message.text
        if message.metadata:
            content = json.dumps({"text": message.text, "metadata": message.metadata}, ensure_ascii=False, default=str)
        if cyrillic_mode:
            content = cyrillic_to_latin(content)
        history.append({"role": "user" if message.sender == "customer" else "assistant", "content": content})
    ai_replies_count = sum(1 for message in history_messages if message.sender == "ai")
    has_ai_reply_in_session = ai_replies_count > 0
    latest_ai_index = max((index for index, message in enumerate(history_messages) if message.sender == "ai"), default=-1)
    pending_customer_messages = [message.text for message in history_messages[latest_ai_index + 1:] if message.sender == "customer"]
    last_customer_message = next((message.text for message in reversed(history_messages) if message.sender == "customer"), "")
    business_settings, _ = BusinessSettings.objects.get_or_create(pk=1)
    ai_settings, _ = AISettings.objects.get_or_create(pk=1)
    open_lead = conversation.leads.order_by("-created_at", "-id").first()
    working_hours = business_settings.working_hours or {}
    context = {
        "today": timezone.localdate().isoformat(),
        "now": timezone.localtime().strftime("%Y-%m-%d %H:%M"),
        "customer": {
            "name": customer.name if valid_customer_name(customer.name) else "",
            "phone": customer.masked_phone,
            "has_phone": bool(customer.phone),
            "instagram_username": customer.instagram_username,
            "previous_orders_count": customer.leads.count(),
            "is_returning": bool(valid_customer_name(customer.name) and customer.phone),
            "last_delivery_address": (customer.leads.exclude(delivery_address="").order_by("-created_at", "-id").values_list("delivery_address", flat=True).first() or ""),
        },
        "conversation": {
            "source": "telegram" if customer.instagram_user_id.startswith("telegram:") else "instagram",
            "fresh_session": fresh_session,
            "has_ai_reply_in_session": has_ai_reply_in_session,
            "pending_customer_messages": pending_customer_messages,
            "customer_attachments": customer_attachment_rows(history_messages),
            "social_post": ai_post_context(conversation),
            "open_lead": {
                "id": open_lead.id,
                "request": open_lead.request_uz,
                "arrangement_type": open_lead.arrangement_type,
                "estimated_price": str(open_lead.estimated_price) if open_lead.estimated_price is not None else "",
                "fulfillment": open_lead.fulfillment,
                "delivery_address": open_lead.delivery_address,
                "desired_date": open_lead.desired_date.isoformat() if open_lead.desired_date else "",
                "desired_time": open_lead.desired_time,
            } if open_lead else None,
            "already_known": {
                "name": bool(valid_customer_name(customer.name)),
                "phone": bool(customer.phone),
                "fulfillment": open_lead.fulfillment if open_lead else "",
                "delivery_address": bool(open_lead.delivery_address) if open_lead else False,
                "desired_date": bool(open_lead.desired_date) if open_lead else False,
                "desired_time": bool(open_lead.desired_time) if open_lead else False,
            },
        },
        "business": {
            "florist_fee": str(business_settings.default_florist_fee),
            "delivery_fee": str(business_settings.delivery_fee),
            "delivery_area_uz": business_settings.delivery_area_uz,
            "delivery_area_ru": business_settings.delivery_area_ru,
            "working_hours_uz": working_hours.get("uz", ""),
            "working_hours_uz_cyril": uz_latin_to_cyril(working_hours.get("uz", "")),
            "working_hours_ru": working_hours.get("ru", ""),
            "shop_address_uz": business_settings.shop_address_uz,
            "shop_address_uz_cyril": business_settings.shop_address_uz_cyril or uz_latin_to_cyril(business_settings.shop_address_uz),
            "shop_address_ru": business_settings.shop_address_ru,
            "shop_orientir_uz": business_settings.shop_orientir_uz,
            "shop_orientir_uz_cyril": business_settings.shop_orientir_uz_cyril or uz_latin_to_cyril(business_settings.shop_orientir_uz),
            "shop_orientir_ru": business_settings.shop_orientir_ru,
            "shop_location_link": business_settings.shop_location_link,
            "shop_phone": business_settings.shop_phone,
            "operator_phone": business_settings.operator_phone or business_settings.shop_phone,
            "operator_hours_uz": business_settings.operator_hours,
            "operator_hours_ru": business_settings.operator_hours_ru,
        },
    }
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key)
    model_input = [{"role": "user", "content": "REAL_CONTEXT_JSON:\n" + json.dumps(context, ensure_ascii=False)}]
    model_input += history
    effective_instructions = MEDIA_MATCHING_PRIORITY_INSTRUCTION.strip() + "\n\n" + (ai_settings.system_prompt or "")
    response_kwargs = {
        "model": ai_settings.openai_model or settings.OPENAI_MODEL,
        "instructions": effective_instructions,
        "input": model_input,
        "max_output_tokens": 8000,
        "reasoning": {"effort": ai_settings.reasoning_effort or "low"},
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
            instructions=effective_instructions,
            previous_response_id=response.id,
            input=tool_outputs,
            max_output_tokens=8000,
            reasoning={"effort": ai_settings.reasoning_effort or "low"},
            tools=ai_tool_definitions(),
            parallel_tool_calls=False,
            text={"format": {"type": "json_schema", "name": "sales_reply", "strict": True, "schema": ai_response_schema()}},
        )
    try:
        result = json.loads(response.output_text)
    except json.JSONDecodeError:
        status_detail = getattr(response, "status", "") or ""
        incomplete = getattr(response, "incomplete_details", None)
        print(f"OPENAI_JSON_DECODE_FAILED conversation={conversation.id} status={status_detail} incomplete={incomplete} output={response.output_text!r}", flush=True)
        response_kwargs["max_output_tokens"] = 16000
        response_kwargs["reasoning"] = {"effort": "low"}
        response = client.responses.create(**response_kwargs)
        try:
            result = json.loads(response.output_text)
        except json.JSONDecodeError:
            print(f"OPENAI_JSON_DECODE_FAILED_RETRY conversation={conversation.id} status={getattr(response, 'status', '')} output={response.output_text!r}", flush=True)
            raise
    result.setdefault("catalog_items", [])
    result.setdefault("stock_items", [])
    if tool_results:
        result["tool_results"] = tool_results
        result = apply_media_match_safeguard(conversation, result, tool_results)
    if cyrillic_mode and result.get("reply"):
        result["reply_latin"] = result["reply"]
        result["reply"] = latin_to_cyrillic(result["reply"])
    created_leads = [row["output"].get("lead_id") for row in tool_results if row.get("name") == "client_lead_create" and row.get("output", {}).get("ok")]
    if created_leads:
        result["lead_created_id"] = created_leads[-1]
        result["lead_ready"] = False
    return result


def ai_follow_up_decision(conversation, expected_ai_message):
    customer = conversation.customer
    history_messages = list(conversation.messages.exclude(sender="system").order_by("created_at", "id"))
    latest_customer_message = next((message.text for message in reversed(history_messages) if message.sender == "customer"), "")
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
        },
        "conversation": {
            "id": conversation.id,
            "source": "telegram" if customer.instagram_user_id.startswith("telegram:") else "instagram",
            "latest_customer_message": latest_customer_message,
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
            *history,
        ],
        max_output_tokens=3000,
        reasoning={"effort": ai_settings.reasoning_effort or "low"},
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


def ai_reply_wait_seconds_remaining(conversation_id, expected_message_id, wait_seconds=None):
    conversation = Conversation.objects.filter(id=conversation_id).first()
    if not conversation:
        return None
    latest = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
    if not latest or latest.id != expected_message_id:
        return None
    elapsed = (timezone.now() - latest.created_at).total_seconds()
    return max(0, (wait_seconds or AI_REPLY_WAIT_SECONDS) - elapsed)


def compact_match_text(value):
    return re.sub(r"[^a-zа-я0-9]+", " ", (value or "").lower()).strip()


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
    available_items = list(available_ai_catalog_queryset())
    for text in texts:
        for item in available_items:
            name = compact_match_text(item.name)
            if name and name in text:
                return item
            tokens = [token for token in name.split() if token not in {"buketi", "buket", "guldasta", "kompozitsiya"}]
            if len(tokens) >= 2 and all(token in text for token in tokens[:2]):
                return item
            if len(tokens) == 1 and len(tokens[0]) >= 4 and tokens[0] in text:
                matches = [row for row in available_items if tokens[0] in compact_match_text(row.name).split()]
                if len(matches) == 1:
                    return item
    return None


def money_uz(value):
    try:
        amount = int(Decimal(str(value)))
    except Exception:
        return str(value or "")
    return f"{amount:,}".replace(",", " ")


def create_ai_reply_for_conversation(conversation):
    if not ai_allowed_for_conversation(conversation):
        return None
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
    reply = Message.objects.create(conversation=conversation, sender="ai", text=result.get("reply", ""), metadata=result)
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
    if not ai_allowed_for_conversation(conversation):
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
    if not ai_globally_active():
        return None
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
