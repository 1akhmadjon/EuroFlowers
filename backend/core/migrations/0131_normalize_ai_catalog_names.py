from django.db import migrations

from core.models import normalize_display_name


def normalize_names(apps, schema_editor):
    AICatalogItem = apps.get_model("core", "AICatalogItem")
    for item in AICatalogItem.objects.all():
        normalized = normalize_display_name(item.name)
        if normalized != item.name:
            item.name = normalized
            item.save(update_fields=["name"])


class Migration(migrations.Migration):
    """Avval qo'shilgan nomlar ham bir ko'rinishga keltiriladi. Ortga qaytarish yo'q — asl yozuv saqlanmaydi."""

    dependencies = [("core", "0130_catalog_sale_restore_action")]

    operations = [migrations.RunPython(normalize_names, migrations.RunPython.noop)]
