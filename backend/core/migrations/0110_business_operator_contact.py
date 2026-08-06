from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0109_catalog_rework"),
    ]

    operations = [
        migrations.AddField(
            model_name="businesssettings",
            name="operator_phone",
            field=models.CharField(blank=True, default="+998 88 009 33 30", max_length=64),
        ),
        migrations.AddField(
            model_name="businesssettings",
            name="operator_hours",
            field=models.CharField(blank=True, default="08:00 dan 00:00 gacha", max_length=64),
        ),
        migrations.AddField(
            model_name="businesssettings",
            name="operator_hours_ru",
            field=models.CharField(blank=True, default="с 08:00 до 00:00", max_length=64),
        ),
    ]
