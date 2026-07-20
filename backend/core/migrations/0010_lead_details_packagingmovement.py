import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_conversation_ai_pause_reason_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='details',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='PackagingMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('movement_type', models.CharField(choices=[('in', 'Kirim'), ('out', 'Chiqim'), ('adjustment', 'Tuzatish'), ('waste', 'Hisobdan chiqarish'), ('transfer_out', 'Filialdan chiqim'), ('transfer_in', 'Filialga kirim')], max_length=20)),
                ('quantity', models.IntegerField()),
                ('reference_type', models.CharField(blank=True, max_length=40)),
                ('reference_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('packaging', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='movements', to='core.packaging')),
                ('performed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='packaging_movements', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
