from django.db import migrations


ANCHOR = """To'g'ri: kelishilgan narxni ayt, keyin manzilni ayt. Ikki qisqa qator.
"""

INSERT = """Ikki savolga javob berganda ham har bir savol O'Z javobini oladi. Savdolashuv
savoli boshqa savol bilan birga kelgani uchun oddiy narx savoliga aylanib qolmaydi:
"Nechpul qberasla" yonida "Manzil qayoda" turgan bo'lsa ham narx kelishilgan narx
bo'lib qoladi.
Xato: mijoz "Nechpul qberasla" va "Manzil qayoda" dedi, sen 1 000 000 so'm va
manzilni aytding. Savdolashuv payqalmay qoldi.
To'g'ri: 800 000 so'm va manzil.
"""

MARKER = "Savdolashuv\nsavoli boshqa savol bilan birga kelgani uchun"


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

    dependencies = [("core", "0141_ai_prompt_bargaining_verbs")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
