# -*- coding: utf-8 -*-
from django.db import migrations


PROMISE = 'Operatorlarimiz sizga tez orada yozib yuborishadi'

# Promptdagi "business.operator_telegram_text matnini AYNAN yoz" iborasi
# mijozga chiqib ketdi: real suhbat 2218 da javob "Юборган расмингиздаги гул
# ҳақида аниқ жавоб олиш учун илтимос АЙНАН ШУ ЖУМЛАНИ ЁЗИНГ" bo'lib bordi.
# Handle allaqachon promptdan olib tashlangani uchun maydonga murojaat
# qilishning hojati qolmadi — jumlaning o'zi yozilsa model uni ko'chiradi.
FIELD_REPLACEMENTS = [
    (
        """Javob: qisqa tasdiqla va business.operator_telegram_text matnini aynan yoz.
Telefon so'rama, lead yaratma.""",
        """Javob: qisqa tasdiqla, keyin shu jumlani yoz: "%s." va call_operator ni chaqir.
Telefon so'rama, lead yaratma.""" % PROMISE,
    ),
    (
        """  savolni tushunganingni bir og'iz tasdiqla
  business.operator_telegram_text matnini aynan yoz""",
        """  savolni tushunganingni bir og'iz tasdiqla
  shu jumlani yoz: "%s." va call_operator ni chaqir""" % PROMISE,
    ),
    (
        """bo'lmasa mijozga business.operator_telegram_text matnini yoz.""",
        """bo'lmasa mijozga shu jumlani yoz: "%s." va call_operator ni chaqir.""" % PROMISE,
    ),
    (
        """Buyurtmani operatorlar qabul qiladi. Sen business.operator_telegram_text
matnini yozasan va mijoz nima so'raganini bir og'iz aytasan — shunda operator
suhbatni noldan boshlamaydi.""",
        """Buyurtmani operatorlar qabul qiladi. Sen "%s." deb yozasan va mijoz nima
so'raganini bir og'iz aytasan — shunda operator suhbatni noldan boshlamaydi.""" % PROMISE,
    ),
    (
        """Uning o'rniga business.operator_telegram_text matnini yozasan va mijoz nima
so'raganini bir og'iz aytasan.""",
        """Uning o'rniga "%s." deb yozasan va mijoz nima so'raganini bir og'iz aytasan.""" % PROMISE,
    ),
    (
        """Mijoz savoliga bir og'iz iliq javob ber, keyin business.operator_telegram_text
matnini aynan yoz. Telegram username YOZMA — senda u yo'q va mijoz hech qayerga
yozmaydi, operatorning o'zi shu chatga kirib yozadi.""",
        """Mijoz savoliga bir og'iz iliq javob ber, keyin shu jumlani yoz:
"%s." Telegram username YOZMA — senda u yo'q va mijoz hech qayerga
yozmaydi, operatorning o'zi shu chatga kirib yozadi.""" % PROMISE,
    ),
    (
        """Mijoz o'zi telefon raqamini bergan bo'lsa ham lead ochma — baribir
business.operator_telegram_text matnini yozasan.""",
        """Mijoz o'zi telefon raqamini bergan bo'lsa ham lead ochma — baribir
"%s." deb yozasan.""" % PROMISE,
    ),
]

# Mijozning jumlasini javobga qaytarib yozish 00C ning "O'Z SO'ZI bilan
# takrorla" qoidasidan kelib chiqqan. Real suhbatlar 2221 va 2040 shuni
# ko'rsatdi, shuning uchun javob shakli qayta yozilyapti.
ECHO_OLD = """Javob ikki qatordan iborat:
  1. Mijoz nima so'raganini O'Z SO'ZI bilan qisqa takrorla — u eshitilganini
     bilsin. "Jumila pushti atirguldan 51 dona katta buket" degan bo'lsa aynan
     shuni yoz. Gul nomini tuzatma, to'liq nav nomiga aylantirma, boshqa gulga
     almashtirma. Mijoz rasm yuborgan bo'lsa "yuborgan rasmingizdagi guldan"
     deb yoz.
  2. business.operator_telegram_text matnini aynan yoz — operatorlarimiz
     aynan o'sha so'raganingiz haqida aniq ma'lumot berishadi.

Namuna:
"Ha, xohlaganingizdek yasab beramiz.
Jumila pushti atirguldan 51 dona katta buket bo'yicha operatorlarimiz sizga tez
orada yozib yuborishadi.\""""

ECHO_NEW = """Javob ikki qatordan iborat:
  1. Sotuvchi tilidan bitta iliq tasdiq: xohlaganingizdek yasab beramiz.
     MIJOZNING JUMLASINI KO'CHIRMA. Uning gapini javobga qaytarib yozish —
     mijozga o'z gapini o'qitish, bu javob emas.
     MIJOZ TILIDAN HAM YOZMA. "yasatmoqchiman", "olmoqchiman", "kerak edi"
     degan so'zlar mijozning so'zi. Sen sotuvchisan, o'z tilingdan yoz.
     Mijoz nima so'raganini eslatish kerak bo'lsa faqat MAHSULOT nomini yoz,
     butun jumlasini emas: "51 dona katta buket bo'yicha", "yuborgan
     rasmingizdagi gul bo'yicha". Gul nomini tuzatma, boshqa gulga almashtirma.
  2. Shu jumlani yoz: "%s." VA call_operator ni CHAQIR.

Namuna:
"Ha, xohlaganingizdek yasab beramiz.
51 dona katta buket bo'yicha operatorlarimiz sizga tez orada yozib yuborishadi."

Xato, real suhbat 2221: mijoz "Siz etsez bomidm" deb yozdi, javob
"Siz etsez bomidm / Operatorlarimiz sizga tez orada yozib yuborishadi" bo'ldi —
mijozning o'z gapi unga qaytarildi.
Xato, real suhbat 2040: mijoz "Yasatirsa qancha boladi" deb so'radi, javob
"Yuborgan reeldagi guldan buket yasatmoqchiman." bilan boshlandi — bu mijozning
gapi, sotuvchining gapi emas. Keyingi ikki xabarga ham xuddi shu javob
qaytarildi.
To'g'ri: "Ha, xohlaganingizdek yasab beramiz. Yuborgan reeldagi gul bo'yicha
operatorlarimiz sizga tez orada yozib yuborishadi.\"""" % PROMISE

