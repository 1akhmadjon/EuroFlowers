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

COLORS = [
    "white", "cream", "pink", "hot_pink", "red", "burgundy", "peach", "orange",
    "yellow", "lavender", "purple", "blue", "green", "brown", "black", "mixed",
]
COLOR_NEIGHBOURS = {
    "white": {"cream"},
    # Krem bilan sariq gulda ayri tushuncha: "tiniq sariq" katalog "krem-pushti" emas.
    "cream": {"white", "peach"},
    "pink": {"hot_pink", "peach"},
    "hot_pink": {"pink", "purple", "red"},
    "red": {"burgundy", "hot_pink"},
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
# Savat bilan buketni chalkashtirib bo'lmaydi, lekin o'ralgan va o'ralmagan buket yaqin.
CONTAINER_NEIGHBOURS = {
    "wrapped_bouquet": {"unwrapped_bouquet"},
    "unwrapped_bouquet": {"wrapped_bouquet"},
    "hat_box": {"box"},
    "box": {"hat_box"},
}

COLOR_PATTERNS = ["solid", "two_tone", "multi_color", "gradient"]
SIZES = ["small", "medium", "large", "extra_large"]
COUNT_BUCKETS = ["under_25", "25_to_50", "50_to_100", "over_100"]

VERDICTS = ["same_product", "similar_only", "different"]

FINGERPRINT_VERSION = 2


def vision_model():
    return settings.OPENAI_VISION_MODEL or settings.OPENAI_MODEL


def vision_detail():
    """Gul navini ajratish uchun past sifat yetmaydi, shuning uchun default high."""
    value = (getattr(settings, "OPENAI_VISION_DETAIL", "") or "high").lower()
    return value if value in {"low", "high", "auto"} else "high"


def vision_reasoning():
    value = (getattr(settings, "OPENAI_VISION_REASONING", "") or "medium").lower()
    return value if value in {"minimal", "low", "medium", "high"} else "medium"


def shortlist_size():
    return max(2, min(int(getattr(settings, "AI_CATALOG_MATCH_SHORTLIST", 4) or 4), 6))


def min_match_score():
    return max(0, min(int(getattr(settings, "AI_CATALOG_MATCH_MIN_SCORE", 55) or 55), 100))


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
    return {
        "type": "object",
        "properties": {
            "source_summary": {"type": "string"},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "catalog_id": {"type": "integer"},
                        "verdict": {"type": "string", "enum": VERDICTS},
                        "flower_form_match": {"type": "boolean"},
                        "color_match": {"type": "boolean"},
                        "container_match": {"type": "boolean"},
                        "differences": {"type": "string"},
                    },
                    "required": ["catalog_id", "verdict", "flower_form_match", "color_match", "container_match", "differences"],
                    "additionalProperties": False,
                },
            },
            "best_catalog_id": {"type": "integer", "description": "0 = mos keladigani yo'q"},
        },
        "required": ["source_summary", "candidates", "best_catalog_id"],
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


def fingerprint_score(source, target, item_text=""):
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
    source_container = source.get("container")
    target_container = target.get("container")
    if source_container and source_container == target_container:
        score += 17
    elif source_container and target_container in CONTAINER_NEIGHBOURS.get(source_container, set()):
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
    return int(score)


def vision_json(client, *, schema_name, schema, instructions, content, max_output_tokens):
    """Vision so'rovini yuborib JSON qaytaradi, kesilib qolsa bir marta qayta uradi.

    reasoning byudjeti max_output_tokens ichidan yeyiladi, shuning uchun uzunroq
    o'ylagan javob JSON tugamasdan kesilib qolishi mumkin. Bunday holatda joyni
    kengaytirib va o'ylashni qisqartirib qayta so'raymiz.
    """
    attempts = [
        (max_output_tokens, vision_reasoning()),
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
            "The customer text may point at one specific arrangement in the photo: a circle or arrow drawn on the image, 'tepadan 2chisi' (second from top), 'chapdagi' (the left one), 'qizili' (the red one).",
            "If it points at one item, set region_requested=true, describe it in region_description and describe ONLY that item in the fingerprint.",
            "If no pointing is present, describe the main arrangement in the photo.",
            "Set multiple_products_visible=true when the photo shows several different arrangements.",
        ]
    content = [
        {"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)},
        {"type": "input_image", "image_url": image_url, "detail": vision_detail()},
    ]
    raw = vision_json(
        OpenAI(api_key=api_key),
        schema_name="flower_fingerprint",
        schema=fingerprint_schema(with_region=with_region),
        instructions=instructions or "You describe flower arrangements for a flower shop catalog. Be literal and precise about flower form and flower colour. Return JSON only.",
        content=content,
        max_output_tokens=3000,
    )
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
        instructions="You describe a flower shop's own catalog product photo. The shop's own name and note for the product are given as context — trust them for the variety name, but read the colours and the flower form from the image itself. Return JSON only.",
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
        score = fingerprint_score(source, fingerprint, catalog_item_context(item))
        scored.append({"item": item, "score": score, "fingerprint": fingerprint})
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored


def verify_candidates(source_url, source, rows, customer_text="", api_key=""):
    """Qisqa ro'yxatdagi rasmlarni mijoz rasmi bilan yonma-yon solishtiradi.

    Bu yerda rasm soni 3-4 ta bo'lgani uchun model qaysi rasm qaysi catalog_id ekanini
    adashtirmaydi. 40 ta rasmda esa adashtirib yuborardi.
    """
    api_key = api_key or openai_api_key()
    if not api_key or not rows:
        return {}
    payload = {
        "task": "Decide whether the customer photo shows the SAME product as one of the numbered catalog photos.",
        "customer_text": customer_text or "",
        "customer_fingerprint": {key: source.get(key) for key in ("flower_form", "dominant_colors", "color_pattern", "container", "size", "count_bucket", "region_description")},
        "rules": [
            "Compare the flowers first: petal form and flower colour decide the verdict.",
            "same_product only when flower form, dominant flower colours and container all match. A different rose variety or a different dominant colour is never same_product.",
            "Similar basket shape, similar density, a person holding it, similar wrapping or similar overall style is NOT enough. That is similar_only.",
            "best_catalog_id must be 0 unless exactly one candidate is same_product.",
        ],
    }
    content = [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]
    content.append({"type": "input_text", "text": "CUSTOMER PHOTO:"})
    content.append({"type": "input_image", "image_url": source_url, "detail": vision_detail()})
    for row in rows:
        item = row["item"]
        content.append({"type": "input_text", "text": f"CATALOG PHOTO catalog_id={item.id} name={item.name}"})
        content.append({"type": "input_image", "image_url": item.image_url, "detail": vision_detail()})
    return vision_json(
        OpenAI(api_key=api_key),
        schema_name="catalog_verification",
        schema=verification_schema(),
        instructions="You are a strict flower identification checker for a flower shop. You decide only whether two photos show the same catalog product. Prefer 'different' over a wrong match. Return JSON only.",
        content=content,
        max_output_tokens=4000,
    )
