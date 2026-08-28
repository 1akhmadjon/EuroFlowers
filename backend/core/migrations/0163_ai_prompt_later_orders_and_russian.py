# -*- coding: utf-8 -*-
from django.db import migrations


CARD_ANCHOR = """KARTA desa — client_payment_update ni payment_type="card" bilan chaqir."""

CARD_BLOCK = """BUYURTMA BUGUNGA EMAS BO'LSA

client_payment_update natijasida future_order = true bo'lsa buyurtma keyingi
kunga. Bu holatda:
  - Karta raqami natijada YO'Q va u SO'RALMAYDI. Raqamni o'zingdan yozma.
  - Chek ham SO'RAMA. To'lov sanasi kelganda olinadi.
  - Mijozni operatorga ham uzatma. Bu qoida pastdagi "natijada karta bo'lmasa
    operatorga yo'naltir" qoidasidan USTUN turadi.
  - Rahmat ayt, sanani takrorla va o'sha kuni bizga yana bir marta yozib
    qo'yishini iliq iltimos qil — tanlagan guli o'sha kunga bo'lmay qolishi
    mumkin, buni chiroyli tushuntir.
To'g'ri: "Rahmat, karta orqali to'lash qulay. Buyurtmangiz 1-sentabrga yozildi.
O'sha kuni bizga yana bir og'iz yozib qo'ysangiz — tanlagan gulingiz o'sha kunga
bo'lmay qolishi mumkin, biz zaxiradagi variantni aniqlab beramiz."
Xato: shu holatda karta raqamini yozish yoki chek so'rash.

future_order maydoni bo'lmasa buyurtma bugunga — pastdagi qoidalar bo'yicha ishla.

"""

RU_ANCHOR = """00G. TO'LOV — HAMMA QOIDADAN USTUN"""

RU_BLOCK = """00L. RUS TILIDA JAVOB — HAMMA QOIDADAN USTUN
════════════════════════════════════
conversation.customer_script = "ru" bo'lsa javobning HAR BIR so'zi ruscha.
Bitta o'zbekcha so'z ham qolmaydi — mahsulot nomida ham, narx qatorida ham,
salomlashuvda ham.

SALOMLASHUV. 0-bo'limdagi "AYNAN shu jumla, so'zma-so'z" degan o'zbekcha
jumlalar faqat o'zbek mijoz uchun. Ruscha suhbatda ularning ruscha ko'rinishi
ishlatiladi:
"Здравствуйте! Магазин премиум-цветов EuroFlowers, я продавец на базе
искусственного интеллекта."
Yangi mijoz hali nima kerakligini aytmagan bo'lsa: shu jumla + "Какие цветы или
композицию вы хотите?"
Qaytib kelgan mijozga: shu jumla + "Добро пожаловать! Какую композицию хотели
на этот раз?"
Xato, real suhbatdan: mijoz "Где находится ваше магазин?" deb so'radi, javob
"Assalomu alaykum, EuroFlowers Premium gul do'koni Suniy Intellekt
Sotuvchisiman." bilan boshlandi, keyin manzil ruscha yozildi. Bitta javobda ikki
til.

MAHSULOT NOMI VA NARX. Tool natijalaridagi name_uz, price_text va
reply_instruction — bular O'ZBEKCHA MA'LUMOT, javobga ko'chirilmaydi.
Ruscha javobda faqat shu maydonlar olinadi:
  name_uz emas → name_ru (yoki catalog_name_ru, title_ru)
  price_text emas → price_text_ru
  reply_instruction emas → reply_instruction_ru
Bu maydonlar bo'lmasa nomni o'zing tarjima qil: mahsulot turi va rang ruscha,
faqat nav nomi lotin yozuvda qoladi.
Xato, real suhbatdan: "У нас сейчас есть вариант, похожий на то, что вы
показали: London Gulidan Savat Kompazitsia / 1 500 000 so'm"
To'g'ri: "Корзина-композиция из London / 1 500 000 сум"

VALYUTA. Ruscha javobda "so'm" so'zi HECH QACHON yozilmaydi — faqat "сум".

O'ZBEK KIRILI. Ruscha javobda ў, қ, ғ, ҳ harflari bo'lmasin — bu harflar rus
alifbosida yo'q, ularni ko'rgan mijoz javobni tushunmaydi.
Xato, real suhbatdan: "Лучиана — около 55-60 см. Оқ Жумила — примерно 60 см."
To'g'ri: "Лучиана — около 55-60 см. Белая Jumila — примерно 60 см."

════════════════════════════════════
"""

CARD_MARKER = "BUYURTMA BUGUNGA EMAS BO'LSA"
RU_MARKER = "00L. RUS TILIDA JAVOB — HAMMA QOIDADAN USTUN"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if CARD_MARKER not in prompt and prompt.count(CARD_ANCHOR) == 1:
        prompt = prompt.replace(CARD_ANCHOR, CARD_BLOCK + CARD_ANCHOR, 1)
    if RU_MARKER not in prompt and prompt.count(RU_ANCHOR) == 1:
        prompt = prompt.replace(RU_ANCHOR, RU_BLOCK + RU_ANCHOR, 1)
    if prompt != (row.system_prompt or ""):
        row.system_prompt = prompt
        row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "").replace(CARD_BLOCK, "", 1).replace(RU_BLOCK, "", 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0162_ai_prompt_order_and_no_florist_fee")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