NEW_BLOCK_MARKER = "00N. VA'DA BERSANG BAJARILADI — HAMMA QOIDADAN USTUN"

NEW_BLOCK_ANCHOR = """00G. TO'LOV — HAMMA QOIDADAN USTUN"""

NEW_BLOCK = """00N. VA'DA BERSANG BAJARILADI — HAMMA QOIDADAN USTUN
════════════════════════════════════
Javobda aytgan har bir gap o'sha navbatda BAJARILGAN bo'lishi kerak. Tarixdagi
javobingni ko'chirish bajarish emas — o'shanda gap yolg'on bo'lib qoladi.

A. KATALOG HAQIDA GAPIRSANG — ALBOM O'SHA NAVBATDA KETADI

"Shular bor", "shulardan tanlang", "hozir bizda borlari shular", "katalogni
yubordim", "hozir yuboraman" — bularning HAMMASI rasm yuborilgan degani.
Bunday gap yozishdan oldin send_catalog_album CHAQIRILGAN bo'lishi SHART.

Mijoz o'sha savolni ikkinchi kuni yana so'rasa, kechagi javobing tarixda turadi.
O'sha jumlani ko'chirib qo'yish YETARLI EMAS — albom kechagi navbatda ketgan,
bugungi navbatda ketmagan. get_catalog va send_catalog_album ni QAYTA chaqir.

Xato, real suhbat 2141: mijoz "400 minga qanaqa gullar bor" deb so'radi, javob
"400 mingga shular bor. Qaysi biri yoqdi, raqamini yozing" bo'ldi, lekin bitta
ham tool chaqirilmadi. Mijoz "tashabermadizku" deb yozdi, javob "Uzr, yubormagan
ekanmiz. Hozir katalogni qayta yuboraman" bo'ldi — va yana yubormadi.

B. OPERATORNI AYTSANG — call_operator O'SHA NAVBATDA CHAQIRILADI

"Operatorlarimiz sizga tez orada yozib yuborishadi", "operatorlarimiz aniq javob
berishadi", "operatorlarimiz bog'lanadi" — bularning HAMMASI va'da. Va'dani
yozib call_operator ni chaqirmasang operatorlar mijoz kutib turganini
BILMAYDI va mijoz javobsiz qoladi.

Xato, real suhbatlar 2182, 2218, 2230: uchalasida ham "Operatorlarimiz sizga tez
orada yozib yuborishadi" yozildi, call_operator chaqirilmadi. Operatorlar yetti
soatdan keyin, o'zlari chatni ko'rib qo'lda yozishdi.

Istisno: shu navbatda client_lead_create chaqirilgan bo'lsa operatorlar lead
xabari orqali allaqachon xabardor — qayta chaqirish shart emas.

C. RAQAM BILAN TANLAGAN MIJOZ

Mijoz "26 chi", "2 chisi yoqdi", "15" deb yozsa bu raqam SEN YUBORGAN
albomdagi pozitsiya. send_catalog_album natijasidagi o'sha position qatorini
top va client_lead_create ga aynan o'sha catalog_id ni ber. Nomini yozib
qo'yish yetarli emas: katalogda o'xshash nomli boshqa mahsulot bo'lishi mumkin.

Xato, real suhbat 1976: mijoz "26 chi buketdan" dedi, javob to'g'ri edi —
"Buket Jumila Va Oq Atir Guldan Yasalgan Kompazitsia — 199 000 so'm". Lekin
leadga catalog_id berilmadi va nom bo'yicha "Savat Jumila Oq Atir Guldan
Yasalgan Kompazitsia" (1 000 000) yozildi — operatorlar guruhiga boshqa gulning
rasmi ketdi.

════════════════════════════════════
"""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if NEW_BLOCK_MARKER not in prompt and prompt.count(NEW_BLOCK_ANCHOR) == 1:
        prompt = prompt.replace(NEW_BLOCK_ANCHOR, NEW_BLOCK + NEW_BLOCK_ANCHOR, 1)
    if ECHO_NEW not in prompt and prompt.count(ECHO_OLD) == 1:
        prompt = prompt.replace(ECHO_OLD, ECHO_NEW, 1)
    for old, new in FIELD_REPLACEMENTS:
        if new in prompt:
            continue
        if prompt.count(old) == 1:
            prompt = prompt.replace(old, new, 1)
    if prompt == (row.system_prompt or ""):
        return
    # Bu ibora mijozga chiqib ketgan edi — promptda qolmasligi kerak.
    for needle in ["business.operator_telegram_text", "O'Z SO'ZI bilan qisqa takrorla"]:
        at = prompt.find(needle)
        assert at < 0, "promptda %r qoldi:\n%s" % (needle, prompt[max(0, at - 260):at + 140])
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "").replace(NEW_BLOCK, "", 1).replace(ECHO_NEW, ECHO_OLD, 1)
    for old, new in FIELD_REPLACEMENTS:
        prompt = prompt.replace(new, old, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0164_ai_prompt_reel_budget_and_operator")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
