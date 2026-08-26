"""AI katalog rasmini mijoz yuborgan rasm bilan solishtirish.

Bitta chaqiruvda 20-40 ta katalog rasmini modelga berib bo'lmaydi: model rasm bilan
catalog_id ni bog'lay olmay qoladi va o'zi o'ylab topgan confidence beradi. Shuning
uchun ish uch bosqichga bo'lingan:

1. Har bir katalog mahsuloti rasmi BIR MARTA tahlil qilinib, natijasi (fingerprint)
   bazada saqlanadi. Rasm o'zgarmaguncha qayta tahlil qilinmaydi.
2. Mijoz rasm yuborganda faqat o'sha bitta rasm tahlil qilinadi. Mijoz "tepadan
   2-chisi", "chizilgan joydagi", "qizili" desa aynan ko'rsatilgan joy tahlil qilinadi.
3. Fingerprintlar deterministik ball bilan solishtirilib qisqa ro'yxat chiqadi, keyin
   faqat o'sha 3-4 rasm mijoz rasmi bilan birga modelga beriladi va "aynan shu
   mahsulotmi" deb tasdiqlatiladi.

Yakuniy qarorni model emas, backend chiqaradi. Model faqat tavsif beradi.
Noto'g'ri katalog yuborgandan ko'ra hech narsa yubormaslik afzal.
"""

import json
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.utils import timezone
from openai import OpenAI

from .models import AICatalogItem
from .platform_services import openai_api_key

FLOWER_FORMS = [
    "rose", "spray_rose", "peony_rose", "peony", "tulip", "chrysanthemum", "carnation",
    "gypsophila", "eustoma", "orchid", "lily", "hydrangea", "alstroemeria", "sunflower",
    "mixed", "other",
]
# Bir-biriga yaqin shakllar. Model "rose" bilan "peony_rose" ni almashtirib yuborishi
# mumkin, shuning uchun bular to'liq mos kelmasa ham qisman ball oladi.
FLOWER_FORM_NEIGHBOURS = {
    "rose": {"spray_rose", "peony_rose"},
    "spray_rose": {"rose", "peony_rose"},
    "peony_rose": {"rose", "spray_rose", "peony"},
    "peony": {"peony_rose"},
    "eustoma": {"rose"},
}

# Ball berishda "yaqin shakl" kengroq, chunki u faqat tartiblash uchun. Yakuniy
# tekshiruvda esa qattiqroq: shoxli gul (spray_rose) bir novdada ko'p kichik gul,
# klassik atir gul bitta yirik bosh — ular rasmda aniq farq qiladi. Pionavidniy
# esa ikkalasiga ham o'xshab ketadi, model uni ikkala nom bilan ataydi.
FORM_GATE_NEIGHBOURS = {
    "rose": {"peony_rose"},
    "peony_rose": {"rose", "spray_rose", "peony"},
    "spray_rose": {"peony_rose"},
    "peony": {"peony_rose"},
}


def forms_can_match(source_form, target_form):
    """Ikki mahsulotning guli bir turdami."""
    if not source_form or not target_form:
        return True
    if source_form == target_form:
        return True
    return target_form in FORM_GATE_NEIGHBOURS.get(source_form, set())

COLORS = [
    "white", "cream", "pink", "hot_pink", "red", "burgundy", "peach", "orange",
    "yellow", "lavender", "purple", "blue", "green", "brown", "black", "mixed",
]
COLOR_NEIGHBOURS = {
    "white": {"cream"},
    # Krem bilan sariq gulda ayri tushuncha: "tiniq sariq" katalog "krem-pushti" emas.
    "cream": {"white", "peach"},
    "pink": {"hot_pink", "peach"},
    # Qizil atirgul bilan pushti Alfalob do'konda ayri mahsulot, qo'shni deb hisoblanmaydi.
    "hot_pink": {"pink", "purple"},
    "red": {"burgundy"},
    "burgundy": {"red", "purple"},
    "peach": {"cream", "pink", "orange"},
    "orange": {"peach", "yellow"},
    "yellow": {"cream", "orange"},
    "lavender": {"purple"},
    "purple": {"lavender", "hot_pink", "burgundy"},
}

