# -*- coding: utf-8 -*-
from django.db import migrations


OLD_MARKERS = """B. RUS TILI → to'liq RUS TILIDA javob.
   Rus tili belgilari: цветы, какие, сколько, стоит, есть, адрес, где,
   здравствуйте, спасибо, доставка, нужен, хочу, работаете, дорого, букет из."""

NEW_MARKERS = """B. RUS TILI → to'liq RUS TILIDA javob.
   Rus tili belgilari: цветы, какие, сколько, стоит, есть, где,
   здравствуйте, спасибо, нужен, хочу, работаете, дорого, букет из.

TILNI BITTA SO'ZGA QARAB TANLAMA. Javob tili conversation.customer_script
maydonidan olinadi, o'zingdan taxmin qilmaysan:
   "latin"    → o'zbek lotin javob
   "uz_cyril" → o'zbekcha javob, LOTINDA yozasan (tizim kirillga o'giradi)
   "ru"       → rus tilida javob

Quyidagi so'zlar rus tilining belgisi EMAS — o'zbek mijozlar ham aynan shunday
yozadi: доставка, дастафка, адрес, локация, вариант, заказ, букет, сават,
наличия, скидка, номер, карта.
Xato, real suhbatdan: mijoz "жойила катта", "вилоятга борми", "доставка" deb
yozdi, sen "доставка" ni ko'rib butun javobni rus tilida berding — mijoz esa
o'zbekcha yozgan edi.
To'g'ri: customer_script "uz_cyril" bo'lgani uchun javob o'zbekcha bo'ladi."""

MARKER = "TILNI BITTA SO'ZGA QARAB TANLAMA"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or OLD_MARKERS not in prompt:
        return
    row.system_prompt = prompt.replace(OLD_MARKERS, NEW_MARKERS, 1)
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(NEW_MARKERS, OLD_MARKERS, 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0147_ai_prompt_cyrillic_and_pending")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
