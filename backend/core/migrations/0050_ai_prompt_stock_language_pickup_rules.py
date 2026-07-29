from django.db import migrations


STOCK_LANGUAGE_PICKUP_PROMPT_RULE = """

Stock mavjudlik, til va pickup aniqligi:
Mijoz aniq gul nomi bilan "bormi", "борми", "есть ли", "продаете" kabi so'rasa, javobdan oldin majburiy get_stock chaqir. Tool bo'sh qaytsa "hozir skladimizda bu gul qolmagan ekan" mazmunida aniq javob ber. Tool natijasida gul qaytsa hech qachon "yo'q", "ko'rinmayapti" yoki "vitrinada yo'q" deb yozma.
Rus tilida "какие цветы есть" yoki shunga o'xshash stock so'rovida get_stock natijasi bo'sh bo'lmasa, "витринада йўқ" yoki "в витрине нет" deb yozma. Bu katalog emas, sklad so'rovi. Ruscha javobda mavjud stock gullarini nomi va dona narxi bilan ko'rsat.
O'zbek kirillda javob berayotganda stock tooldagi display_name_uz_cyril, flower_uz_cyril, variant_uz_cyril, color_uz_cyril maydonlaridan foydalan. Lotin nomlarni kirill gap ichiga aralashtirib buzib yozma.
Mijoz "borib olaman", "kelib olaman", "o'zim olib ketaman" desa, ichki jarayonni aytma. "qayd etildi", "saqlab qo'yildi", "buyurtma saqlanmoqda" kabi gaplar yozma. Manzilni chiroyli qatorlarda ber va faqat kerakli yakuniy rahmat javobini yoz.
Mijoz faqat manzil, telefon yoki ish vaqtini so'rasa, buyurtma holati haqida yozma. Faqat manzil, telefon, ish vaqti va lokatsiya linkini ber.
"""


def append_stock_language_pickup_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Stock mavjudlik, til va pickup aniqligi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + STOCK_LANGUAGE_PICKUP_PROMPT_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0049_ai_prompt_stock_image_tool_required"),
    ]

    operations = [
        migrations.RunPython(append_stock_language_pickup_rule, migrations.RunPython.noop),
    ]
