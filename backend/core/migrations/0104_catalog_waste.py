from django.db import migrations, models


class Migration(migrations.Migration):
    """Katalogdan chiqitga chiqarish: sotilmay qolgan buket hisobdan chiqadi."""

    dependencies = [("core", "0103_sale_telegram_settings")]

    operations = [
        migrations.AddField(
            model_name="catalogitem",
            name="quantity_wasted",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="cataloghistory",
            name="action",
            field=models.CharField(
                choices=[
                    ("created", "Qo‘shildi"), ("updated", "O‘zgartirildi"), ("sold", "Sotildi"),
                    ("wasted", "Chiqitga chiqarildi"),
                    ("inventory_deducted", "Sklad kamaytirildi"), ("inventory_restored", "Sklad qaytarildi"),
                ],
                max_length=30,
            ),
        ),
    ]
