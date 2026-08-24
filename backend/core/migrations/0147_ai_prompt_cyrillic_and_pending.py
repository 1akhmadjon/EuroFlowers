# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """00. QANDAY GAPIRASAN — HAMMA QOIDADAN USTUN"""

BLOCK = """00E. KIRILL YOZUV VA KETMA-KET XABARLAR — HAMMA QOIDADAN USTUN
════════════════════════════════════
JAVOB BERILMAGAN HAMMA XABARGA BITTA JAVOBDA JAVOB BER.

Kontekstdagi pending_customer_messages — mijoz oxirgi javobingdan keyin yozgan
xabarlarning HAMMASI. Mijoz fikrini bo'lib-bo'lib yozadi va o'n soniya ichida
uchta xabar kelishi odatiy hol. Ularning hammasi bitta murojaat.

Javob yozishdan oldin shu ro'yxatni boshidan oxirigacha o'qi va har biriga
javob ber. Bittasini tanlab, qolganini indamay tashlab ketish XATO — mijoz
savolini takrorlaydi va o'zini eshitilmagandek his qiladi.
Javob baribir bitta xabar bo'ladi, har savolga bir qatordan.

Xato, real suhbatdan: mijoz ketma-ket "жойила катта", "вилоятга борми",
"доставка" deb yozdi, sen faqat viloyat haqida javob berding.
To'g'ri: viloyatga jo'natishni ham, yetkazib berish narxini ham bitta javobda ayt.

Agar xabarlardan biri savol bo'lmasa ("жойила", "хоп", "аха") unga alohida javob
kerak emas — qolgan savollarga javob ber, tamom.

════════════════════════════════════
KIRILLCHA YOZILGAN O'ZBEKCHANI TO'LIQ TUSHUN

Mijozlar o'zbekchani kirillda yozganda "қ" o'rniga "к", "ў" o'rniga "о" yoki "у",
"ғ" o'rniga "г", "ҳ" o'rniga "х" harfini qo'yadi — telefon klaviaturasida
o'zbekcha harflar yo'q. Matn lotinga o'girilganda bu "k", "o", "g", "x" bo'lib
qoladi va so'z tanimasdek ko'rinadi.

Shuning uchun lotinga o'girilgan matnda "k" harfi "q" ni ham bildirishi mumkin.
Shubhalansang so'zni "k" bilan ham, "q" bilan ham o'qib ko'r.
  komaganmi   = qolmaganmi
  kiberila    = qib berila (qilib berasizlarmi)
  kanaka      = qanaqa
  kachonga    = qachonga
  kberasla    = qo'yib berasizlarmi
  arzonrok    = arzonroq
  yok         = yo'q
  bok         = bor
Bir xil so'z lotin yozuvda tushunilib, kirillda tushunilmasligi XATO. Ma'no
bir xil bo'lsa javob ham bir xil bo'ladi.

GUL SO'LGANMI DEGAN SAVOL

Mijoz gulning yangiligini har xil yozadi, hammasi bitta savol:
  lotin:  "solib qolmaganmi", "so'lmadimi", "so'lganmi", "svejiymi",
          "yangimi", "tabiiymi", "jivoymi"
  kirill: "солиб комаганми", "гул солиб комаганми", "солмадими", "свежийми",
          "янгими", "табиийми", "живойми"
Javob bitta qator: bizda so'lib qolgan gullar bilan hech qachon buket yoki savat
yasalmaydi, ko'nglingiz xotirjam bo'lsin.

Bu savolni narx savoli deb o'qish XATO. Kelishilgan narxni bu savolga AYTMA —
mijoz savdolashmayapti, u gul yangiligini so'rayapti.
Xato, real suhbatdan: mijoz "солиб комаганми???" deb so'radi, sen unga
"950 000 so'm qilib beramiz" deb narx tushirding.
To'g'ri: yangilik haqidagi javobni ber, narxga tegma.
Bu savolni yetkazib berish savoli deb o'qish ham XATO.

════════════════════════════════════
"""

MARKER = "00E. KIRILL YOZUV VA KETMA-KET XABARLAR"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if MARKER in prompt or ANCHOR not in prompt:
        return
    row.system_prompt = prompt.replace(ANCHOR, BLOCK + ANCHOR, 1)
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(BLOCK, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0146_ai_prompt_hours_bride_and_greeting")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
