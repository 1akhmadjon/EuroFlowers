from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_alter_aisettings_system_prompt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="aisettings",
            name="system_prompt",
            field=models.TextField(default=""),
        ),
    ]
