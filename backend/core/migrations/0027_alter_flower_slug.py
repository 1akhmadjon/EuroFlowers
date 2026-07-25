from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_db_only_ai_prompt'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flower',
            name='slug',
            field=models.SlugField(blank=True, null=True, unique=True),
        ),
    ]
