# -*- coding: utf-8 -*-
from django.db import migrations


ANCHOR = """00G. TO'LOV — HAMMA QOIDADAN USTUN"""

BLOCK = """00H. OPERATOR CHAQIRISH — HAMMA QOIDADAN USTUN
════════════════════════════════════
MIJOZGA TELEGRAM USERNAME BERILMAYDI.

"@euroflowerspremium ga yozing", "Telegramimizga yozing", "shu akkauntga yozing"
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
chaqirmaysan, operator allaqachon xabardor.

════════════════════════════════════
"""

MARKER = "00H. OPERATOR CHAQIRISH"


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

    dependencies = [("core", "0156_ai_prompt_payment_flow")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
