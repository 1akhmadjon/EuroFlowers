# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """00G. TO'LOV — HAMMA QOIDADAN USTUN"""

BLOCK = """00J. BUYURTMANI YOPISHDAN OLDIN IKKI SAVOL — HAMMA QOIDADAN USTUN
════════════════════════════════════
Mijoz mahsulotni tanlagach buyurtma shu ikki savol javobi olinmaguncha yopilmaydi:

  1) Yetkazib beraylikmi yoki o'zingiz kelib olib ketasizmi?
  2) To'lovingiz naqdmi yoki kartami?

already_known.fulfillment bo'sh bo'lsa 1-savol hali berilmagan — ber.
already_known.payment_type bo'sh bo'lsa 2-savol hali berilmagan — ber.
To'lgan bo'lsa qayta so'rama.

Ikkalasini bitta xabarda so'rama, har javobda bitta savol.
Ketma-ketlik: mahsulot → sana → yetkazib berish/kelib olish → (yetkazib berish
bo'lsa manzil) → ism va telefon → to'lov turi → yakuniy javob.

"Buyurtmangizni qabul qildik", "operatorlarimiz tez orada bog'lanishadi",
"tayyorlab qo'yamiz" kabi YAKUNLOVCHI gapni ikkala javob olinmaguncha YOZMA.
Yozib qo'ysang mijoz gulni qanday olishini va qanday to'lashini aytmagan bo'ladi,
operator hammasini qaytadan so'rashga majbur bo'ladi.

Ism va telefon kelib lead yaralgan bo'lsa ham to'xtamaysan — qolgan savolni
o'sha javobning ichida so'ra.
Xato: "Rahmat, Ahmad. Buyurtmangizni qabul qildik. Operatorlarimiz bog'lanadi."
To'g'ri: "Rahmat, Ahmad. Gulni o'zingiz kelib olib ketasizmi yoki yetkazib beraylikmi?"

Mijoz sanani o'zgartirsa yoki boshqa tuzatish kiritsa ham shu ikki maydon
tekshiriladi: bo'sh bo'lgani bo'lsa avval shuni so'ra, keyin yakunla.

════════════════════════════════════
"""

MARKER = "00J. BUYURTMANI YOPISHDAN OLDIN"

FIELD_ANCHOR = (
    '  already_known.fulfillment     — "pickup" yoki "delivery" '
    "bo'lsa tanlov qilingan, qayta so'rama\n"
)
FIELD_LINE = (
    '  already_known.payment_type    — "cash" yoki "card" '
    "bo'lsa to'lov turi olingan, qayta so'rama\n"
)


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or ANCHOR not in prompt:
        return
    prompt = prompt.replace(ANCHOR, BLOCK + ANCHOR, 1)
    if FIELD_LINE not in prompt:
        prompt = prompt.replace(FIELD_ANCHOR, FIELD_ANCHOR + FIELD_LINE, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "").replace(BLOCK, "", 1)
    row.system_prompt = prompt.replace(FIELD_LINE, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0158_ai_prompt_delivery_location")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
