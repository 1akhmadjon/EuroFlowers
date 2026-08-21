from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0127_packagingmovement_payment_split"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pagepermission",
            name="page",
            field=models.CharField(
                choices=[
                    ("dashboard", "Dashboard"),
                    ("inventory", "Sklad"),
                    ("catalog", "Katalog"),
                    ("ai_catalog", "AI katalog"),
                    ("crm", "CRM"),
                    ("customers", "Mijozlar"),
                    ("conversations", "Instagram inbox"),
                    ("social_posts", "Postlar"),
                    ("notifications", "Bildirishnomalar"),
                    ("suppliers", "Postavshiklar"),
                    ("florists", "Floristlar"),
                    ("attendance", "Ish vaqti"),
                    ("settings", "Sozlamalar"),
                    ("ai_settings", "AI sozlamalari"),
                    ("integrations", "Integratsiyalar"),
                    ("users", "Jamoa"),
                    ("mini_app", "Mini app"),
                    ("expenses", "Rasxodlar"),
                    ("audit", "Audit"),
                ],
                max_length=40,
            ),
        ),
    ]
