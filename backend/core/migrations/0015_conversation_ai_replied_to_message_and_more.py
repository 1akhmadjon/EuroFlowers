import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_alter_lead_options_lead_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='ai_replied_to_message',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.message'),
        ),
        migrations.AddField(
            model_name='conversation',
            name='ai_reply_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='conversation',
            name='ai_reply_started_for_message',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.message'),
        ),
    ]