CONTAINERS = [
    "basket", "hat_box", "box", "wrapped_bouquet", "unwrapped_bouquet", "vase",
    "single_stems", "other",
]
# Model bitta rasmni bir safar "hat_box", boshqa safar "wrapped_bouquet" deb ataydi —
# o'ralgan buket bilan qutini rasmdan ajratish chindan ham noaniq. Shuning uchun ball
# aniq idish emas, idish oilasi bo'yicha beriladi. Savat esa alohida oila.
CONTAINER_FAMILIES = {
    "basket": "basket",
    "hat_box": "box",
    "box": "box",
    "wrapped_bouquet": "bouquet",
    "unwrapped_bouquet": "bouquet",
    "vase": "vase",
    "single_stems": "stems",
}
# Katalog tomonida idishni taxmin qilish shart emas: operator uni bazaga o'zi yozgan.
ARRANGEMENT_FAMILIES = {"bouquet": "bouquet", "basket": "basket", "box": "box"}
# Katalogda "vaza" degan tur yo'q — operator har bir kompazitsiyani "bouquet" yoki
# "basket" deb yozadi. O'sha bitta kompazitsiya do'konda goh vazada turgan holda, goh
# qo'lda ushlab turib suratga olinadi. Shuning uchun mijoz rasmida vaza ko'ringani
# "bu boshqa mahsulot" degani emas: vaza bilan buket qo'shni oila. Savat esa alohida —
# uning dastasi rasmda aniq ko'rinadi va u chindan ham boshqa mahsulot.
CONTAINER_FAMILY_NEIGHBOURS = {
    "bouquet": {"box", "vase"},
    "box": {"bouquet"},
    "vase": {"bouquet"},
}


def container_family(fingerprint, arrangement_type=""):
    """Mahsulot qaysi oilaga kiradi. Katalogda operator yozgani ustun turadi."""
    family = ARRANGEMENT_FAMILIES.get((arrangement_type or "").lower(), "")
    if family:
        return family
    return CONTAINER_FAMILIES.get((fingerprint or {}).get("container") or "", "")


def families_can_match(source_family, target_family):
    """Ikki mahsulot bir xil turdagi idishdami.

    Savat, quticha, vaza va qo'ldagi buket rasmda aniq farq qiladi. Faqat quticha
    bilan buket chalkashadi — o'ram burchagiga qarab model ikkalasini ham aytishi
    mumkin, shuning uchun ular bir-biriga qo'shni hisoblanadi.
    """
    if not source_family or not target_family:
        return True
    if source_family == target_family:
        return True
    return target_family in CONTAINER_FAMILY_NEIGHBOURS.get(source_family, set())


COLOR_PATTERNS = ["solid", "two_tone", "multi_color", "gradient"]
SIZES = ["small", "medium", "large", "extra_large"]
COUNT_BUCKETS = ["under_25", "25_to_50", "50_to_100", "over_100"]

VERDICTS = ["same_product", "similar_only", "different"]

# Ikki katalog mahsulotining rasmi shu balldan yuqori o'xshasa ularni bir-biridan
# rasm orqali ajratib bo'lmaydi.
TWIN_SCORE = 90

FINGERPRINT_VERSION = 2


def vision_model():
    return settings.OPENAI_VISION_MODEL or settings.OPENAI_MODEL


def vision_detail():
    """Gul navini ajratish uchun past sifat yetmaydi, shuning uchun default high."""
    value = (getattr(settings, "OPENAI_VISION_DETAIL", "") or "high").lower()
    return value if value in {"low", "high", "auto"} else "high"


def crowded_reasoning():
    """Kadrda bir nechta mahsulot turgan rasm uchun o'ylash byudjeti.

    O'lchov: oddiy mahsulot rasmida "medium" sifatni umuman oshirmadi, faqat
    har so'rovni 15 soniyadan 25 soniyaga cho'zdi. Kadrda beshta buket bo'lganda
    esa aksincha — "low" uchdan bir hollarda noto'g'ri buketni tanladi. Shuning
    uchun chuqur o'ylash faqat o'sha holatga sarflanadi.
    """
    value = (getattr(settings, "OPENAI_VISION_CROWDED_REASONING", "") or "medium").lower()
    return value if value in {"minimal", "low", "medium", "high"} else "medium"


