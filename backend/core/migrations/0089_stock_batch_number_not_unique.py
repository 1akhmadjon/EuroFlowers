from django.db import migrations, models


class Migration(migrations.Migration):
    """Partiya raqami takrorlanishi mumkin.

    Bir xil raqamli partiyalar turli gul va turli kunlarda kelaveradi,
    shuning uchun yagonalik sharti olib tashlanadi. Qidiruv tez qolishi uchun
    o'rniga oddiy indeks qo'yiladi.
    """

    dependencies = [("core", "0088_branch_and_catalog_transfer")]

    operations = [
        migrations.RemoveConstraint(model_name="stockbatch", name="unique_batch_number"),
        migrations.AddIndex(
            model_name="stockbatch",
            index=models.Index(fields=["batch_number"], name="core_stockb_batch_n_idx"),
        ),
    ]
