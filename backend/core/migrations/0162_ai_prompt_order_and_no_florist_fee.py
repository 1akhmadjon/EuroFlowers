# -*- coding: utf-8 -*-
"""Ahmad suhbatidan chiqqan ikki tuzatish.

1. Yetkazib berish/kelib olish savoli lead ochilishidan oldin berilardi, shu
   sababli xarita havolasi hech qachon berilmadi — delivery_location_link lead
   bo'lmasa bo'sh qaytaradi. Ism va telefon endi oldinda turadi.
2. AI katalog narxiga florist haqi 50 000 ni qo'shib "jami 299 000" deb yozdi.
   Florist haqi mijozga umuman aytilmaydi va hech narsaga qo'shilmaydi.
"""
from django.db import migrations


ORDER_OLD = """Ketma-ketlik: mahsulot → sana → yetkazib berish/kelib olish → ism va telefon
→ (yetkazib berish bo'lsa xarita havolasi) → to'lov turi → yakuniy javob.

Xarita havolasi va to'lov turi buyurtma ochilgandan keyin so'raladi. Ism va
telefon kelmaguncha delivery_location_link ni chaqirma — u bo'sh qaytaradi va
mijoz havolasiz qolib ketadi.
"""

ORDER_NEW = """Ketma-ketlik: mahsulot → sana → ISM VA TELEFON → yetkazib berish/kelib olish
→ (yetkazib berish bo'lsa xarita havolasi) → to'lov turi → yakuniy javob.

Ism va telefon eng oldinda: ular kelganda client_lead_create chaqiriladi va
buyurtma ochiladi. Qolgan uch savol shundan keyin so'raladi.

Yetkazib berishni tanlasa manzilni MATN bilan so'ramaysan —
delivery_location_link ni chaqirasan va natijadagi havolani mijozga yozasan:
"Manzilingizni xaritada belgilang: <link>"
Bu tool lead ochilgandan keyin ishlaydi, shuning uchun ism va telefon oldinda.
Havola bo'sh kelsagina manzilni matn bilan so'ra.
"""

QUESTIONS_OLD = """  1) Yetkazib beraylikmi yoki o'zingiz kelib olib ketasizmi?
  2) To'lovingiz naqdmi yoki kartami?
"""

QUESTIONS_NEW = """  1) Yetkazib beraylikmi yoki o'zingiz kelib olib ketasizmi?
  2) To'lovingiz naqdmi yoki kartami?

Ikkalasi ham ism va telefon olinib buyurtma ochilgandan keyin so'raladi.
"""

FLORIST_OLD = """SUMMANI HECH QACHON AYTMA
Mijoz "floristika nechpul", "florist haqi qancha", "yasash uchun qancha",
"ishi qancha turadi", "xizmat haqi qancha" deb so'rasa raqam AYTMA.
Kontekstdagi florist_fee ni ham, boshqa taxminiy summani ham aytma.
Javob: floristika xizmati narxini operatorlarimiz aytadi.
Bu chegirma savoli emas, chalkashtirma.
"""

FLORIST_NEW = """FLORIST HAQI HECH QAYERGA QO'SHILMAYDI
Katalogdagi mahsulotning narxi TO'LIQ va OXIRGI narx. Unga florist haqi,
xizmat haqi, qadoq yoki bezak puli QO'SHILMAYDI.

"Jami 299 000: buket 199 000, florist xizmat haqi 50 000" kabi hisob-kitob
QAT'IY TAQIQLANADI. Bunday summani yozish mijozni yo'qotadi — u katalogda
ko'rgan narxdan boshqa raqamni to'lashga majbur qilinganini o'ylaydi.

Mijoz "nechpul to'lashim kerak" deb so'rasa mahsulotning o'z narxini ayt.
Yetkazib berish tanlangan bo'lsa uni alohida qator qilib ayt, qo'shib
"jami" chiqarma.

Mijoz "floristika nechpul", "florist haqi qancha", "yasash uchun qancha",
"ishi qancha turadi", "xizmat haqi qancha" deb so'rasa raqam AYTMA.
Javob: floristika xizmati narxini operatorlarimiz aytadi.
Bu chegirma savoli emas, chalkashtirma.
"""

FEE_FIELD_OLD = """- florist_fee — null qoldir, uni operator belgilaydi.
"""

FEE_FIELD_NEW = """- florist haqi yozilmaydi, uni operator CRM da belgilaydi.
"""

PAIRS = [
    (ORDER_OLD, ORDER_NEW),
    (QUESTIONS_OLD, QUESTIONS_NEW),
    (FLORIST_OLD, FLORIST_NEW),
    (FEE_FIELD_OLD, FEE_FIELD_NEW),
]


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    for old, new in PAIRS:
        if old and old in prompt:
            prompt = prompt.replace(old, new, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    for old, new in PAIRS:
        if new and new in prompt:
            prompt = prompt.replace(new, old, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0161_ai_prompt_freshness_and_reply")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
