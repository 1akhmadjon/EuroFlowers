import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_lead_details_packagingmovement'),
    ]

    operations = [
        migrations.AddField(
            model_name='catalogitem',
            name='quantity_sold',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='catalogitem',
            name='quantity_stock_deducted',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='catalogitem',
            name='quantity_total',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='lead',
            name='florist_fee',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='lead',
            name='stock_deducted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='LeadPackagingUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='packaging_usage', to='core.lead')),
                ('packaging', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lead_usages', to='core.packaging')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='LeadStockUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity_stems', models.PositiveIntegerField()),
                ('quantity_bunches', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stock_usage', to='core.lead')),
                ('stock_batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lead_usages', to='core.stockbatch')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
