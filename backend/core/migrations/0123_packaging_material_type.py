from django.db import migrations, models


MATERIAL_NAMES = ["Gupka", "Lak", "Lenta", "sumka"]


def move_existing_materials(apps, schema_editor):
    Packaging = apps.get_model("core", "Packaging")
    Packaging.objects.filter(packaging_type="other", name_uz__in=MATERIAL_NAMES).update(packaging_type="material")


def move_existing_materials_back(apps, schema_editor):
    Packaging = apps.get_model("core", "Packaging")
    Packaging.objects.filter(packaging_type="material", name_uz__in=MATERIAL_NAMES).update(packaging_type="other")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0122_packagingmovement_sale_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="packaging",
            name="packaging_type",
            field=models.CharField(
                choices=[
                    ("wrap", "Buket qog‘ozi"),
                    ("basket", "Savat"),
                    ("box", "Quti"),
                    ("material", "Material"),
                    ("other", "Aksessuar"),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(move_existing_materials, move_existing_materials_back),
    ]
