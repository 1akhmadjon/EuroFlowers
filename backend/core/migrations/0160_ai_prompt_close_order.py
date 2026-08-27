# -*- coding: utf-8 -*-
"""00J dagi ketma-ketlikni xarita havolasiga moslaydi.

delivery_location_link lead ochilgandan keyin ishlaydi: ism va telefon kelmasa
havola bo'sh qaytadi. Shuning uchun manzil qadami ism va telefondan keyin
turadi, yetkazib berish/kelib olish savoli esa avvalgidek oldinda qoladi —
u leadning o'ziga yoziladi.
"""
from django.db import migrations


OLD = """Ketma-ketlik: mahsulot → sana → yetkazib berish/kelib olish → (yetkazib berish
bo'lsa manzil) → ism va telefon → to'lov turi → yakuniy javob.
"""

NEW = """Ketma-ketlik: mahsulot → sana → yetkazib berish/kelib olish → ism va telefon
→ (yetkazib berish bo'lsa xarita havolasi) → to'lov turi → yakuniy javob.

Xarita havolasi va to'lov turi buyurtma ochilgandan keyin so'raladi. Ism va
telefon kelmaguncha delivery_location_link ni chaqirma — u bo'sh qaytaradi va
mijoz havolasiz qolib ketadi.
"""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if NEW in prompt or OLD not in prompt:
        return
    row.system_prompt = prompt.replace(OLD, NEW, 1)
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(NEW, OLD, 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0159_ai_prompt_close_checklist")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
