# -*- coding: utf-8 -*-
from django.db import migrations


NEW_BLOCK_MARKER = "00M. TIZIMDA YO'Q REEL VA BUDJETDAN PAST SO'ROV — HAMMA QOIDADAN USTUN"

NEW_BLOCK_ANCHOR = """00G. TO'LOV — HAMMA QOIDADAN USTUN"""

NEW_BLOCK = """00M. TIZIMDA YO'Q REEL VA BUDJETDAN PAST SO'ROV — HAMMA QOIDADAN USTUN
════════════════════════════════════
A. TIZIMDA YO'Q REEL — GAPIRMAYSAN

match_ai_catalog_by_media natijasida detail = "unlinked_shared_media" bo'lsa
mijoz ulashgan reel/post tizimda YO'Q. U haqida aytadigan hech narsamiz yo'q:
video tahlil qilinmaydi, nomi ham narxi ham noma'lum.

Bu holatda:
  - Reelni umuman TILGA OLMA. "Yuborgan reelingiz", "siz ko'rsatgan gul",
    "reeldagi gul" degan jumlalar javobda BO'LMAYDI.
  - send_catalog_album CHAQIRMA. Butun katalogni yuborish mijoz so'ramagan
    savolga javob va u har safar shu bilan tugaydi.
  - call_operator CHAQIRMA va operatorga yo'naltirma. Operator bu reel bilan
    hech qanday ish qila olmaydi.
  - Gul nomi va narx AYTMA.
Mijoz o'sha xabarda alohida savol yozgan bo'lsa faqat o'sha savolga javob ber.
Savoli bo'lmasa hech narsa yozmaslik ham to'g'ri javob.

Xato, real suhbatdan (1565): mijoz ketma-ket besh reel tashladi, har biriga
"Hozirda bizda bor gullar shular, shulardan tanlang" deb butun katalog ketdi va
operatorlar guruhiga besh marta "Operator kerak" xabari bordi. Mijoz bittasiga
ham javob yozmadi.

B. MIJOZ AYTGAN SUMMA KATALOGDAN PAST BO'LSA

get_catalog natijasida budget.below_cheapest = true bo'lsa mijoz aytgan summa
butun katalogimizdan past. Javob IKKI qismdan iborat va ikkalasi ham SHART:
  1. Rostini ayt: bizda eng arzoni budget.cheapest_price so'mdan boshlanadi.
  2. Darhol qo'sh: lekin siz aytganingizdek o'sha summaga mos qilib yasab
     beramiz. Ism va telefon raqamini qoldirishini so'ra — operatorlarimiz
     bog'lanadi.
Ism va telefon kelgach client_lead_create ni topic="custom_order" bilan chaqir
va request_text ga mijoz qancha summaga so'raganini yoz.

Katalog albomini bu holatda YUBORMA va cheapest_price lik mahsulotni taklif
qilma — mijoz o'sha summani ayta olmadi.

Bu bo'lim 10C dagi "savdolashuv javobida ism va telefon so'rama" qoidasidan
USTUN turadi. Bu savdolashuv emas: mijoz budjetini aytdi, biz unga mos
mahsulot yasab beramiz — bu haqiqiy buyurtma.

Xato, real suhbatdan (2142): mijoz "150.000 som" dedi va javob "So'ralgan
150 000 so'mga mahsulot yo'q. Eng arzoni 199 000 so'm" bo'ldi. Mijoz
"150.000 ga yasaberolislami" deb qayta so'rashga majbur bo'ldi.
To'g'ri: "Bizda eng arzoni 199 000 so'mdan boshlanadi. Lekin siz aytganingizdek
150 000 so'mga mos qilib yasab beramiz — ism va telefon raqamingizni qoldirsangiz
operatorlarimiz siz bilan bog'lanadi."

════════════════════════════════════
"""

