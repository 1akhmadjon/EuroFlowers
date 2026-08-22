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
from .models import AICatalogItem, AISettings, BusinessSettings, CatalogItem, Conversation, FlowerVariant, Lead, LeadStockUsage, Message, Notification, Packaging, SocialPost, StockBatch
from .platform_services import instagram_send_carousel, instagram_send_image, openai_api_key, telegram_send_image, telegram_send_media_group, telegram_send_rich_message_with, telegram_send_with
from . import vision_services


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
# Instagram bitta karusel xabariga 10 ta rasm sig'adi. Bitta reelga do'kon ettita
# mahsulot qo'yishi mumkin va mijoz ularning hammasini ko'rishi kerak.
MAX_LINK_MATCHES = 10
# Fingerprint bali shundan past bo'lsa mahsulot qisqa ro'yxatga ham tushmaydi —
# modelga umuman aloqasi yo'q rasmni ko'rsatib o'tirishning hojati yo'q.
AI_CATALOG_SHORTLIST_FLOOR = 30

# Guruhga faqat g'olib bilan shu qadar yaqin ballar tushadi. Undan pastdagisi
# g'olibga yetmagan mahsulot — uni ko'rsatish mijozni chalg'itadi.
TIED_SCORE_GAP = 6

MEDIA_MATCH_FOUND_INSTRUCTION = (
    "Aynan shu mahsulot topildi. send_catalog_image ni shu catalog_id bilan chaqir. "
    "Javobni \"Bizda hozirda bor, siz ko'rsatganga o'xshagan variant:\" degan mazmundagi "
    "bitta jumla bilan boshla, keyin yangi qatordan mahsulot nomi, narxi va bitta savol. "
    "Mijoz qaysi tilda yozgan bo'lsa shu tilda yoz."
)
MEDIA_MATCH_OWN_STORY_INSTRUCTION = (
    "Mijoz bizning o'z storyimizni yubordi va uning ma'lumoti tizimda saqlangan: "
    "story maydonidagi title va price_text aynan shu mahsulotniki. Mijozga o'sha nomni "
    "va o'sha narxni ayt, keyin bitta savol ber. \"O'xshagan\" yoki \"o'xshash variant\" "
    "DEMA — bu aynan o'sha gulning o'zi. Katalogdan boshqa rasm YUBORMA va boshqa "
    "mahsulot nomini aytma. Mijoz shu storydagi gulning rasmini so'rasa "
    "send_post_image ni social_post_id bilan chaqir."
)
MEDIA_MATCH_OWN_POST_INSTRUCTION = (
    "Mijoz bizning o'z story/postimizni yubordi va undagi mahsulot aniq topildi. "
    "send_catalog_image ni shu catalog_id bilan chaqir. \"O'xshagan\", \"o'xshash\" yoki "
    "\"shunga o'xshagan variant\" DEMA — bu aynan o'sha mahsulotning o'zi. To'g'ridan-to'g'ri "
    "mahsulot nomi, narxi va bitta savol yoz. Mijoz qaysi tilda yozgan bo'lsa shu tilda yoz."
)
MEDIA_MATCH_GROUP_INSTRUCTION = (
    "Katalogda bu rasmga o'xshaydigan bir nechta mahsulot bor, farqi hajmi va gul "
    "sonida — rasmdan qaysi biri ekanini aytib bo'lmaydi. group_matches dagi "
    "catalog_id larni send_catalog_album bilan yubor va mijozdan qaysi biri "
    "kerakligini so'ra. Bittasini tanlab narx aytma."
)
MEDIA_MATCH_NOT_FOUND_INSTRUCTION = (
    "Aynan mos mahsulot topilmadi. Katalogdan hech qanday rasm YUBORMA va taxmin qilib "
    "mahsulot nomi yoki narxini aytma. Mijozdan telefon raqamini so'ra va "
    "handoff_media_to_operator bilan operatorga uzat."
)
MEDIA_MATCH_LINK_GROUP_INSTRUCTION = (
    "Mijoz yuborgan post/reelga bir nechta katalog mahsuloti qo'yilgan, qaysi birini "
    "so'raganini aytib bo'lmaydi. group_matches dagi catalog_id larni send_catalog_album "
    "bilan yubor va \"siz yuborgan reeldan hozir bizda borlari shular\" degan mazmunda "
    "yozib, qaysi biri kerakligini so'ra."
)
MEDIA_MATCH_LINK_FALLBACK_INSTRUCTION = (
    "Rasmdagi aynan mahsulot topilmadi, lekin mijoz yuborgan post/reelga qo'yilgan "
    "kataloglar ma'lum. group_matches dagi catalog_id larni send_catalog_album bilan "
    "yubor va \"siz yuborgan reeldan hozir bizda borlari shular\" degan mazmunda yozib, "
    "qaysi biri kerakligini so'ra. Aynan rasmdagini topdim dema."
)
MEDIA_MATCH_SIMILAR_INSTRUCTION = (
    "Mijoz yuborgan rasmdagi aynan o'sha mahsulot katalogda yo'q. Bir nechta tanlab "
    "o'tirma — send_catalog_album ni catalog_ids BO'SH massiv bilan chaqirib butun "
    "katalogni yubor. Keyin shu mazmunda yoz: hozirda bizda bor gullar shular, "
    "shulardan tanlasangiz ham bo'ladi; yoki siz yuborgan gul ko'proq qiziq bo'lsa "
    "telefon raqamingizni yuboring, operatorlarimiz aloqaga chiqib aniq narxini "
    "aytishadi. \"Aynan shu\" yoki \"topdim\" dema. Mijoz raqamini bergach "
    "handoff_media_to_operator chaqir."
)
MEDIA_MATCH_CLOSE_INSTRUCTION = (
    "Rasmdagi gul katalogimizdagi bir nechta mahsulotga juda yaqin, lekin qaysi biri "
    "ekaniga to'liq ishonch yo'q. group_matches dagi catalog_id larni send_catalog_album "
    "bilan yubor va \"shu rasmga eng mos variantlarimiz shular\" degan mazmunda yozib, "
    "qaysi biri kerakligini so'ra. \"Aynan o'sha gul yo'q\" DEMA — bo'lishi mumkin, "
    "shunchaki qaysi biri ekanini mijozning o'zi tasdiqlashi kerak. Bittasini tanlab "
    "narx aytma. Agar mijoz \"bular emas\", \"o'xshamaydi\" desa, butun katalogni "
    "send_catalog_album bilan yuborib, telefon raqamini so'ra."
)
MEDIA_MATCH_CROP_INSTRUCTION = (
    "Rasmda bir nechta gul bor va mijoz bittasini ko'rsatgan, lekin qaysi biri ekanini "
    "aniq ayta olmadik. Katalogdan hech qanday rasm YUBORMA, narx aytma va hozircha "
    "operatorga ham uzatma. Mijozdan aynan o'sha gulni rasmdan kesib (crop qilib) "
    "qayta yuborishini iltimos qil — kesilgan rasmdan aniq topamiz. Bu iltimosni "
    "faqat bir marta qil."
)

