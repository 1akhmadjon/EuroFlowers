from django.db import migrations


QUALITY_REASSURANCE_PROMPT_RULE = """

Sifat, yangi gul va obyom bo'yicha aniq javob qoidasi:
Mijoz "obyomi kichkina bo'lib qolmaydimi", "hajmi kichkina bo'p qolmaydimi", "razmeri kichkina emasmi", "obyomi qanaqa" kabi xavotir bildirsa, texnik balandlik yoki keraksiz tavsif yozma. Shunday mazmunda qisqa, ishonchli javob ber: "Ko'nglingiz xotirjam bo'lsin, floristlarimiz mahoratli. Buket yoki savat hajmini ko'nglingizdagidek chiroyli qilib tayyorlab berishadi." Keyin faqat juda zarur bo'lsa bitta keyingi savol ber.
Mijoz "gulila yangimi", "so'lib qolgan gullardan yasab bermaysizlarmi", "eski gul bilan yasamaysizlarmi", "gulila yaxshimi", "solib qolgan gul" kabi sifat va yangilik haqida so'rasa, hech qachon so'lib qolgan guldan yasab berishni taklif qilma. Javob qat'iy bo'lsin: "Bizda so'lib qolgan gullar bilan hech qachon buket yoki savat yasalmaydi, ko'nglingiz xotirjam bo'lsin." Mijoz qaysi tilda va yozuvda yozgan bo'lsa, shu mazmunni o'sha tilda yoz.
Bunday sifat yoki obyom savollarida yangi gul nomi, qo'shimcha tarkib, aralashtirish, qaysi guldan nechta qo'shamiz kabi narsalarni o'zingdan generate qilma. Faqat mijoz so'ragan xavotirga javob ber.
"""


def append_quality_reassurance_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Sifat, yangi gul va obyom bo'yicha aniq javob qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + QUALITY_REASSURANCE_PROMPT_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0047_restore_legacy_ai_prompt"),
    ]

    operations = [
        migrations.RunPython(append_quality_reassurance_rule, migrations.RunPython.noop),
    ]
