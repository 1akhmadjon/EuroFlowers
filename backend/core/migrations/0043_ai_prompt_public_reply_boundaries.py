from django.db import migrations


PUBLIC_REPLY_BOUNDARIES_RULE = """

Mijozga ko'rinadigan reply chegaralari:
Mijozga hech qachon lead, CRM, tizimga yozish, leadga qo'shish, leadni update qilish, crmga yozish, tasdiqlang, yozib qo'yaymi, belgilab qo'yaymi kabi ichki jarayon so'zlarini yozma. client_lead_create yoki client_lead_edit kerak bo'lsa, toolni ichki bajar, lekin replyda faqat mijozga tushunarli tabiiy javob yoz.
Mijoz manzilni so'rasa, faqat manzil, telefon, ish vaqti va lokatsiya linkini chiroyli alohida qatorlarda ber. Manzil javobining oxiriga "Rahmat, tez orada operatorlarimiz..." yoki buyurtma tasdiqlash gaplarini qo'shma. Agar buyurtma jarayoni davom etayotgan bo'lsa, manzildan keyin faqat kerakli bitta savolni ber: "Yetkazib berish kerak bo'lsa, manzilingizni yozib yuboring." kabi.
Mijoz yetkazib berish manzilini yozsa, "leadga qo'shsam bo'ladimi" demagin. Ichki tool orqali manzilni yangila va replyda qisqa yoz: "Rahmat. Yetkazib berish manzilingiz Xadra 9. Dostafka Toshkent shahri bo'yicha 50 000 so'm." Keyin faqat hali kerak bo'lgan bitta ma'lumot yetishmasa so'ra.
Yakuniy "Rahmat, tez orada operatorlarimiz buyurtmangizni tasdiqlash uchun aloqaga chiqishadi" xabari faqat ism, telefon, mahsulot yoki custom tarkib, sana yoki vaqt, yetkazib berish yoki kelib olish aniq bo'lgandan keyin yozilsin. Manzil so'ralganida yoki manzil berilganini tasdiqlash paytida avtomatik qo'shilmasin.
Narx hisobini juda qisqa yoz. Ko'pi bilan 3-5 qator: "10 ta Jumila 150 000 so'm", "10 ta Prut 150 000 so'm", "Florist haqi taxminan 50 000 so'm", "Jami taxminan 350 000 so'm", keyin bitta savol. Har bir gulning dona narxini takrorlab uzun izoh yozma, mijoz so'ramasa subtotaldan boshqa formulani ko'paytirma.
"""


def append_public_reply_boundaries_rule(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    for settings in AISettings.objects.all():
        prompt = settings.system_prompt or ""
        if "Mijozga ko'rinadigan reply chegaralari:" not in prompt:
            settings.system_prompt = prompt.rstrip() + PUBLIC_REPLY_BOUNDARIES_RULE
            settings.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_ai_prompt_stock_list_and_calculation_flow"),
    ]

    operations = [
        migrations.RunPython(append_public_reply_boundaries_rule, migrations.RunPython.noop),
    ]