MEDIA_MATCHING_PRIORITY_INSTRUCTION = """
MEDIA MATCHING FIRST:
If REAL_CONTEXT_JSON.conversation.customer_attachments has any customer image, story, post or reel media and the customer asks about that media, call match_ai_catalog_by_media before asking for phone or handing off to an operator.
An attachment whose kind is "ad" is the banner of the Instagram ad this conversation started from. The customer did not send it. Never run the photo matcher because of it.
A question about the address, opening hours, delivery or payment is not a question about the photo. Answer it, and leave the matcher alone even when a photo is sitting in the conversation.
Never skip media matching for "shu nechpul", "shundan bormi", "rasmdagi", "storydagi", "reeldagi", "tepadan 2chisi", "qizili", or circled/marked flower requests.
The tool result field allow_send is the only thing that decides what you may do next.
allow_send true: matches has exactly one item. Call send_catalog_image with that catalog_id, then give the item name, the price, and one next question, in the customer's own language. How you open that reply depends on own_post: when own_post is false, lead with one line saying this is what the shop has that looks like what they showed ("Bizda hozirda bor, siz ko'rsatganga o'xshagan variant:"). When own_post is true the customer sent one of our own stories or reels, so the flower is not a resemblance, it is that exact product — never say "o'xshagan" or "similar", just name it and price it.
Never invent a product name or a price. Every name and every price you write must come from a tool result in this turn. If you have not called a tool, you do not know what the shop sells.
Once a flower has been identified this turn, asking "what else do you have like it" is a request for the catalog, not for that same flower again: call send_catalog_album, do not call match_ai_catalog_by_media a second time on the same picture.
When the flower under discussion came from one of our own stories, "send me the picture" means that story's picture: call send_post_image with its social_post_id, which stays valid for the rest of the conversation. Sending the whole catalog album instead answers a question nobody asked.
detail "own_story_matched": the customer sent one of our own stories and the shop wrote its name and price into the system when the story was posted. That is the answer — give the story.title and the story.price_text, ask one next question, and send no catalog image. Do not say "similar" and do not name any other product. If they ask to see the flower again, call send_post_image with story.social_post_id.
allow_group true: call send_catalog_album with exactly the group_matches catalog_ids, then ask which one the customer means. Do not pick one of them yourself and do not quote a single price. The detail field says what the group is: "several_look_the_same" means these catalog items are indistinguishable in a photo and differ only in size and price; "instagram_link_group" and "instagram_link_fallback" mean these are the items posted on the reel or story the customer shared, so say that these are the ones from their reel that the shop has right now; "similar_only" (with show_whole_catalog) means the exact flower is NOT in the catalog and nothing on the shelf is close enough to offer as a substitute: call send_catalog_album with an empty catalog_ids to send the whole catalog, say these are the flowers available and they are welcome to pick one, and offer to have an operator price the flower they actually sent if they leave a number; "close_matches" means one of these probably IS it but the check was not conclusive, so offer them as the closest matches and let the customer confirm — do not tell them the flower is unavailable.
ask_for_crop true: the photo holds several arrangements and the customer pointed at one, but it could not be told apart. Do not send any catalog image, do not name an item, do not quote a price and do not hand off yet. Ask the customer to crop that one flower out of the photo and send it again, warmly and in one sentence. Ask this only once in a conversation.
allow_send false, allow_group false and ask_for_crop false: you have NOT identified the flower. Do not send any catalog image, do not name a catalog item, do not quote a price, and do not describe near_matches to the customer. near_matches is internal information for the operator only. Ask for the phone number and call handoff_media_to_operator.
Never send a catalog image and then say the operator will confirm. Those two things contradict each other. Either you identified it, or you hand it over.
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


def is_ad_attachment(url):
    """Bu mijoz yuborgan rasm emas, u bosgan reklamaning banneri.

    Instagram reklama orqali kelgan suhbatda o'sha banner mijozning HAR bir
    xabariga qo'shib yuboriladi va imzosi har safar o'zgaradi. Uni oddiy media
    deb qabul qilsak, mijoz "adres qayerda" deb yozganda ham rasm tahlili
    ishga tushadi va savoliga javob o'rniga katalog albomi ketadi.
    """
    return "facebook.com/ads/image" in (url or "").lower()


def customer_attachment_rows(history_messages):
    """Mijoz yuborgan rasm va media havolalari, eng yangisi oxirida.

    Havolalar Instagram yoki Telegram tomonidan beriladi, biz ularni serverga
    ko'chirmaymiz — leadga aynan shu havola yoziladi.
    """
    rows = []
    ad_seen = False
    for message in history_messages:
        if message.sender != "customer":
            continue
        for attachment in (message.metadata or {}).get("attachments", []) or []:
            url = attachment.get("url")
            if not url or any(row["url"] == url for row in rows):
                continue
            if is_ad_attachment(url):
                # Reklama banneri suhbatda bir marta hisobga olinadi.
                if ad_seen:
                    continue
                ad_seen = True
                rows.append({"kind": "ad", "url": url})
                continue
            rows.append({"kind": attachment.get("kind") or "media", "url": url})
    return rows[-MAX_CONTEXT_ATTACHMENTS:]


def previous_visit_context(conversation, history_messages):
    """Mijoz avval qachon yozgan va nima so'ragan.

    Ertaga qaytib kelgan mijozni "Sizga qanday gul kerak?" bilan kutib olish uni
    birinchi marta ko'rayotgandek muomala qilish demak. Sanani va o'tgan safargi
    so'rovni bilsak, suhbatni odamdek davom ettirish mumkin.
    """
    latest_customer = next((message for message in reversed(history_messages) if message.sender == "customer"), None)
    previous = None
    for message in reversed(history_messages):
        if latest_customer and message.id == latest_customer.id:
            continue
        if message.sender in {"customer", "ai", "operator"}:
            previous = message
            break
    if not previous or not latest_customer:
        return {"days_since_previous_message": None, "previous_message_date": "", "previous_request": ""}
    gap_days = (timezone.localtime(latest_customer.created_at).date() - timezone.localtime(previous.created_at).date()).days
    lead = conversation.leads.order_by("-created_at", "-id").first()
    return {
        "days_since_previous_message": gap_days,
        "previous_message_date": timezone.localtime(previous.created_at).date().isoformat(),
        "previous_request": (lead.request_uz if lead else "")[:200],
    }


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


def ai_catalog_match_items(limit=MAX_AI_CATALOG_MATCH_CANDIDATES):
    return list(available_ai_catalog_queryset().exclude(image_url="").order_by("-created_at", "-id")[:limit])


def catalog_match_row(item, score=None, fingerprint=None, verdict="", differences="", reason=""):
    """AI ga qaytariladigan bitta katalog qatori. Narx va izoh ham shu yerda."""
    row = {
        "catalog_id": item.id,
        "name": item.name,
        "type": item.arrangement_type,
        "quantity": item.quantity,
        "volume": item.volume,
        "price": str(item.price),
        "price_text": f"{money_uz(item.price)} so'm",
        "note_uz": item.note,
        "has_image": bool(item.image_url),
    }
    if score is not None:
        row["score"] = score
    if fingerprint:
        row["looks_like"] = fingerprint.get("summary", "")
    if verdict:
        row["verdict"] = verdict
    if differences:
        row["differences"] = differences[:400]
    if reason:
        row["reason"] = reason[:400]
    return row


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


def items_matching_link(items, link):
    """Katalogdagi instagram_link shu havola bilan bir xilmi."""
    key = media_url_match_key(link)
    if not key:
        return []
    matched = []
    for item in items:
        catalog_key = media_url_match_key(item.instagram_link)
        if not catalog_key:
            continue
        if catalog_key == key or catalog_key in key or key in catalog_key:
            matched.append(item)
    return matched


def customer_shared_our_post(attachment):
    """Mijoz yuborgani bizning o'z story/postimizmi.

    Storyga javob yozgan yoki uni directga tashlagan mijoz o'sha storydagi gulni
    so'rayapti — bu tasodifiy o'xshashlik emas.
    """
    kind = (attachment or {}).get("kind", "").lower()
    if kind in {"story", "reel", "post", "ig_story", "ig_reel", "share", "media_share"}:
        return True
    return bool(social_post_permalink_for_media(attachment))


def shared_link_is_the_media(attachment, media_url):
    """Mijoz yuborgani rasm emas, postning o'zi (reel/story share) bo'lsa.

    Bunday yuborishda tahlil qiladigan rasm yo'q — havolaning o'zi javob beradi.
    """
    kind = (attachment or {}).get("kind", "").lower()
    if kind in {"reel", "video", "post", "share", "ig_reel", "media_share"}:
        return True
    return "instagram.com/" in (media_url or "").lower()


def social_post_for_media(attachment):
    """Mijoz yuborgan story/reel qaysi bizning postimiz ekanini bazadan topadi.

    Instagram direct'da kelgan story rasm CDN havolasi bo'lib keladi, ichida
    faqat asset_id turadi. O'sha asset_id bo'yicha SocialPost topiladi.
    """
    from .webhook_services import social_post_by_media_or_url

    url = (attachment or {}).get("url") or ""
    if not url:
        return None
    try:
        return social_post_by_media_or_url(url=url)
    except Exception as error:
        print(f"AI_CATALOG_SOCIAL_POST_LOOKUP_FAILED url={url[:80]} error={error}", flush=True)
        return None


def social_post_permalink_for_media(attachment):
    """Post topilsa uning permalink'i — katalogdagi instagram_link bilan solishtirish uchun."""
    post = social_post_for_media(attachment)
    return (post.permalink or post.webhook_story_url or "") if post else ""