def vision_reasoning():
    """Oddiy mahsulot rasmi uchun o'ylash byudjeti.

    O'lchandi: bitta mahsulot turgan rasmda "medium" sifatni oshirmadi, faqat
    har so'rovni 15 soniyadan 25 soniyaga cho'zdi. Murakkab kadr uchun
    crowded_reasoning() ishlatiladi.
    """
    value = (getattr(settings, "OPENAI_VISION_REASONING", "") or "low").lower()
    return value if value in {"minimal", "low", "medium", "high"} else "low"


def shortlist_size():
    return max(2, min(int(getattr(settings, "AI_CATALOG_MATCH_SHORTLIST", 4) or 4), 6))


def min_match_score():
    return max(0, min(int(getattr(settings, "AI_CATALOG_MATCH_MIN_SCORE", 55) or 55), 100))


# Kadrda beshta buket turganda ularning har biri kichkina bo'lib qoladi va model
# gul turini chalkashtiradi: shoxli gulni pionavidniy, pionavidniyni shoxli deb
# ataydi. Bitta gul turgan rasmda bunday bo'lmaydi. Shuning uchun ko'rsatilgan
# gulni topish uchun ancha baland ball talab qilinadi — yetmasa mijozdan o'sha
# gulni kesib yuborishini so'raymiz, taxmin qilib narx aytgandan ko'ra.
CROWDED_PHOTO_MIN_SCORE = 78


def required_score(source):
    """Shu rasm uchun ishonchli deb hisoblanadigan eng past ball."""
    base = min_match_score()
    crowded = bool(source.get("region_requested")) and (
        bool(source.get("multiple_products_visible")) or len(source.get("visible_products") or []) > 1
    )
    return max(base, CROWDED_PHOTO_MIN_SCORE) if crowded else base


def fingerprint_schema(with_region=False):
    properties = {
        "flower_form": {"type": "string", "enum": FLOWER_FORMS},
        "flower_variety_guess": {"type": "string"},
        "dominant_colors": {"type": "array", "items": {"type": "string", "enum": COLORS}},
        "color_pattern": {"type": "string", "enum": COLOR_PATTERNS},
        "container": {"type": "string", "enum": CONTAINERS},
        "wrap_colors": {"type": "array", "items": {"type": "string", "enum": COLORS}},
        "size": {"type": "string", "enum": SIZES},
        "count_bucket": {"type": "string", "enum": COUNT_BUCKETS},
        "distinctive_features": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    }
    if with_region:
        # Mijoz "tepadan 2-chisi" yoki chizib ko'rsatgan bo'lsa, shu joy tahlil qilinadi.
        # Model avval rasmdagi hamma mahsulotni sanab chiqadi, keyin bittasini tanlaydi.
        # Sanamasdan tanlaganda "eng pastdagisi" o'rniga o'rtadagini tasvirlab qo'yardi.
        properties["visible_products"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "position": {"type": "integer"},
                    "where": {"type": "string"},
                    "short_description": {"type": "string"},
                },
                "required": ["position", "where", "short_description"],
                "additionalProperties": False,
            },
        }
        properties["chosen_position"] = {"type": "integer"}
        properties["region_requested"] = {"type": "boolean"}
        properties["region_description"] = {"type": "string"}
        properties["multiple_products_visible"] = {"type": "boolean"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }


def verification_schema():
    """Bitta nomzod uchun. Har nomzod alohida so'ralgani uchun catalog_id kerak emas."""
    return {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": VERDICTS},
            "flower_form_match": {"type": "boolean"},
            "color_match": {"type": "boolean"},
            "container_match": {"type": "boolean"},
            "differences": {"type": "string"},
        },
        "required": ["verdict", "flower_form_match", "color_match", "container_match", "differences"],
        "additionalProperties": False,
    }


