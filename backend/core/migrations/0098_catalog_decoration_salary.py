from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0097_reservation_and_catalog_restoration"),
    ]

    operations = [
        migrations.AddField(
            model_name="floristprofile",
            name="decoration_fee",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="decoration_florist",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="decorated_catalog_items", to="core.floristprofile"),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="decoration_salary_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name="floristsalaryentry",
            name="source",
            field=models.CharField(choices=[("catalog", "Katalog"), ("custom_catalog", "Custom katalog"), ("decoration", "Oformleniya"), ("sale_decoration", "Sotuv oformleniya"), ("daily", "Kunlik"), ("manual", "Qo‘lda")], max_length=30),
        ),
    ]
