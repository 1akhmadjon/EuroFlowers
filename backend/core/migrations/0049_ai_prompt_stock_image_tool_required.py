from django.db import migrations


STOCK_IMAGE_TOOL_REQUIRED_RULE = """

Sklad rasmi bo'yicha qat'iy qoida:
Mijoz skladdagi gullar ro'yxatini so'raganda faqat mavjud gul nomi va dona narxini qisqa ro'yxat qil. Javob oxirida "rasmni ko'rmoqchimisiz", "rasmini yuboraymi", "qaysi turini ko'rgingiz keladi" kabi rasmga majburlovchi savol yozma. Faqat "Qaysi biridan buket yoki savat yasaymiz?" mazmunida tugat.
Mijoz "rasm ko'rsat", "rasmini yubor", "qani", "rasm korsatchi" kabi rasm so'rasa, albatta send_stock_image yoki send_stock_images toolini chaqir. Tool chaqirmasdan hech qachon "rasmni yubordim", "mana rasmi", "rasmi" deb yozma.
send_stock_image yoki send_stock_images tooli ok true qaytganidan keyingina rasm yuborilganini bildiruvchi qisqa reply yozish mumkin. Agar tool image_not_found qaytarsa, rasm hozir yo'qligini ayt va gul nomi, dona narxi hamda nechta dona qilib buket yoki savat yasashni so'ra.
"""


def append_stock_image_tool_required_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Sklad rasmi bo'yicha qat'iy qoida:" not in prompt:
            settings.system_prompt = prompt.rstrip() + STOCK_IMAGE_TOOL_REQUIRED_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0048_ai_prompt_quality_reassurance"),
    ]

    operations = [
        migrations.RunPython(append_stock_image_tool_required_rule, migrations.RunPython.noop),
    ]
