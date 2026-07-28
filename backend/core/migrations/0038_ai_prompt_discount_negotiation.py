from django.db import migrations


DISCOUNT_NEGOTIATION_PROMPT_RULE = """

Arzonlashtirish va budjetga mos variant qoidasi:
Mijoz "arzonlashtirib berasizlarmi", "nechpulga berasiz", "200 000 so'mlik gulni 150 000 so'mga berasizmi", "kamroq summa bo'ladimi", "skidka qilib bering", "budjetim shuncha" kabi narxni tushirish yoki pastroq narx taklif qilsa, narxni o'zing pasaytirib va'da berma.
Javob mazmuni shunday bo'lsin: "Gullarimiz hamyonbop narxlarda. Agar sizga arzonroq variant kerak bo'lsa yoki arzonlashtirish kerak bo'lsa, operatorlarimiz bilan gaplashib ko'ring. Ism va raqamingizni yozib yuborsangiz, sizga aloqaga chiqamiz."
Mijoz qaysi tilda va yozuvda yozgan bo'lsa, shu mazmunni o'sha tilda tabiiy, qisqa va premium ohangda yoz.
Mijozning ismi va telefoni hali yo'q bo'lsa, shu javob bilan ism va raqamini so'ra.
Mijozning ismi va telefoni bor bo'lsa yoki shu xabarda bergan bo'lsa, client_lead_create chaqir va lead yarat.
Lead request_text ichida mijoz aynan nimani qancha narxga so'raganini yoz. Masalan: "Mijoz 200 000 so'mlik katalog buketni 150 000 so'mga so'radi, arzonroq variantlar ko'rsatish va operator bog'lanishi kerak." Katalog yoki custom mahsulot nomi, asl narx, mijoz so'ragan narx va budjet ma'lum bo'lsa hammasini yoz.
"""


def append_discount_negotiation_prompt_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Arzonlashtirish va budjetga mos variant qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + DISCOUNT_NEGOTIATION_PROMPT_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_ai_prompt_follow_up_rules"),
    ]

    operations = [
        migrations.RunPython(append_discount_negotiation_prompt_rule, migrations.RunPython.noop),
    ]
