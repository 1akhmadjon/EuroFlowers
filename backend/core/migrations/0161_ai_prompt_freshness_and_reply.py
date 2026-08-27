# -*- coding: utf-8 -*-
"""Ikki tuzatish, ikkalasi ham real suhbatdan.

1. "качон ясалган" savoli operatorga yo'naltirilardi. Bu javobi bor savol:
   gullar har doim yangi gulladan yasaladi.
2. Mijoz albomdagi rasmga reply qilib savol berganini AI ko'rmasdi. Endi
   suhbat matniga "Tizim izohi: mijoz shu mahsulot rasmiga javob qildi" qatori
   tushadi, shu blok esa uni qanday o'qishni aytadi.
"""
from django.db import migrations


FRESHNESS_OLD = (
    '"Qachon yasalgan", "necha kun turadi", "diametri qancha" — izohda bo\'lmasa\n'
    "operatorga yo'naltiriladi. O'zingdan kun soni, parvarish maslahati yoki\n"
    "do'konda yo'q gul nomini (lola, krizantema) YOZMA.\n"
)

FRESHNESS_NEW = (
    '"Qachon yasalgan", "qachon yasaldi", "качон ясалган", "qachon qilingan" — bu\n'
    "gul yangiligi savoli, operatorga YO'NALTIRILMAYDI. Javob bitta qator:\n"
    '"Gullarimiz har doim yangi — svejiy gullardan yasaladi, eskirgan gullarni\n'
    'sotmaymiz."\n'
    "Sana, kun yoki soat AYTMA — buketlar buyurtmaga yasaladi.\n"
    '"Necha kun turadi", "diametri qancha" — izohda bo\'lmasa operatorga\n'
    "yo'naltiriladi. O'zingdan kun soni, parvarish maslahati yoki\n"
    "do'konda yo'q gul nomini (lola, krizantema) YOZMA.\n"
)

ANCHOR = """00G. TO'LOV — HAMMA QOIDADAN USTUN"""

REPLY_BLOCK = """00K. MIJOZ RASMGA JAVOB QILSA — HAMMA QOIDADAN USTUN
════════════════════════════════════
Suhbat matnida "Tizim izohi: mijoz shu mahsulot rasmiga javob qildi — <nomi>"
qatori bo'lsa, mijozning savoli AYNAN o'sha mahsulot haqida.

"Nechpul", "qberasla", "bormi", "hajmi qanaqa", "qachonga tayyor bo'ladi" —
hammasi shu mahsulotga tegishli. "Qaysi gulni nazarda tutyapsiz" deb SO'RAMA,
katalogni qayta YUBORMA, budjet ham so'rama — mahsulot allaqachon ma'lum.

Javobni o'sha mahsulotning ma'lumotidan ol: oddiy narx savoliga price,
savdolashuvga izohdagi kelishilgan narx, hajm va bo'y savoliga izohdagi tafsilot.

Izohda bir necha nom bo'lsa mijoz butun albomga javob qilgan — o'shanda qaysi
biri ekanini so'ra.

════════════════════════════════════
"""

MARKER = "00K. MIJOZ RASMGA JAVOB QILSA"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if FRESHNESS_NEW not in prompt:
        prompt = prompt.replace(FRESHNESS_OLD, FRESHNESS_NEW, 1)
    if MARKER not in prompt and ANCHOR in prompt:
        prompt = prompt.replace(ANCHOR, REPLY_BLOCK + ANCHOR, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "").replace(REPLY_BLOCK, "", 1)
    row.system_prompt = prompt.replace(FRESHNESS_NEW, FRESHNESS_OLD, 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0160_ai_prompt_close_order")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