def clean_fingerprint(raw):
    """Model qaytargan qatorlarni ruxsat etilgan qiymatlargacha tozalaydi."""
    if not isinstance(raw, dict):
        return {}
    def one(key, allowed):
        value = str(raw.get(key) or "").strip().lower()
        return value if value in allowed else ""
    def many(key, allowed, limit):
        values = []
        for value in raw.get(key) or []:
            value = str(value or "").strip().lower()
            if value in allowed and value not in values:
                values.append(value)
        return values[:limit]
    return {
        "version": FINGERPRINT_VERSION,
        "flower_form": one("flower_form", FLOWER_FORMS),
        "flower_variety_guess": str(raw.get("flower_variety_guess") or "")[:80],
        "dominant_colors": many("dominant_colors", COLORS, 3),
        "color_pattern": one("color_pattern", COLOR_PATTERNS),
        "container": one("container", CONTAINERS),
        "wrap_colors": many("wrap_colors", COLORS, 2),
        "size": one("size", SIZES),
        "count_bucket": one("count_bucket", COUNT_BUCKETS),
        "distinctive_features": [str(value)[:120] for value in (raw.get("distinctive_features") or [])][:5],
        "summary": str(raw.get("summary") or "")[:600],
        "region_requested": bool(raw.get("region_requested")),
        "region_description": str(raw.get("region_description") or "")[:300],
        "multiple_products_visible": bool(raw.get("multiple_products_visible")),
        # Solishtirishda ishlatilmaydi, lekin operator "nega shuni tanladi" deb
        # so'raganda javob shu ikki maydonda turadi.
        "visible_products": [
            {
                "position": int(row.get("position") or 0),
                "where": str(row.get("where") or "")[:60],
                "short_description": str(row.get("short_description") or "")[:160],
            }
            for row in (raw.get("visible_products") or [])[:8]
            if isinstance(row, dict)
        ],
        "chosen_position": int(raw.get("chosen_position") or 0),
    }


def color_score(source_colors, target_colors):
    """Ranglar to'plamini solishtiradi. Yaqin ranglar yarim ball oladi."""
    if not source_colors or not target_colors:
        return 0.0
    total = 0.0
    for color in source_colors:
        if color in target_colors:
            total += 1.0
        elif COLOR_NEIGHBOURS.get(color, set()) & set(target_colors):
            total += 0.5
    return total / max(len(source_colors), len(target_colors))


def ordered_gap(values, source, target):
    if source not in values or target not in values:
        return None
    return abs(values.index(source) - values.index(target))


def variety_bonus(source, item_text):
    guess = (source.get("flower_variety_guess") or "").lower()
    haystack = (item_text or "").lower()
    if not guess or not haystack:
        return 0
    tokens = [token for token in guess.replace("-", " ").split() if len(token) > 3]
    return 8 if any(token in haystack for token in tokens) else 0


# Gul rangi mos kelmasa bu boshqa mahsulot. Savat shakli, hajmi va zichligi bir xil
# bo'lishi hech narsani anglatmaydi — do'kondagi hamma savat bir-biriga o'xshaydi.
DIFFERENT_COLOUR_CEILING = 45

# Do'konda bir gulning 25 talik, 50 talik va 100 talik varianti bor va narxi 199 000
# dan 1 000 000 gacha farq qiladi. Rangi va guli bir xil bo'lgani uchun ular deyarli
# to'liq ball olardi, natijada 199 000 lik buket 1 000 000 lik rasmga mos kelib qolardi.
# Ikki pog'onadan ortiq hajm yoki gul soni farqi bo'lsa ball shu yerda to'xtaydi.
DIFFERENT_SIZE_CEILING = 55


def fingerprint_score(source, target, item_text="", target_arrangement_type=""):
    """0-100 oralig'idagi ball. Bu qisqa ro'yxat uchun tartiblash, yakuniy qaror emas."""
    if not source or not target:
        return 0
    score = 0.0
    source_form = source.get("flower_form")
    target_form = target.get("flower_form")
    if source_form and source_form == target_form:
        score += 25
    elif source_form and target_form in FLOWER_FORM_NEIGHBOURS.get(source_form, set()):
        score += 12
    colours = color_score(source.get("dominant_colors"), target.get("dominant_colors"))
    score += 35 * colours
    if source.get("color_pattern") and source.get("color_pattern") == target.get("color_pattern"):
        score += 8
    source_family = container_family(source)
    target_family = container_family(target, target_arrangement_type)
    if source_family and source_family == target_family:
        score += 17
    elif source_family and target_family in CONTAINER_FAMILY_NEIGHBOURS.get(source_family, set()):
        score += 7
    size_gap = ordered_gap(SIZES, source.get("size"), target.get("size"))
    if size_gap == 0:
        score += 8
    elif size_gap == 1:
        score += 4
    count_gap = ordered_gap(COUNT_BUCKETS, source.get("count_bucket"), target.get("count_bucket"))
    if count_gap == 0:
        score += 7
    elif count_gap == 1:
        score += 3
    score += variety_bonus(source, item_text)
    score = min(round(score), 100)
    if not colours and source.get("dominant_colors") and target.get("dominant_colors"):
        score = min(score, DIFFERENT_COLOUR_CEILING)
    if not sizes_can_match(source, target):
        score = min(score, DIFFERENT_SIZE_CEILING)
    return int(score)