def social_post_answer(post):
    """Storyning o'zida nomi va narxi yozilgan bo'lsa, javob shu yerda.

    Operator storyni tizimga qo'yganda nomi, narxi va tavsifini yozadi. Mijoz
    o'sha storyni directga tashlaganda taxmin qilishning hojati yo'q — rasmni
    tahlil qilish faqat noaniqlik qo'shadi. Chatda aynan shunday bo'lgan:
    story "Alfalob 200 tali, 1 600 000" edi, rasm tahlili esa 100 talik
    boshqa mahsulotni topib bergan.
    """
    if not post or not post.is_active:
        return None
    title = (post.title_uz or "").strip()
    price = post.price
    if not title or price in (None, ""):
        return None
    return {
        "social_post_id": post.id,
        "title": title,
        "description": (post.description_uz or "").strip()[:400],
        "price": str(price),
        "price_text": f"{money_uz(price)} so'm",
        "flower_count": post.flower_count or None,
        "has_image": bool(post.image_url),
    }


def conversation_shared_links(conversation):
    """Suhbatda mijoz yuborgan story/reel havolalari, oxirgisi birinchi bo'lib.

    Mijoz avval reel yuborib, keyin o'sha reeldan screenshot tashlashi mumkin.
    Screenshot'ning o'z havolasi yo'q, lekin reel hali ham suhbatda turadi.
    """
    links = []
    for message in conversation.messages.filter(sender="customer").order_by("-created_at", "-id")[:30]:
        for attachment in (message.metadata or {}).get("attachments") or []:
            url = attachment.get("url") or ""
            if url and url not in links:
                links.append(url)
    return links


def direct_ai_catalog_link_matches(items, source_url, attachment=None, conversation=None):
    """Mijoz yuborgan story/post linki katalogdagi link bilan aynan mos kelsa.

    Bunda rasmni tahlil qilish shart emas — link o'zi aniq javob. Havola uch
    joydan qidiriladi: yuborilgan URL'ning o'zidan, o'sha media bog'langan
    SocialPost'ning permalink'idan, va suhbatda oldinroq yuborilgan story/reel'dan.
    """
    matched = items_matching_link(items, source_url)
    if matched:
        return matched
    permalink = social_post_permalink_for_media(attachment or {"url": source_url})
    if permalink:
        matched = items_matching_link(items, permalink)
        if matched:
            return matched
    if conversation is None:
        return []
    for link in conversation_shared_links(conversation):
        if media_url_match_key(link) == media_url_match_key(source_url):
            continue
        matched = items_matching_link(items, link)
        if matched:
            return matched
        permalink = social_post_permalink_for_media({"url": link})
        if permalink:
            matched = items_matching_link(items, permalink)
            if matched:
                return matched
    return []


# Aynan mos kelmagan, lekin mijozga ko'rsatishga arziydigan mahsulot uchun eng past
# ball. Rangi butunlay boshqa bo'lgan mahsulot 45 dan oshmaydi (DIFFERENT_COLOUR_CEILING),
# lekin gul turi va idishi bir xil bo'lsa u ham ko'rsatishga arziydi: "binafsha savat
# yo'q, lekin savatlarimiz shular" — bu mijozni quruq qaytarishdan yaxshi.
SIMILAR_ENOUGH_SCORE = 42

# Bundan yuqori ball olgan nomzod haqida "bizda bunday gul yo'q" deb bo'lmaydi —
# u aniq o'sha mahsulot bo'lishi mumkin, faqat tekshiruvdan o'tmagan.
CLOSE_MATCH_SCORE = 70


def similar_enough_rows(rejected, source, limit=3):
    """Aynan o'shasi bo'lmasa, katalogdagi eng yaqin mahsulotlar.

    Model bu mahsulotlarni "different" deb belgilagan bo'lishi mumkin va u haq —
    biz ham mijozga aynan o'shasi emasligini aytamiz. Shuning uchun bu yerda
    modelning hukmi emas, fingerprint o'xshashligi hal qiladi.

    Gul turi va idishi mos kelishi shart: pushti savat so'ralganda qizil quti
    ko'rsatish o'xshashlik emas. Rangi boshqa bo'lishi mumkin — binafsha savat
    yo'q bo'lsa, savatlarimizni ko'rsatish mijozni quruq qaytarishdan yaxshi.
    """
    source_family = vision_services.container_family(source)
    rows = [
        row for row in rejected
        if row["score"] >= SIMILAR_ENOUGH_SCORE
        and vision_services.families_can_match(source_family, row["family"])
        and vision_services.forms_can_match(source.get("flower_form"), row["fingerprint"].get("flower_form"))
    ]
    return sorted(rows, key=lambda row: row["score"], reverse=True)[:limit]


