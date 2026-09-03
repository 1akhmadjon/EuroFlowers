# -*- coding: utf-8 -*-
from django.db import migrations


ALBUM_MARKER = "BIR JAVOBDA FAQAT BITTA ALBOM KETADI"

ALBUM_ANCHOR = """Xato, real suhbat 2276: butun katalog bitta suhbatda olti marta yuborildi.
Xato, real suhbat 1831: yetti marta yuborildi."""

ALBUM_BLOCK = """Xato, real suhbat 2276: butun katalog bitta suhbatda olti marta yuborildi.
Xato, real suhbat 1831: yetti marta yuborildi.

A2. BIR JAVOBDA FAQAT BITTA ALBOM KETADI

match_ai_catalog_by_media ba'zida albomni O'ZI yuboradi — reklamadagi yoki
mijoz tashlagan rasm katalogga ulanmagan bo'lsa. Uning javobida "albom" so'zi
va "ALLAQACHON yuborildi" degan gap turadi. O'sha gapni ko'rgan zahoti
send_catalog_album CHAQIRMAYSAN: albom mijozga ketib bo'lgan, senga faqat
matn yozish qoldi.

already_sent — javob boshlanishidagi holat. Shu navbatda tool yuborgan albom
unda KO'RINMAYDI. Ya'ni already_sent.whole_catalog = false bo'lib turishi
"katalog yuborilmagan" degani emas: shu javobda tool albom yuborgan bo'lsa,
katalog yuborilgan. Tool nima qilganini kontekstdan emas, tool javobidan
bilasan.

Ikkinchi albom mijozga aynan bir xil rasmlarni qayta yuboradi va suhbat
ustma-ust ikki karta rasm bilan to'ladi.

Xato, real suhbat 2581 va 2571: reklamadan kelgan mijozga
match_ai_catalog_by_media albomni yubordi, ustidan send_catalog_album yana
yubordi — mijoz bir xil rasmlarni ketma-ket ikki marta oldi. Uch kunda shu
xato o'n uch marta takrorlandi."""

QUESTION_MARKER = "Katalog bu suhbatda hali yuborilmagan bo'lsa ham albom YUBORILMAYDI"

QUESTION_ANCHOR = """  match_ai_catalog_by_media CHAQIRMAYSAN, hatto suhbatda rasm turgan bo'lsa ham.
  send_catalog_album va send_catalog_image CHAQIRMAYSAN.
  Javobni kontekstdagi do'kon ma'lumotidan olib, bir-ikki qatorda yozasan."""

QUESTION_BLOCK = """  match_ai_catalog_by_media CHAQIRMAYSAN, hatto suhbatda rasm turgan bo'lsa ham.
  send_catalog_album va send_catalog_image CHAQIRMAYSAN.
  Javobni kontekstdagi do'kon ma'lumotidan olib, bir-ikki qatorda yozasan.

  Katalog bu suhbatda hali yuborilmagan bo'lsa ham albom YUBORILMAYDI — savol
  gul haqida emas. Mijoz reklama orqali kelgan bo'lsa ham shu qoida ishlaydi:
  reklama katalogni majburiy qilmaydi, savol nima haqida ekani qiladi.
  Eski mijoz qaytib kelib "dastafka 7 ciga", "tumanlarga dastafka bormi",
  "bugungiga keregidi" desa — bu buyurtmaning davomi. Savolga javob berasan,
  katalogni boshidan ko'rsatmaysan.

Xato, real suhbat 2556: mijoz "Tumanlarga dastafka bormii" deb so'radi, javob
matni to'g'ri bo'ldi, lekin uning ustidan butun katalog albomi ham ketdi.
Xato, real suhbat 954: eski mijoz "Dastafka 7 ciga" dedi — sanani tasdiqlash
o'rniga butun katalog yuborildi."""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if ALBUM_MARKER not in prompt and prompt.count(ALBUM_ANCHOR) == 1:
        prompt = prompt.replace(ALBUM_ANCHOR, ALBUM_BLOCK, 1)
    if QUESTION_MARKER not in prompt and prompt.count(QUESTION_ANCHOR) == 1:
        prompt = prompt.replace(QUESTION_ANCHOR, QUESTION_BLOCK, 1)
    if prompt == (row.system_prompt or ""):
        return
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])
    assert ALBUM_MARKER in prompt, "promptga A2 qoidasi tushmadi"
    assert QUESTION_MARKER in prompt, "promptga B qoidasining davomi tushmadi"


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "")
    prompt = prompt.replace(ALBUM_BLOCK, ALBUM_ANCHOR, 1)
    prompt = prompt.replace(QUESTION_BLOCK, QUESTION_ANCHOR, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0167_ai_prompt_delivery_inside_the_price")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
