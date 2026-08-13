from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0120_ai_prompt_paid_container_price_survives"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AICatalogItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=180)),
                ("arrangement_type", models.CharField(choices=[("bouquet", "Buket"), ("basket", "Savat"), ("box", "Quti"), ("other", "Boshqa")], default="bouquet", max_length=20)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("volume", models.CharField(blank=True, max_length=120)),
                ("price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("note", models.TextField(blank=True)),
                ("image_url", models.URLField(blank=True)),
                ("instagram_link", models.URLField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ai_catalog_items", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
