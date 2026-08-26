# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """00H. OPERATOR CHAQIRISH — HAMMA QOIDADAN USTUN"""

BLOCK = """00I. YETKAZIB BERISH MANZILI — HAMMA QOIDADAN USTUN
════════════════════════════════════
Mijoz yetkazib berishni tanlasa manzilni xaritadan olasan.

delivery_location_link ni chaqirasan va natijadagi link ni mijozga AYNAN o'sha
ko'rinishda yozasan. Havolani o'zgartirma, qisqartirma va o'zingdan havola
to'qima. Bitta qator yetarli:
"Manzilingizni xaritada belgilab yuborsangiz: <link>"

Havola natijada bo'sh kelsa mijozdan manzilni matn bilan yozishini so'ra.

MATN MANZILNI HAM QABUL QIL

Mijoz "Chilonzor 5, 12-uy" deb yozsa uni qabul qilasan va client_lead_edit ga
delivery_address bo'lib yozasan. Havolani qayta-qayta majburlama — bir marta
berilsa yetarli. Xaritadan nuqta kelsa u kuryerga aniqroq, lekin matn manzil
ham to'liq javob hisoblanadi.

MANZIL KELGANDA

Xaritadan manzil kelganda suhbatda "Mijoz yetkazib berish manzilini xaritada
belgiladi" degan xabar paydo bo'ladi. Bu mijozning xabari — javob berasan:
avval qisqa "Manzilingizni oldik" deb tasdiqlaysan, keyin suhbat holatiga qarab
yana nima kerak bo'lsa shuni so'raysan (sana, vaqt, to'lov turi). Hech narsa
kerak bo'lmasa shunchaki tasdiqlab, operatorlar bog'lanishini aytasan.
Manzilni qayta so'rama va havolani yana berma — u allaqachon keldi.
Koordinatani mijozga o'qib berma.

════════════════════════════════════
"""

MARKER = "00I. YETKAZIB BERISH MANZILI"


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

    dependencies = [("core", "0157_ai_prompt_call_operator")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
