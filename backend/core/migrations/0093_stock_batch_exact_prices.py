from decimal import Decimal

from django.db import migrations, models


def fill_exact_prices(apps, schema_editor):
    """Mavjud partiyalarda aniq hisobni to'ldiradi.

    Pochka narxi bor bo'lsa undan bo'linadi, bo'lmasa dona narxining o'zi olinadi.
    """
    StockBatch = apps.get_model("core", "StockBatch")
    for batch in StockBatch.objects.all().iterator():
        stems = Decimal(batch.stems_per_bunch or 0)
        cost = Decimal(batch.cost_per_bunch or 0) / stems if stems else Decimal("0")
        sale = Decimal(batch.sale_price_per_bunch or 0) / stems if stems else Decimal("0")
        batch.cost_per_stem_exact = cost or Decimal(batch.cost_per_stem or 0)
        batch.sale_price_per_stem_exact = sale or Decimal(batch.sale_price_per_stem or 0)
        batch.save(update_fields=["cost_per_stem_exact", "sale_price_per_stem_exact"])


class Migration(migrations.Migration):
    dependencies = [("core", "0092_rename_delivery_number_index")]

    operations = [
        migrations.AddField(
            model_name="stockbatch",
            name="cost_per_stem_exact",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="stockbatch",
            name="sale_price_per_stem_exact",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.RunPython(fill_exact_prices, migrations.RunPython.noop),
    ]
