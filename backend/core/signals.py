from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import AICatalogItem, Conversation, Lead, Message, Notification
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
        transaction.on_commit(lambda: broadcast_to_page("notifications", payload))


@receiver(post_save, sender=Conversation)
def broadcast_conversation(sender, instance, created, **kwargs):
    if not created:
        return
    payload = {
        "type": "conversation.created",
        "conversation": ConversationSerializer(instance).data,
    }
    transaction.on_commit(lambda: broadcast_to_page("conversations", payload))


@receiver(post_save, sender=Message)
def broadcast_message(sender, instance, created, **kwargs):
    if not created:
        return
    payload = {
        "type": "message.created",
        "conversation_id": instance.conversation_id,
        "message": MessageSerializer(instance).data,
    }
    transaction.on_commit(lambda: broadcast_to_page("conversations", payload))


@receiver(post_save, sender=Lead)
def broadcast_lead(sender, instance, created, **kwargs):
    payload = {
        "type": "lead.created" if created else "lead.updated",
        "lead": LeadSerializer(instance).data,
    }
    transaction.on_commit(lambda: broadcast_to_page("crm", payload))


@receiver(post_save, sender=AICatalogItem)
def queue_ai_catalog_fingerprint(sender, instance, created, **kwargs):
    """Rasm qo'shilganda yoki almashtirilganda tahlilni navbatga qo'yadi.

    Admin saqlashini kutib turmaydi — tahlil fon ishida bo'ladi. Celery ishlamay
    qolsa ham tizim yiqilmaydi: mijoz rasm yuborganda fingerprint o'sha yerda yasaladi.
    """
    if not instance.image_url:
        return
    from .vision_services import fingerprint_is_stale

    if not fingerprint_is_stale(instance):
        return

    def enqueue():
        from .tasks import build_ai_catalog_fingerprint

        try:
            build_ai_catalog_fingerprint.delay(instance.id)
        except Exception as error:
            print(f"AI_CATALOG_FINGERPRINT_ENQUEUE_FAILED catalog_id={instance.id} error={error}", flush=True)

    transaction.on_commit(enqueue)
