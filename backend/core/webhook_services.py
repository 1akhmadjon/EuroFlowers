import json
import re

from django.conf import settings
from django.db.models import Q

from .models import Branch, CatalogItem, Conversation, Customer, InstagramWebhookEvent, IntegrationSettings, SocialPost
from .platform_services import find_active_story_by_media_url, find_media_by_id, media_id_from_url, normalize_instagram_permalink, telegram_file_url
from .services import ingest_customer_message


def flatten_interesting_payload(value, prefix=""):
    matches = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            key_lower = key.lower()
            if any(part in key_lower for part in ["story", "url", "link", "permalink", "media", "referral", "reply_to", "source"]):
                matches[path] = child
            matches.update(flatten_interesting_payload(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.update(flatten_interesting_payload(child, f"{prefix}[{index}]"))
    return matches


def urls_from_value(value):
    urls = []
    if isinstance(value, str):
        urls.extend(re.findall(r"https?://[^\s\"'<>]+", value))
    elif isinstance(value, dict):
        for child in value.values():
            urls.extend(urls_from_value(child))
    elif isinstance(value, list):
        for child in value:
            urls.extend(urls_from_value(child))
    return urls


def attachment_kind(source, attachment_type, url):
    text = f"{source} {attachment_type} {url}".lower()
    if "voice" in text or "audio" in text:
        return "voice"
    if "story" in text:
        return "story"
    if "reel" in text:
        return "reel"
    if "post" in text or "media" in text or "instagram.com/p/" in text:
        return "post"
    return "media"


def attachment_label(kind):
    return {
        "story": "Story link",
        "post": "Post link",
        "reel": "Reel link",
        "voice": "Voice message",
        "media": "Media link",
    }.get(kind, "Media link")


def unique_attachment_rows(rows):
    result = []
    seen = set()
    for row in rows:
        url = row.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(row)
    return result


def append_attachment_links(text, attachments):
    base = (text or "").strip()
    lines = [base] if base else []
    for row in attachments:
        url = row.get("url")
        if url:
            lines.append(f"{attachment_label(row.get('kind'))}: {url}")
    return "\n".join(lines)


def first_string_from_keys(data, keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def social_post_media_query(media_id):
    return Q(media_id=media_id) | Q(story_share_id=media_id) | Q(webhook_story_id=media_id) | Q(webhook_story_id__contains=media_id)


def social_post_url_query(url):
    normalized = normalize_instagram_permalink(url)
    asset_id = media_id_from_url(url)
    is_instagram_permalink = "instagram.com/" in normalized
    query = Q()
    if normalized and is_instagram_permalink:
        query |= Q(permalink__startswith=normalized) | Q(webhook_story_url__startswith=normalized) | Q(catalog_items__instagram_story_url__startswith=normalized)
    if asset_id:
        query |= Q(media_id=asset_id) | Q(story_share_id=asset_id) | Q(webhook_story_id__contains=asset_id) | Q(webhook_story_url__contains=asset_id) | Q(permalink__contains=asset_id) | Q(catalog_items__instagram_story_url__contains=asset_id)
    return query


def social_post_by_media_or_url(media_id="", url="", branch=None):
    query = Q()
    if media_id:
        query |= social_post_media_query(str(media_id))
    if url:
        query |= social_post_url_query(url)
    if not query:
        return None
    queryset = SocialPost.objects.filter(query, is_active=True).distinct()
    if branch:
        queryset = queryset.filter(branch=branch)
    return queryset.order_by("-updated_at", "-created_at").first()


def catalog_item_url_query(url):
    normalized = normalize_instagram_permalink(url)
    asset_id = media_id_from_url(url)
    is_instagram_permalink = "instagram.com/" in normalized
    query = Q()
    if normalized and is_instagram_permalink:
        query |= Q(instagram_story_url__startswith=normalized) | Q(social_post__permalink__startswith=normalized) | Q(social_post__webhook_story_url__startswith=normalized)
    if asset_id:
        query |= Q(instagram_story_url__contains=asset_id) | Q(social_post__media_id=asset_id) | Q(social_post__story_share_id=asset_id) | Q(social_post__webhook_story_id__contains=asset_id) | Q(social_post__webhook_story_url__contains=asset_id)
    return query


def catalog_item_by_url(url="", branch=None):
    query = catalog_item_url_query(url)
    if not query:
        return None
    queryset = CatalogItem.objects.filter(query, status="available").select_related("branch", "social_post").distinct()
    if branch:
        queryset = queryset.filter(branch=branch)
    return queryset.order_by("-updated_at", "-created_at").first()


def social_post_type_from_url(url, fallback="post"):
    normalized = normalize_instagram_permalink(url)
    if "/stories/" in normalized:
        return "story"
    if "/reel/" in normalized:
        return "reel"
    if "/p/" in normalized:
        return "post"
    return fallback


def story_share_id_from_url(url):
    parsed = normalize_instagram_permalink(url)
    parts = [part for part in parsed.split("/") if part]
    if len(parts) >= 3 and parts[-3] == "stories":
        return parts[-1]
    return ""


def social_post_from_catalog_item(item, webhook_event=None, permalink=""):
    if not item:
        return None
    if item.social_post_id:
        return item.social_post
    permalink = permalink or item.instagram_story_url
    media_id = (webhook_event.media_id or webhook_event.story_id) if webhook_event else ""
    if not media_id or SocialPost.objects.filter(media_id=media_id).exists():
        media_id = f"catalog-item-{item.id}"
    post_type = social_post_type_from_url(permalink, "story" if webhook_event and webhook_event.event_type in ["story_reply", "story_send"] else "post")
    post = SocialPost.objects.create(
        branch=item.branch,
        post_type=post_type,
        media_id=media_id,
        permalink=permalink,
        story_share_id=story_share_id_from_url(permalink),
        webhook_story_id=(webhook_event.story_id or webhook_event.media_id) if webhook_event and post_type == "story" else "",
        webhook_story_url=webhook_event.story_url if webhook_event and post_type == "story" else "",
        title_uz=item.name_uz,
        title_ru=item.name_ru,
        description_uz=item.description_uz,
        description_ru=item.description_ru,
        price=item.price,
        image_url=item.image_url,
        is_active=True,
    )
    item.social_post = post
    item.save(update_fields=["social_post", "updated_at"])
    return post


def append_story_webhook_id(post, story_id):
    if not story_id:
        return False
    values = [value.strip() for value in (post.webhook_story_id or "").splitlines() if value.strip()]
    if story_id in values:
        return False
    values.append(story_id)
    post.webhook_story_id = "\n".join(values)
    return True


def nested_get(data, path):
    value = data
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) else ""


def first_story_attachment(message):
    for attachment in message.get("attachments", []) or []:
        payload = attachment.get("payload", {}) or {}
        if attachment.get("type") == "ig_story" or payload.get("story_media_id") or payload.get("story_media_url"):
            return payload
    return {}


def first_media_attachment(message):
    for attachment in message.get("attachments", []) or []:
        payload = attachment.get("payload", {}) or {}
        if attachment.get("type") == "ig_story" or payload.get("story_media_id"):
            continue
        media_url = first_string_from_keys(payload, ["url", "media_url", "permalink", "link", "share_url"])
        media_id = first_string_from_keys(payload, ["ig_post_media_id", "ig_reel_media_id", "reel_video_id", "reel_media_id", "media_id", "media_share_id", "media_product_id", "id", "source_id", "target_id"]) or media_id_from_url(media_url)
        if media_id or media_url:
            return {"id": media_id, "url": media_url, "type": attachment.get("type", ""), "payload": payload}
    return {}


def instagram_message_metadata(event, webhook_event=None):
    message = event.get("message", {}) or {}
    rows = []
    for attachment in message.get("attachments", []) or []:
        attachment_type = attachment.get("type", "")
        payload = attachment.get("payload", {}) or {}
        for url in urls_from_value(payload):
            rows.append({"kind": attachment_kind("instagram_attachment", attachment_type, url), "type": attachment_type, "url": url, "source": "instagram_attachment"})
    for source, value in [
        ("instagram_message", message),
        ("instagram_referral", event.get("referral") or message.get("referral") or {}),
        ("instagram_reply_to", message.get("reply_to") or {}),
    ]:
        for url in urls_from_value(value):
            rows.append({"kind": attachment_kind(source, "", url), "type": "", "url": url, "source": source})
    if webhook_event and webhook_event.story_url:
        rows.append({"kind": attachment_kind("instagram_webhook_event", webhook_event.event_type, webhook_event.story_url), "type": webhook_event.event_type, "url": webhook_event.story_url, "source": "instagram_webhook_event"})
    return {"attachments": unique_attachment_rows(rows)}


def save_instagram_webhook_event(payload, entry, event):
    message = event.get("message", {}) or {}
    referral = event.get("referral") or message.get("referral") or {}
    reply_to = message.get("reply_to") or {}
    story_attachment = first_story_attachment(message)
    media_attachment = first_media_attachment(message)
    extracted = flatten_interesting_payload(event)
    media_id = first_string_from_keys(referral, ["media_id", "source_id", "id"]) or first_string_from_keys(reply_to, ["media_id", "story_id", "id"]) or nested_get(reply_to, ["story", "id"]) or first_string_from_keys(story_attachment, ["story_media_id", "media_id", "id"]) or media_attachment.get("id", "")
    story_id = first_string_from_keys(reply_to, ["story_id", "id"]) or first_string_from_keys(referral, ["story_id"]) or nested_get(reply_to, ["story", "id"]) or first_string_from_keys(story_attachment, ["story_media_id", "story_id", "id"])
    story_url = first_string_from_keys(referral, ["source_url", "url", "link", "permalink"]) or first_string_from_keys(reply_to, ["url", "link", "permalink"]) or nested_get(reply_to, ["story", "url"]) or first_string_from_keys(story_attachment, ["story_media_url", "url", "media_url"]) or media_attachment.get("url", "")
    media_id = media_id or media_id_from_url(story_url)
    if story_attachment or reply_to:
        story_id = story_id or media_id_from_url(story_url)
    event_type = "story_send" if story_attachment and not reply_to else "story_reply" if reply_to or "story" in json.dumps(extracted, ensure_ascii=False).lower() else "media_send" if media_attachment else "message"
    try:
        saved = InstagramWebhookEvent.objects.create(
            event_type=event_type,
            sender_id=str(event.get("sender", {}).get("id", "")),
            recipient_id=str(event.get("recipient", {}).get("id", "")),
            message_id=str(message.get("mid", "")),
            text=str(message.get("text", "")),
            media_id=str(media_id or ""),
            story_id=str(story_id or ""),
            story_url=str(story_url or ""),
            postback_referral=referral or {},
            extracted=extracted,
            raw_payload={"entry": entry, "event": event, "payload": payload},
        )
        print(f"INSTAGRAM_WEBHOOK_EVENT id={saved.id} type={saved.event_type} sender={saved.sender_id} mid={saved.message_id} media_id={saved.media_id} story_id={saved.story_id} story_url={saved.story_url} extracted_keys={list(extracted.keys())}", flush=True)
        return saved
    except Exception as exc:
        print(f"INSTAGRAM_WEBHOOK_EVENT_SAVE_FAILED error={exc} event={json.dumps(event, ensure_ascii=False)}", flush=True)
        return None


def link_story_post_from_event(webhook_event, branch=None):
    if not webhook_event or webhook_event.event_type not in ["story_reply", "story_send"]:
        return None
    story_id = webhook_event.story_id or webhook_event.media_id
    if not story_id and webhook_event.story_url:
        story_id = media_id_from_url(webhook_event.story_url)
    exact = social_post_by_media_or_url(story_id, webhook_event.story_url, branch)
    if exact:
        updates = []
        if append_story_webhook_id(exact, story_id):
            updates.append("webhook_story_id")
        if webhook_event.story_url and exact.webhook_story_url != webhook_event.story_url:
            exact.webhook_story_url = webhook_event.story_url
            updates.append("webhook_story_url")
        if updates:
            exact.save(update_fields=updates + ["updated_at"])
        return exact
    story = None
    if webhook_event.story_url:
        try:
            story = find_active_story_by_media_url(webhook_event.story_url)
        except Exception as exc:
            print(f"INSTAGRAM_ACTIVE_STORY_LOOKUP_FAILED webhook_event_id={webhook_event.id} error={exc}", flush=True)
    if story:
        story_permalink = story.get("permalink", "")
        story_api_id = str(story.get("id", ""))
        post = social_post_by_media_or_url(story_api_id, story_permalink, branch)
        if not post:
            item = catalog_item_by_url(story_permalink, branch)
            post = social_post_from_catalog_item(item, webhook_event, story_permalink)
        if post:
            updates = []
            for value in [story_id, story_api_id]:
                if append_story_webhook_id(post, value):
                    updates.append("webhook_story_id")
            if webhook_event.story_url and post.webhook_story_url != webhook_event.story_url:
                post.webhook_story_url = webhook_event.story_url
                updates.append("webhook_story_url")
            if story_permalink and not post.permalink:
                post.permalink = story_permalink
                updates.append("permalink")
            if updates:
                post.save(update_fields=sorted(set(updates + ["updated_at"])))
            print(f"INSTAGRAM_STORY_LINKED social_post_id={post.id} story_id={story_id} active_story_id={story_api_id} webhook_event_id={webhook_event.id}", flush=True)
            return post
    if not story_id:
        return None
    queryset = SocialPost.objects.filter(post_type="story", is_active=True, webhook_story_id="")
    if branch:
        queryset = queryset.filter(branch=branch)
    candidates = list(queryset.order_by("-created_at")[:2])
    if len(candidates) != 1:
        return None
    post = candidates[0]
    post.webhook_story_id = story_id
    post.webhook_story_url = webhook_event.story_url
    post.save(update_fields=["webhook_story_id", "webhook_story_url", "updated_at"])
    print(f"INSTAGRAM_STORY_LINKED social_post_id={post.id} story_id={story_id} webhook_event_id={webhook_event.id}", flush=True)
    return post


def link_media_post_from_event(webhook_event):
    if not webhook_event or webhook_event.event_type != "media_send":
        return None
    media_id = webhook_event.media_id
    if media_id:
        exact = social_post_by_media_or_url(media_id, "", None)
        if exact:
            return exact
        media = find_media_by_id(media_id)
        if media:
            exact = social_post_by_media_or_url(media_id, media.get("permalink", ""), None)
            if exact:
                if exact.media_id != media_id:
                    exact.media_id = media_id
                    exact.save(update_fields=["media_id", "updated_at"])
                return exact
    if webhook_event.story_url:
        exact = social_post_by_media_or_url(media_id, webhook_event.story_url, None)
        if not exact:
            item = catalog_item_by_url(webhook_event.story_url, None)
            exact = social_post_from_catalog_item(item, webhook_event, webhook_event.story_url)
        if exact and media_id and exact.media_id != media_id and not SocialPost.objects.filter(media_id=media_id).exclude(pk=exact.pk).exists():
            exact.media_id = media_id
            exact.save(update_fields=["media_id", "updated_at"])
        return exact
    return None


def resolve_instagram_event(payload):
    entries = payload.get("entry", [])
    results = []
    for entry in entries:
        for event in entry.get("messaging", []):
            webhook_event = save_instagram_webhook_event(payload, entry, event)
            sender_id = event.get("sender", {}).get("id")
            integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
            own_ids = {value for value in [integration.instagram_account_id, integration.instagram_business_id, settings.INSTAGRAM_ACCOUNT_ID] if value}
            message = event.get("message", {})
            text = message.get("text")
            story_attachment = first_story_attachment(message)
            media_attachment = first_media_attachment(message)
            story_text = "Mijoz Instagram storyni directga yubordi." if story_attachment else ""
            media_text = "Mijoz Instagram post/reelni directga yubordi." if media_attachment else ""
            message_metadata = instagram_message_metadata(event, webhook_event)
            message_text = append_attachment_links(text or story_text or media_text, message_metadata.get("attachments", []))
            if not sender_id or sender_id in own_ids or not message_text or message.get("is_echo"):
                continue
            branch = getattr(SocialPost.objects.filter(is_active=True).first(), "branch", None) or Branch.objects.filter(is_active=True).first() or Branch.objects.first()
            if not branch:
                continue
            referral = event.get("referral") or message.get("referral") or {}
            media_id = referral.get("media_id") or referral.get("source_id") or (webhook_event.story_id if webhook_event else "") or (webhook_event.media_id if webhook_event else "")
            post = social_post_by_media_or_url(media_id, "", branch)
            if not post:
                post = link_story_post_from_event(webhook_event, branch)
            if not post:
                post = link_media_post_from_event(webhook_event)
            customer, _ = Customer.objects.get_or_create(instagram_user_id=sender_id, defaults={"branch": branch})
            conversation = Conversation.objects.filter(customer=customer, status__in=["ai", "operator"]).first()
            if not conversation:
                conversation = Conversation.objects.create(customer=customer, branch=customer.branch or branch, social_post=post)
            elif post and conversation.social_post_id != post.id:
                conversation.social_post = post
                conversation.branch = post.branch
                conversation.save(update_fields=["social_post", "branch", "updated_at"])
            elif (story_attachment or media_attachment or message_metadata.get("attachments")) and not post and conversation.social_post_id:
                conversation.social_post = None
                conversation.save(update_fields=["social_post", "updated_at"])
            if (story_attachment or media_attachment or message_metadata.get("attachments")) and not post:
                message_text = append_attachment_links(f"{message_text}\nTizim izohi: yuborilgan Instagram media bazadagi story/post/reel katalogiga bog‘lanmagan.", [])
            saved_message = ingest_customer_message(conversation, message_text, message.get("mid", ""), message_metadata)
            if saved_message:
                results.append({"conversation_id": conversation.id, "message_id": saved_message.id, "recipient_id": sender_id})
    return results


def telegram_media_file_id(message):
    if message.get("voice"):
        return "voice", message["voice"].get("file_id", "")
    if message.get("audio"):
        return "audio", message["audio"].get("file_id", "")
    if message.get("video"):
        return "video", message["video"].get("file_id", "")
    if message.get("video_note"):
        return "video_note", message["video_note"].get("file_id", "")
    if message.get("document"):
        return "document", message["document"].get("file_id", "")
    if message.get("photo"):
        photo = sorted(message["photo"], key=lambda row: row.get("file_size", 0))[-1]
        return "photo", photo.get("file_id", "")
    return "", ""


def telegram_message_metadata(message):
    rows = []
    for url in urls_from_value(message):
        rows.append({"kind": attachment_kind("telegram_message", "", url), "type": "", "url": url, "source": "telegram_message"})
    media_type, file_id = telegram_media_file_id(message)
    if file_id:
        row = {"kind": attachment_kind("telegram_file", media_type, ""), "type": media_type, "file_id": file_id, "source": "telegram_file"}
        try:
            file_url = telegram_file_url(file_id)
        except Exception as exc:
            print(f"TELEGRAM_FILE_URL_FAILED file_id={file_id} error={exc}", flush=True)
            file_url = ""
        if file_url:
            row["url"] = file_url
            rows.append(row)
        else:
            rows.append(row)
    return {"attachments": unique_attachment_rows(rows)}


def resolve_telegram_update(payload):
    message = payload.get("message") or payload.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    metadata = telegram_message_metadata(message)
    message_text = append_attachment_links(text, metadata.get("attachments", []))
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    if not chat_id or not user_id or not message_text:
        return []
    branch = getattr(SocialPost.objects.filter(is_active=True).first(), "branch", None) or Branch.objects.filter(is_active=True).first() or Branch.objects.first()
    if not branch:
        return []
    external_id = f"telegram:{user_id}"
    defaults = {"branch": branch, "language": "uz"}
    customer, created = Customer.objects.get_or_create(instagram_user_id=external_id, defaults=defaults)
    conversation = Conversation.objects.filter(customer=customer, status__in=["ai", "operator"]).first()
    if not conversation:
        conversation = Conversation.objects.create(customer=customer, branch=customer.branch or branch)
    message_id = message.get("message_id", "")
    saved_message = ingest_customer_message(conversation, message_text, f"telegram:{chat_id}:{message_id}", metadata)
    if not saved_message:
        return []
    return [{"conversation_id": conversation.id, "message_id": saved_message.id, "chat_id": chat_id}]
