from django.db import migrations, models


class Migration(migrations.Migration):
    """Postavshik tekinga qo'shib bergan gul.

    Bunday gul sotib olinmagan, shuning uchun tannarxi yozilmaydi va
    postavshik qarziga qo'shilmaydi. Sotuv narxi esa odatdagidek yoziladi.
    """

    dependencies = [("core", "0099_seed_basic_other_materials")]

    operations = [
        migrations.AddField(
            model_name="stockbatch",
            name="is_free",
            field=models.BooleanField(default=False),
        ),
    ]
