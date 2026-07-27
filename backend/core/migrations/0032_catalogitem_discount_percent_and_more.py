import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_catalogitem_florist_salary_amount_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='catalogitem',
            name='discount_percent',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=7),
        ),
        migrations.AddField(
            model_name='catalogitem',
            name='discount_reason',
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name='CatalogHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(choices=[('created', 'Qo‘shildi'), ('updated', 'O‘zgartirildi'), ('sold', 'Sotildi'), ('inventory_deducted', 'Sklad kamaytirildi'), ('inventory_restored', 'Sklad qaytarildi')], max_length=30)),
                ('quantity', models.PositiveIntegerField(default=0)),
                ('listed_unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('sold_unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('discount_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('discount_percent', models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ('discount_reason', models.TextField(blank=True)),
                ('note', models.TextField(blank=True)),
                ('snapshot', models.JSONField(blank=True, default=dict)),
                ('catalog_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history', to='core.catalogitem')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='catalog_history', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
