# -*- coding: utf-8 -*-
from django.db import migrations


OLD_BLOCK = """  lotin:  "solib qolmaganmi", "so'lmadimi", "so'lganmi", "svejiymi",
          "yangimi", "tabiiymi", "jivoymi"
  kirill: "солиб комаганми", "гул солиб комаганми", "солмадими", "свежийми",
          "янгими", "табиийми", "живойми\""""

NEW_BLOCK = """  lotin:  "solib qolmaganmi", "solib komaganmi", "so'lmadimi", "so'lganmi",
          "svejiymi", "yangimi", "eskimaganmi"
  kirill: "солиб комаганми", "гул солиб комаганми", "солмадими", "солганми",
          "свежийми", "янгими", "эскимаганми"

"Tabiiymi", "jivoymi", "sun'iymasmi", "табиийми", "живойми" esa BOSHQA savol —
gulning tirikligi haqida. Unga so'lish javobini berma, javob bir og'iz:
ha, hammasi tabiiy tirik gul."""

MARKER = "esa BOSHQA savol"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or OLD_BLOCK not in prompt:
        return
    row.system_prompt = prompt.replace(OLD_BLOCK, NEW_BLOCK, 1)
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(NEW_BLOCK, OLD_BLOCK, 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0148_ai_prompt_language_from_script")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
