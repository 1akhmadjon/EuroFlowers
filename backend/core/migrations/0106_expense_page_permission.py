from django.db import migrations


def grant_expenses_page(apps, schema_editor):
    """Yangi 'Rasxodlar' sahifasi adminlarga darrov ochiq bo'lsin.

    Ruxsat qatori bo'lmasa sahifa faqat superuser'ga ko'rinadi, shuning uchun
    mavjud admin va developerlarga qator ochib qo'yiladi.
    """
    PagePermission = apps.get_model("core", "PagePermission")
    UserProfile = apps.get_model("core", "UserProfile")
    for profile in UserProfile.objects.filter(role__in=["admin", "developer"]).select_related("user"):
        PagePermission.objects.update_or_create(
            user_id=profile.user_id, page="expenses",
            defaults={"can_view": True, "can_control": True},
        )


def revoke_expenses_page(apps, schema_editor):
    PagePermission = apps.get_model("core", "PagePermission")
    PagePermission.objects.filter(page="expenses").delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0105_alter_pagepermission_page_expense")]

    operations = [migrations.RunPython(grant_expenses_page, revoke_expenses_page)]
