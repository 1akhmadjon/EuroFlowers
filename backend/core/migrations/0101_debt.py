from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Katalog qarzga sotilganda ochiladigan qarz yozuvi."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0100_stock_batch_is_free"),
    ]

    operations = [
        migrations.CreateModel(
            name="Debt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("note", models.TextField(blank=True)),
                ("is_paid", models.BooleanField(default=False)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("paid_method", models.CharField(blank=True, choices=[("cash", "Naqd"), ("card", "Karta")], max_length=20)),
                ("catalog_history", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="debts", to="core.cataloghistory")),
                ("catalog_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="debts", to="core.catalogitem")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_debts", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="debts", to="core.customer")),
                ("paid_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="closed_debts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["is_paid", "-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="debt",
            index=models.Index(fields=["customer", "is_paid"], name="core_debt_custome_9b8e1f_idx"),
        ),
    ]
