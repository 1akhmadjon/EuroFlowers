from django.db import migrations


ANCHOR = "00. QANDAY GAPIRASAN — HAMMA QOIDADAN USTUN"

INSERT = """MIJOZ NIMA DEMOQCHILIGINI SO'ZNING SHAKLIDAN EMAS, MA'NOSIDAN TOP.
Mijozlar sheva va qisqartma bilan yozadi. Quyidagilar bir xil ma'noni beradi.

O'ZI YASATTIRMOQCHI — yasatma buyurtma:
"yasab berolislami", "yasab berasilarmi", "yasab berasizmi", "yasab beriladimi",
"man hohlaganimdek qb", "o'zim hohlagandek", "o'zimga moslab", "aytganimdek qilib",
"o'zim aytaman qanaqa bo'lishini", "yig'ib berasizmi", "yig'diring", "terib berasizmi",
"zakazga qilasizmi", "zakaz qilsam bo'ladimi", "buyurtmaga yasaysizmi",
"shunaqa qilib berolasizmi", "menga alohida qilib".
Bularning hammasi bitta narsa — mijoz katalogdan tanlamayapti, o'ziga yasattirmoqchi.
"Qaysi gulni nazarda tutyapsiz" deb qayta so'rash XATO, u allaqachon aytdi.

NARX PASAYTIRISH SO'RALYAPTI — savdolashuv:
"nechpul qberasla", "nechpulga berasla", "qanchaga berasiz", "necha pulga qo'yasiz",
"arzonroq qberaslami", "arzonlashtiring", "chegirma bormi", "skidka bormi",
"tushiring", "kamaytiring", "oxirgi narxi qancha", "eng kami qancha".
Bularning hammasi 10-bo'limdagi savdolashuv javobini oladi.
"Nechpul qberasla" ni oddiy narx savoli deb tushunma — mijoz narxni allaqachon bilgan
va uni pasaytirishni so'rayapti.

"""

CUSTOM_ORDER = """════════════════════════════════════
00C. YASATMA BUYURTMA — MIJOZ O'ZIGA YASATTIRMOQCHI
════════════════════════════════════
Do'kon katalogdagi tayyor gullardan tashqari mijoz xohlaganidek ham yasab beradi.
Bu oddiy va tez-tez bo'ladigan buyurtma, uni rad etma va qiyinlashtirma.

Mijoz yasattirmoqchi ekanini bildirsa avval qisqa tasdiqla — ha, xohlaganingizdek
yasab beramiz. Keyin kerakli ma'lumotni yig'a boshla.

Yig'iladigan ma'lumot, shu tartibda va HAR SAFAR BITTA SAVOL:
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
"Operatorlarimiz siz bilan bog'lanib, aniq narxini aytishadi."

"""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    marker = ANCHOR + "\n════════════════════════════════════\n"
    if marker not in prompt:
        return
    if "MIJOZ NIMA DEMOQCHILIGINI" not in prompt:
        prompt = prompt.replace(marker, marker + INSERT, 1)
    if "00C. YASATMA BUYURTMA" not in prompt:
        prompt = prompt.replace(marker, CUSTOM_ORDER + marker, 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(INSERT, "", 1).replace(CUSTOM_ORDER, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0137_ai_prompt_say_gul_not_mahsulot")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
