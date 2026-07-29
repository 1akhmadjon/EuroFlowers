from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0060_ai_prompt_reply_shape"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="leadstatus",
            name="name_ru",
        ),
    ]
