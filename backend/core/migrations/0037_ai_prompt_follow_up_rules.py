from django.db import migrations


FOLLOW_UP_PROMPT_RULE = """

Follow up va yopuvchi javob qoidasi:
Mijoz "hop", "yaxshi", "rahmat", "bo'ldi rahmat", "keyinroq", "o'ylab ko'raman" kabi suhbatni yumshoq yopadigan gap yozsa, qisqa javob ber: "Rahmat, kuningiz xayrli o'tsin" mazmunida. Bunday javoblarda yana savol berma, "yordam kerak bo'lsa yozing", "yana qanday savolingiz bor" yoki ism-raqam so'ramagin.
Mijoz "qimmat ekan", "yoqmadi", "boshqa joydan olaman", "kerak emas" kabi rad javob yozsa, bahslashma va qayta sotishga urinma. Qisqa xushmuomala javob ber va suhbatni yop.
Katalog, rasm, tayyor mahsulot narxi yoki custom buket/savat taxminiy narxini ko'rsatganingdan keyin mijoz jim qolsa, follow up task alohida AI tahlil bilan qaror qiladi. Follow up uchun shablon matn yozma, conversation holatiga qarab tabiiy premium ohangda budjetga mos variantni operatorlar ko'rsatishi mumkinligini aytib ism va raqam so'rash mumkin.
Lead yaratilgan, ism-raqam olingan yoki mijoz suhbatni yopgan holatlarda follow up kerak emas.
"""


def append_follow_up_prompt_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Follow up va yopuvchi javob qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + FOLLOW_UP_PROMPT_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_ai_prompt_available_catalog_only"),
    ]

    operations = [
        migrations.RunPython(append_follow_up_prompt_rule, migrations.RunPython.noop),
    ]
