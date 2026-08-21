from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0129_stockmovement_sale_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cataloghistory",
            name="action",
            field=models.CharField(choices=[("created", "Qo‘shildi"), ("updated", "O‘zgartirildi"), ("sold", "Sotildi"), ("sale_restored", "Sotuv qaytarildi"), ("wasted", "Chiqitga chiqarildi"), ("inventory_deducted", "Sklad kamaytirildi"), ("inventory_restored", "Sklad qaytarildi"), ("reworked", "Restavratsiya qilindi")], max_length=30),
        ),
    ]
