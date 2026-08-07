from decimal import Decimal
from django.db import migrations


def to_extra_decoration(apps, schema_editor):
    """Qo'lda yozilgan oformleniya haqlarini yangi turga o'tkazadi.

    Kataloga bog'lanmagan oformleniya yozuvi faqat qo'lda yozilgan bo'lishi
    mumkin — endi ular alohida tur bo'ldi. Soni va bittasining narxi
    saqlanmagani uchun florist profilidagi oformleniya narxidan chiqariladi:
    summa unga bo'linsa soni shu, bo'linmasa bitta qilib qo'yiladi.
    """
    FloristSalaryEntry = apps.get_model("core", "FloristSalaryEntry")
    rows = FloristSalaryEntry.objects.filter(source="decoration", catalog_item__isnull=True).select_related("florist")
    for row in rows:
        amount = Decimal(row.amount or 0)
        fee = Decimal(row.florist.decoration_fee or 0)
        if fee > 0 and amount > 0 and amount % fee == 0:
            row.quantity = int(amount / fee)
            row.unit_amount = fee
        else:
            row.quantity = 1
            row.unit_amount = amount
        row.source = "extra_decoration"
        row.save(update_fields=["source", "quantity", "unit_amount"])


def to_decoration(apps, schema_editor):
    FloristSalaryEntry = apps.get_model("core", "FloristSalaryEntry")
    FloristSalaryEntry.objects.filter(source="extra_decoration").update(source="decoration", quantity=0, unit_amount=0)


class Migration(migrations.Migration):

    dependencies = [("core", "0112_florist_extra_decoration")]

    operations = [migrations.RunPython(to_extra_decoration, to_decoration)]
