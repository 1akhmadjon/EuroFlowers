from django.db import migrations, models


def fill_supplier_type(apps, schema_editor):
    """Material yuki bo'lgan postavshiklarni material turiga o'tkazadi."""
    Supplier = apps.get_model("core", "Supplier")
    MaterialDelivery = apps.get_model("core", "MaterialDelivery")
    ids = set(MaterialDelivery.objects.exclude(supplier__isnull=True).values_list("supplier_id", flat=True))
    if ids:
        Supplier.objects.filter(id__in=ids).update(supplier_type="material")


class Migration(migrations.Migration):
    dependencies = [("core", "0095_rename_material_delivery_index")]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="supplier_type",
            field=models.CharField(
                choices=[("flower", "Gul"), ("material", "Material"), ("both", "Gul va material")],
                default="flower", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="packaging",
            name="unit",
            field=models.CharField(choices=[("piece", "Dona"), ("bunch", "Pochka")], default="piece", max_length=20),
        ),
        migrations.AddField(
            model_name="packaging",
            name="units_per_bunch",
            field=models.PositiveIntegerField(default=20),
        ),
        migrations.AddField(
            model_name="packaging",
            name="basket_material",
            field=models.CharField(
                blank=True,
                choices=[("wooden", "Yog‘ochli"), ("plastic_handle", "Plastmassa ruchkali"), ("woven", "To‘qima")],
                max_length=20,
            ),
        ),
        migrations.RunPython(fill_supplier_type, migrations.RunPython.noop),
    ]
