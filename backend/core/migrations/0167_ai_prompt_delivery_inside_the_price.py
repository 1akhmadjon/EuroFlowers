# -*- coding: utf-8 -*-
from django.db import migrations


MARKER = "NARX ICHIDA YETKAZIB BERISH BORMI"

ANCHOR = """YETKAZIB BERISH — mijozga olib borish xizmati
Shakllari: dostafka, dastafka, dostavka, yetkazib berish, yetkazasizlarmi, olib kelasizmi,
uyga olib kelasizmi, dastavka bormi.
Javob: yetkazib berish narxi va hududi.
Toshkentdan tashqari joy aytilsa 9A bo'limi ishlaydi."""

BLOCK = """YETKAZIB BERISH — mijozga olib borish xizmati
Shakllari: dostafka, dastafka, dostavka, yetkazib berish, yetkazasizlarmi, olib kelasizmi,
uyga olib kelasizmi, dastavka bormi.
Javob: yetkazib berish narxi va hududi.
Toshkentdan tashqari joy aytilsa 9A bo'limi ishlaydi.

NARX ICHIDA YETKAZIB BERISH BORMI — "ichida" so'zi qutini anglatmaydi
Shakllari: "dastafka ichidami", "dastafka ichida qib berolasmi", "dostavka ichidami",
"narx ichida dostavkasi bormi", "shu pulga olib kelasizmi", "yetkazib berish shu
summaga kiradimi", "доставка входит в цену", "с доставкой столько ми".
Bu QUTI, IDISH yoki QADOQ haqidagi savol EMAS. Mijoz yetkazib berish puli
mahsulot narxiga kirganmi deb so'rayapti.
Javob: yetkazib berish alohida to'lanadi, Toshkent shahri ichida 50 000 so'm.
Keyin jamini ayt: gul narxi + yetkazib berish.
To'g'ri: "Yetkazib berish alohida — Toshkent ichida 50 000 so'm. Gul 200 000,
jami 250 000 so'm bo'ladi."
Xato, real suhbat 2259: mijoz "Dastafka ichida qib berolasmi oka 200 mi" deb
so'radi, javobda quti ranglari va idish narxi aytildi — mijoz esa yetkazib
berish haqida so'ragan edi.
Idish, quti va qadoq haqidagi savol boshqacha yoziladi: "qutida bo'ladimi",
"korobkada", "idishi qanaqa", "upakovka"."""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or prompt.count(ANCHOR) != 1:
        return
    row.system_prompt = prompt.replace(ANCHOR, BLOCK, 1)
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(BLOCK, ANCHOR, 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0166_ai_prompt_operator_consent_and_function_rules")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
