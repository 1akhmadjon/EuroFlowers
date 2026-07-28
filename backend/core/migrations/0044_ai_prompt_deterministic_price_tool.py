from django.db import migrations


DETERMINISTIC_PRICE_TOOL_RULE = """

Deterministik custom narx hisoblash qoidasi:
Custom buket yoki savat narxini hech qachon o'zing hisoblama. Mijoz gul turi va sonini aytgandan keyin avval get_stock orqali batch_id va dona narxini aniqlagin, keyin majburiy calculate_custom_arrangement_price toolini chaqir. Replyda faqat calculate_custom_arrangement_price qaytargan lines, display_summary_uz va total qiymatlaridan foydalan.
Mijoz "50 ta Jumiladan buket nechpul" desa, get_stock orqali Jumila batchini top, calculate_custom_arrangement_price ga quantity_stems 50 bilan yubor va tool qaytargan jami narxni ayt. Agar tool 800 000 qaytarsa, 775 000, 750 000 yoki boshqa raqam yozma.
Mijoz "10 ta Jumila va 10 ta Prutdan bitta buket" desa, calculate_custom_arrangement_price ga ikkala batchni alohida row qilib yubor. Tool qaytargan totalni o'zgartirma.
calculate_custom_arrangement_price errors qaytarsa, narx aytma. Qaysi guldan qoldiq yetmasligini qisqa ayt va boshqa miqdor yoki boshqa gul tanlashni so'ra.
Narx javobi qisqa bo'lsin: tool qaytargan har bir line, florist haqi, jami taxminiy narx va keyingi bitta savol. Hech qanday qo'shimcha formulani o'zing yozma.
"""


def append_deterministic_price_tool_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Deterministik custom narx hisoblash qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + DETERMINISTIC_PRICE_TOOL_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_ai_prompt_public_reply_boundaries"),
    ]

    operations = [
        migrations.RunPython(append_deterministic_price_tool_rule, migrations.RunPython.noop),
    ]
