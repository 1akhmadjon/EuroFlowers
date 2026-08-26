from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("core", "0154_ai_catalog_ads_mapping")]

    operations = [
        migrations.AddField(
            model_name="businesssettings",
            name="payment_card_number",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="businesssettings",
            name="payment_card_holder",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