def unanswered_customer_media(conversation):
    """Oxirgi AI javobidan keyin mijoz rasm/story/reel yuborganmi.

    Faqat yangi media uchun. Eski rasm haqida mijoz keyinroq savol bersa, model
    o'zi qaror qiladi — u yerda majburlash suhbatni orqaga tortib yuborardi.
    Reklama banneri media hisoblanmaydi: u mijozning har bir xabariga o'zi
    qo'shiladi, mijoz uni yubormagan.
    """
    messages = list(conversation.messages.exclude(sender="system").order_by("-created_at", "-id")[:12])
    for message in messages:
        if message.sender == "ai":
            return False
        if message.sender != "customer":
            continue
        for attachment in (message.metadata or {}).get("attachments") or []:
            url = attachment.get("url")
            if url and not is_ad_attachment(url):
                return True
    return False


def crop_would_help(conversation, source):
    """Kesilgan rasm so'rashning ma'nosi bormi.

    Faqat mijoz kadrdagi bitta gulni ko'rsatgan va kadrda boshqa gullar ham
    turgan holatda. Bitta gul turgan rasmni kesib berish hech narsani o'zgartirmaydi.
    Bir suhbatda bu iltimos bir marta qilinadi — mijoz kesa olmasa yoki kesilgani
    ham topilmasa, ikkinchi marta so'rash o'rniga operatorga uzatiladi.
    """
    if not source.get("region_requested"):
        return False
    if not source.get("multiple_products_visible") and len(source.get("visible_products") or []) < 2:
        return False
    asked = Message.objects.filter(
        conversation=conversation,
        sender="system",
        metadata__ai_catalog_media_match__detail="ask_for_crop",
    ).exists()
    return not asked


def media_match_result(conversation, payload):
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"ai_catalog_media_match": payload})
    return payload


def media_match_outcome(tool_results):
    """Shu navbatda media matching ishlaganmi va nima ruxsat etilgani.

    Ishonchli mos kelmasa allowed bo'sh bo'ladi va katalog rasmi yuborilmaydi.
    Production'da aynan shu holat noto'g'ri gul yuborilishiga sabab bo'lgan edi:
    model mos kelmagan bo'lsa ham ikkita katalog rasmini yuborib, ustidan
    "operatorlarimiz aniq javob berishadi" deb yozgan.
    """
    outcome = {"ran": False, "allow_send": False, "allow_group": False, "ask_for_crop": False, "allowed_ids": set(), "group_ids": set(), "candidate_ids": set()}
    for row in tool_results or []:
        if row.get("name") != "match_ai_catalog_by_media":
            continue
        output = row.get("output") or {}
        outcome["ran"] = True
        def ids(key):
            return {match.get("catalog_id") for match in (output.get(key) or []) if match.get("catalog_id")}
        allowed = ids("matches")
        group = ids("group_matches")
        if output.get("allow_send") and allowed:
            outcome["allow_send"] = True
            outcome["allowed_ids"] |= allowed
        if output.get("allow_group") and group:
            outcome["allow_group"] = True
            outcome["group_ids"] |= group
        if output.get("ask_for_crop"):
            outcome["ask_for_crop"] = True
        outcome["candidate_ids"] |= allowed | group | ids("near_matches")
    return outcome


def media_match_send_block(tool_results, name, catalog_ids):
    """Ishonchsiz media matchdan keyin katalog rasmi yuborilishini to'xtatadi.

    Bu AI ning matnini o'zgartirmaydi — faqat tool chaqiruvini rad etadi va nima
    qilish kerakligini javobda yozib beradi.
    """
    outcome = media_match_outcome(tool_results)
    if not outcome["ran"] or outcome["allow_send"]:
        return None
    if outcome["allow_group"]:
        # Bir nechta mahsulot bir xil ko'rinadi: albom bo'lib hammasi ketadi, lekin
        # bittasini ajratib "mana sizniki" deb yuborib bo'lmaydi.
        if name == "send_catalog_album" and set(catalog_ids or []) <= outcome["group_ids"]:
            return None
        return {"ok": False, "detail": "media_match_needs_a_group", "group_ids": sorted(outcome["group_ids"]), "instruction_uz": MEDIA_MATCH_GROUP_INSTRUCTION}
    if name == "send_catalog_image":
        blocked = True
    else:
        blocked = bool(set(catalog_ids or []) & outcome["candidate_ids"])
    if not blocked:
        return None
    if outcome["ask_for_crop"]:
        return {"ok": False, "detail": "media_match_needs_a_crop", "instruction_uz": MEDIA_MATCH_CROP_INSTRUCTION}
    return {
        "ok": False,
        "detail": "media_match_not_confident",
        "instruction_uz": MEDIA_MATCH_NOT_FOUND_INSTRUCTION,
    }


def whole_catalog_already_sent(conversation, minimum=6):
    """Butun katalog bu suhbatda allaqachon albom bo'lib ketganmi."""
    for message in conversation.messages.filter(sender="system").order_by("-created_at", "-id")[:40]:
        rows = ((message.metadata or {}).get("catalog_album_result") or {}).get("items") or []
        if len([row for row in rows if row.get("delivered")]) >= minimum:
            return True
    return False


def catalog_image_already_sent(conversation, catalog_id):
    """Shu katalog rasmi bu suhbatda allaqachon yuborilganmi."""
    if not catalog_id:
        return False
    for message in conversation.messages.filter(sender="system").order_by("-created_at", "-id")[:40]:
        result = (message.metadata or {}).get("image_tool_result") or {}
        if result.get("catalog_id") == catalog_id and result.get("delivered"):
            return True
        for row in ((message.metadata or {}).get("catalog_album_result") or {}).get("items") or []:
            if row.get("catalog_id") == catalog_id and row.get("delivered"):
                return True
    return False


def matched_story_ids(conversation, tool_results):
    """Suhbatda media matching qaysi storylarni topgan bo'lsa, o'shalarning id si.

    Mijoz storyni yuborib narxini bilgach, keyingi xabarda "rasmini ko'rsat"
    deydi — o'shanda shu navbatda match natijasi bo'lmaydi, lekin story hali
    ham suhbatning mavzusi. Shuning uchun oldingi navbatlar ham hisobga olinadi.
    """
    ids = set()
    for row in tool_results or []:
        if row.get("name") != "match_ai_catalog_by_media":
            continue
        story = (row.get("output") or {}).get("story") or {}
        if story.get("social_post_id"):
            ids.add(story["social_post_id"])
    for message in conversation.messages.filter(sender="system").order_by("-created_at", "-id")[:20]:
        story = ((message.metadata or {}).get("ai_catalog_media_match") or {}).get("story") or {}
        if story.get("social_post_id"):
            ids.add(story["social_post_id"])
    return ids