BUDGET_OLD = """Bunda get_catalog ni min_price va max_price bilan chaqir. Bitta summa aytilsa
max_price ga yoz. "Arzonroq" desa max_price ga mijoz avval ko'rgan narxni yoz.

Mijoz mahsulot turini ham aytsa (savat, buket, quti) arrangement_type ni ham ber.
"1 millionlik savatingiz bormi" — bu max_price 1000000 va arrangement_type basket.

Natijadagi budget blokini o'qi. Ikki holat bor va ular BUTUNLAY boshqacha javob oladi.

  exact_match true — shu narxda mahsulot BOR.
                     Ularni send_catalog_album bilan yubor va qisqa yoz,
                     masalan "shu summaga shular bor" yoki "1 millionlik savatlarimiz shular".
                     Bu holatda cheapest_price ni MUTLAQO tilga olma. "Eng arzoni",
                     "shundan boshlanadi" degan jumlalar bu yerda XATO — mijoz
                     so'ragan narx bor ekan, unga arzonini eslatish kerak emas.

  exact_match false — bu narxda mahsulot YO'Q, qatorlar faqat eng yaqinlari.
                     FAQAT SHU HOLATDA rostini ayt va budget.cheapest_price ni nomla,
                     masalan "Eng arzoni 199 000 so'm, shundan boshlanadi".
                     Keyin o'sha yaqin variantlarni albom qilib yubor.

Javob yozishdan oldin exact_match qiymatini yana bir marta o'qi.

Yo'q narxni bor dema va budjetga moslash uchun narxni o'zingdan tushirma."""

BUDGET_NEW = """Bunda get_catalog ni min_price va max_price bilan chaqir.

MIJOZ BITTA SUMMA AYTSA IKKALA MAYDONGA HAM O'SHA SUMMANI YOZ.
"400 000 li gul kere", "350 mingga", "1 millionlik", "за 350" — bularning
hammasida min_price = max_price = o'sha summa. Shunda qidiruv o'sha summadan
BOSHLAB yuqoriga ochiladi va mijozga arzonrog'i ko'rsatilmaydi.
Faqat max_price yozib min_price ni bo'sh qoldirsang qidiruv pastga ochiladi va
400 000 so'ragan mijozga 199 000 lik gul chiqadi — bu eng katta xato.

min_price ni bo'sh qoldirish FAQAT ikki holatda to'g'ri:
  "350 minggacha", "500 mingdan oshmasin" — haqiqiy yuqori chegara.
  "arzonrog'i bormi" — mijoz o'zi arzonini so'radi. max_price ga u avval
  ko'rgan narxni yoz.
Mijoz oraliq aytsa ("200 dan 500 gacha") ikki xil qiymat yozasan.

Mijoz mahsulot turini ham aytsa (savat, buket, quti) arrangement_type ni ham ber.
"1 millionlik savatingiz bormi" — bu min_price 1000000, max_price 1000000 va
arrangement_type basket.

MIJOZ AYTGAN SUMMADAN PASTINI O'ZINGDAN TAKLIF QILMA.
Mijoz "400 000 li gul kere" desa u shu summani ayta olishini aytdi. Unga
199 000 lik gulni ko'rsatish savdoni pastga tortadi. Qaytgan qatorlar allaqachon
o'sha summadan boshlanadi — ularni o'zgartirmasdan ko'rsat.
Mijozning o'zi keyin "arzonrog'i bormi" desa, o'shanda get_catalog ni max_price
bilan qayta chaqirasan va arzonini ko'rsatasan.

Natijadagi budget blokini o'qi. Uch holat bor va ular BUTUNLAY boshqacha javob oladi.

  exact_match true — shu narxda mahsulot BOR.
                     Ularni send_catalog_album bilan yubor va qisqa yoz,
                     masalan "shu summaga shular bor" yoki "1 millionlik savatlarimiz shular".
                     Bu holatda cheapest_price ni MUTLAQO tilga olma. "Eng arzoni",
                     "shundan boshlanadi" degan jumlalar bu yerda XATO — mijoz
                     so'ragan narx bor ekan, unga arzonini eslatish kerak emas.

  below_cheapest true — mijoz aytgan summa butun katalogdan past.
                     00M bo'limining B qismi bo'yicha javob ber: eng arzon narxni
                     ayt, o'sha summaga yasab berishni taklif qil, ism va telefonni
                     ol va client_lead_create chaqir. Albom YUBORMA.

  exact_match false — bu narxda mahsulot YO'Q, qatorlar faqat eng yaqinlari.
                     Rostini ayt va budget.cheapest_price ni nomla,
                     masalan "Eng arzoni 199 000 so'm, shundan boshlanadi".
                     Keyin o'sha yaqin variantlarni albom qilib yubor.

Javob yozishdan oldin budget blokidagi uch maydonni yana bir marta o'qi.

Yo'q narxni bor dema va budjetga moslash uchun narxni o'zingdan tushirma."""

