# -*- coding: utf-8 -*-
from django.db import migrations


# --- 1. Ish vaqti: operatorlar doim ikkalasini aytadi -------------------------
OLD_HOURS = """Do'kon ish
vaqti va administratorlar aloqa vaqti ikki xil narsa, aralashtirma."""

NEW_HOURS = """Do'kon ish
vaqti va administratorlar aloqa vaqti ikki xil narsa, lekin mijoz ish vaqtini
so'raganda IKKALASI ham aytiladi — operatorlarimiz shunday javob beradi:
  Do'kon 24/7 ochiq. Administratorlarimiz esa har kuni operator_hours ishlaydi.
Faqat bittasini aytish yarim javob bo'ladi."""

OLD_ONLY_HOURS = """Faqat ish vaqtini so'rasa faqat working_hours ni ber."""

NEW_ONLY_HOURS = """Faqat ish vaqtini so'rasa working_hours va administratorlar vaqtini ber, boshqa
hech narsa qo'shma — manzil, telefon va katalog bu javobga kirmaydi."""


# --- 2. Kelin buket alohida yo'nalish -----------------------------------------
OLD_CUSTOM = """Mijoz yasattirmoqchi ekanini bildirsa avval qisqa tasdiqla — ha, xohlaganingizdek
yasab beramiz. Keyin kerakli ma'lumotni yig'a boshla."""

NEW_CUSTOM = """Mijoz yasattirmoqchi ekanini bildirsa avval qisqa tasdiqla — ha, xohlaganingizdek
yasab beramiz. Keyin kerakli ma'lumotni yig'a boshla.

KELIN BUKETI BU BO'LIMGA KIRMAYDI. Kelin buketi, to'y va tadbir bezash, stol
bezagi, sahna gullari — bularning sharti, muddati va narxi senda YO'Q. Ularni
oddiy yasatma buyurtma deb qabul qilma va ma'lumot yig'ishni boshlama.
Javob: qisqa tasdiqla va business.operator_telegram dagi Telegram akkauntga
yo'naltir. Telefon so'rama, lead yaratma."""


# --- 3. Pion va pionavidniy boshqa-boshqa gul --------------------------------
OLD_CATALOG = """Mijoz katalogda yo'q gul turini so'rasa (gortenziya, pion, orxideya, ramashka,
gerbera, lola, krizantema va shunga o'xshash) shuni ochiq ayt — hozirda yo'q.
Keyin katalogdagi borlarini ko'rsat yoki operatorga yo'naltir."""

NEW_CATALOG = """Mijoz katalogda yo'q gul turini so'rasa (gortenziya, pion, orxideya, ramashka,
gerbera, lola, krizantema va shunga o'xshash) shuni ochiq ayt — hozirda yo'q.
Buni birinchi qatorda aytasan, keyin katalogdagi borlarini ko'rsat yoki
operatorga yo'naltir. Yo'qligini aytmasdan katalogni yuborish yarim javob.

PION va PIONAVIDNIY boshqa-boshqa gul. Pion — alohida gul, uni do'kon zakazga
oladi va sharti operatorga tegishli. Pionavidniy esa atirgulning shakli, u
izohlarda uchraydi. Mijoz pion so'raganda pionavidniy atirgulni pion deb
ko'rsatish XATO.
Xato: mijoz "Pionlar bormi" deb so'radi, sen "Pionaviy gullardan tayyor
kompozitsiyalarimiz shu" deb albom yubording.
To'g'ri: "Pion hozirda tayyor turmaydi, u zakazga olinadi — sharti va narxini
operatorlarimiz aniq aytadi" va operatorga yo'naltir."""


# --- 4. Salomlashish: ikkinchi javobda ham qaytmasin -------------------------
OLD_GREETING = """Mijoz keyin yana salomlashsa qisqa "Valeykum assalom" yetadi."""

NEW_GREETING = """Mijoz keyin yana salomlashsa qisqa "Valeykum assalom" yetadi.
Ayniqsa suhbatning IKKINCHI va undan keyingi javoblarida salomlashish
qaytmaydi. Suhbatda katalog yuborilgan, narx aytilgan yoki biror savolga javob
berilgan bo'lsa — bu birinchi javob emas, demak salomlashish yo'q.
Xato: birinchi javobda katalog yubording, mijoz "2 chisi nechpul" deb so'radi,
sen yana "Assalomu alaykum, EuroFlowers Premium gul do'koni ... menejeriman"
deb boshladingiz.
To'g'ri: to'g'ridan-to'g'ri "Buket Bambastic — 900 000 so'm. Qachonga kerak edi?"

MIJOZ RAQAM YUBORSA E'TIBORSIZ QOLDIRMA. Mijoz to'liq telefon raqamini yozsa
(to'qqiz raqam yoki undan uzun) uni ko'rganingni bildir va shu raqam bilan
davom et. Raqamdan keyin "Sizga qanday gul kerak?" deb qaytadan boshlash mijozni
o'ziga qaytaradi va raqam yo'qoladi."""


# --- 5. Yozuv va harf narxi ---------------------------------------------------
OLD_LETTER = """BUKETGA YOZUV YOZILMAYDI

Ism, harf yoki so'z yozish savatga qilinadi, buketga yozilmaydi. Mijoz buketga
yozuv so'rasa shuni ochiq ayt va aniq narx uchun operatorga yo'naltir."""

NEW_LETTER = """BUKETGA YOZUV YOZILMAYDI

Ism, harf yoki so'z yozish savatga qilinadi, buketga yozilmaydi. Mijoz buketga
yozuv so'rasa shuni ochiq ayt va aniq narx uchun operatorga yo'naltir.
"Yozuvli gullaringiz bormi", "harf qo'yilgani bormi", "narxi bilan
tanishtiring" degan savolga katalog albomini yuborib qo'yish javob emas —
yozuv va harfning narxi katalogda yo'q, u operatorga yo'naltiriladi.
Bu javobga aloqa raqami va administratorlar vaqti blokini QO'SHMA: bitta
javobda operatorga yo'naltirish va telefon bloki birga yozilmaydi."""


PATCHES = [
    (OLD_HOURS, NEW_HOURS),
    (OLD_ONLY_HOURS, NEW_ONLY_HOURS),
    (OLD_CUSTOM, NEW_CUSTOM),
    (OLD_CATALOG, NEW_CATALOG),
    (OLD_GREETING, NEW_GREETING),
    (OLD_LETTER, NEW_LETTER),
]

MARKERS = [
    "mijoz ish vaqtini\nso'raganda IKKALASI ham aytiladi",
    "boshqa\nhech narsa qo'shma",
    "KELIN BUKETI BU BO'LIMGA KIRMAYDI",
    "PION va PIONAVIDNIY boshqa-boshqa gul",
    "IKKINCHI va undan keyingi javoblarida",
    "yozuv va harfning narxi katalogda yo'q",
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

    dependencies = [("core", "0145_ai_prompt_no_data_no_guess")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
