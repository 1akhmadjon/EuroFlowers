from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_remove_floristvolumerate_unique_branch_arrangement_volume_rate_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='catalogitem',
            name='florist_salary_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name='catalogitem',
            name='volume',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AlterField(
            model_name='floristvolumerate',
            name='volume',
            field=models.CharField(max_length=80),
        ),
    ]
