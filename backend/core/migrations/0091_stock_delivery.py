from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def fill_deliveries(apps, schema_editor):
    """Mavjud partiyalarni yangi Partiya yozuviga bog'laydi.

    Bir xil raqam va sanadagi gullar bitta partiyaga tushadi — amalda ular
    bitta yuk bilan kelgan. Pochka tannarxi ham dona narxidan hisoblanadi.
    """
    StockBatch = apps.get_model("core", "StockBatch")
    StockDelivery = apps.get_model("core", "StockDelivery")
    cache = {}
    for batch in StockBatch.objects.all().iterator():
        key = (batch.batch_number, batch.received_at, batch.supplier_id)
        delivery = cache.get(key)
        if delivery is None:
            delivery = StockDelivery.objects.create(
                number=batch.batch_number or "-",
                received_at=batch.received_at,
                supplier_id=batch.supplier_id,
                note="Avtomatik ko‘chirildi",
                is_active=True,
            )
            cache[key] = delivery
        batch.delivery = delivery
        if not batch.cost_per_bunch:
            batch.cost_per_bunch = Decimal(batch.cost_per_stem or 0) * Decimal(batch.stems_per_bunch or 1)
        batch.save(update_fields=["delivery", "cost_per_bunch"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0090_rename_batch_number_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("number", models.CharField(max_length=40)),
                ("received_at", models.DateField(default=django.utils.timezone.localdate)),
                ("note", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_deliveries", to=settings.AUTH_USER_MODEL)),
                ("supplier", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="deliveries", to="core.supplier")),
            ],
            options={"ordering": ["-received_at", "-id"], "verbose_name_plural": "Stock deliveries"},
        ),
        migrations.AddIndex(
            model_name="stockdelivery",
            index=models.Index(fields=["number"], name="core_stockd_number_ff0e60_idx"),
        ),
        migrations.AddField(
            model_name="stockbatch",
            name="cost_per_bunch",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name="stockbatch",
            name="delivery",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="batches", to="core.stockdelivery"),
        ),
        migrations.RunPython(fill_deliveries, migrations.RunPython.noop),
    ]
