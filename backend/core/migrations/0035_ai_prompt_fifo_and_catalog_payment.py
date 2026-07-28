from django.db import migrations


FIFO_PROMPT_RULE = """

Sklad FIFO qoidasi:
get_stock yoki get_flower_variant_info natijasida bir xil gul turi, navi va rang bo‘yicha faqat eng birinchi kelgan faol partiya mijozga ko‘rsatiladi. Bir xil tur/nav/rangdan bir nechta partiya bo‘lsa, qolgan partiyalarni mijozga sanama. Birinchi kelgan partiya tugamaguncha keyingi partiya narxi yoki qoldig‘ini aytma. Tool natijasida bitta variant uchun bitta active_stock qaytsa, shu do‘kon sotayotgan hozirgi partiya deb qabul qil.
"""


def append_fifo_prompt_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Sklad FIFO qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + FIFO_PROMPT_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_remove_packaging_branch_remove_notification_branch_and_more"),
    ]

    operations = [
        migrations.RunPython(append_fifo_prompt_rule, migrations.RunPython.noop),
    ]
