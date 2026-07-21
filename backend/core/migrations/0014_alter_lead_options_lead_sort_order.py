from decimal import Decimal
from django.db import migrations, models


def populate_sort_order_and_permissions(apps, schema_editor):
    Lead = apps.get_model("core", "Lead")
    PagePermission = apps.get_model("core", "PagePermission")
    UserProfile = apps.get_model("core", "UserProfile")
    for index, lead in enumerate(Lead.objects.order_by("status", "-created_at", "id"), start=1):
        lead.sort_order = Decimal(index * 1000)
        lead.save(update_fields=["sort_order"])
    developer_user_ids = list(UserProfile.objects.filter(role="developer").values_list("user_id", flat=True))
    PagePermission.objects.filter(page__in=["ai_settings", "integrations", "audit"]).exclude(user_id__in=developer_user_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_lead_status_recall'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='lead',
            options={'ordering': ['status', 'sort_order', '-created_at', 'id']},
        ),
        migrations.AddField(
            model_name='lead',
            name='sort_order',
            field=models.DecimalField(db_index=True, decimal_places=6, default=0, max_digits=20),
        ),
        migrations.RunPython(populate_sort_order_and_permissions, migrations.RunPython.noop),
    ]
