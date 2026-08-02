from decimal import Decimal
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0096_material_units_and_supplier_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Reservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("active", "Bron"), ("fulfilled", "Sotildi"), ("cancelled", "Bekor qilindi")], default="active", max_length=20)),
                ("payment_status", models.CharField(choices=[("unpaid", "To‘lanmagan"), ("deposit", "Zaklad"), ("paid", "To‘liq to‘langan")], default="unpaid", max_length=20)),
                ("request_uz", models.TextField()),
                ("arrangement_type", models.CharField(blank=True, choices=[("bouquet", "Buket"), ("basket", "Savat"), ("stems", "Donalab"), ("catalog", "Katalog")], max_length=20)),
                ("estimated_price", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("desired_date", models.DateField(blank=True, null=True)),
                ("desired_time", models.CharField(blank=True, max_length=20)),
                ("fulfillment", models.CharField(blank=True, choices=[("delivery", "Yetkazib berish"), ("pickup", "Kelib olish")], max_length=20)),
                ("delivery_address", models.CharField(blank=True, max_length=255)),
                ("note", models.TextField(blank=True)),
                ("catalog_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reservations", to="core.catalogitem")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_reservations", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reservations", to="core.customer")),
            ],
            options={"ordering": ["status", "-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="ReservationPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("method", models.CharField(choices=[("cash", "Naqd"), ("card", "Karta"), ("transfer", "O‘tkazma")], default="cash", max_length=20)),
                ("paid_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("note", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_reservation_payments", to=settings.AUTH_USER_MODEL)),
                ("reservation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payments", to="core.reservation")),
            ],
            options={"ordering": ["-paid_at", "-id"]},
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="reservation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="catalog_items", to="core.reservation"),
        ),
        migrations.AddField(
            model_name="cataloghistory",
            name="reservation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="catalog_history", to="core.reservation"),
        ),
    ]
