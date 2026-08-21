from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0126_floristpayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="packagingmovement",
            name="cash_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="packagingmovement",
            name="card_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