def send_social_post_image(conversation, social_post_id, tool_results=None):
    """Mijoz yuborgan storyning o'z rasmini qayta yuboradi.

    Faqat shu navbatda media matching topgan story uchun. Aks holda AI o'zi
    tanlagan boshqa postning rasmini yuborib qo'yishi mumkin edi.
    """
    allowed = matched_story_ids(conversation, tool_results)
    if not allowed:
        return {"ok": False, "detail": "no_matched_story", "instruction_uz": "Bu tool faqat match_ai_catalog_by_media own_story_matched qaytarganda ishlaydi."}
    if social_post_id not in allowed:
        return {"ok": False, "detail": "story_not_matched", "allowed_ids": sorted(allowed)}
    post = SocialPost.objects.filter(id=social_post_id, is_active=True).first()
    if not post or not post.image_url:
        return {"ok": False, "detail": "post_image_not_found"}
    delivered, detail, sent = send_image_to_customer(conversation.customer, post.image_url, conversation)
    if not delivered:
        return {"ok": False, "detail": detail or "send_failed"}
    Message.objects.create(conversation=conversation, sender="system", text="", metadata={"post_image_result": {"social_post_id": post.id, "image_url": post.image_url, "detail": detail, "sent": sent}})
    return {"ok": True, "image_sent": True, "social_post_id": post.id, "title": post.title_uz, "price_text": f"{money_uz(post.price)} so'm" if post.price else ""}


def first_confident_media_match(tool_results):
    for row in reversed(tool_results or []):
        if row.get("name") != "match_ai_catalog_by_media":
            continue
        output = row.get("output") or {}
        if not output.get("allow_send"):
            continue
        matches = output.get("matches") or []
        if len(matches) == 1:
            return matches[0]
    return None


def tool_results_sent_catalog(tool_results, catalog_id):
    for row in tool_results or []:
        if row.get("name") == "send_catalog_image" and (row.get("output") or {}).get("catalog_id") == catalog_id and (row.get("output") or {}).get("ok"):
            return True
    return False


def apply_media_match_safeguard(conversation, result, tool_results):
    """Aniq mos kelgan mahsulot rasmi yuborilmay qolgan bo'lsa, o'zi yuboradi.

    Javob matniga tegilmaydi — matn tizim promptining ishi.
    """
    match = first_confident_media_match(tool_results)
    if not match:
        return result
    item = catalog_album_queryset().filter(id=match.get("catalog_id")).first()
    if item and not tool_results_sent_catalog(tool_results, item.id):
        output = send_catalog_item_image(conversation, item)
        tool_results.append({"name": "send_catalog_image", "arguments": {"query": "", "catalog_id": item.id, "safeguard": True}, "output": output})
    return result


