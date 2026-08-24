from django.db import migrations


OLD_BARGAIN = """NARX PASAYTIRISH SO'RALYAPTI — savdolashuv:
"nechpul qberasla", "nechpulga berasla", "qanchaga berasiz", "necha pulga qo'yasiz",
"arzonroq qberaslami", "arzonlashtiring", "chegirma bormi", "skidka bormi",
"tushiring", "kamaytiring", "oxirgi narxi qancha", "eng kami qancha".
Bularning hammasi 10-bo'limdagi savdolashuv javobini oladi.
"Nechpul qberasla" ni oddiy narx savoli deb tushunma — mijoz narxni allaqachon bilgan
va uni pasaytirishni so'rayapti.
"""

NEW_BARGAIN = """NARX PASAYTIRISH SO'RALYAPTI — savdolashuv:
"nechpul qberasla", "nechpul qberas", "shuni nechpul qberas", "nechpulga berasla",
"qanchaga berasiz", "nechchiga berasiz", "necha pulga qo'yasiz", "qanchaga qo'yasiz",
"qanchaga qilib berasiz", "bolishi nechpul", "bo'lishi qancha",
"bo'ladigan narxi qancha", "arzonroq qberaslami", "arzonlashtiring",
"chegirma bormi", "skidka bormi", "tushiring", "kamaytiring",
"oxirgi narxi qancha", "eng kami qancha", "qanchada kelishamiz".
Bularning hammasi 10-bo'limdagi savdolashuv javobini oladi.

RO'YXATNI YOD OLMA, FE'LGA QARA. Savdolashuvni fe'l bildiradi:
  "turadi", "narxi qancha", "nechpul" → oddiy narx savoli. price ni ayt.
  "berasiz", "qberasla", "qo'yasiz", "qilib berasiz", "bo'lishi" → savdolashuv.
Farqi shu: "qancha turadi" — gulning narxi haqida. "Nechpul qberasla" — SENING
narxing haqida, ya'ni mijoz narxni pasaytirishingni so'rayapti.
Ikkovi bir xil so'zdan boshlanishi mumkin, farqi oxiridagi fe'lda.
Mijoz narxni allaqachon ko'rgan bo'lsa, qayta so'rashi deyarli doim savdolashuv.

Xato: mijoz "Shuni nechpul qberas" dedi, sen 1 000 000 so'm deding.
To'g'ri: "OQ JUMILA ATIR GULIDAN KOMPAZITSIYA 800 000 so'm qilib beramiz."
Xato: mijoz "Bolishi nechpul" dedi, sen "Yetkazib berishmi yoki kelib olib
ketasizmi?" deb so'rading — narx savoli javobsiz qoldi.
To'g'ri: avval kelishilgan narxni ayt. Yetkazib berishni undan keyin so'raysan.
"""

OLD_BLOCKS = """Ikkitasi bir javobga tushsa javob reklama varaqasiga o'xshaydi va mijoz o'qimaydi.
"""

NEW_BLOCKS = """Ikkitasi bir javobga tushsa javob reklama varaqasiga o'xshaydi va mijoz o'qimaydi.

Bu qoida SEN o'zingdan qo'shgan bloklarga tegishli. Mijozning o'zi so'ragan savol
blok emas — unga javob berish shart.
Mijoz ikki narsa so'rasa — bitta xabarda yoki ketma-ket ikki xabarda — ikkalasiga
ham javob ber. Bittasini tanlab, ikkinchisini indamay tashlab ketma.
Xato, real suhbatdan: mijoz "Nechpul qberasla" va "Manzil qayoda" deb yozdi,
sen faqat manzilni aytding va narx savoli javobsiz qoldi.
To'g'ri: kelishilgan narxni ayt, keyin manzilni ayt. Ikki qisqa qator.
"""

MARKER = "RO'YXATNI YOD OLMA, FE'LGA QARA"
BLOCK_MARKER = "Mijozning o'zi so'ragan savol"


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    changed = False
    if MARKER not in prompt and OLD_BARGAIN in prompt:
        prompt = prompt.replace(OLD_BARGAIN, NEW_BARGAIN, 1)
        changed = True
    if BLOCK_MARKER not in prompt and OLD_BLOCKS in prompt:
        prompt = prompt.replace(OLD_BLOCKS, NEW_BLOCKS, 1)
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
    prompt = prompt.replace(NEW_BARGAIN, OLD_BARGAIN, 1)
    prompt = prompt.replace(NEW_BLOCKS, OLD_BLOCKS, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0140_ai_prompt_telegram_instead_of_handoff")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
