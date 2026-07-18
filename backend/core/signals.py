from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Notification
from .permissions import has_page_permission
from .serializers import NotificationSerializer


@receiver(post_save, sender=Notification)
def broadcast_notification(sender, instance, created, **kwargs):
    if not created:
        return
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    payload = {
        "type": "notification.created",
        "notification": NotificationSerializer(instance).data,
    }
    users = User.objects.filter(is_active=True).select_related("profile").prefetch_related("profile__branches", "page_permissions")
    for user in users:
        if not has_page_permission(user, "notifications", False):
            continue
        profile = getattr(user, "profile", None)
        if not user.is_superuser and profile and profile.branches.exists() and instance.branch_id not in profile.branches.values_list("id", flat=True):
            continue
        try:
            async_to_sync(channel_layer.group_send)(f"notifications_user_{user.id}", {"type": "notification", "payload": payload})
        except Exception as exc:
            print(f"NOTIFICATION_WS_BROADCAST_FAILED user={user.id} notification={instance.id} error={exc}", flush=True)