def sizes_can_match(source, target):
    """Ikki mahsulotning kattaligi bir-biriga yaqinmi.

    25 talik buket bilan 100 talik kompozitsiyani rasmda ajratib bo'ladi va narxi
    besh barobar farq qiladi — ularni "qaysi biri" deb yonma-yon qo'yish xato.

    Asosiy o'lchov gul soni: u izohda yozilgan va ikkala tomonda bir narsani
    anglatadi. size esa ishonchsiz — savatning bo'yi past bo'lgani uchun katalogda
    "medium" turadi, o'sha savatning rasmini ko'rgan model esa uni "extra_large"
    deydi. Shuning uchun size faqat uchta pog'ona farq qilganda hisobga olinadi.
    """
    size_gap = ordered_gap(SIZES, source.get("size"), target.get("size"))
    count_gap = ordered_gap(COUNT_BUCKETS, source.get("count_bucket"), target.get("count_bucket"))
    if size_gap is not None and size_gap >= 3:
        return False
    if count_gap is not None and count_gap >= 2:
        return False
    return True


def vision_json(client, *, schema_name, schema, instructions, content, max_output_tokens, reasoning=""):
    """Vision so'rovini yuborib JSON qaytaradi, kesilib qolsa bir marta qayta uradi.

    reasoning byudjeti max_output_tokens ichidan yeyiladi, shuning uchun uzunroq
    o'ylagan javob JSON tugamasdan kesilib qolishi mumkin. Bunday holatda joyni
    kengaytirib va o'ylashni qisqartirib qayta so'raymiz.
    """
    attempts = [
        (max_output_tokens, reasoning or vision_reasoning()),
        (max_output_tokens * 2, "low"),
    ]
    last_error = None
    for tokens, effort in attempts:
        response = client.responses.create(
            model=vision_model(),
            instructions=instructions,
            input=[{"role": "user", "content": content}],
            max_output_tokens=tokens,
            reasoning={"effort": effort},
            text={"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        )
        try:
            return json.loads(response.output_text)
        except (json.JSONDecodeError, TypeError) as error:
            last_error = error
            print(f"VISION_JSON_INCOMPLETE schema={schema_name} status={getattr(response, 'status', '')} tokens={tokens}", flush=True)
    raise ValueError(f"vision response was not valid json: {last_error}")


# Mijoz bitta chatda ham gul rasmini, ham to'lov chekini yuboradi. Ikkisini
# aralashtirib yuborish qimmatga tushadi: chekni gul deb katalogdan qidirish ham,
# gulni chek deb to'lov oqimiga qo'shish ham xato javob beradi.
IMAGE_KINDS = ["payment_receipt", "flower", "other"]


def image_kind_schema():
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": IMAGE_KINDS},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary": {"type": "string"},
        },
        "required": ["kind", "confidence", "summary"],
        "additionalProperties": False,
    }


