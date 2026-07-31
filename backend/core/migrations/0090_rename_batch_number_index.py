from django.db import migrations


class Migration(migrations.Migration):
    """0089 dagi indeks nomini Django o'zi hisoblaydigan nomga keltiradi.

    Faqat nom o'zgaradi, indeksning o'zi va xatti-harakat o'sha-o'sha.
    """

    dependencies = [("core", "0089_stock_batch_number_not_unique")]

    operations = [
        migrations.RenameIndex(
            model_name="stockbatch",
            new_name="core_stockb_batch_n_790203_idx",
            old_name="core_stockb_batch_n_idx",
        ),
    ]
