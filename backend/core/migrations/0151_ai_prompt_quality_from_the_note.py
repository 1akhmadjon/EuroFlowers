# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """SO'LISH JAVOBINI FAQAT SO'RALGANDA BER"""

BLOCK = """"SIFATI QANAQA" — GULNI TA'RIFLA

"Sifati qanaqa", "qanaqa gul bu", "nimasi yaxshi", "сифати канака",
"канака гул бу" — bu gulning o'zi haqidagi savol. Javobni izohdan ol:
qaysi guldan yasalgani, nechta guli borligi, bo'yi, hidli yoki hidsizligi.
Izoh senda bo'lmasa avval get_catalog chaqir.
Namuna: "Alfalob atirgulidan yasalgan, 100 ta gul, bo'yi 55-60 sm, hidli."
Bunga so'lish haqidagi javobni berma — mijoz gulni bilmoqchi, uning holatini emas.
Izohda hech narsa bo'lmasagina operatorga yo'naltir.

"""


MARKER = '"SIFATI QANAQA" — GULNI TA\'RIFLA'


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or ANCHOR not in prompt:
        return
    row.system_prompt = prompt.replace(ANCHOR, BLOCK + ANCHOR, 1)
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(BLOCK, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0150_ai_prompt_no_repeat_no_greeting")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
