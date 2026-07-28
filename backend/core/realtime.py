from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth.models import User

from .permissions import has_page_permission


def users_for_realtime(page):
    users = User.objects.filter(is_active=True).select_related("profile").prefetch_related("page_permissions")
    for user in users:
        if not has_page_permission(user, page, False):
            continue
        yield user


def broadcast_to_page(page, payload):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    for user in users_for_realtime(page):
        try:
            async_to_sync(channel_layer.group_send)(f"notifications_user_{user.id}", {"type": "notification", "payload": payload})
        except Exception as exc:
            print(f"REALTIME_WS_BROADCAST_FAILED page={page} user={user.id} type={payload.get('type')} error={exc}", flush=True)


def broadcast_to_user(user_id, payload):
    channel_layer = get_channel_layer()
    if not channel_layer or not user_id:
        return
    try:
        async_to_sync(channel_layer.group_send)(f"notifications_user_{user_id}", {"type": "notification", "payload": payload})
    except Exception as exc:
        print(f"REALTIME_WS_BROADCAST_FAILED user={user_id} type={payload.get('type')} error={exc}", flush=True)
