import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Material partiyasi: buket qog'ozi, savat, gupka kelgan yuk.

    Material gul kabi partiyalarga bo'linmaydi — bitta qator bo'lib qoladi,
    kirim uning sonini oshiradi va tannarxini yangilaydi. Partiya faqat
    kirim yozuvlarini guruhlaydi va postavshikni saqlaydi.
    """

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0093_stock_batch_exact_prices"),
    ]

    operations = [
        migrations.CreateModel(
            name="MaterialDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("number", models.CharField(max_length=40)),
                ("received_at", models.DateField(default=django.utils.timezone.localdate)),
                ("note", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_material_deliveries", to=settings.AUTH_USER_MODEL)),
                ("supplier", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="material_deliveries", to="core.supplier")),
            ],
            options={"ordering": ["-received_at", "-id"], "verbose_name_plural": "Material deliveries"},
        ),
        migrations.AddIndex(
            model_name="materialdelivery",
            index=models.Index(fields=["number"], name="core_materi_number_31ce43_idx"),
        ),
        migrations.AddField(
            model_name="packagingmovement",
            name="delivery",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="movements", to="core.materialdelivery"),
        ),
        migrations.AddField(
            model_name="packagingmovement",
            name="unit_cost",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
