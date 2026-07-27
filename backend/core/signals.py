from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Conversation, Lead, Message, Notification
from .realtime import broadcast_to_page, broadcast_to_user
from .serializers import ConversationSerializer, LeadSerializer, MessageSerializer, NotificationSerializer


@receiver(post_save, sender=Notification)
def broadcast_notification(sender, instance, created, **kwargs):
    if not created:
        return
    payload = {
        "type": "notification.created",
        "notification": NotificationSerializer(instance).data,
    }
    if instance.target_user_id:
        transaction.on_commit(lambda: broadcast_to_user(instance.target_user_id, payload))
    else:
        transaction.on_commit(lambda: broadcast_to_page("notifications", payload, instance.branch_id))


@receiver(post_save, sender=Conversation)
def broadcast_conversation(sender, instance, created, **kwargs):
    if not created:
        return
    payload = {
        "type": "conversation.created",
        "conversation": ConversationSerializer(instance).data,
    }
    transaction.on_commit(lambda: broadcast_to_page("conversations", payload, instance.branch_id))


@receiver(post_save, sender=Message)
def broadcast_message(sender, instance, created, **kwargs):
    if not created:
        return
    payload = {
        "type": "message.created",
        "conversation_id": instance.conversation_id,
        "message": MessageSerializer(instance).data,
    }
    branch_id = instance.conversation.branch_id
    transaction.on_commit(lambda: broadcast_to_page("conversations", payload, branch_id))


@receiver(post_save, sender=Lead)
def broadcast_lead(sender, instance, created, **kwargs):
    payload = {
        "type": "lead.created" if created else "lead.updated",
        "lead": LeadSerializer(instance).data,
    }
    transaction.on_commit(lambda: broadcast_to_page("crm", payload, instance.branch_id))
