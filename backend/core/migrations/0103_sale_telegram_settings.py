from django.db import migrations, models


class Migration(migrations.Migration):
    """Sotuv xabari uchun alohida bot va guruh."""

    dependencies = [("core", "0102_rename_debt_index")]

    operations = [
        migrations.AddField(
            model_name="integrationsettings",
            name="sale_bot_token",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="integrationsettings",
            name="sale_group_chat_id",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