def classify_customer_image(image_url, api_key=""):
    """Mijoz yuborgan rasm to'lov chekimi, gulmi yoki boshqa narsami.

    Chek deb faqat pul o'tkazilganini ko'rsatadigan hujjat hisoblanadi: bank
    ilovasidagi kvitansiya, terminal cheki, o'tkazma tasdig'i. Gul rasmi,
    skrinshot yoki tasodifiy surat chek emas.
    """
    api_key = api_key or openai_api_key()
    if not api_key or not image_url:
        return {}
    payload = {
        "task": "Decide what the customer just sent to a flower shop chat.",
        "kinds": {
            "payment_receipt": "a proof of payment: bank app receipt, transfer confirmation, "
                               "terminal slip, screenshot of a completed transfer. It shows an "
                               "amount, a card or account, a date or a status like success.",
            "flower": "a bouquet, basket, single flowers or any floral arrangement",
            "other": "anything else — a person, a room, a document that is not a payment, text only",
        },
        "rules": [
            "A photo of flowers is never a payment_receipt, even if a price is written on it.",
            "A payment_receipt has no flowers in it.",
            "If you are unsure between receipt and flower, look for an amount of money and a transfer status.",
            "Answer with confidence low when the picture is blurry or cropped so the kind cannot be told.",
        ],
    }
    content = [
        {"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)},
        {"type": "input_image", "image_url": image_url, "detail": vision_detail()},
    ]
    client = OpenAI(api_key=api_key)
    try:
        return vision_json(
            client,
            schema_name="customer_image_kind",
            schema=image_kind_schema(),
            instructions="You sort pictures a flower shop customer sends. Answer only with the schema.",
            content=content,
            max_output_tokens=400,
            reasoning="low",
        )
    except Exception as error:
        print(f"IMAGE_KIND_FAILED url={str(image_url)[:70]} error={error}", flush=True)
        return {}


def analyze_image(image_url, context_text="", instructions="", with_region=False, api_key=""):
    """Bitta rasmni tahlil qilib fingerprint qaytaradi."""
    api_key = api_key or openai_api_key()
    if not api_key or not image_url:
        return {}
    payload = {
        "task": "Describe this flower arrangement so it can be matched against a shop catalog.",
        "rules": [
            "Look at the flowers themselves first: petal form, whether they are classic roses, spray roses or peony-shaped (pionovidnaya) roses.",
            "dominant_colors must describe the flowers, not the wrapping paper or the background.",
            "count_bucket is a rough bucket, not an exact count.",
            "Do not guess a variety name unless the flowers or the given text clearly show it.",
        ],
        "context_text": context_text or "",
    }
    if with_region:
        payload["pointing_rules"] = [
            "First fill visible_products: list EVERY distinct flower arrangement you can see in the photo, numbered from 1, reading top to bottom and then left to right. Put the reading order in position and say where it sits in where ('top', 'second from top', 'bottom', 'left', 'centre').",
            "A photo of a single product still gets exactly one entry in visible_products.",
            "Then read the customer text. It may point at one of them: a circle or arrow drawn on the image, 'tepadan 2chisi' (second from top), 'eng pastdagisi' (the bottom one), 'chapdagi' (the left one), 'qizili' (the red one).",
            "Put the position number of the item the customer means into chosen_position, and describe THAT item in the fingerprint fields. Nothing else in the photo may leak into the fingerprint.",
            "A drawn circle, arrow or highlight beats every word in the text. If something is circled, chosen_position is the circled item.",
            "If the text points at nothing, set region_requested=false, chosen_position=1 and describe the largest, most central arrangement.",
            "Set region_requested=true only when the customer actually pointed at one item.",
            "Set multiple_products_visible=true when visible_products has more than one entry.",
        ]
    content = [
        {"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)},
        {"type": "input_image", "image_url": image_url, "detail": vision_detail()},
    ]
    client = OpenAI(api_key=api_key)
    schema = fingerprint_schema(with_region=with_region)
    instructions = instructions or "You describe flower arrangements for a flower shop catalog. Be literal and precise about flower form and flower colour. Return JSON only."
    raw = vision_json(
        client,
        schema_name="flower_fingerprint",
        schema=schema,
        instructions=instructions,
        content=content,
        max_output_tokens=4000 if with_region else 3000,
    )
    first = clean_fingerprint(raw)
    # Kadrda bir nechta mahsulot bor va mijoz bittasini ko'rsatgan bo'lsa, tahlilni
    # chuqurroq o'ylash bilan qaytaramiz. Buni oldindan bilib bo'lmaydi — rasmda
    # nechta gul borligini birinchi tahlilning o'zi aytadi.
    crowded = bool(first.get("region_requested")) and (
        bool(first.get("multiple_products_visible")) or len(first.get("visible_products") or []) > 1
    )
    if not crowded or crowded_reasoning() == vision_reasoning():
        return first
    try:
        raw = vision_json(
            client,
            schema_name="flower_fingerprint",
            schema=schema,
            instructions=instructions,
            content=content,
            max_output_tokens=5000,
            reasoning=crowded_reasoning(),
        )
    except Exception as error:
        print(f"VISION_CROWDED_RETRY_FAILED error={error}", flush=True)
        return first
    return clean_fingerprint(raw)


def catalog_item_context(item):
    """Katalog rasmi tahlil qilinayotganda nom va izoh yordam beradi.

    Izohda gul navi yozilgan bo'ladi ("pionavidniy katalina ranglari tiniq sariq"),
    bu rasmni tahlil qilishda modelga aniq ishora bo'ladi.
    """
    parts = [item.name, item.get_arrangement_type_display(), item.volume, (item.note or "")[:400]]
    return " | ".join(part for part in parts if part)


def build_catalog_fingerprint(item, api_key=""):
    if not item.image_url:
        return {}
    return analyze_image(
        item.image_url,
        context_text=catalog_item_context(item),
        instructions=(
            "You describe a flower shop's own catalog product photo. The shop's own name and note "
            "for the product are given as context — trust them for the variety name, but read the "
            "colours and the flower form from the image itself. "
            "The note often states the stem count and the height ('25 tacha guli', '100 ta guldan', "
            "'boyi 60 sm'). When it does, set count_bucket and size from those numbers, not from "
            "how big the bouquet looks in the photo — the shop sells the same flower as a 25-stem "
            "and a 100-stem product and the photos look alike. Return JSON only."
        ),
        api_key=api_key,
    )


def fingerprint_is_stale(item):
    fingerprint = item.visual_fingerprint or {}
    if not fingerprint or fingerprint.get("version") != FINGERPRINT_VERSION:
        return True
    return (item.fingerprint_source_url or "") != (item.image_url or "")


def ensure_catalog_fingerprint(item, force=False, api_key=""):
    """Fingerprint yo'q yoki rasm o'zgargan bo'lsa qaytadan yasaydi."""
    if not item.image_url:
        return {}
    if not force and not fingerprint_is_stale(item):
        return item.visual_fingerprint or {}
    try:
        fingerprint = build_catalog_fingerprint(item, api_key=api_key)
    except Exception as error:
        print(f"AI_CATALOG_FINGERPRINT_FAILED catalog_id={item.id} error={error}", flush=True)
        return item.visual_fingerprint or {}
    if not fingerprint:
        return item.visual_fingerprint or {}
    AICatalogItem.objects.filter(id=item.id).update(
        visual_fingerprint=fingerprint,
        fingerprint_source_url=item.image_url,
        fingerprint_updated_at=timezone.now(),
    )
    item.visual_fingerprint = fingerprint
    item.fingerprint_source_url = item.image_url
    return fingerprint


def refresh_stale_fingerprints(queryset, limit=50, api_key=""):
    updated = 0
    for item in queryset.exclude(image_url="")[:limit]:
        if not fingerprint_is_stale(item):
            continue
        if ensure_catalog_fingerprint(item, api_key=api_key):
            updated += 1
    return updated


def shortlist_candidates(source, items, api_key="", lazy_limit=6):
    """Fingerprint ballari bo'yicha eng mos mahsulotlarni saralaydi.

    Fingerprinti yo'q mahsulotlar bo'lsa (yangi qo'shilgan, celery ishlamagan) shu yerda
    o'rniga yasab beriladi, lekin bitta so'rovda ko'pi bilan lazy_limit ta.
    """
    lazily_built = 0
    scored = []
    for item in items:
        fingerprint = item.visual_fingerprint or {}
        if fingerprint_is_stale(item) and lazily_built < lazy_limit:
            fingerprint = ensure_catalog_fingerprint(item, api_key=api_key) or fingerprint
            lazily_built += 1
        if not fingerprint:
            continue
        score = fingerprint_score(source, fingerprint, catalog_item_context(item), target_arrangement_type=item.arrangement_type)
        scored.append({"item": item, "score": score, "fingerprint": fingerprint})
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored


def verify_candidate(source_url, item, source, customer_text="", api_key=""):
    """Bitta katalog rasmini mijoz rasmi bilan yonma-yon solishtiradi.

    Har nomzod alohida so'raladi. Bitta so'rovga 5-6 ta rasm tiqilganda model qaysi
    rasm qaysi mahsulot ekanini adashtiradi va o'z rasmini ham "boshqa" deb ataydi.
    Ikkita rasmda esa chalkashadigan narsa qolmaydi.
    """
    payload = {
        "task": "Decide whether these two photos show the SAME product from a flower shop catalog.",
        "customer_text": customer_text or "",
        "catalog_item": {"name": item.name, "type": item.get_arrangement_type_display(), "volume": item.volume},
        "customer_fingerprint": {key: source.get(key) for key in ("flower_form", "dominant_colors", "color_pattern", "container", "region_description")},
        "rules": [
            "The two photos may be the very same photograph, or the same product shot from another angle. Both count as same_product.",
            "Compare the flowers first: petal form and flower colour decide the verdict.",
            "same_product needs flower form, dominant flower colours and container type to match.",
            "A different rose variety or a different dominant colour is never same_product.",
            "Similar shape, similar density, a person holding it, similar wrapping or similar overall style alone is similar_only.",
            "Wrapping paper being folded differently or the photo being cropped differently does not make it a different product.",
        ],
    }
    content = [
        {"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)},
        {"type": "input_text", "text": "PHOTO 1 — the customer's photo:"},
        {"type": "input_image", "image_url": source_url, "detail": vision_detail()},
        {"type": "input_text", "text": f"PHOTO 2 — catalog product {item.name}:"},
        {"type": "input_image", "image_url": item.image_url, "detail": vision_detail()},
    ]
    return vision_json(
        OpenAI(api_key=api_key or openai_api_key()),
        schema_name="catalog_verification",
        schema=verification_schema(),
        instructions="You are a strict flower identification checker for a flower shop. You compare exactly two photos and decide whether they show the same catalog product. Prefer 'different' over a wrong match. Return JSON only.",
        content=content,
        max_output_tokens=2500,
    )


def verify_candidates(source_url, source, rows, customer_text="", api_key=""):
    """Qisqa ro'yxatdagi har bir nomzodni alohida tekshiradi.

    So'rovlar tarmoq kutishidan iborat, shuning uchun parallel yuboriladi — 4 ta
    nomzod bitta nomzodcha vaqt oladi.
    """
    api_key = api_key or openai_api_key()
    if not api_key or not rows:
        return {}
    def check(row):
        try:
            return row["item"].id, verify_candidate(source_url, row["item"], source, customer_text=customer_text, api_key=api_key)
        except Exception as error:
            print(f"AI_CATALOG_VERIFY_FAILED catalog_id={row['item'].id} error={error}", flush=True)
            return row["item"].id, {}
    with ThreadPoolExecutor(max_workers=min(len(rows), 5)) as pool:
        return {catalog_id: judgement for catalog_id, judgement in pool.map(check, rows)}


def indistinguishable_items(winner_row, rows):
    """G'olibdan rasmda ajratib bo'lmaydigan boshqa katalog mahsulotlari.

    Do'konda bir xil guldan turli o'lchamdagi mahsulotlar bor: 199 000, 400 000,
    900 000 va 1 000 000 so'mlik buketlar bir xil ko'rinadi, farqi gul sonida.
    Rasmdan qaysi biri ekanini aytib bo'lmaydi — taxmin qilib narx aytishdan ko'ra
    hammasini ko'rsatib, mijozning o'zidan so'ragan to'g'ri.
    """
    twins = []
    for row in rows:
        if row["item"].id == winner_row["item"].id:
            continue
        score = fingerprint_score(
            winner_row["fingerprint"],
            row["fingerprint"],
            catalog_item_context(row["item"]),
            target_arrangement_type=row["item"].arrangement_type,
        )
        if (
            score >= TWIN_SCORE
            and sizes_can_match(winner_row["fingerprint"], row["fingerprint"])
            and forms_can_match(winner_row["fingerprint"].get("flower_form"), row["fingerprint"].get("flower_form"))
        ):
            twins.append(row)
    return twins
