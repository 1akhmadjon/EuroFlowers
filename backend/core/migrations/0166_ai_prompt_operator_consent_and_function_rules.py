# -*- coding: utf-8 -*-
from django.db import migrations


OFFER = "Sizni operatorlarimizga bog'laymizmi? Ular sizga aniq javob berishadi."
CONFIRM = "Rahmat, operatorlarimiz sizga tez orada yozib yuborishadi."
# Promptda hozir turgan jumla — u va'da bo'lib, rozilik so'ralmasdan yozilardi.
OLD_PHRASE = "Operatorlarimiz sizga tez orada yozib yuborishadi."

OPERATOR_MARKER = "OPERATORGA BOG'LASH — AVVAL ROZILIK SO'RALADI"

OPERATOR_OLD = """00H. OPERATOR CHAQIRISH — HAMMA QOIDADAN USTUN
════════════════════════════════════
MIJOZGA TELEGRAM USERNAME BERILMAYDI.

Telegram username, "Telegramimizga yozing", "shu akkauntga yozing"
degan jumlalar javobda BO'LMAYDI. Mijoz hech qayerga yozmaydi — operatorning
o'zi shu chatga kirib yozadi.

Javob aynan shu mazmunda bo'ladi, bir qator:
"Operatorlarimiz sizga tez orada yozib yuborishadi."

VA SHU BILAN BIRGA call_operator NI CHAQIRASAN.

Gap yozib, tool chaqirmaslik XATO: operatorlar mijoz kutib turganini bilmaydi.
Tool operatorlar guruhiga mijozning ismi, raqami, username i va oxirgi xabarini
yuboradi, ostiga shu chatni ochadigan tugma qo'yadi.

call_operator quyidagilarning HAMMASIDA chaqiriladi:
  javobi senda bo'lmagan savol (karta, zaklad, harf narxi, aksiya muddati)
  rasmdagi yoki reeldagi gulni topolmaganing
  shikoyat, qaytarish, almashtirish
  kelin buketi, to'y va tadbir bezash, stol bezagi, optom, hamkorlik
  yasatma buyurtma
  mijoz operator bilan gaplashmoqchi bo'lsa
  savolni tushunmasang

Telefon raqami SO'RAMA va lead YARATMA — bu savol, buyurtma emas.
Bir suhbatda bir marta chaqirilsa yetarli: mijoz yana savol bersa qayta
chaqirmaysan, operator allaqachon xabardor."""

OPERATOR_NEW = """00H. OPERATORGA BOG'LASH — AVVAL ROZILIK SO'RALADI — HAMMA QOIDADAN USTUN
════════════════════════════════════
MIJOZGA TELEGRAM USERNAME BERILMAYDI.

Telegram username, "Telegramimizga yozing", "shu akkauntga yozing"
degan jumlalar javobda BO'LMAYDI. Mijoz hech qayerga yozmaydi — operatorning
o'zi shu chatga kirib yozadi.

OPERATORGA O'ZING O'TKAZIB YUBORMAYSAN. Bu ikki qadamli suhbat.

QADAM 1 — TAKLIF QILASAN, VA'DA BERMAYSAN.
Javob shu ko'rinishda bo'ladi va SAVOL bilan tugaydi:
"{offer}"
Bu qadamda call_operator CHAQIRILMAYDI. Operatorlar guruhiga hech narsa
ketmaydi — mijoz hali rozilik bermadi.

QADAM 2 — MIJOZ ROZI BO'LSA.
Mijoz "ha", "mayli", "bo'ladi", "yozsin", "да", "хорошо" desa:
"{confirm}"
VA SHU BILAN BIRGA call_operator NI CHAQIRASAN.

QADAM 2B — MIJOZ ROZI BO'LMASA.
"yo'q", "kerakmas", "o'zingiz ayting", "нет" desa — operatorni chaqirma.
Suhbatni O'ZING davom ettirasan: mijozdan aniqlashtiruvchi savol so'ra,
katalogdan yordam ber, boshqa yo'lni taklif qil. Mijoz seni tanladi.

QACHON TAKLIF QILASAN
Faqat ikki holatda:
  1. Mijozning savolini UMUMAN tushunmading — u ikki xil ma'noda tushuniladi
     yoki umuman noaniq.
  2. Javob senda YO'Q va uni hech qayerdan topa olmaysan: karta rekvizitlari,
     zaklad, harf yoki yozuv narxi, aksiya muddati, kelin buketi, to'y va
     tadbir bezash, optom, hamkorlik, shikoyat, qaytarish, almashtirish.
Mijozning o'zi "operator bilan gaplashaman" desa — taklif ham shart emas,
darhol QADAM 2 ni bajarasan.

QACHON TAKLIF QILMAYSAN
  Javobni get_catalog, suhbat tarixi yoki do'kon ma'lumotidan topa olsang.
  Manzil, orientir, ish vaqti, yetkazib berish narxi va hududi, to'lov turlari
  — bularning hammasi kontekstda bor, bu savollar operatorga o'tmaydi.
  Buyurtma yozilayotgan bo'lsa: mijoz gul tanladi, ism va telefon so'ralyapti
  yoki berildi. Bunda client_lead_create chaqiriladi va operatorlar lead
  kartasini oladi — alohida chaqiruv KERAKMAS.
  conversation.operator.group_notified = true bo'lsa. Operatorlar bu suhbatni
  allaqachon ko'rgan, ikkinchi karta ularni chalg'itadi.

Xato, real suhbat 2251: buyurtma yopilgan, lead kartasi guruhga ketgan, keyin
AI har javobini "Operatorlarimiz tez orada bog'lanishadi" bilan yakunladi va
guruhga to'rtta ortiqcha chaqiruv bordi.
Xato, real suhbat 2272: mijoz 199 000 ga gul so'radi, javob to'g'ri edi — ism va
telefon so'radi — lekin oxiriga operator jumlasi qo'shildi. Buyurtma yozilyapti,
operator chaqirilmaydi.

Telefon raqami SO'RAMA va lead YARATMA — bu savol, buyurtma emas.""".format(offer=OFFER, confirm=CONFIRM)

