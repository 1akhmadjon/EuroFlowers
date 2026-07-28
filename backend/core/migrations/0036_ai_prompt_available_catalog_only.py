from django.db import migrations


CATALOG_ONLY_PROMPT_RULE = """

Katalog mavjudlik qoidasi:
Katalog, vitrina, tayyor buket yoki tayyor savat haqida javob berishdan oldin doim get_catalog chaqir. Mijozga faqat get_catalog natijasida qaytgan mahsulotlarni ayt. get_catalog bo‘sh qaytsa, katalogda hozir tayyor mahsulot yo‘q ekan deb qisqa ayt va custom buket yoki savat yig‘dirib berishni taklif qil. Promptdagi eski misollar, oldingi chatdagi mahsulotlar, o‘chirilgan yoki sotilgan katalog gullarini hech qachon mijozga mavjud deb aytma.
"""


def append_catalog_only_prompt_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Katalog mavjudlik qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + CATALOG_ONLY_PROMPT_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_ai_prompt_fifo_and_catalog_payment"),
    ]

    operations = [
        migrations.RunPython(append_catalog_only_prompt_rule, migrations.RunPython.noop),
    ]
