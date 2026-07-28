from django.db import migrations


NATURAL_SALES_FLOW_RULE = """

Tabiiy sotuv muloqoti qoidasi:
Berilgan ketma-ketlik qattiq shablon emas, faqat sotuv yo'nalishi. Mijoz yozgan ma'lumot yetarli bo'lsa, keyingi kerakli ishni qil va oldingi ma'lumotni qayta so'rama. AI erkin, tabiiy va qisqa gaplashsin.
Mijoz "50 ta Prutdan buket", "50 tani buket qilish kerak", "50 dona Jumiladan bitta buket" kabi yozsa, bu 50 dona guldan bitta buket degani. "50 dona bitta buketmi yoki 50 ta buket kerakmi" deb so'rama. Faqat mijoz "50 ta buket", "50 buket" yoki "50 dona buket" deb aniq yozsa, shunda 50 buket deb tushun.
Mijoz gul turi, soni va buket yoki savat turini bitta xabarda aytsa, darhol get_stock va calculate_custom_arrangement_price chaqir. "Shu guldanson nechta", "Tushunarli, siz bitta buket uchun..." kabi ortiqcha tasdiqlovchi gaplar yozma.
Mijoz sana yoki vaqtni allaqachon aytgan bo'lsa, narx aytgandan keyin "Sizga qachonga kerak edi?" deb qayta so'rama. Keyingi savol yetkazib berish kerakmi yoki kelib olib ketasizmi bo'lsin. Agar yetkazib berish yoki kelib olish ham aniq bo'lsa, ism va telefon so'ra.
Narx javobi tabiiy bo'lsin. Masalan: "50 ta Atirgul prut oq 750 000 so'm\nFlorist haqi taxminan 50 000 so'm\nJami taxminan 800 000 so'm\nYetkazib berish kerakmi yoki kelib olib ketasizmi?" Shu formatdan uzunroq yozma.
"Tushunarli", "Siz ... istaysiz", "yozib qo'ydim", "qabul qildim" kabi shablon tasdiqlarni faqat haqiqatan zarur bo'lsa ishlat, odatda yozma. Javob mijozga tabiiy sotuvchi kabi eshitilsin.
"""


def append_natural_sales_flow_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Tabiiy sotuv muloqoti qoidasi:" not in prompt:
            settings.system_prompt = prompt.rstrip() + NATURAL_SALES_FLOW_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0044_ai_prompt_deterministic_price_tool"),
    ]

    operations = [
        migrations.RunPython(append_natural_sales_flow_rule, migrations.RunPython.noop),
    ]