def match_ai_catalog_by_media(conversation, source_url="", user_text="", limit=MAX_AI_CATALOG_MATCH_CANDIDATES):
    """Mijoz yuborgan rasmni AI katalogdagi mahsulot bilan solishtiradi.

    Qaror uch bosqichda chiqadi: link tekshiruvi -> fingerprint bo'yicha qisqa ro'yxat ->
    faqat shu qisqa ro'yxatni mijoz rasmi bilan yonma-yon ko'rsatib tasdiqlatish.
    Yakuniy qarorni model emas, shu funksiya chiqaradi. Ishonch bo'lmasa allow_send
    false bo'ladi va AI hech qanday katalog rasmini yubora olmaydi.
    """
    attachment = latest_customer_media_attachment(conversation, source_url)
    if not attachment or not attachment.get("url"):
        return {"ok": False, "allow_send": False, "allow_group": False, "detail": "no_customer_media", "matches": [], "group_matches": [], "near_matches": []}
    items = ai_catalog_match_items(max(1, min(int(limit or MAX_AI_CATALOG_MATCH_CANDIDATES), MAX_AI_CATALOG_MATCH_CANDIDATES)))
    if not items:
        return {"ok": False, "allow_send": False, "allow_group": False, "detail": "ai_catalog_empty_or_no_images", "source": attachment, "matches": [], "group_matches": [], "near_matches": []}
    media_url = attachment["url"]

    # Mijoz yuborgan story/reel bizning qaysi postimiz ekani ma'lum bo'lsa, o'sha
    # postga qo'yilgan kataloglar aniq javob — rasmni tahlil qilish shart emas.
    own_post = social_post_for_media(attachment)
    linked = direct_ai_catalog_link_matches(items, media_url, attachment=attachment, conversation=conversation)
    story = social_post_answer(own_post) if not linked else None
    if story:
        # Storyning o'zida nomi va narxi turibdi — bu eng aniq manba.
        return media_match_result(conversation, {
            "ok": True,
            "allow_send": False,
            "allow_group": False,
            "detail": "own_story_matched",
            "source": attachment,
            "source_description": "Mijoz bizning storyimizni yubordi, uning ma'lumoti tizimda saqlangan.",
            "own_post": True,
            "story": story,
            "matches": [],
            "group_matches": [],
            "near_matches": [],
            "no_match_reason": "",
            "instruction_uz": MEDIA_MATCH_OWN_STORY_INSTRUCTION,
        })
    if len(linked) == 1:
        return media_match_result(conversation, {
            "ok": True,
            "allow_send": True,
            "allow_group": False,
            "detail": "instagram_link_matched",
            "source": attachment,
            "source_description": "Mijoz yuborgan Instagram linki katalogdagi mahsulot bilan aynan mos keldi.",
            "matches": [catalog_match_row(item, reason="instagram_link matched") for item in linked],
            "group_matches": [],
            "near_matches": [],
            "no_match_reason": "",
            "own_post": True,
            "instruction_uz": MEDIA_MATCH_OWN_POST_INSTRUCTION,
        })
    if linked and (not vision_compatible_media_url(media_url, attachment.get("kind")) or shared_link_is_the_media(attachment, media_url)):
        # Reel video: tahlil qiladigan rasm yo'q, lekin o'sha reeldagi mahsulotlar ma'lum.
        return media_match_result(conversation, {
            "ok": True,
            "allow_send": False,
            "allow_group": True,
            "detail": "instagram_link_group",
            "source": attachment,
            "source_description": "Mijoz yuborgan Instagram postiga bir nechta katalog mahsuloti qo'yilgan.",
            "matches": [],
            "group_matches": [catalog_match_row(item, reason="instagram_link matched") for item in linked[:MAX_LINK_MATCHES]],
            "near_matches": [],
            "no_match_reason": "",
            "instruction_uz": MEDIA_MATCH_LINK_GROUP_INSTRUCTION,
        })

    if not vision_compatible_media_url(media_url, attachment.get("kind")):
        return {"ok": False, "allow_send": False, "allow_group": False, "detail": "media_url_not_image", "source": attachment, "matches": [], "group_matches": [], "near_matches": []}
    api_key = openai_api_key()
    if not api_key:
        return {"ok": False, "allow_send": False, "allow_group": False, "detail": "openai_api_key_missing", "source": attachment, "matches": [], "group_matches": [], "near_matches": []}

    text = (user_text or "").strip()
    if not text:
        latest_customer = conversation.messages.filter(sender="customer").order_by("-created_at", "-id").first()
        text = latest_customer.text if latest_customer else ""
    try:
        source = vision_services.analyze_image(media_url, context_text=text, with_region=True, api_key=api_key)
    except Exception as error:
        print(f"AI_CATALOG_MEDIA_SOURCE_FAILED conversation={conversation.id} error={error}", flush=True)
        return {"ok": False, "allow_send": False, "allow_group": False, "detail": "source_analysis_failed", "source": attachment, "error": str(error)[:400], "matches": [], "group_matches": [], "near_matches": []}
    if not source:
        return {"ok": False, "allow_send": False, "allow_group": False, "detail": "source_analysis_empty", "source": attachment, "matches": [], "group_matches": [], "near_matches": []}

    scored = vision_services.shortlist_candidates(source, items, api_key=api_key)
    shortlist = [row for row in scored[:vision_services.shortlist_size()] if row["score"] >= AI_CATALOG_SHORTLIST_FLOOR]
    if not shortlist and linked:
        return media_match_result(conversation, {
            "ok": True,
            "allow_send": False,
            "allow_group": True,
            "detail": "instagram_link_fallback",
            "source": attachment,
            "source_description": source.get("summary", ""),
            "region_description": source.get("region_description", ""),
            "matches": [],
            "group_matches": [catalog_match_row(item, reason="instagram_link matched") for item in linked[:MAX_LINK_MATCHES]],
            "near_matches": [],
            "no_match_reason": "Rasmdagi aynan mahsulot topilmadi, post havolasidagilar ko'rsatilyapti.",
            "instruction_uz": MEDIA_MATCH_LINK_FALLBACK_INSTRUCTION,
        })
    if not shortlist:
        return media_match_result(conversation, {
            "ok": False,
            "allow_send": False,
            "allow_group": False,
            "detail": "no_similar_catalog_item",
            "source": attachment,
            "source_description": source.get("summary", ""),
            "region_description": source.get("region_description", ""),
            "instruction_uz": MEDIA_MATCH_NOT_FOUND_INSTRUCTION,
            "matches": [],
            "group_matches": [],
            "near_matches": [],
            "no_match_reason": "Katalogda bu rasmga o'xshash mahsulot topilmadi.",
        })

    verdicts = vision_services.verify_candidates(media_url, source, shortlist, customer_text=text, api_key=api_key)

    # Mijoz rasmidagi idish savatmi, buketmi, quticha yoki vazami. Model bir savatni
    # qo'ldagi buketga "same_product" deb qo'yishi mumkin, shu yerda to'xtatiladi.
    source_family = vision_services.container_family(source)
    required = vision_services.required_score(source)

    passed = []
    rejected = []
    for row in shortlist:
        item = row["item"]
        judgement = verdicts.get(item.id) or {}
        # Yakuniy shart backendda: model "same_product" desa ham gul shakli, rangi va
        # idishi mos kelmasa yoki fingerprint bali chegaradan past bo'lsa o'tmaydi.
        row["verdict"] = judgement.get("verdict") or "different"
        row["differences"] = judgement.get("differences") or ""
        row["family"] = vision_services.container_family(row["fingerprint"], item.arrangement_type)
        row["passed"] = (
            row["verdict"] == "same_product"
            and bool(judgement.get("flower_form_match"))
            and bool(judgement.get("color_match"))
            and bool(judgement.get("container_match"))
            and row["score"] >= required
            and vision_services.families_can_match(source_family, row["family"])
            and vision_services.sizes_can_match(source, row["fingerprint"])
            and vision_services.forms_can_match(source.get("flower_form"), row["fingerprint"].get("flower_form"))
        )
        (passed if row["passed"] else rejected).append(row)

    def as_row(row):
        return catalog_match_row(row["item"], score=row["score"], fingerprint=row["fingerprint"], verdict=row["verdict"], differences=row["differences"])

    common = {
        "source": attachment,
        "source_description": source.get("summary", ""),
        "region_requested": bool(source.get("region_requested")),
        "region_description": source.get("region_description", ""),
        "multiple_products_visible": bool(source.get("multiple_products_visible")),
        "visible_products": source.get("visible_products") or [],
        "chosen_position": source.get("chosen_position") or 0,
    }
    near = [as_row(row) for row in rejected if row["verdict"] in {"same_product", "similar_only"}]
    winner = max(passed, key=lambda row: row["score"], default=None)
    if not winner:
        # Mijoz ko'p gulli rasmda bittasini chizib ko'rsatgan bo'lsa, aybdor ko'pincha
        # rasmning o'zi: qolgan gullar ham kadrda turadi va tahlilga aralashadi.
        # Operatorga uzatishdan oldin o'sha gulni kesib yuborishini so'raymiz.
        if crop_would_help(conversation, source):
            return media_match_result(conversation, dict(common, **{
                "ok": True,
                "allow_send": False,
                "allow_group": False,
                "ask_for_crop": True,
                "detail": "ask_for_crop",
                "matches": [],
                "group_matches": [],
                "near_matches": near[:3],
                "no_match_reason": "Rasmda bir nechta gul bor, ko'rsatilganini aniq ajratib bo'lmadi.",
                "instruction_uz": MEDIA_MATCH_CROP_INSTRUCTION,
            }))
        # Mijoz reel yuborgan bo'lsa, o'sha reelga qo'yilgan kataloglar aniq ma'lum.
        # Rasmdagi aynan gulni topolmasak ham, "shu reeldan hozir borlari shular"
        # deyish operatorga uzatishdan ko'ra ancha foydali.
        if linked:
            return media_match_result(conversation, dict(common, **{
                "ok": True,
                "allow_send": False,
                "allow_group": True,
                "detail": "instagram_link_fallback",
                "matches": [],
                "group_matches": [catalog_match_row(item, reason="instagram_link matched") for item in linked[:MAX_LINK_MATCHES]],
                "near_matches": near[:3],
                "no_match_reason": "Rasmdagi aynan mahsulot topilmadi, post havolasidagilar ko'rsatilyapti.",
                "instruction_uz": MEDIA_MATCH_LINK_FALLBACK_INSTRUCTION,
            }))
        # Aynan o'shasi yo'q bo'lsa ham, mijoz quruq ketmasin: katalogdagi eng
        # o'xshashlarini ko'rsatamiz. Bu "topdim" degani emas va shuni aytish shart.
        similar = similar_enough_rows(rejected, source)
        if similar:
            # Eng yaqin nomzod baland ball olgan bo'lsa, bu "bizda bunday gul yo'q"
            # degani emas — model o'z javobida ziddiyatga tushgan bo'lishi mumkin
            # (mahsulotni "same_product" deb, ayni paytda rangini mos emas deb).
            # Bunday holatda mijozga "aynan yo'q" deyish yolg'on bo'lardi.
            close = similar[0]["score"] >= CLOSE_MATCH_SCORE
            if close:
                return media_match_result(conversation, dict(common, **{
                    "ok": True,
                    "allow_send": False,
                    "allow_group": True,
                    "detail": "close_matches",
                    "matches": [],
                    "group_matches": [as_row(row) for row in similar],
                    "near_matches": [],
                    "no_match_reason": "",
                    "instruction_uz": MEDIA_MATCH_CLOSE_INSTRUCTION,
                }))
            # Uzoqdan o'xshagan ikki-uchta mahsulotni ko'rsatish mijozni ishontirmaydi.
            # Butun katalogni ko'rsatib, tanlash imkonini berish va aynan o'sha gul
            # kerak bo'lsa operatorga uzatish ancha halol va foydali.
            return media_match_result(conversation, dict(common, **{
                "ok": True,
                "allow_send": False,
                "allow_group": True,
                "show_whole_catalog": True,
                "detail": "similar_only",
                "matches": [],
                "group_matches": [],
                "near_matches": [as_row(row) for row in similar],
                "no_match_reason": "Aynan shu mahsulot yo'q, butun katalog ko'rsatilyapti.",
                "instruction_uz": MEDIA_MATCH_SIMILAR_INSTRUCTION,
            }))
        return media_match_result(conversation, dict(common, **{
            "ok": False,
            "allow_send": False,
            "allow_group": False,
            "detail": "not_confident",
            "matches": [],
            "group_matches": [],
            "near_matches": near[:3],
            "no_match_reason": "Katalogdagi mahsulotlar bilan aynan mos kelmadi.",
            "instruction_uz": MEDIA_MATCH_NOT_FOUND_INSTRUCTION,
        }))

    # G'olibdan rasmda ajratib bo'lmaydigan mahsulotlar bormi. Bo'lsa bittasini tanlab
    # narx aytish xato bo'ladi — hammasini ko'rsatib mijozning o'zidan so'raymiz.
    #
    # Guruh tor bo'lishi kerak. Model "similar_only" degani — "bu boshqa mahsulot";
    # uni mijozga ko'rsatish "shulardan qaysi biri" degan keraksiz savol tug'diradi.
    # Shuning uchun guruhga faqat modelning o'zi ham aynan shu mahsulot deganlari,
    # va ballari g'olibga juda yaqinlari kiradi.
    twins = [
        row for row in vision_services.indistinguishable_items(winner, shortlist)
        if row["verdict"] == "same_product"
    ]
    twins += [
        row for row in passed
        if row is not winner and row not in twins and winner["score"] - row["score"] <= TIED_SCORE_GAP
    ]
    # Savat bilan buketni yonma-yon qo'yib "qaysi biri" deb so'rash ma'nosiz — ular
    # rasmda aniq farq qiladi, mijoz allaqachon birini ko'rsatgan.
    twins = [row for row in twins if row["family"] == winner["family"]]
    twins = [row for row in twins if vision_services.sizes_can_match(winner["fingerprint"], row["fingerprint"])]
    twins = [row for row in twins if vision_services.forms_can_match(winner["fingerprint"].get("flower_form"), row["fingerprint"].get("flower_form"))]
    # Narxi g'olib bilan bir xil bo'lgan egizakni so'rashning ma'nosi yo'q — mijoz
    # qaysi birini tanlasa ham javob o'zgarmaydi.
    twins = [row for row in twins if row["item"].price != winner["item"].price]
    if twins:
        group = [as_row(row) for row in [winner] + sorted(twins, key=lambda row: row["score"], reverse=True)][:4]
        group_ids = {row["catalog_id"] for row in group}
        return media_match_result(conversation, dict(common, **{
            "ok": True,
            "allow_send": False,
            "allow_group": True,
            "detail": "several_look_the_same",
            "matches": [],
            "group_matches": group,
            "near_matches": [row for row in near if row["catalog_id"] not in group_ids][:3],
            "no_match_reason": "",
            "instruction_uz": MEDIA_MATCH_GROUP_INSTRUCTION,
        }))

    # Mijoz bizning o'z story yoki reelimizni yuborgan bo'lsa, undagi gul aniq —
    # unga "o'xshagan variant" deb javob berish mijozni shubhaga soladi.
    own_post = customer_shared_our_post(attachment)
    return media_match_result(conversation, dict(common, **{
        "ok": True,
        "allow_send": True,
        "allow_group": False,
        "detail": "matched",
        "matches": [as_row(winner)],
        "group_matches": [],
        "near_matches": near[:3],
        "no_match_reason": "",
        "own_post": own_post,
        "instruction_uz": MEDIA_MATCH_OWN_POST_INSTRUCTION if own_post else MEDIA_MATCH_FOUND_INSTRUCTION,
    }))


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
            "description": "Majburiy media matching tool. Conversation.customer_attachments bo'lsa va mijoz yuborgan rasm/story/post/reel haqida narx, bor-yo'qlik yoki aynan qaysi gul ekanini so'rasa, telefon so'rash yoki handoff qilishdan oldin doim shu toolni chaqir. Mijoz 'shu nechpul', 'shundan bormi', 'tepadan 2chisi', 'qizili', 'chizilgan joydagi' kabi yozsa shu tool shart. source_url bo'sh bo'lsa oxirgi customer media olinadi. Natijadagi allow_send=true bo'lsagina matches ichidagi mahsulot mijozniki: send_catalog_image chaqir. allow_group=true bo'lsa bir nechta mahsulot rasmda bir xil ko'rinadi: group_matches dagi catalog_id larni send_catalog_album bilan yubor va qaysi biri kerakligini so'ra. allow_group=true bo'lgan holatlar detail bilan farqlanadi: several_look_the_same — bir xil ko'rinadigan mahsulotlar; instagram_link_group va instagram_link_fallback — mijoz yuborgan reel/storyga qo'yilgan mahsulotlar, siz yuborgan reeldan hozir borlari shular deb ayt; similar_only — aynan o'sha gul katalogda yo'q, bular faqat o'xshaydiganlari, shuni rostini ayt. ask_for_crop=true bo'lsa rasmda bir nechta gul bor va mijoz bittasini ko'rsatgan, lekin qaysi biri ekanini ajratib bo'lmadi: rasm yuborma, narx aytma, handoff ham qilma — mijozdan o'sha gulni rasmdan kesib qayta yuborishini iltimos qil. Uchalasi ham false bo'lsa gul aniqlanmagan — katalogdan rasm yuborilmaydi, nom va narx aytilmaydi, near_matches mijozga ko'rsatilmaydi (u faqat operator uchun), telefon so'rab handoff_media_to_operator chaqiriladi.",
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
            "name": "send_post_image",
            "description": "Mijoz yuborgan bizning story/postimizning o'z rasmini qayta yuborish. Faqat match_ai_catalog_by_media detail=own_story_matched qaytarganda va mijoz o'sha gulning rasmini so'raganda ishlatiladi. social_post_id o'sha tool natijasidagi story.social_post_id dan olinadi. Katalog mahsuloti uchun bu tool emas, send_catalog_image ishlatiladi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "social_post_id": {"type": "integer"},
                },
                "required": ["social_post_id"],
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
    remember_sent_instagram_message(result)
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
        delivered, detail = send_catalog_album_chunk(customer, platform, chat_id, chunk, conversation=conversation)
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