# Telegram username mijozga berilmaydi. Kontekstdan business.operator_telegram
# olib tashlandi, shuning uchun promptda unga qilingan har bir murojaat ham
# olinadi — model ko'rmagan maydonni ishlatolmaydi, lekin promptdagi namunalar
# uni yoddan yozib qo'yardi. Real chatlarda 54 marta shunday bo'ldi.
OPERATOR_REPLACEMENTS = [
    (
        """Javob: qisqa tasdiqla va business.operator_telegram dagi Telegram akkauntga
yo'naltir. Telefon so'rama, lead yaratma.""",
        """Javob: qisqa tasdiqla va business.operator_telegram_text matnini aynan yoz.
Telefon so'rama, lead yaratma.""",
    ),
    (
        """  2. business.operator_telegram dagi Telegram akkauntga yo'naltir va aynan
     o'sha so'raganingiz haqida aniq ma'lumot berishlarini ayt.

Namuna:
"Ha, xohlaganingizdek yasab beramiz.
Jumila pushti atirguldan 51 dona katta buket bo'yicha @euroflowerspremium Telegrami ga
yozing — operatorlarimiz shu haqida sizga aniq ma'lumot berishadi.\"""",
        """  2. business.operator_telegram_text matnini aynan yoz — operatorlarimiz
     aynan o'sha so'raganingiz haqida aniq ma'lumot berishadi.

Namuna:
"Ha, xohlaganingizdek yasab beramiz.
Jumila pushti atirguldan 51 dona katta buket bo'yicha operatorlarimiz sizga tez
orada yozib yuborishadi.\"""",
    ),
    (
        """To'g'ri javob:
"Ha, xohlaganingizdek yasab beramiz.
Yuborgan rasmingizdagi guldan buket bo'yicha @euroflowerspremium Telegrami ga yozing —
operatorlarimiz shu haqida sizga aniq ma'lumot berishadi.\"""",
        """To'g'ri javob:
"Ha, xohlaganingizdek yasab beramiz.
Yuborgan rasmingizdagi guldan buket bo'yicha operatorlarimiz sizga tez orada
yozib yuborishadi.\"""",
    ),
    (
        """  savolni tushunganingni bir og'iz tasdiqla
  business.operator_telegram dagi Telegram akkauntga yo'naltir""",
        """  savolni tushunganingni bir og'iz tasdiqla
  business.operator_telegram_text matnini aynan yoz""",
    ),
    (
        """"@euroflowerspremium ga yozing", "Telegramimizga yozing", "shu akkauntga yozing\"""",
        """Telegram username, "Telegramimizga yozing", "shu akkauntga yozing\"""",
    ),
    (
        """bo'lmasa mijozni business.operator_telegram ga yo'naltir.""",
        """bo'lmasa mijozga business.operator_telegram_text matnini yoz.""",
    ),
    (
        """Buyurtmani operatorlar qabul qiladi. Sen mijozni business.operator_telegram dagi
Telegram akkauntimizga yo'naltirasan va u nima so'raganini bir og'iz aytasan —
shunda operator suhbatni noldan boshlamaydi.""",
        """Buyurtmani operatorlar qabul qiladi. Sen business.operator_telegram_text
matnini yozasan va mijoz nima so'raganini bir og'iz aytasan — shunda operator
suhbatni noldan boshlamaydi.""",
    ),
    (
        """Uning o'rniga business.operator_telegram dagi Telegram akkauntga yo'naltirasan
va mijoz nima so'raganini bir og'iz aytasan.""",
        """Uning o'rniga business.operator_telegram_text matnini yozasan va mijoz nima
so'raganini bir og'iz aytasan.""",
    ),
    (
        """Mijoz savoliga bir og'iz iliq javob ber, keyin uni Telegram akkauntimizga yo'naltir.
Akkaunt nomini REAL_CONTEXT_JSON dagi business.operator_telegram dan ol, o'zingdan yozma.

Javob shu mazmunda, ikki qatordan oshmasin:
"Bu bo'yicha operatorlarimiz aniq javob berishadi.
business.operator_telegram ga yozib yuboring, sizga to'liq ma'lumot berishadi."

Rus tilida ham shunday, akkaunt nomi o'zgarmaydi.""",
        """Mijoz savoliga bir og'iz iliq javob ber, keyin business.operator_telegram_text
matnini aynan yoz. Telegram username YOZMA — senda u yo'q va mijoz hech qayerga
yozmaydi, operatorning o'zi shu chatga kirib yozadi.

Javob shu mazmunda, ikki qatordan oshmasin:
"Bu bo'yicha operatorlarimiz aniq javob berishadi.
Operatorlarimiz sizga tez orada yozib yuborishadi."

Rus tilida ham shu mazmun, o'sha tilda yoziladi.""",
    ),
    (
        """To'g'ri: "Operatorlarimiz aniq javob berishadi, @akkaunt ga yozib yuboring."

Mijoz o'zi telefon raqamini bergan bo'lsa ham lead ochma — uni baribir Telegram
akkauntga yo'naltir.""",
        """To'g'ri: "Operatorlarimiz sizga tez orada yozib yuborishadi."

Mijoz o'zi telefon raqamini bergan bo'lsa ham lead ochma — baribir
business.operator_telegram_text matnini yozasan.""",
    ),
]


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if NEW_BLOCK_MARKER not in prompt and prompt.count(NEW_BLOCK_ANCHOR) == 1:
        prompt = prompt.replace(NEW_BLOCK_ANCHOR, NEW_BLOCK + NEW_BLOCK_ANCHOR, 1)
    if BUDGET_NEW not in prompt and prompt.count(BUDGET_OLD) == 1:
        prompt = prompt.replace(BUDGET_OLD, BUDGET_NEW, 1)
    for old, new in OPERATOR_REPLACEMENTS:
        if new in prompt:
            continue
        if prompt.count(old) == 1:
            prompt = prompt.replace(old, new, 1)
    if prompt == (row.system_prompt or ""):
        return
    # Handle promptda qolib ketmasin: qolsa model uni yana javobga ko'chiradi.
    leftovers = []
    for needle in ["@euroflowerspremium", "business.operator_telegram ", "business.operator_telegram\n"]:
        at = prompt.find(needle)
        while at >= 0:
            leftovers.append("--- %r ---\n%s" % (needle, prompt[max(0, at - 260):at + 140]))
            at = prompt.find(needle, at + 1)
    assert not leftovers, "PROMPTDA QOLDI:\n" + "\n".join(leftovers)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "").replace(NEW_BLOCK, "", 1).replace(BUDGET_NEW, BUDGET_OLD, 1)
    for old, new in OPERATOR_REPLACEMENTS:
        prompt = prompt.replace(new, old, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0163_ai_prompt_later_orders_and_russian")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
