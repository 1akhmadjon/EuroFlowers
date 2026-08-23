from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("core", "0138_ai_prompt_custom_order_and_bargaining")]

    operations = [
        migrations.AddField(
            model_name="businesssettings",
            name="operator_telegram",
            field=models.CharField(blank=True, default="@euroflowerspremium", max_length=120),
        ),
    ]
