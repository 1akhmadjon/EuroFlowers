from django.db import migrations


def seed_materials(apps, schema_editor):
    Packaging = apps.get_model("core", "Packaging")
    rows = [
        ("Gupka", "bunch"),
        ("Lenta", "piece"),
        ("Lak", "piece"),
    ]
    for name, unit in rows:
        material = Packaging.objects.filter(name_uz=name).first()
        if material:
            material.packaging_type = "other"
            material.unit = unit
            material.is_active = True
            material.save(update_fields=["packaging_type", "unit", "is_active", "updated_at"])
        else:
            Packaging.objects.create(packaging_type="other", name_uz=name, unit=unit, cost_price=0, sale_price=0, quantity=0, is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0098_catalog_decoration_salary"),
    ]

    operations = [
        migrations.RunPython(seed_materials, migrations.RunPython.noop),
    ]