SENT_INSTAGRAM_MESSAGE_IDS = []


def remember_sent_instagram_message(result):
    """Biz yuborgan Instagram xabar id sini eslab qolamiz.

    Faqat bitta so'rov davomida kerak: albom yuborilgach uning echo'si darhol
    keladi va o'sha paytda bazada hali hech narsa yozilmagan bo'ladi.
    """
    if not isinstance(result, dict):
        return
    message_id = result.get("message_id") or ""
    if not message_id:
        return
    SENT_INSTAGRAM_MESSAGE_IDS.append(message_id)
    del SENT_INSTAGRAM_MESSAGE_IDS[:-200]


def send_catalog_album_chunk(customer, platform, chat_id, chunk, conversation=None):
    """Bitta albom xabarini yuboradi. (delivered, detail) qaytaradi, exception ko'tarmaydi.

    Do'konning bir nechta Instagram akkaunti bor. Mijoz qaysi akkauntga yozgan bo'lsa,
    javob ham o'sha akkauntdan ketishi kerak — bo'lmasa Instagram 400 qaytaradi.
    """
    account_id = conversation_instagram_account_id(conversation) if conversation else None
    try:
        if platform == "telegram":
            if len(chunk) == 1:
                result = telegram_send_image(chat_id, chunk[0]["image_url"], caption=chunk[0]["caption"])
            else:
                result = telegram_send_media_group(chat_id, [{"image_url": row["image_url"], "caption": row["caption"]} for row in chunk])
        else:
            result = instagram_send_carousel(chat_id, [{"title": f"{row['position']}. {row['item'].name}", "subtitle": f"{money_uz(row['item'].price)} so'm", "image_url": row["image_url"]} for row in chunk], account_id)
    except Exception as error:
        print(f"CATALOG_ALBUM_FAILED customer={customer.id} platform={platform} count={len(chunk)} error={error}", flush=True)
        return False, "album_failed"
    if isinstance(result, dict) and result.get("mocked"):
        return True, "mocked"
    if isinstance(result, dict) and result.get("ok") is False:
        print(f"CATALOG_ALBUM_REJECTED customer={customer.id} platform={platform} result={result}", flush=True)
        return False, "album_rejected"
    # Instagram yuborilgan xabarni webhook orqali bizga qaytaradi. Uning id sini
    # eslab qolmasak, o'z albomimiz "operator javob yozdi" deb hisoblanadi va AI
    # o'zini o'n besh daqiqaga to'xtatib qo'yadi.
    remember_sent_instagram_message(result)
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


