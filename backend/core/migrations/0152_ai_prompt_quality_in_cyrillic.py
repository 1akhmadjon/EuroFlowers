# -*- coding: utf-8 -*-
from django.db import migrations


OLD = '''"Sifati qanaqa", "qanaqa gul bu", "nimasi yaxshi", "сифати канака",
"канака гул бу" — bu gulning o'zi haqidagi savol.'''

NEW = '''"Sifati qanaqa", "qanaqa gul bu", "nimasi yaxshi", "nechta guli bor",
"сифати канака", "канака гул бу" — bu gulning o'zi haqidagi savol.
Kirilldan o'girilganda bu savol "sifati kanaka", "kanaka gul bu", "sifati kanday"
bo'lib keladi — "k" bu yerda "q" degani, shuni ham shu savol deb qabul qil.'''

MARKER = '"sifati kanaka", "kanaka gul bu"'


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or OLD not in prompt:
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

    dependencies = [("core", "0151_ai_prompt_quality_from_the_note")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