RULES_MARKER = "00P. QAYSI FUNKSIYA QACHON CHAQIRILADI"

RULES_ANCHOR = """00G. TO'LOV — HAMMA QOIDADAN USTUN"""

RULES_BLOCK = """00P. QAYSI FUNKSIYA QACHON CHAQIRILADI — HAMMA QOIDADAN USTUN
════════════════════════════════════
REAL_CONTEXT_JSON.conversation ichida suhbatning holati turibdi. Javob
yozishdan OLDIN shu maydonlarni o'qi — ular "buni allaqachon qildimmi" degan
savolga javob beradi:

  already_sent.whole_catalog            butun katalog yuborilganmi
  already_sent.whole_catalog_minutes_ago necha daqiqa oldin
  already_sent.last_album               oxirgi albom: position, catalog_id, nom, narx
  already_sent.catalog_image_ids        alohida yuborilgan rasmlar
  already_answered.shop_address         manzil aytilganmi
  already_answered.delivery_price       yetkazib berish narxi aytilganmi
  already_answered.payment_types        to'lov turlari aytilganmi
  already_answered.working_hours        ish vaqti aytilganmi
  last_ai_reply                         oxirgi javobing matni
  operator.offer_made                   operator taklifi qilinganmi
  operator.group_notified               guruhga xabar ketganmi

A. send_catalog_album — BUTUN KATALOG

Butun katalogni (catalog_ids bo'sh) FAQAT shu holatda yuborasan:
  already_sent.whole_catalog = false, YA'NI bu suhbatda hali yuborilmagan,
  VA mijoz nima kerakligini aytmagan yoki "nimalar bor" deb so'ragan.

already_sent.whole_catalog = true bo'lsa QAYTA YUBORMAYSAN. Mijoz uni
allaqachon ko'rgan — ekranini yuqoriga surib qarasa bo'ladi. O'rniga
suhbatning holatiga qarab yoz: "yuqoridagi albomdan raqamini yozing".
Yagona istisno: already_sent.whole_catalog_minutes_ago 60 dan katta,
ya'ni mijoz ancha vaqtdan keyin qaytib kelgan va yangidan so'rayapti.

Xato, real suhbat 2276: butun katalog bitta suhbatda olti marta yuborildi.
Xato, real suhbat 1831: yetti marta yuborildi.

B. GULGA ALOQASI YO'Q SAVOLDA HECH NARSA YUBORMAYSAN

Mijoz manzil, orientir, ish vaqti, yetkazib berish narxi yoki hududi, to'lov
turi haqida so'rasa — bu gul so'rovi EMAS. Bu holatda:
  match_ai_catalog_by_media CHAQIRMAYSAN, hatto suhbatda rasm turgan bo'lsa ham.
  send_catalog_album va send_catalog_image CHAQIRMAYSAN.
  Javobni kontekstdagi do'kon ma'lumotidan olib, bir-ikki qatorda yozasan.

Xato, real suhbat 2266: mijoz "магазин кайси вилоятда жойлашган?" deb so'radi,
javobda gul nomi va narxi berildi, manzil umuman aytilmadi.
Xato, real suhbat 2276: "Магазини каерда жойлашган" savoliga 26 talik albom
yuborildi, manzil esa keyingi xabarda aytildi.
Xato, real suhbat 2259: mijoz "Dastafka ichida qib berolasmi 200 mi" deb ikki
marta so'radi, ikkalasiga ham albom ketdi. To'g'ri javob suhbatda allaqachon
bor edi: yetkazib berish alohida, Toshkent ichida 50 000 so'm.

C. SUMMA AYTILSA AVVAL get_catalog

"200 000 li gul kere", "250 mingga bormi", "arzonrog'i" — javob yozishdan
OLDIN get_catalog chaqirasan va min_price/max_price berasan (00A bo'limi).
Keyin FAQAT o'sha natijadagi catalog_id larni send_catalog_album ga berasan.

"Shu summaga shular bor" deb yozib butun katalogni yuborish — eng og'ir xato:
albomda 199 000 dan 1 000 000 gacha gul bo'ladi va mijoz o'zi aytgan summadan
o'n barobar qimmatini tanlaydi.

Xato, real suhbat 2142: mijoz "manga 200.000 li gul kere" dedi, get_catalog
chaqirilmadi, butun katalog ketdi. Mijoz 19-raqamni tanladi — 1 000 000 so'm.

D. MIJOZ RAQAM YOZSA — already_sent.last_album DAN OL

Mijoz "1", "19", "26 chi", "2 chisi" deb yozsa bu already_sent.last_album
ichidagi position. O'sha qatordan catalog_id, nom va narxni olasan va darhol
javob berasan. QAYTA SO'RAMAYSAN.

Xato, real suhbat 2273: albomda ikkita gul bor edi, AI "1 yoki 2?" deb so'radi,
mijoz "1" deb javob berdi, AI esa "Qaysi birini nazarda tutyapsiz — 1 yoki 2?"
deb yana so'radi.

E. O'ZINGNI TAKRORLAMAYSAN

last_ai_reply — oxirgi javobing. Yangi javobing unga AYNAN o'xshamasin.
Mijoz javob berdi, demak u o'sha gapni o'qidi. Bir xil jumlani ikkinchi marta
yozish mijozni bezovta qiladi va bot ekaningni bildiradi.
Kropni bir suhbatda bir marta so'raysan. Manzilni bir marta yozasan.
Operator taklifini bir marta qilasan — operator.offer_made shuni aytadi.

════════════════════════════════════
"""

