from rest_framework.permissions import BasePermission, SAFE_METHODS
from .models import PagePermission


FLORIST_ALLOWED_PAGES = {"florists", "attendance", "notifications"}


def has_page_permission(user, page, control=False):
    if not user or not user.is_authenticated:
        return False
    role = getattr(getattr(user, "profile", None), "role", None)
    if role == "developer":
        return True
    if role in ["florist", "apprentice"] and page and page not in FLORIST_ALLOWED_PAGES:
        return False
    if page in PagePermission.DEVELOPER_ONLY_PAGES:
        return False
    if not page:
        return True
    row = PagePermission.objects.filter(user=user, page=page).first()
    if not row:
        return bool(user.is_superuser)
    return row.can_control if control else row.can_view or row.can_control


class RolePermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return has_page_permission(request.user, getattr(view, "permission_page", None), False)
        page = getattr(view, "permission_page", None)
        if page:
            return has_page_permission(request.user, page, True)
        role = getattr(getattr(request.user, "profile", None), "role", None)
        if role == "developer":
            return True
        return role in getattr(view, "write_roles", ["admin"])
