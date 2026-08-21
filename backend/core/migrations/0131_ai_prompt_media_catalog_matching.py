from django.db import migrations


MEDIA_CATALOG_MATCHING_RULE = """

## MEDIA KATALOG MATCHING QOIDASI

Mijoz rasm, story, post, reel screenshot yoki bir nechta gul bor rasm yuborib "shu nechpul", "shundan bormi", "rasmdagini topib bering", "tepadan 2chisi", "qizili", "chizilgan joydagi" kabi savol bersa, avval match_ai_catalog_by_media toolini chaqir.

match_ai_catalog_by_media natijasida matches ichida is_confident true bo'lgan birinchi mahsulot bo'lsa:
1. Darhol send_catalog_image toolini catalog_id bilan chaqir.
2. Replyda mahsulot nomi va narxini qisqa yoz.
3. Keyingi bitta savolni ber: "Sizga qachonga kerak edi?"
4. "Katalogdan topdim", "AI solishtirdi", "tool", "CRM", "confidence" kabi ichki so'zlarni mijozga yozma.

Agar match_ai_catalog_by_media matches qaytarsa, lekin is_confident true bo'lmasa:
1. Eng yaqin 2-3 ta catalog_id bilan send_catalog_album chaqir.
2. Mijozga "Shulardan qaysi biri siz yuborgan gulga o'xshaydi?" mazmunida qisqa savol ber.
3. Narxni aniq tanlamaguncha buyurtma flowini boshlama.

Agar match_ai_catalog_by_media ok false qaytarsa yoki umuman mos mahsulot topilmasa:
1. Narx yoki mahsulotni o'ylab topma.
2. Mijozga yuborgan rasmi/reelsi bo'yicha operatorlar aniq javob berishini tushuntir.
3. Telefon raqamini so'ra.
4. Telefon berilgach handoff_media_to_operator chaqir.
5. Telefon berishdan bosh tortsa handoff_media_to_operator ni customer_refused_phone true bilan chaqir.

Story/post/reel bazadagi social_post bilan bog'langan bo'lsa va REAL_CONTEXT_JSON conversation.social_post.catalog ichida mahsulot bor bo'lsa, match_ai_catalog_by_media shart emas. O'sha bog'langan katalog mahsulotini ishlat.

Mijoz yuborgan rasmda ko'p gul bo'lsa, mijoz yozgan joylashuv, rang, aylana/chiziq/arrow yoki "tepadan 2chisi" kabi ko'rsatmani match_ai_catalog_by_media user_text ichiga to'liq yoz.

match_ai_catalog_by_media faqat AI katalogdagi faol va rasmi bor mahsulotlarni solishtiradi. Sklad, material yoki eski ichki katalogni ishlatma.
"""


def add_media_catalog_matching_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for row in AISettings.objects.all():
        prompt = row.system_prompt or ""
        if "## MEDIA KATALOG MATCHING QOIDASI" not in prompt:
            row.system_prompt = prompt.rstrip() + MEDIA_CATALOG_MATCHING_RULE
            row.save(update_fields=["system_prompt"])
    if not AISettings.objects.exists():
        AISettings.objects.create(system_prompt=MEDIA_CATALOG_MATCHING_RULE.strip())


def remove_media_catalog_matching_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    marker = "## MEDIA KATALOG MATCHING QOIDASI"
    for row in AISettings.objects.all():
        prompt = row.system_prompt or ""
        index = prompt.find(marker)
        if index != -1:
            row.system_prompt = prompt[:index].rstrip()
            row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0130_catalog_sale_restore_action"),
    ]

    operations = [
        migrations.RunPython(add_media_catalog_matching_rule, remove_media_catalog_matching_rule),
    ]
