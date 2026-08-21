from django.db import migrations


MEDIA_MATCHING_PRIORITY_RULE = """

## MEDIA MATCHING USTUVOR QOIDASI

Bu qoida media handoff qoidalaridan ustun turadi.

REAL_CONTEXT_JSON conversation.customer_attachments ichida kamida bitta rasm, story, post yoki reel media bo'lsa va mijoz shu media haqida "shu nechpul", "bormi", "qaysi gul", "rasmdagi", "storydagi", "reeldagi", "tepadan 2chisi", "qizili", "chizilgan joydagi" kabi so'rasa:
1. Telefon so'rashdan oldin majburiy match_ai_catalog_by_media toolini chaqir.
2. match_ai_catalog_by_media natijasida is_confident true bo'lgan match bo'lsa, handoff_media_to_operator chaqirma.
3. Ishonchli match topilganda send_catalog_image ni catalog_id bilan chaqir va mijozga faqat nomi, narxi va keyingi bitta savolni yoz.
4. Faqat match_ai_catalog_by_media ok false qaytarsa yoki matches bo'sh bo'lsa operatorga yo'naltirish flowiga o't.
5. Oddiy rasm/screenshot kelgan holatda "operatorlar aniq javob beradi" deb darrov telefon so'rash xato. Avval katalogdan topishga urin.

Javob namunasi ishonchli match bo'lsa:
[Mahsulot nomi]
Narxi [narx] so'm
Sizga qachonga kerak edi?
"""


def add_media_matching_priority_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for row in AISettings.objects.all():
        prompt = row.system_prompt or ""
        if "## MEDIA MATCHING USTUVOR QOIDASI" not in prompt:
            row.system_prompt = prompt.rstrip() + MEDIA_MATCHING_PRIORITY_RULE
            row.save(update_fields=["system_prompt"])
    if not AISettings.objects.exists():
        AISettings.objects.create(system_prompt=MEDIA_MATCHING_PRIORITY_RULE.strip())


def remove_media_matching_priority_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    marker = "## MEDIA MATCHING USTUVOR QOIDASI"
    for row in AISettings.objects.all():
        prompt = row.system_prompt or ""
        index = prompt.find(marker)
        if index != -1:
            row.system_prompt = prompt[:index].rstrip()
            row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0131_ai_prompt_media_catalog_matching"),
    ]

    operations = [
        migrations.RunPython(add_media_matching_priority_rule, remove_media_matching_priority_rule),
    ]
