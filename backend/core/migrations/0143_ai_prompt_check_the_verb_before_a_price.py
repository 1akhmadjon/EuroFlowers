from django.db import migrations


ANCHOR = """Ikkovi bir xil so'zdan boshlanishi mumkin, farqi oxiridagi fe'lda.
Mijoz narxni allaqachon ko'rgan bo'lsa, qayta so'rashi deyarli doim savdolashuv.
"""

INSERT = """
NARX YOZISHDAN OLDIN TEKSHIR. Har safar raqam yozishdan oldin mijozning oxirgi
xabarlariga qaytib qara: shu fe'llardan biri bormi —
"berasiz", "berasla", "qberasla", "qberas", "beras", "qo'yasiz", "qo'yib berasiz",
"qilib berasiz", "bo'lishi", "bolishi".
Bo'lsa yozadigan raqam kelishilgan narx, katalog narxi EMAS.
Bu tekshiruvni quyidagilar O'ZGARTIRMAYDI:
  xabarda boshqa savol ham turgani (manzil, yetkazib berish, ish vaqti)
  mijoz ikkita xabar ketma-ket yozgani
  savdolashuv savoli ikkinchi bo'lib kelgani
Ikki savolga javob yozayotganda ham narx qatorini shu tekshiruvdan o'tkaz.
"""

MARKER = "NARX YOZISHDAN OLDIN TEKSHIR"


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

    dependencies = [("core", "0142_ai_prompt_haggle_keeps_its_answer")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
