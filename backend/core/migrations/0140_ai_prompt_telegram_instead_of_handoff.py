from django.db import migrations


OLD_BLOCK = 'Faqat A holatida operatorga topshiriladi. Quyidagi tartib A uchun.\n\nQanday topshirish kerak, ikki holat bor.\n\n1-HOLAT. ISM YOKI TELEFON HALI YO\'Q\nAvval mijoz savoliga bir og\'iz iliq javob ber — operatorlarimiz aniq ma\'lumot berishini ayt.\nKeyin ism va telefonni so\'ra. Savol shaklida emas, iltimos shaklida:\n"Operatorlarimiz sizga aniq javob berishadi. Ism va telefon raqamingizni qoldiring."\nIkkalasi kelgach client_lead_create chaqir, topic ga question yoki other yoz.\n\n2-HOLAT. ISM VA TELEFON ALLAQACHON BOR\nalready_known.name va already_known.phone ikkalasi ham true bo\'lsa ularni QAYTA SO\'RAMA.\nBuning o\'rniga bitta qisqa savol bilan roziligini ol:\n"Shu masalada operatorimiz siz bilan bog\'lanib, aniq ma\'lumot bersinmi?"\nMijoz "ha", "mayli", "bo\'ladi", "yaxshi" desa — client_lead_create chaqir.\nMijoz "yo\'q", "kerak emas" desa lead yaratma, suhbatni iliq yop.\n\nRoziligisiz lead yaratma va rozilikni ikki marta so\'rama.\n\nHAR IKKI HOLATDA request_text ni ideal yoz. Operator suhbatni o\'qimaydi, faqat shu\nmatnni ko\'radi. Ichida bo\'lishi kerak:\nmijoz nima so\'radi, o\'z so\'zi bilan\nqaysi gul yoki mahsulot haqida gap ketyapti\nsana, hajm, manzil kabi aytilgan tafsilotlar\nnega operator kerak\n\nYomon: "Savol bor"\nYaxshi: "Mijoz to\'y uchun 20 ta stol bezagini so\'radi, 12.09.2026 ga kerak, narx va imkoniyatni operator aniqlashi kerak"'

NEW_BLOCK = 'Faqat A holatida mijoz operatorga yo\'naltiriladi. Quyidagi tartib A uchun.\n\nTELEFON RAQAMI SO\'RALMAYDI VA LEAD YARATILMAYDI.\nMijoz savoliga bir og\'iz iliq javob ber, keyin uni Telegram akkauntimizga yo\'naltir.\nAkkaunt nomini REAL_CONTEXT_JSON dagi business.operator_telegram dan ol, o\'zingdan yozma.\n\nJavob shu mazmunda, ikki qatordan oshmasin:\n"Bu bo\'yicha operatorlarimiz aniq javob berishadi.\nbusiness.operator_telegram ga yozib yuboring, sizga to\'liq ma\'lumot berishadi."\n\nRus tilida ham shunday, akkaunt nomi o\'zgarmaydi.\n\nBu holatda client_lead_create CHAQIRILMAYDI. Lead faqat buyurtma uchun ochiladi —\nmijoz katalogdan gul tanlaganda yoki o\'ziga yasattirmoqchi bo\'lganda. Savol,\nrasm bo\'yicha so\'rov, shikoyat, hamkorlik — bularning hech biri lead emas.\n\nIsm va telefon ham SO\'RALMAYDI. Ular faqat buyurtma tasdiqlanayotganda so\'raladi.\nXato: "Operatorlarimiz sizga aniq javob berishadi. Ism va telefon raqamingizni qoldiring."\nTo\'g\'ri: "Operatorlarimiz aniq javob berishadi, @akkaunt ga yozib yuboring."\n\nMijoz o\'zi telefon raqamini bergan bo\'lsa ham lead ochma — uni baribir Telegram\nakkauntga yo\'naltir.'

TOP_RULE = """ISM VA TELEFON FAQAT BUYURTMA UCHUN.
Ularni faqat ikki holatda so'raysan:
  mijoz katalogdan aniq gulni tanlab, buyurtma bermoqchi bo'lganda
  mijoz o'ziga yasattirmoqchi bo'lib, kerakli ma'lumot yig'ilib bo'lganda
Boshqa hech qachon. Savolga javob berolmasang, rasmdagi gulni topolmasang,
shikoyat yoki hamkorlik bo'lsa — telefon SO'RAMA. Bunday paytda mijozni
business.operator_telegram dagi Telegram akkauntimizga yo'naltirasan va
lead YARATMAYSAN. Lead — buyurtma, savol emas.

"""

ANCHOR = "00. QANDAY GAPIRASAN — HAMMA QOIDADAN USTUN"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if OLD_BLOCK in prompt:
        prompt = prompt.replace(OLD_BLOCK, NEW_BLOCK, 1)
    marker = ANCHOR + "\n" + "\u2550" * 36 + "\n"
    if "ISM VA TELEFON FAQAT BUYURTMA UCHUN" not in prompt and marker in prompt:
        prompt = prompt.replace(marker, marker + TOP_RULE, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(NEW_BLOCK, OLD_BLOCK, 1).replace(TOP_RULE, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0139_business_operator_telegram")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
