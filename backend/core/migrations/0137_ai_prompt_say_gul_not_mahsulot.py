from django.db import migrations


ANCHOR = "00. QANDAY GAPIRASAN — HAMMA QOIDADAN USTUN"

INSERT = """MIJOZGA "MAHSULOT" DEMA. Bu ichki, do'kon ichidagi so'z. Mijoz gul sotib olyapti.
Uning o'rniga "gul", "buket", "savat", "kompozitsiya" yoki mahsulotning o'z nomini ishlat.
Xato: "Qaysi mahsulotni nazarda tutyapsiz?"
To'g'ri: "Qaysi gulni nazarda tutyapsiz?"
Xato: "Bu mahsulot 900 000 so'm."
To'g'ri: "Bu buket 900 000 so'm."
Xato: "Mahsulot haqida batafsil ma'lumot beray."
To'g'ri: "Shu savat haqida aytib beray."
Rus tilida ham shunday — "товар" so'zini ishlatma, "цветы", "букет", "корзина" de.

"""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if 'MIJOZGA "MAHSULOT" DEMA' in prompt or ANCHOR not in prompt:
        return
    marker = ANCHOR + "\n════════════════════════════════════\n"
    if marker not in prompt:
        return
    prompt = prompt.replace(marker, marker + INSERT, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(INSERT, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0136_ai_prompt_no_contact_ask_while_bargaining")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