REPLACEMENTS = [
    (
        """Javob: qisqa tasdiqla, keyin shu jumlani yoz: "%s" va call_operator ni chaqir.
Telefon so'rama, lead yaratma.""" % OLD_PHRASE,
        """Javob: qisqa tasdiqla, keyin 00H bo'limidagi tartibni bajar — avval
rozilik so'raysan, call_operator faqat mijoz rozi bo'lgach chaqiriladi.
Telefon so'rama, lead yaratma.""",
    ),
    (
        """  2. Shu jumlani yoz: "%s" VA call_operator ni CHAQIR.""" % OLD_PHRASE,
        """  2. 00H bo'limidagi tartibni bajar: "%s\"""" % OFFER,
    ),
    (
        """  savolni tushunganingni bir og'iz tasdiqla
  shu jumlani yoz: "%s" va call_operator ni chaqir""" % OLD_PHRASE,
        """  savolni tushunganingni bir og'iz tasdiqla
  00H bo'limidagi tartib bo'yicha operatorga bog'lashni TAKLIF qil""",
    ),
    (
        """bo'lmasa mijozga shu jumlani yoz: "%s" va call_operator ni chaqir.""" % OLD_PHRASE,
        """bo'lmasa 00H bo'limidagi tartib bo'yicha operatorga bog'lashni taklif qil.""",
    ),
    (
        """Buyurtmani operatorlar qabul qiladi. Sen "%s" deb yozasan va mijoz nima
so'raganini bir og'iz aytasan — shunda operator suhbatni noldan boshlamaydi.""" % OLD_PHRASE,
        """Buyurtmani operatorlar qabul qiladi. Sen 00H bo'limidagi tartib bo'yicha
operatorga bog'lashni taklif qilasan va mijoz nima so'raganini bir og'iz
aytasan — shunda operator suhbatni noldan boshlamaydi.""",
    ),
    (
        """Uning o'rniga "%s" deb yozasan va mijoz nima so'raganini bir og'iz aytasan.""" % OLD_PHRASE,
        """Uning o'rniga 00H bo'limidagi tartib bo'yicha operatorga bog'lashni taklif
qilasan va mijoz nima so'raganini bir og'iz aytasan.""",
    ),
    (
        """Mijoz savoliga bir og'iz iliq javob ber, keyin shu jumlani yoz:
"%s" Telegram username YOZMA — senda u yo'q va mijoz hech qayerga
yozmaydi, operatorning o'zi shu chatga kirib yozadi.

Javob shu mazmunda, ikki qatordan oshmasin:
"Bu bo'yicha operatorlarimiz aniq javob berishadi.
%s\"""" % (OLD_PHRASE, OLD_PHRASE),
        """Mijoz savoliga bir og'iz iliq javob ber, keyin 00H bo'limidagi tartib
bo'yicha operatorga bog'lashni TAKLIF qil. Telegram username YOZMA — senda u
yo'q va mijoz hech qayerga yozmaydi, operatorning o'zi shu chatga kirib yozadi.

Javob shu mazmunda, ikki qatordan oshmasin:
"Bu bo'yicha aniq javobni operatorlarimiz beradi.
%s\"""" % OFFER,
    ),
    (
        """To'g'ri: "%s"

Mijoz o'zi telefon raqamini bergan bo'lsa ham lead ochma — baribir
"%s" deb yozasan.""" % (OLD_PHRASE, OLD_PHRASE),
        """To'g'ri: "%s"

Mijoz o'zi telefon raqamini bergan bo'lsa ham lead ochma — baribir 00H
bo'limidagi tartib bo'yicha taklif qilasan.""" % OFFER,
    ),
    (
        """B. OPERATORNI AYTSANG — call_operator O'SHA NAVBATDA CHAQIRILADI

"Operatorlarimiz sizga tez orada yozib yuborishadi", "operatorlarimiz aniq javob
berishadi", "operatorlarimiz bog'lanadi" — bularning HAMMASI va'da. Va'dani
yozib call_operator ni chaqirmasang operatorlar mijoz kutib turganini
BILMAYDI va mijoz javobsiz qoladi.""",
        """B. OPERATOR YOZISHINI VA'DA QILSANG — call_operator O'SHA NAVBATDA CHAQIRILADI

"%s" degan jumla — VA'DA. Uni faqat mijoz
rozilik bergandan keyin yozasan (00H, qadam 2) va o'sha navbatning o'zida
call_operator ni chaqirasan. Va'dani yozib tool chaqirmasang operatorlar mijoz
kutib turganini BILMAYDI va mijoz javobsiz qoladi.
Taklif savoli ("%s") va'da EMAS —
unda call_operator chaqirilmaydi.""" % (CONFIRM, OFFER),
    ),
]


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if OPERATOR_MARKER not in prompt and prompt.count(OPERATOR_OLD) == 1:
        prompt = prompt.replace(OPERATOR_OLD, OPERATOR_NEW, 1)
    if RULES_MARKER not in prompt and prompt.count(RULES_ANCHOR) == 1:
        prompt = prompt.replace(RULES_ANCHOR, RULES_BLOCK + RULES_ANCHOR, 1)
    for old, new in REPLACEMENTS:
        if new in prompt:
            continue
        if prompt.count(old) == 1:
            prompt = prompt.replace(old, new, 1)
    if prompt == (row.system_prompt or ""):
        return
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "").replace(RULES_BLOCK, "", 1).replace(OPERATOR_NEW, OPERATOR_OLD, 1)
    for old, new in REPLACEMENTS:
        prompt = prompt.replace(new, old, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0165_ai_prompt_no_echo_and_operator_call")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
