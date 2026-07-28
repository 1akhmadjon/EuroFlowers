import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from core.models import PagePermission, UserProfile


class Command(BaseCommand):
    def handle(self, *args, **options):
        username = os.getenv("DEVELOPER_USERNAME", "developer")
        password = os.getenv("DEVELOPER_PASSWORD", os.getenv("ADMIN_PASSWORD", ""))
        email = os.getenv("DEVELOPER_EMAIL", "developer@euroflowers.uz")
        first_name = os.getenv("DEVELOPER_FIRST_NAME", "Developer")
        last_name = os.getenv("DEVELOPER_LAST_NAME", "EuroFlowers")
        user, _ = User.objects.get_or_create(username=username, defaults={"email": email, "first_name": first_name, "last_name": last_name, "is_staff": True, "is_superuser": False, "is_active": True})
        changed = []
        if user.email != email:
            user.email = email
            changed.append("email")
        if user.first_name != first_name:
            user.first_name = first_name
            changed.append("first_name")
        if user.last_name != last_name:
            user.last_name = last_name
            changed.append("last_name")
        if not user.is_staff:
            user.is_staff = True
            changed.append("is_staff")
        if not user.is_active:
            user.is_active = True
            changed.append("is_active")
        if password:
            user.set_password(password)
            changed.append("password")
        if changed:
            user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "developer", "language": "uz"})
        profile_changed = []
        if profile.role != "developer":
            profile.role = "developer"
            profile_changed.append("role")
        if profile.language != "uz":
            profile.language = "uz"
            profile_changed.append("language")
        if profile_changed:
            profile.save(update_fields=profile_changed + ["updated_at"])
        for page, _ in PagePermission.PAGE_CHOICES:
            PagePermission.objects.update_or_create(user=user, page=page, defaults={"can_view": True, "can_control": True})
        self.stdout.write(self.style.SUCCESS(f"Developer user ready: {username}"))
