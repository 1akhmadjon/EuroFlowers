from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0121_ai_catalog_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="packagingmovement",
            name="unit_price",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="packagingmovement",
            name="payment_type",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
