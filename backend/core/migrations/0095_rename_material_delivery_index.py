from django.db import migrations


class Migration(migrations.Migration):
    """Material partiya indeksining nomini Django o'zi hisoblaydigan nomga keltiradi."""

    dependencies = [("core", "0094_material_delivery")]

    operations = [
        migrations.RenameIndex(
            model_name="materialdelivery",
            new_name="core_materi_number_afffac_idx",
            old_name="core_materi_number_31ce43_idx",
        ),
    ]
