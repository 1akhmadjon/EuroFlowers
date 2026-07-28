from django.db import migrations
from django.db.models import Q


def remove_developer_notifications(apps, schema_editor):
    UserProfile = apps.get_model("core", "UserProfile")
    FloristProfile = apps.get_model("core", "FloristProfile")
    FloristAttendance = apps.get_model("core", "FloristAttendance")
    FloristSalaryEntry = apps.get_model("core", "FloristSalaryEntry")
    Notification = apps.get_model("core", "Notification")
    developer_user_ids = list(UserProfile.objects.filter(role="developer").values_list("user_id", flat=True))
    developer_florist_ids = list(FloristProfile.objects.filter(user_id__in=developer_user_ids).values_list("id", flat=True))
    developer_attendance_ids = list(FloristAttendance.objects.filter(florist_id__in=developer_florist_ids).values_list("id", flat=True))
    developer_salary_ids = list(FloristSalaryEntry.objects.filter(florist_id__in=developer_florist_ids).values_list("id", flat=True))
    filters = Q(target_user_id__in=developer_user_ids)
    if developer_attendance_ids:
        filters |= Q(reference_type="attendance", reference_id__in=developer_attendance_ids)
    if developer_salary_ids:
        filters |= Q(reference_type="florist_salary", reference_id__in=developer_salary_ids)
    Notification.objects.filter(filters).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0038_ai_prompt_discount_negotiation"),
    ]

    operations = [
        migrations.RunPython(remove_developer_notifications, migrations.RunPython.noop),
    ]
