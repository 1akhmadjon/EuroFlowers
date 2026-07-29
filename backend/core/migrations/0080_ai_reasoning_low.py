from django.db import migrations


def set_low_effort(apps, schema_editor):
    AISettings = apps.get_model("core", "AISettings")
    AISettings.objects.all().update(reasoning_effort="low")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0079_ai_prompt_no_image_offer_and_quantity"),
    ]

    operations = [
        migrations.RunPython(set_low_effort, migrations.RunPython.noop),
    ]
