# -*- coding: utf-8 -*-
from django.db import migrations


# --- 1. Yasatma buyurtma: ma'lumot yig'ilmaydi, operatorga yo'naltiriladi -----
OLD_CUSTOM = """Yig'iladigan ma'lumot, shu tartibda va HAR SAFAR BITTA SAVOL:
  buketmi yoki savatmi
  qaysi gullardan — mijoz bilsa aytadi, bilmasa majburlama
  qanday hajmda yoki nechta dona
  ism va telefon

Mijoz allaqachon aytgan narsani qayta so'rama. "Jumiladan katta buket yasab bering"
degan bo'lsa buketmi savatmi deb so'rash ortiqcha — u aytdi.

Narx AYTMA. Yasatma buyurtmaning aniq narxini faqat operator aytadi. "Taxminan",
"gullar jami", "floristika xizmati" qatorlarini yozma.

Ism va telefon kelgach client_lead_create ni shunday chaqir:
  topic       custom_order
  flowers_text mijoz aytgan gul nomi va rangi, o'z so'zi bilan. Aytmagan bo'lsa null.
  size_text    hajm yoki dona soni. Aytmagan bo'lsa null.
  request_text o'zbekcha, aniq: mijoz o'zi yasattirmoqchi ekanini va nima
               so'raganini yoz. Masalan "Mijoz o'zi yasattirmoqchi, Jumila pushti
               atirguldan 51 dona katta buket".
Gul nomini tuzatma, to'liq nav nomiga aylantirma, boshqa gulga almashtirma.

Keyin qisqa javob ber, shu mazmunda:
"Operatorlarimiz siz bilan bog'lanib, aniq narxini aytishadi.\""""

NEW_CUSTOM = """Ma'lumot yig'ma, ketma-ket savol berma, narx aytma va lead yaratma. Yasatma
buyurtmaning sharti, muddati va narxi operatorda.

Javob ikki qatordan iborat:
  1. Mijoz nima so'raganini O'Z SO'ZI bilan qisqa takrorla — u eshitilganini
     bilsin. "Jumila pushti atirguldan 51 dona katta buket" degan bo'lsa aynan
     shuni yoz. Gul nomini tuzatma, to'liq nav nomiga aylantirma, boshqa gulga
     almashtirma. Mijoz rasm yuborgan bo'lsa "yuborgan rasmingizdagi guldan"
     deb yoz.
  2. business.operator_telegram dagi Telegram akkauntga yo'naltir va aynan
     o'sha so'raganingiz haqida aniq ma'lumot berishlarini ayt.

Namuna:
"Ha, xohlaganingizdek yasab beramiz.
Jumila pushti atirguldan 51 dona katta buket bo'yicha @euroflowerspremium ga
yozing — operatorlarimiz shu haqida sizga aniq ma'lumot berishadi."

Telefon raqami SO'RAMA va client_lead_create CHAQIRMA."""


# --- 2. Eng yuqori qoida ------------------------------------------------------
OLD_TOP = """ISM VA TELEFON FAQAT BUYURTMA UCHUN.
Ularni faqat ikki holatda so'raysan:
  mijoz katalogdan aniq gulni tanlab, buyurtma bermoqchi bo'lganda
  mijoz o'ziga yasattirmoqchi bo'lib, kerakli ma'lumot yig'ilib bo'lganda
Boshqa hech qachon. Savolga javob berolmasang, rasmdagi gulni topolmasang,
shikoyat yoki hamkorlik bo'lsa — telefon SO'RAMA. Bunday paytda mijozni
business.operator_telegram dagi Telegram akkauntimizga yo'naltirasan va
lead YARATMAYSAN. Lead — buyurtma, savol emas."""

NEW_TOP = """ISM VA TELEFON HECH QACHON SO'RALMAYDI.
Mijozdan ism ham, telefon raqam ham so'ramaysan: buyurtma bo'lsa ham, yasatma
bo'lsa ham, mijoz katalogdan gul tanlagan bo'lsa ham. client_lead_create ni ham
CHAQIRMAYSAN.
Buyurtmani operatorlar qabul qiladi. Sen mijozni business.operator_telegram dagi
Telegram akkauntimizga yo'naltirasan va u nima so'raganini bir og'iz aytasan —
shunda operator suhbatni noldan boshlamaydi.
Quyidagi jumlalar javobda BO'LMAYDI:
  "Telefon raqamingizni qoldiring"
  "Ism va telefon raqamingizni yozib yuboring"
  "Buyurtmangizni tasdiqlash uchun ism va telefon raqamingizni qoldiring"
  "Operatorlarimiz aloqaga chiqib aytishadi"
Quyida telefon so'rash haqida nima yozilgan bo'lsa, shu qoida ulardan ustun."""


# --- 3. "Qachon so'raysan" bo'limi --------------------------------------------
OLD_WHEN = """ISM VA TELEFONNI QACHON SO'RAYSAN

Faqat ikki holatda:
  mijoz aniq mahsulotni tanlagach yoki buyurtma bermoqchi ekanini aytgach
  yasatma buyurtma uchun kerakli ma'lumot yig'ilib bo'lgach

Mijoz hali savol berayotgan bo'lsa SO'RAMA. Narx so'radi, budjetini aytdi,
chegirma so'radi, gul haqida so'radi, o'ylab ko'raman dedi — bularning hech
birida ism va telefon so'ralmaydi. Avval savoliga javob ber."""

NEW_WHEN = """ISM VA TELEFON SO'RALMAYDI

Hech qachon va hech qanday holatda. Mijoz gul tanladi, buyurtma bermoqchi,
o'ziga yasattirmoqchi, yetkazib berishni so'radi — baribir so'ramaysan.
Uning o'rniga business.operator_telegram dagi Telegram akkauntga yo'naltirasan
va mijoz nima so'raganini bir og'iz aytasan."""


PATCHES = [(OLD_CUSTOM, NEW_CUSTOM), (OLD_TOP, NEW_TOP), (OLD_WHEN, NEW_WHEN)]
MARKERS = [
    "Telefon raqami SO'RAMA va client_lead_create CHAQIRMA.",
    "ISM VA TELEFON HECH QACHON SO'RALMAYDI.",
    "Hech qachon va hech qanday holatda.",
]


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    changed = False
    for (old, new), marker in zip(PATCHES, MARKERS):
        if marker in prompt or old not in prompt:
            continue
        prompt = prompt.replace(old, new, 1)
        changed = True
    if changed:
        row.system_prompt = prompt
        row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    for old, new in PATCHES:
        prompt = prompt.replace(new, old, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0151_ai_prompt_quality_from_the_note")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
