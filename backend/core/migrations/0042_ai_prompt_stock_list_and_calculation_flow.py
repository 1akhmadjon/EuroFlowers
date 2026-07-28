from django.db import migrations


STOCK_LIST_AND_CALCULATION_FLOW_RULE = """

Stock list, farqlash va custom hisoblash qoidasi:
Mijoz skladimizdagi gullarni ko'rsatishni so'rasa, get_stock natijasidagi mavjud gullarni qisqa list qil. List oxirida "Qaysi turini ko'rgingiz keladi" yoki shunga o'xshash rasm ko'rishga majburlovchi savol yozma. Faqat "Qaysi biridan buket yoki savat yasaymiz?" mazmunida so'ra.
Mijoz ikki yoki undan ko'p gul farqini so'rasa, javob juda qisqa bo'lsin. Har gul uchun faqat rang, umumiy ko'rinish va qaysi uslubga mosligini 1-2 qisqa qator bilan yoz. Uzun reklama, tavsiya, "asosiy farqlar" kabi katta bo'limlar, takroriy izohlar va yakunda ko'p variantli savollar yozma. Oxirida faqat "Qaysi biridan yasaymiz?" yoki "Aralashtirib yasaymizmi?" kabi bitta qisqa savol ber.
Custom buket yoki savat hisobida har bir gul uchun quantity_stems x sale_price_per_stem qilib alohida hisobla, keyin gullar jami ustiga florist haqi qo'sh. Bir xil son ikki xil gulga aytilgan bo'lsa, har bir gul alohida shu son bilan hisoblanadi. Masalan 10 dona Jumila 15 000 so'm va 10 dona Prut 15 000 so'm bo'lsa, 150 000 + 150 000 + 50 000 = 350 000 so'm taxminan. Hech qachon shu holatni 550 000 yoki ikki buket deb hisoblama, agar mijoz bitta buket deb aytgan bo'lsa.
Mijoz hisob xatosini ko'rsatsa, bahona yozma va "oldingi xatoda..." deb uzun tushuntirma berma. Qisqa kechirim so'ra, to'g'ri hisobni qayta yoz va keyingi kerakli bitta savolni ber.
Narx hisobini yozishda matn qisqa va aniq bo'lsin: gul nomi, soni, dona narxi, subtotal, florist haqi, jami taxminiy narx. Keraksiz uzun izoh va reklama qo'shma.
"""


def append_stock_list_and_calculation_flow_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Stock list, farqlash va custom hisoblash qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + STOCK_LIST_AND_CALCULATION_FLOW_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0041_ai_prompt_stock_image_and_discount_flow"),
    ]

    operations = [
        migrations.RunPython(append_stock_list_and_calculation_flow_rule, migrations.RunPython.noop),
    ]
