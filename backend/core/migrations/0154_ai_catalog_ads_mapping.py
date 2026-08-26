from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0153_ai_prompt_photo_plus_make_it_is_custom"),
    ]

    operations = [
        migrations.AddField(
            model_name="aicatalogitem",
            name="instagram_ad_id",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="aicatalogitem",
            name="instagram_ad_post_id",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
