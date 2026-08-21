from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0128_pagepermission_ai_catalog_choice"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="card_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="cash_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="payment_type",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="sale_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="unit_price",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
