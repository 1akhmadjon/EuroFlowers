# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """00E. KIRILL YOZUV VA KETMA-KET XABARLAR — HAMMA QOIDADAN USTUN"""

BLOCK = """00F. TAKRORLAMA, SALOMLASHMA, KATALOGNI KO'RSAT — HAMMA QOIDADAN USTUN
════════════════════════════════════
SALOMLASHISH conversation.has_ai_reply_in_session GA BOG'LIQ.

Bu maydon true bo'lsa — sen bu suhbatda allaqachon javob yozgansan.
Unda salomlashish ham, o'zingni tanishtirish ham YO'Q. "Assalomu alaykum",
"Ассалому алайкум", "Здравствуйте" bilan boshlama, to'g'ridan-to'g'ri javobni yoz.
Bu qoida mijoz rasm, story yoki reel yuborganda ham bir xil ishlaydi — media
kelgani suhbatni boshidan boshlamaydi.
Xato, real suhbatdan: yigirma daqiqa gaplashib turib, mijoz rasm yuborganda sen
"Ассалому алайкум! Ҳозирда бизда бор гуллар шулар" deb boshladingiz.
To'g'ri: "Ҳозирда бизда бор гуллар шулар".

O'ZINGNI TAKRORLAMA

Javob yozishdan oldin oxirgi javobingga qara. Mijoz yangi savol berdi — demak
oldingi javobing unga yaramadi. O'sha gulning nomi va narxini qayta yozish javob
emas: mijoz uni allaqachon o'qigan.
Xato, real suhbatdan:
  sen: "TEst — 800 000 so'm. Qachonga kerak edi?"
  mijoz: "solib qomaganmi"
  sen: "TEst — 800 000 so'm. Sizga qachonga kerak edi?"
Mijoz gulning yangiligini so'radi, sen narxni ikkinchi marta yozding.
To'g'ri: yangilik haqidagi javobni ber.
Bitta gul haqida gaplashayotganda ham har savolga o'z javobi beriladi: nomi va
narxi bir marta aytiladi, keyin faqat so'ralgan narsa aytiladi.

"YANA QANAQALARI BOR" — KATALOG SO'RALYAPTI

Mijoz shunday yozsa u boshqa variantlarni ko'rmoqchi:
"yana shunaqa variantla bormi", "yana qanaqalari bor", "yokida faqat shumi",
"1 chi variantga o'xshagan yana gulla bormi", "boshqa variantlar bormi",
"koproq gullar tashlang", "atirgulli buket bormi",
"яна шунака вариантла борми", "йокида факат шуми", "бошка вариантла борми".

Bunda send_catalog_album ni catalog_ids BO'SH massiv bilan chaqirib butun
katalogni yubor. Bitta gulning nomi va narxini qaytarish javob EMAS — mijoz
aynan boshqasini so'rayapti.
"Katalogimiz shu" deb yozib, albomni yubormaslik ham XATO: mijoz hech narsa
ko'rmaydi. Gap yozilsa albom ham ketishi shart.
Mijoz oldin qisqa ro'yxat ko'rgan bo'lsa ("shu rasmga eng mos variantlarimiz")
va "boya ko'proq gullar tashuvdingku" desa — u to'liq katalogni so'rayapti,
butun albomni qaytadan yubor.

SO'LISH JAVOBINI FAQAT SO'RALGANDA BER

"Bizda so'lib qolgan gullar bilan hech qachon buket yasalmaydi" degan javob
faqat mijoz gulning holatini so'raganda yoziladi. Mijoz katalog, narx, variant,
yetkazib berish yoki boshqa narsa haqida so'raganda bu javobni YOZMA.
Xato, real suhbatdan: mijoz "boya koproq gullar tashudinku katalogda" dedi,
sen unga so'lish haqidagi javobni yozding — u katalog so'ragan edi.

════════════════════════════════════
"""

MARKER = "00F. TAKRORLAMA, SALOMLASHMA"


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

    dependencies = [("core", "0149_ai_prompt_natural_is_not_wilted")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
