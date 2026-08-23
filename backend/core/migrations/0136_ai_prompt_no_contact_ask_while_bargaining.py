from django.db import migrations


ANCHOR = """O'z imkoniyating yoki cheklovlaring haqida gapirma. Quyidagilar QAT'IY TAQIQLANADI:"""

INSERT = """SAVDOLASHUV JAVOBIDA ISM VA TELEFON SO'RAMA. Mijoz hali hech narsa buyurtma
qilmadi, u narx haqida gaplashyapti. "Buyurtmangizni tasdiqlash uchun ism va telefon
raqamingizni qoldiring" degan qatorni bu javobga QO'SHMA.
Javob ikki qatordan oshmasin: narxning asosi va budjet savoli.
Xato:
"Gullarimiz yangiligi va floristlarimizning mehnati narxga ta'sir qiladi.
Budjetingiz qancha?
Buyurtmangizni tasdiqlash uchun ism va telefon raqamingizni qoldiring."
To'g'ri: shu javobning birinchi ikki qatori, uchinchisisiz.
Mijoz budjetini aytgach get_catalog ni max_price bilan chaqir va mos variantlarni ko'rsat.
Ism va telefon faqat mijoz aniq mahsulotni tanlagach so'raladi.

"""


def apply_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    prompt = row.system_prompt or ""
    if "SAVDOLASHUV JAVOBIDA ISM VA TELEFON SO'RAMA" not in prompt and ANCHOR in prompt:
        prompt = prompt.replace(ANCHOR, INSERT + ANCHOR, 1)
        row.system_prompt = prompt
        row.save(update_fields=["system_prompt"])


def revert_prompt(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    row = AISettings.objects.filter(pk=1).first()
    if not row:
        return
    row.system_prompt = (row.system_prompt or "").replace(INSERT, "", 1)
    row.save(update_fields=["system_prompt"])


class Migration(migrations.Migration):

    dependencies = [("core", "0135_ai_prompt_budget_and_contact_timing")]

    operations = [migrations.RunPython(apply_prompt, revert_prompt)]