def execute_ai_tool(name, arguments, conversation, tool_results=None):
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
        blocked = media_match_send_block(tool_results, name, catalog_ids)
        if blocked:
            return blocked
        items = catalog_album_items(catalog_ids)
        if not items:
            return {"ok": False, "detail": "catalog_empty", "items": [], "not_sent": []}
        if not catalog_ids and whole_catalog_already_sent(conversation):
            # Butun katalogni ikkinchi marta yuborish mijozga yangi hech narsa
            # bermaydi — u allaqachon hammasini ko'rgan.
            return {
                "ok": False,
                "detail": "catalog_already_sent",
                "instruction_uz": (
                    "Butun katalog bu suhbatda allaqachon yuborilgan, qayta yuborma. "
                    "Mijoz katalogdan hech narsa tanlamayotgan bo'lsa telefon raqamini so'ra "
                    "va bergach handoff_media_to_operator chaqir. Raqam berishni xohlamasa "
                    "bahslashma, operatorlar chat orqali ko'rib chiqishini qisqa ayt."
                ),
            }
        return send_catalog_album(conversation, items)
    if name == "send_catalog_image":
        query = arguments.get("query") or ""
        catalog_id = arguments.get("catalog_id")
        blocked = media_match_send_block(tool_results, name, [catalog_id] if catalog_id else [])
        if blocked:
            return blocked
        item = catalog_album_queryset().filter(id=catalog_id).first() if catalog_id else None
        if not item:
            item = _catalog_item_for_ai(query)
        if not item:
            return {"ok": False, "detail": "catalog_not_found"}
        if catalog_image_already_sent(conversation, item.id):
            # Mijoz bu rasmni allaqachon ko'rgan. Uni qayta yuborish "yana qanaqalari
            # bor" degan savolga javob emas — o'sha savolni yana bir marta bermoqda.
            return {
                "ok": False,
                "detail": "catalog_image_already_sent",
                "catalog_id": item.id,
                "instruction_uz": (
                    f"{item.name} rasmi bu suhbatda allaqachon yuborilgan. Uni qayta yuborma. "
                    "Mijoz boshqa variantlarni so'rayotgan bo'lsa send_catalog_album chaqirib "
                    "katalogni ko'rsat, aks holda shunchaki savoliga javob yoz."
                ),
            }
        return send_catalog_item_image(conversation, item)
    if name == "send_post_image":
        return send_social_post_image(conversation, arguments.get("social_post_id"), tool_results=tool_results)
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
            **previous_visit_context(conversation, history_messages),
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
    forced_media_match = False
    for _ in range(10):
        calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") == "function_call":
                calls.append(item)
        if not calls:
            # Model media matching toolini chaqirmasdan javob yozib qo'yishi mumkin va
            # o'shanda katalogda yo'q mahsulotni o'ylab topadi. Production'da aynan
            # shunday bo'lgan: mijoz story yuborgan, model "Alfalob 200 tali, 1 600 000
            # so'm" deb javob bergan — katalogda bunday mahsulot ham, bunday narx ham
            # yo'q. Toolni o'zimiz chaqirib, natijasi bilan javobni qayta yozdiramiz.
            already_matched = any(row.get("name") == "match_ai_catalog_by_media" for row in tool_results)
            if not forced_media_match and not already_matched and unanswered_customer_media(conversation):
                forced_media_match = True
                output = execute_ai_tool(
                    "match_ai_catalog_by_media",
                    {"source_url": None, "user_text": latest_customer_text},
                    conversation,
                    tool_results=tool_results,
                )
                tool_results.append({"name": "match_ai_catalog_by_media", "arguments": {"source_url": None, "user_text": latest_customer_text, "forced_by_backend": True}, "output": output})
                response = client.responses.create(
                    model=ai_settings.openai_model or settings.OPENAI_MODEL,
                    instructions=effective_instructions,
                    previous_response_id=response.id,
                    input=[{
                        "role": "user",
                        "content": (
                            "SYSTEM: Mijoz media yuborgan, lekin sen match_ai_catalog_by_media ni chaqirmading. "
                            "Tool sening o'rningga chaqirildi, natijasi quyida. Javobingni shu natija asosida "
                            "qayta yoz va instruction_uz da yozilganini bajar. Katalogda yo'q mahsulot nomini "
                            "yoki narxini yozish qat'iy taqiqlanadi.\n\nMATCH_AI_CATALOG_BY_MEDIA natijasi:\n"
                            + json.dumps(output, ensure_ascii=False, default=str)
                        ),
                    }],
                    max_output_tokens=8000,
                    reasoning={"effort": ai_settings.reasoning_effort or "low"},
                    tools=ai_tool_definitions(),
                    parallel_tool_calls=False,
                    text={"format": {"type": "json_schema", "name": "sales_reply", "strict": True, "schema": ai_response_schema()}},
                )
                continue
            break
        tool_outputs = []
        for call in calls:
            try:
                arguments = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            output = execute_ai_tool(call.name, arguments, conversation, tool_results=tool_results)
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
