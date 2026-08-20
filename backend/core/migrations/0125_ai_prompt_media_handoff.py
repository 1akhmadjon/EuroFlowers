from django.db import migrations


MEDIA_HANDOFF_RULE = """

## MEDIA OPERATORGA TOPSHIRISH QOIDASI

Mijoz rasm, post, reel, story yoki boshqa media yuborsa va sen undan aniq gul/narx/mahsulotni ishonchli aniqlay olmasang, javobni o'zingdan to'qima.

Birinchi javobda mijozga tabiiy tushuntir:
"Yuborgan rasmingiz/reelsingiz bo'yicha operatorlarimiz sizga aniq javob berishadi. Telefon raqamingizni yozib yuboraolasizmi?"
Mijoz tiliga mos yoz. Agar o'zbek kirillda yozsa kirillda yoz.

Telefon raqam kelgach handoff_media_to_operator toolini chaqir.
summary ichida operatorga kerak bo'ladigan xulosani yoz: mijoz nima yubordi, nimani so'radi, qanday javob kerak.
phone ichiga mijoz bergan raqamni yoz.
customer_refused_phone false bo'lsin.

Agar mijoz telefon berishni xohlamasa, rad etsa yoki "raqamsiz bo'ladimi" desa, bahslashma. handoff_media_to_operator toolini phone null va customer_refused_phone true bilan chaqir. Mijozga operatorlar chat orqali ko'rib chiqishini qisqa ayt.

Media linklarini argumentga yozma. Tool suhbatdagi customer_attachments dan o'zi oladi.

handoff_media_to_operator chaqirilgandan keyin mijozga "Operatorlarimiz ko'rib chiqib, sizga aniq javob berishadi" mazmunida qisqa javob ber.
Hech qachon mijozga "tool", "CRM", "guruhga yubordim", "tizimga qo'shdim" kabi ichki so'zlarni yozma.
"""


def add_media_handoff_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for row in AISettings.objects.all():
        prompt = row.system_prompt or ""
        if "## MEDIA OPERATORGA TOPSHIRISH QOIDASI" not in prompt:
            row.system_prompt = prompt.rstrip() + MEDIA_HANDOFF_RULE
            row.save(update_fields=["system_prompt"])
    if not AISettings.objects.exists():
        AISettings.objects.create(system_prompt=MEDIA_HANDOFF_RULE.strip())


def remove_media_handoff_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    marker = "## MEDIA OPERATORGA TOPSHIRISH QOIDASI"
    for row in AISettings.objects.all():
        prompt = row.system_prompt or ""
        index = prompt.find(marker)
        if index != -1:
            row.system_prompt = prompt[:index].rstrip()
            row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0124_supplier_debt_adjustment"),
    ]

    operations = [
        migrations.RunPython(add_media_handoff_rule, remove_media_handoff_rule),
    ]
