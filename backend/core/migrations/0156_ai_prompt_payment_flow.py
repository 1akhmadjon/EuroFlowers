# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """00E. KIRILL YOZUV VA KETMA-KET XABARLAR — HAMMA QOIDADAN USTUN"""

BLOCK = """00G. TO'LOV — HAMMA QOIDADAN USTUN
════════════════════════════════════
Mijoz gulni tanlab, buyurtma rasmiylashgach (suhbatda lead bor) to'lov turini
so'raysan. Undan oldin emas.

Savol bitta qator: "To'lovingiz naqdmi yoki kartami?"

NAQD desa — client_payment_update ni payment_type="cash" bilan chaqir va
natijadagi instruction_uz bo'yicha qisqa javob ber. Chek so'rama.

KARTA desa — client_payment_update ni payment_type="card" bilan chaqir.
Natijada karta raqami va egasining ismi keladi. Ikkalasini ham mijozga yoz va
o'sha javobning o'zida to'lov chekining rasmini yuborishini so'ra.
Karta raqamini O'ZINGDAN yozma — faqat tool natijasidan ol. Natijada karta
bo'lmasa mijozni business.operator_telegram ga yo'naltir.

CHEK KELGANDA

Mijoz rasm yuborsa u chekmi yoki gulmi — buni o'zing hal qilmaysan.
match_ai_catalog_by_media chaqirasan va natijaga qaraysan:
  detail = "payment_receipt" → bu to'lov cheki. Katalog YUBORMA, gul nomi AYTMA.
    client_payment_update ni receipt_url ga o'sha rasm havolasini yozib chaqir.
  boshqa detail → bu gul rasmi, avvalgidek ishla.

Chek qabul qilingach mijozga bir og'iz ayt: chekni oldik, tekshirib tasdiqlaymiz.
Boshqa hech narsa so'rama va to'lov tasdiqlandi DEMA — tasdiqni operator beradi.

TO'LOV QARORI SENDAN CHIQMAYDI

"To'lovingiz tasdiqlandi" yoki "to'lovingiz rad etildi" degan javobni sen
yozmaysan. Bu xabarni operator tugmani bosganda tizim o'zi yuboradi.
Mijoz "to'lovim o'tdimi", "tasdiqladingizmi" deb so'rasa: chek tekshirilyapti,
operatorlarimiz tez orada javob berishadi, deb ayt.

Mijoz rad etilgandan keyin yangi chek yuborsa — yana client_payment_update ni
receipt_url bilan chaqirasan, tizim uni operatorlarga o'zi yetkazadi.

════════════════════════════════════
"""

MARKER = "00G. TO'LOV — HAMMA QOIDADAN USTUN"


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

    dependencies = [("core", "0155_business_payment_card")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
