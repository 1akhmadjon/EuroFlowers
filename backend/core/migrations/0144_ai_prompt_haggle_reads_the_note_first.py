from django.db import migrations


ANCHOR = """Ikki savolga javob yozayotganda ham narx qatorini shu tekshiruvdan o'tkaz.
"""

INSERT = """
Fe'l topilgach shu ikki qadamni bajar, boshqasini emas:
  1. get_catalog chaqirib mijoz gapirayotgan gulni top. Qaysi gul ekani suhbatda
     aniq turibdi — bu oxirgi ko'rsatilgan yoki oxirgi gaplashilgan gul.
     "Qaysi gulni nazarda tutyapsiz" deb qayta SO'RAMA.
  2. Uning izohidagi kelishilgan narxni bitta qatorda ayt:
     "<GUL NOMI> 800 000 so'm qilib beramiz."

Izohda kelishilgan narx bor ekan, 10C dagi umumiy javobni YOZMA. Ya'ni
"gullarimizning yangiligi va floristlarimizning mehnati narxga ta'sir qiladi",
"budjetingiz qancha" degan qatorlar bu yerga YOZILMAYDI. Ular faqat izohda
kelishilgan narx bo'lmaganda ishlatiladi.
Xato: mijoz "Nechpul qberasla" dedi, sen "Gullarimiz yangiligi va floristlarimizning
mehnati narxga ta'sir qiladi. Budjetingiz qancha?" deb yozding — izohda 800 000
turgan bo'lsa ham.
To'g'ri: "OQ JUMILA ATIR GULIDAN KOMPAZITSIYA 800 000 so'm qilib beramiz."
"""

MARKER = "Fe'l topilgach shu ikki qadamni bajar"


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

    dependencies = [("core", "0143_ai_prompt_check_the_verb_before_a_price")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
