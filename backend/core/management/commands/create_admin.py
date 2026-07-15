import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from core.models import Branch, PagePermission, UserProfile


class Command(BaseCommand):
    def handle(self, *args, **options):
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD", "")
        email = os.getenv("ADMIN_EMAIL", "admin@euroflowers.uz")
        first_name = os.getenv("ADMIN_FIRST_NAME", "Admin")
        last_name = os.getenv("ADMIN_LAST_NAME", "EuroFlowers")
        user, _ = User.objects.get_or_create(username=username, defaults={"email": email, "first_name": first_name, "last_name": last_name, "is_staff": True, "is_superuser": True})
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
        if not user.is_superuser:
            user.is_superuser = True
            changed.append("is_superuser")
        if password:
            user.set_password(password)
            changed.append("password")
        if changed:
            user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "admin", "language": "uz"})
        if profile.role != "admin":
            profile.role = "admin"
            profile.save(update_fields=["role", "updated_at"])
        branches = list(Branch.objects.filter(is_active=True))
        if branches:
            profile.branches.set(branches)
        for page, _ in PagePermission.PAGE_CHOICES:
            PagePermission.objects.update_or_create(user=user, page=page, defaults={"can_view": True, "can_control": True})
        self.stdout.write(self.style.SUCCESS(f"Admin user ready: {username}"))
