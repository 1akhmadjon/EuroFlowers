# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """Telefon raqami SO'RAMA va client_lead_create CHAQIRMA."""

INSERT = """

RASM BILAN KELGAN YASATMA HAM SHU BO'LIMGA KIRADI

Mijoz rasm yuborib "shu guldan yasab berolislami", "shunaqasini yasang",
"shu guldan buket qb bering" desa — bu katalogdan qidirish emas, yasatma
buyurtma. Katalog albomini yuborib "yuborgan gulingiz qiziq bo'lsa operatorga
yozing" deb javob berish savolga javob emas: mijoz gul so'ramadi, yasab
berishni so'radi.
To'g'ri javob:
"Ha, xohlaganingizdek yasab beramiz.
Yuborgan rasmingizdagi guldan buket bo'yicha @euroflowerspremium ga yozing —
operatorlarimiz shu haqida sizga aniq ma'lumot berishadi."
"""

MARKER = "RASM BILAN KELGAN YASATMA HAM SHU BO'LIMGA KIRADI"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or ANCHOR not in prompt:
        return
    row.system_prompt = prompt.replace(ANCHOR, ANCHOR + INSERT, 1)
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(INSERT, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0152_ai_prompt_never_ask_for_a_phone")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
