from django.db import migrations


class Migration(migrations.Migration):
    """Qarz indeksining nomini Django o'zi hisoblaydigan nomga keltiradi."""

    dependencies = [("core", "0101_debt")]

    operations = [
        migrations.RenameIndex(
            model_name="debt",
            new_name="core_debt_custome_e514b8_idx",
            old_name="core_debt_custome_9b8e1f_idx",
        ),
    ]
