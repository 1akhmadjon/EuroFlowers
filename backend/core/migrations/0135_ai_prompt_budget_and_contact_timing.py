from django.db import migrations


BUDGET_OLD = """Natijadagi budget blokini o'qi:
  exact_match true  — shu oraliqda mahsulot bor. Ularni send_catalog_album bilan
                      yubor va qisqa yoz, masalan "shu summaga shular bor".
  exact_match false — bu narxda mahsulot YO'Q. Qatorlar eng yaqinlari.
                      Rostini ayt va eng arzonini nomla, budget.cheapest_price dan.
                      Masalan: "Eng arzoni 199 000 so'm, shundan boshlanadi."
                      Keyin o'sha yaqin variantlarni albom qilib yubor."""

BUDGET_NEW = """Mijoz mahsulot turini ham aytsa (savat, buket, quti) arrangement_type ni ham ber.
"1 millionlik savatingiz bormi" — bu max_price 1000000 va arrangement_type basket.

Natijadagi budget blokini o'qi. Ikki holat bor va ular BUTUNLAY boshqacha javob oladi.

  exact_match true — shu narxda mahsulot BOR.
                     Ularni send_catalog_album bilan yubor va qisqa yoz,
                     masalan "shu summaga shular bor" yoki "1 millionlik savatlarimiz shular".
                     Bu holatda cheapest_price ni MUTLAQO tilga olma. "Eng arzoni",
                     "shundan boshlanadi" degan jumlalar bu yerda XATO — mijoz
                     so'ragan narx bor ekan, unga arzonini eslatish kerak emas.

  exact_match false — bu narxda mahsulot YO'Q, qatorlar faqat eng yaqinlari.
                     FAQAT SHU HOLATDA rostini ayt va budget.cheapest_price ni nomla,
                     masalan "Eng arzoni 199 000 so'm, shundan boshlanadi".
                     Keyin o'sha yaqin variantlarni albom qilib yubor.

Javob yozishdan oldin exact_match qiymatini yana bir marta o'qi."""


CONTACT_TIMING = """
ISM VA TELEFONNI QACHON SO'RAYSAN

Faqat ikki holatda:
  mijoz aniq mahsulotni tanlagach yoki buyurtma bermoqchi ekanini aytgach
  yasatma buyurtma uchun kerakli ma'lumot yig'ilib bo'lgach

Mijoz hali savol berayotgan bo'lsa SO'RAMA. Narx so'radi, budjetini aytdi,
chegirma so'radi, gul haqida so'radi, o'ylab ko'raman dedi — bularning hech
birida ism va telefon so'ralmaydi. Avval savoliga javob ber.

Xato, real suhbatdan: mijoz "arzonroq qberasizmi" deb so'radi va javob
"Narx gullarning yangiligidan kelib chiqadi. Budjetingiz qancha?
Buyurtmangizni tasdiqlash uchun ism va telefon raqamingizni qoldiring."
bo'ldi. Uchinchi qator ortiqcha — mijoz hali hech narsa tanlamagan.
To'g'ri javob shu ikki qator bilan tugaydi.

"""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if BUDGET_OLD in prompt:
        prompt = prompt.replace(BUDGET_OLD, BUDGET_NEW, 1)
    anchor = "\n════════════════════════════════════\n00A. BUDJET AYTILSA"
    if "ISM VA TELEFONNI QACHON SO'RAYSAN" not in prompt and anchor in prompt:
        prompt = prompt.replace(anchor, CONTACT_TIMING + anchor.lstrip("\n"), 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = (row.system_prompt or "").replace(BUDGET_NEW, BUDGET_OLD, 1).replace(CONTACT_TIMING, "", 1)
    row.system_prompt = prompt
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0134_ai_prompt_natural_sales")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
