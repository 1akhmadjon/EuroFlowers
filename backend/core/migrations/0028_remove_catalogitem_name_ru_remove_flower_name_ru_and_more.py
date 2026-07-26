import django.db.models.deletion
from django.db import migrations, models


def accessory_to_other(apps, schema_editor):
    Packaging = apps.get_model('core', 'Packaging')
    Packaging.objects.filter(packaging_type='accessory').update(packaging_type='other')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_alter_flower_slug'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='catalogitem',
            name='name_ru',
        ),
        migrations.RemoveField(
            model_name='flower',
            name='name_ru',
        ),
        migrations.RemoveField(
            model_name='flowervariant',
            name='color_ru',
        ),
        migrations.RemoveField(
            model_name='flowervariant',
            name='name_ru',
        ),
        migrations.RemoveField(
            model_name='packaging',
            name='name_ru',
        ),
        migrations.RunPython(accessory_to_other, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='packaging',
            name='packaging_type',
            field=models.CharField(choices=[('wrap', 'Buket qog‘ozi'), ('basket', 'Savat'), ('box', 'Quti'), ('other', 'Boshqalar')], max_length=20),
        ),
        migrations.CreateModel(
            name='CatalogMaterialUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('catalog_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='materials', to='core.catalogitem')),
                ('packaging', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='catalog_usages', to='core.packaging')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
