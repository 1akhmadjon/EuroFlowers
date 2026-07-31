from django.db import migrations


class Migration(migrations.Migration):
    """Partiya raqami indeksining nomini Django o'zi hisoblaydigan nomga keltiradi."""

    dependencies = [("core", "0091_stock_delivery")]

    operations = [
        migrations.RenameIndex(
            model_name="stockdelivery",
            new_name="core_stockd_number_a111a7_idx",
            old_name="core_stockd_number_ff0e60_idx",
        ),
    ]
