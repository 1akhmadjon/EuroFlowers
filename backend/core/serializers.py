from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .branching import default_branch
from .models import AISettings, AuditLog, Branch, BusinessSettings, CatalogComposition, CatalogItem, Conversation, Customer, Flower, FlowerVariant, InstagramSettings, InstagramWebhookEvent, IntegrationSettings, Lead, LeadCatalogUsage, LeadPackagingUsage, LeadStatus, LeadStockUsage, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement, UserProfile


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = "__all__"


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ["role", "language"]


class PagePermissionSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="get_page_display", read_only=True)

    class Meta:
        model = PagePermission
        fields = ["id", "user", "page", "label", "can_view", "can_control", "created_at", "updated_at"]

    def validate(self, attrs):
        request = self.context.get("request")
        requester_role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
        page = attrs.get("page") or getattr(self.instance, "page", "")
        if requester_role != "developer" and page in PagePermission.DEVELOPER_ONLY_PAGES:
            raise serializers.ValidationError({"page": "Bu permission faqat developer uchun"})
        user = attrs.get("user") or getattr(self.instance, "user", None)
        target_role = getattr(getattr(user, "profile", None), "role", None)
        if target_role != "developer" and page in PagePermission.DEVELOPER_ONLY_PAGES:
            raise serializers.ValidationError({"page": "Bu permission faqat developer userga beriladi"})
        return attrs


class PagePermissionInputSerializer(serializers.Serializer):
    page = serializers.ChoiceField(choices=PagePermission.PAGE_CHOICES)
    can_view = serializers.BooleanField(default=False)
    can_control = serializers.BooleanField(default=False)


def permission_matrix(user) -> list[dict[str, Any]]:
    rows = {row.page: row for row in user.page_permissions.all()} if getattr(user, "id", None) else {}
    data = []
    role = getattr(getattr(user, "profile", None), "role", None)
    for page, label in PagePermission.PAGE_CHOICES:
        if role != "developer" and page in PagePermission.DEVELOPER_ONLY_PAGES:
            continue
        row = rows.get(page)
        default_access = bool(user.is_superuser and not row)
        can_view = True if role == "developer" else bool(row.can_view if row else default_access)
        can_control = True if role == "developer" else bool(row.can_control if row else default_access)
        data.append({"page": page, "label": label, "can_view": can_view, "can_control": can_control})
    return data


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "is_active", "profile", "permissions"]

    def get_permissions(self, obj) -> list[dict[str, Any]]:
        return permission_matrix(obj)


class UserWriteSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=UserProfile.ROLE_CHOICES, write_only=True, required=False)
    language = serializers.ChoiceField(choices=[("uz", "O‘zbek"), ("ru", "Русский")], write_only=True, required=False)
    permissions = PagePermissionInputSerializer(many=True, required=False)
    password = serializers.CharField(write_only=True, required=False, min_length=6)
    profile = UserProfileSerializer(read_only=True)
    permission_matrix = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "password", "is_active", "role", "language", "permissions", "profile", "permission_matrix"]

    def get_permission_matrix(self, obj) -> list[dict[str, Any]]:
        return permission_matrix(obj)

    def validate(self, attrs):
        request = self.context.get("request")
        requester_role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
        permissions = attrs.get("permissions") or []
        if requester_role != "developer" and any(row["page"] in PagePermission.DEVELOPER_ONLY_PAGES for row in permissions):
            raise serializers.ValidationError({"permissions": "AI settings, integrations va audit log permissionlari faqat developer tomonidan beriladi"})
        return attrs

    def save_permissions(self, user, permissions):
        if permissions is None:
            return
        request = self.context.get("request")
        requester_role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
        target_role = getattr(getattr(user, "profile", None), "role", None)
        if requester_role != "developer" and any(row["page"] in PagePermission.DEVELOPER_ONLY_PAGES for row in permissions):
            raise serializers.ValidationError({"permissions": "AI settings, integrations va audit log permissionlari faqat developer tomonidan beriladi"})
        seen = set()
        for row in permissions:
            if target_role != "developer" and row["page"] in PagePermission.DEVELOPER_ONLY_PAGES:
                continue
            seen.add(row["page"])
            PagePermission.objects.update_or_create(user=user, page=row["page"], defaults={"can_view": row["can_view"] or row["can_control"], "can_control": row["can_control"]})
        queryset = PagePermission.objects.filter(user=user).exclude(page__in=seen)
        if target_role == "developer":
            queryset.delete()
        else:
            queryset.exclude(page__in=PagePermission.DEVELOPER_ONLY_PAGES).delete()
            PagePermission.objects.filter(user=user, page__in=PagePermission.DEVELOPER_ONLY_PAGES).delete()

    def create(self, validated_data):
        role = validated_data.pop("role", "operator")
        language = validated_data.pop("language", "uz")
        permissions = validated_data.pop("permissions", None)
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        profile = UserProfile.objects.create(user=user, role=role, language=language)
        profile.branches.set([default_branch()])
        self.save_permissions(user, permissions)
        return user

    def update(self, instance, validated_data):
        role = validated_data.pop("role", None)
        language = validated_data.pop("language", None)
        permissions = validated_data.pop("permissions", None)
        password = validated_data.pop("password", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            instance.set_password(password)
        instance.save()
        profile, _ = UserProfile.objects.get_or_create(user=instance)
        if role is not None:
            profile.role = role
        if language is not None:
            profile.language = language
        profile.save()
        self.save_permissions(instance, permissions)
        return instance


class FlowerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Flower
        fields = "__all__"


class FlowerVariantSerializer(serializers.ModelSerializer):
    flower_detail = FlowerSerializer(source="flower", read_only=True)
    class Meta:
        model = FlowerVariant
        fields = "__all__"


class StockBatchSerializer(serializers.ModelSerializer):
    variant_detail = FlowerVariantSerializer(source="variant", read_only=True)
    remaining_bunches = serializers.IntegerField(read_only=True)
    stock_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    height_label = serializers.CharField(read_only=True)

    class Meta:
        model = StockBatch
        exclude = ["branch"]

    def to_internal_value(self, data):
        data = data.copy()
        if getattr(self, "partial", False):
            for key, value in list(data.items()):
                if value == "":
                    data.pop(key)
        if not data.get("height_cm") and data.get("height_from_cm"):
            data["height_cm"] = data["height_from_cm"]
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        height_cm = attrs.get("height_cm") or getattr(self.instance, "height_cm", None)
        height_from = attrs.get("height_from_cm")
        height_to = attrs.get("height_to_cm")
        if height_from and height_to and height_from > height_to:
            raise serializers.ValidationError({"height_to_cm": "Bo‘y oralig‘i noto‘g‘ri: height_to_cm height_from_cm dan katta yoki teng bo‘lishi kerak."})
        if height_from and not height_to:
            attrs["height_to_cm"] = height_from
        if height_to and not height_from:
            attrs["height_from_cm"] = height_to
        if not height_from and not height_to and height_cm:
            attrs.setdefault("height_from_cm", height_cm)
            attrs.setdefault("height_to_cm", height_cm)
        return attrs

    def create(self, validated_data):
        validated_data.setdefault("branch", default_branch())
        return super().create(validated_data)


class StockMovementSerializer(serializers.ModelSerializer):
    batch_detail = StockBatchSerializer(source="batch", read_only=True)
    performed_by_detail = UserSerializer(source="performed_by", read_only=True)
    class Meta:
        model = StockMovement
        fields = "__all__"
        read_only_fields = ["performed_by"]


class PackagingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Packaging
        exclude = ["branch"]

    def create(self, validated_data):
        validated_data.setdefault("branch", default_branch())
        return super().create(validated_data)


class PackagingMovementSerializer(serializers.ModelSerializer):
    packaging_detail = PackagingSerializer(source="packaging", read_only=True)
    performed_by_detail = UserSerializer(source="performed_by", read_only=True)

    class Meta:
        model = PackagingMovement
        fields = "__all__"
        read_only_fields = ["performed_by"]


class CatalogCompositionSerializer(serializers.ModelSerializer):
    batch_detail = StockBatchSerializer(source="stock_batch", read_only=True)
    class Meta:
        model = CatalogComposition
        fields = ["id", "stock_batch", "batch_detail", "quantity_stems", "quantity_bunches"]


class SocialPostCatalogItemSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    composition = CatalogCompositionSerializer(many=True, required=False)

    class Meta:
        model = CatalogItem
        fields = ["id", "name_uz", "name_ru", "description_uz", "description_ru", "arrangement_type", "height_cm", "diameter_cm", "price", "florist_fee", "status", "image_url", "instagram_story_url", "quantity_total", "quantity_sold", "quantity_stock_deducted", "composition"]
        read_only_fields = ["quantity_sold", "quantity_stock_deducted"]


def catalog_stock_error(batch, needed):
    remaining = batch.remaining_stems
    missing = max(needed - remaining, 0)
    variant = batch.variant
    flower_name = " ".join(part for part in [variant.flower.name_uz, variant.name_uz, variant.color_uz] if part).strip()
    return (
        "Katalogni saqlash uchun sklad qoldig'i yetarli emas.\n"
        f"Gul: {flower_name}\n"
        f"Partiya: {batch.batch_number}\n"
        f"Kerak: {needed} dona\n"
        f"Bor: {remaining} dona\n"
        f"Yetmayapti: {missing} dona"
    )


class SocialPostSerializer(serializers.ModelSerializer):
    reply_count = serializers.IntegerField(read_only=True)
    lead_count = serializers.IntegerField(read_only=True)
    leads = serializers.SerializerMethodField()
    catalog_items = SocialPostCatalogItemSerializer(many=True, required=False)
    class Meta:
        model = SocialPost
        exclude = ["branch"]
        extra_kwargs = {"media_id": {"required": False}, "post_type": {"required": False}}

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_leads(self, obj):
        rows = obj.leads.select_related("customer").prefetch_related("catalog_usage__catalog_item").order_by("-created_at")[:50]
        return [{
            "id": row.id,
            "status": row.status,
            "customer": row.customer_id,
            "customer_name": row.customer.name,
            "customer_phone": row.customer.phone,
            "customer_instagram_user_id": row.customer.instagram_user_id,
            "request_uz": row.request_uz,
            "request_ru": row.request_ru,
            "arrangement_type": row.arrangement_type,
            "estimated_price": str(row.estimated_price) if row.estimated_price is not None else None,
            "source": row.source,
            "created_at": row.created_at.isoformat(),
            "catalog_items": [{"id": usage.catalog_item_id, "name_uz": usage.catalog_item.name_uz, "quantity": usage.quantity} for usage in row.catalog_usage.all()],
        } for row in rows]

    def _fill_story_share_fields(self, validated_data):
        permalink = validated_data.get("permalink") or getattr(self.instance, "permalink", "")
        if not permalink:
            return validated_data
        parsed = urlparse(permalink)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "stories":
            validated_data.setdefault("instagram_username", parts[1])
            validated_data.setdefault("story_share_id", parts[2])
            validated_data.setdefault("post_type", "story")
            if not validated_data.get("media_id"):
                validated_data["media_id"] = f"story-share-{parts[2]}"
            if not validated_data.get("webhook_story_id"):
                try:
                    from .platform_services import find_active_story_by_permalink
                    story = find_active_story_by_permalink(permalink)
                    if story:
                        validated_data["webhook_story_id"] = story.get("id", "")
                        validated_data["webhook_story_url"] = story.get("media_url", "")
                        if story.get("media_url") and len(story["media_url"]) <= 200 and not validated_data.get("image_url"):
                            validated_data["image_url"] = story["media_url"]
                except Exception:
                    pass
        elif not validated_data.get("media_id"):
            try:
                from .platform_services import find_media_by_permalink
                media = find_media_by_permalink(permalink)
                if media:
                    validated_data["media_id"] = media.get("id", "")
                    media_type = (media.get("media_type") or "").lower()
                    if media_type == "video":
                        validated_data.setdefault("post_type", "reel")
                    else:
                        validated_data.setdefault("post_type", "post")
                    media_url = media.get("thumbnail_url") or media.get("media_url")
                    if media_url and len(media_url) <= 200 and not validated_data.get("image_url"):
                        validated_data["image_url"] = media_url
            except Exception:
                pass
            if not validated_data.get("media_id") and parts:
                validated_data["media_id"] = f"post-link-{parts[-1]}"
                validated_data.setdefault("post_type", "reel" if parts[0] in ["reel", "reels"] else "post")
        return validated_data

    def _check_unique_media_id(self, validated_data):
        media_id = validated_data.get("media_id")
        if media_id:
            queryset = SocialPost.objects.filter(media_id=media_id)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            existing = queryset.first()
            if existing:
                raise serializers.ValidationError({"media_id": f"Bu Instagram media allaqachon SocialPost id={existing.id} da bor."})
        permalink = validated_data.get("permalink") or getattr(self.instance, "permalink", "")
        normalized = permalink.split("?")[0].rstrip("/") if permalink else ""
        if normalized:
            queryset = SocialPost.objects.filter(permalink__startswith=normalized)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            existing = queryset.first()
            if existing:
                raise serializers.ValidationError({"permalink": f"Bu Instagram link allaqachon SocialPost id={existing.id} da bor."})

    def _validate_catalog_items(self, post_data, catalog_items):
        branch = post_data.get("branch") or getattr(self.instance, "branch", None)
        for item in catalog_items:
            quantity_total = item.get("quantity_total", 1)
            for row in item.get("composition") or []:
                batch = row["stock_batch"]
                if branch and batch.branch_id != branch.id:
                    raise serializers.ValidationError({"catalog_items": f"{batch.batch_number} boshqa filialga tegishli"})
                needed = row["quantity_stems"] * quantity_total
                if batch.remaining_stems < needed:
                    detail = catalog_stock_error(batch, needed)
                    raise serializers.ValidationError({"detail": detail, "catalog_items": detail})

    def validate(self, attrs):
        attrs.setdefault("branch", default_branch())
        catalog_items = attrs.get("catalog_items") or []
        self._validate_catalog_items(attrs, catalog_items)
        return attrs

    def _sync_catalog_items(self, post, catalog_items):
        for item_data in catalog_items:
            has_composition = "composition" in item_data
            composition = item_data.pop("composition", None)
            item_id = item_data.pop("id", None)
            if not item_data.get("image_url") and post.image_url:
                item_data["image_url"] = post.image_url
            if not item_data.get("instagram_story_url") and post.post_type == "story" and post.permalink:
                item_data["instagram_story_url"] = post.permalink
            if item_id:
                item = post.catalog_items.get(id=item_id)
                for key, value in item_data.items():
                    setattr(item, key, value)
                item.branch = post.branch
                item.social_post = post
                item.save()
            else:
                item = CatalogItem.objects.create(branch=post.branch, social_post=post, **item_data)
            if has_composition:
                item.composition.all().delete()
                CatalogComposition.objects.bulk_create([CatalogComposition(catalog_item=item, **row) for row in composition])

    def create(self, validated_data):
        catalog_items = validated_data.pop("catalog_items", [])
        validated_data = self._fill_story_share_fields(validated_data)
        self._check_unique_media_id(validated_data)
        with transaction.atomic():
            post = super().create(validated_data)
            self._sync_catalog_items(post, catalog_items)
        return post

    def update(self, instance, validated_data):
        catalog_items = validated_data.pop("catalog_items", None)
        validated_data = self._fill_story_share_fields(validated_data)
        self._check_unique_media_id(validated_data)
        with transaction.atomic():
            post = super().update(instance, validated_data)
            if catalog_items is not None:
                self._sync_catalog_items(post, catalog_items)
        return post


class CatalogItemSerializer(serializers.ModelSerializer):
    composition = CatalogCompositionSerializer(many=True, required=False)
    social_post_detail = SocialPostSerializer(source="social_post", read_only=True)
    class Meta:
        model = CatalogItem
        exclude = ["branch"]
        read_only_fields = ["created_by", "sold_at", "stock_deducted_at"]

    def validate(self, attrs):
        attrs.setdefault("branch", getattr(self.instance, "branch", None) or default_branch())
        composition = attrs.get("composition")
        quantity_total = attrs.get("quantity_total", getattr(self.instance, "quantity_total", 1))
        if composition is None and self.instance:
            composition = [{"stock_batch": row.stock_batch, "quantity_stems": row.quantity_stems} for row in self.instance.composition.select_related("stock_batch")]
        if composition:
            for row in composition:
                batch = row["stock_batch"]
                needed = row["quantity_stems"] * quantity_total
                if batch.remaining_stems < needed:
                    detail = catalog_stock_error(batch, needed)
                    raise serializers.ValidationError({"detail": detail, "composition": detail})
        quantity_sold = getattr(self.instance, "quantity_sold", 0)
        if quantity_total < quantity_sold:
            raise serializers.ValidationError({"quantity_total": "Umumiy son sotilgan sondan kam bo‘lishi mumkin emas"})
        return attrs

    def create(self, validated_data):
        composition = validated_data.pop("composition", [])
        validated_data = self._sync_social_post_image_data(validated_data)
        item = CatalogItem.objects.create(**validated_data)
        CatalogComposition.objects.bulk_create([CatalogComposition(catalog_item=item, **row) for row in composition])
        self._sync_social_post_image(item)
        return item

    def update(self, instance, validated_data):
        composition = validated_data.pop("composition", None)
        validated_data = self._sync_social_post_image_data(validated_data)
        instance = super().update(instance, validated_data)
        if composition is not None:
            instance.composition.all().delete()
            CatalogComposition.objects.bulk_create([CatalogComposition(catalog_item=instance, **row) for row in composition])
        self._sync_social_post_image(instance)
        return instance

    def _sync_social_post_image_data(self, validated_data):
        social_post = validated_data.get("social_post") or getattr(self.instance, "social_post", None)
        if social_post and not validated_data.get("image_url") and getattr(social_post, "image_url", ""):
            validated_data["image_url"] = social_post.image_url
        return validated_data

    def _sync_social_post_image(self, item):
        if item.social_post_id and item.image_url and item.social_post.image_url != item.image_url:
            item.social_post.image_url = item.image_url
            item.social_post.save(update_fields=["image_url", "updated_at"])


class CustomerSerializer(serializers.ModelSerializer):
    masked_phone = serializers.CharField(read_only=True)
    leads_count = serializers.IntegerField(source="leads.count", read_only=True)
    purchases_count = serializers.IntegerField(read_only=True)
    total_spent = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    class Meta:
        model = Customer
        exclude = ["branch"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = "__all__"


class ConversationSerializer(serializers.ModelSerializer):
    customer_detail = CustomerSerializer(source="customer", read_only=True)
    messages = MessageSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    source_label = serializers.SerializerMethodField()
    ai_is_active = serializers.SerializerMethodField()
    class Meta:
        model = Conversation
        exclude = ["branch"]

    def to_representation(self, instance):
        if instance.ai_paused_until and instance.ai_paused_until <= timezone.now():
            instance.ai_paused_until = None
            instance.ai_pause_reason = ""
            instance.save(update_fields=["ai_paused_until", "ai_pause_reason", "updated_at"])
        return super().to_representation(instance)

    def get_last_message(self, obj) -> dict[str, Any] | None:
        message = obj.messages.last()
        return MessageSerializer(message).data if message else None

    def get_ai_is_active(self, obj) -> bool:
        return obj.status == "ai" and not (obj.ai_paused_until and obj.ai_paused_until > timezone.now())

    def get_source(self, obj) -> str:
        external_id = obj.customer.instagram_user_id if obj.customer_id else ""
        if external_id.startswith("telegram:"):
            return "telegram"
        if external_id.startswith("miniapp:"):
            return "mini_app"
        return "instagram"

    def get_source_label(self, obj) -> str:
        labels = {"telegram": "Telegram", "mini_app": "Mini app", "instagram": "Instagram"}
        return labels[self.get_source(obj)]


class LeadPackagingUsageInputSerializer(serializers.Serializer):
    packaging = serializers.PrimaryKeyRelatedField(queryset=Packaging.objects.all())
    quantity = serializers.IntegerField(min_value=1, default=1)


class LeadCatalogUsageInputSerializer(serializers.Serializer):
    catalog_item = serializers.PrimaryKeyRelatedField(queryset=CatalogItem.objects.all())
    quantity = serializers.IntegerField(min_value=1, default=1)


class LeadMoveSerializer(serializers.Serializer):
    status = serializers.CharField(required=False, max_length=40)
    before = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all(), required=False, allow_null=True)
    after = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all(), required=False, allow_null=True)
    sort_order = serializers.DecimalField(max_digits=20, decimal_places=6, required=False)


class LeadColumnReorderSerializer(serializers.Serializer):
    status = serializers.CharField(max_length=40)
    lead_ids = serializers.ListField(child=serializers.IntegerField(min_value=1))


class LeadSerializer(serializers.ModelSerializer):
    customer_detail = CustomerSerializer(source="customer", read_only=True)
    status_detail = serializers.SerializerMethodField()
    stock_usage = serializers.SerializerMethodField()
    packaging_usage = serializers.SerializerMethodField()
    catalog_usage = serializers.SerializerMethodField()
    stock_usage_input = CatalogCompositionSerializer(many=True, write_only=True, required=False)
    packaging_usage_input = LeadPackagingUsageInputSerializer(many=True, write_only=True, required=False)
    catalog_usage_input = LeadCatalogUsageInputSerializer(many=True, write_only=True, required=False)
    customer_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=160)
    customer_phone = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=30)
    customer_instagram_user_id = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=100)

    class Meta:
        model = Lead
        exclude = ["branch"]
        extra_kwargs = {"customer": {"required": False}}

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_status_detail(self, obj):
        status = LeadStatus.objects.filter(key=obj.status).first()
        return LeadStatusSerializer(status).data if status else None

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_stock_usage(self, obj):
        return [{
            "id": row.id,
            "stock_batch": row.stock_batch_id,
            "batch_detail": StockBatchSerializer(row.stock_batch).data,
            "quantity_stems": row.quantity_stems,
            "quantity_bunches": row.quantity_bunches,
        } for row in obj.stock_usage.select_related("stock_batch__variant__flower").all()]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_packaging_usage(self, obj):
        return [{
            "id": row.id,
            "packaging": row.packaging_id,
            "packaging_detail": PackagingSerializer(row.packaging).data,
            "quantity": row.quantity,
        } for row in obj.packaging_usage.select_related("packaging").all()]

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_catalog_usage(self, obj):
        return [{
            "id": row.id,
            "catalog_item": row.catalog_item_id,
            "catalog_detail": CatalogItemSerializer(row.catalog_item).data,
            "quantity": row.quantity,
        } for row in obj.catalog_usage.select_related("catalog_item__branch", "catalog_item__social_post").prefetch_related("catalog_item__composition").all()]

    def validate(self, attrs):
        customer = attrs.get("customer") or getattr(self.instance, "customer", None)
        if not customer and not attrs.get("customer_name"):
            raise serializers.ValidationError({"customer_name": "Yangi lead uchun mijoz ismi kerak"})
        if not customer and not attrs.get("customer_phone"):
            raise serializers.ValidationError({"customer_phone": "Yangi lead uchun telefon kerak"})
        status_value = attrs.get("status")
        if status_value and not LeadStatus.objects.filter(key=status_value).exists():
            raise serializers.ValidationError({"status": "Bunday lead statusi mavjud emas"})
        delivery_at = attrs.get("delivery_at")
        if "delivery_at" in attrs and delivery_at and "recall_at" not in attrs:
            attrs["recall_at"] = delivery_at - timedelta(hours=1)
        return attrs

    def _customer_from_attrs(self, attrs):
        customer = attrs.pop("customer", None)
        name = attrs.pop("customer_name", "")
        phone = attrs.pop("customer_phone", "")
        external_id = attrs.pop("customer_instagram_user_id", "")
        if customer:
            return customer
        from .services import normalize_phone
        normalized = normalize_phone(phone) or phone
        branch = attrs.get("branch")
        if not branch:
            branch = default_branch()
            attrs["branch"] = branch
        customer = Customer.objects.filter(phone=normalized).first() if normalized else None
        if customer:
            updates = []
            if name and not customer.name:
                customer.name = name
                updates.append("name")
            if branch and not customer.branch_id:
                customer.branch = branch
                updates.append("branch")
            if updates:
                customer.save(update_fields=updates + ["updated_at"])
            return customer
        external = external_id or f"manual:{normalized or name}"
        customer = Customer.objects.create(name=name, phone=normalized, branch=branch, language="uz", instagram_user_id=external)
        return customer

    def _save_usage(self, lead, stock_rows=None, packaging_rows=None, catalog_rows=None):
        if stock_rows is not None:
            lead.stock_usage.all().delete()
            LeadStockUsage.objects.bulk_create([LeadStockUsage(lead=lead, stock_batch=row["stock_batch"], quantity_stems=row["quantity_stems"], quantity_bunches=row.get("quantity_bunches") or 0) for row in stock_rows])
        if packaging_rows is not None:
            lead.packaging_usage.all().delete()
            LeadPackagingUsage.objects.bulk_create([LeadPackagingUsage(lead=lead, packaging=row["packaging"], quantity=row.get("quantity", 1)) for row in packaging_rows])
        if catalog_rows is not None:
            lead.catalog_usage.all().delete()
            LeadCatalogUsage.objects.bulk_create([LeadCatalogUsage(lead=lead, catalog_item=row["catalog_item"], quantity=row.get("quantity", 1)) for row in catalog_rows])

    def create(self, validated_data):
        stock_rows = validated_data.pop("stock_usage_input", None)
        packaging_rows = validated_data.pop("packaging_usage_input", None)
        catalog_rows = validated_data.pop("catalog_usage_input", None)
        validated_data.setdefault("branch", default_branch())
        customer = self._customer_from_attrs(validated_data)
        lead = Lead.objects.create(customer=customer, **validated_data)
        self._save_usage(lead, stock_rows, packaging_rows, catalog_rows)
        return lead

    def update(self, instance, validated_data):
        stock_rows = validated_data.pop("stock_usage_input", None)
        packaging_rows = validated_data.pop("packaging_usage_input", None)
        catalog_rows = validated_data.pop("catalog_usage_input", None)
        validated_data.pop("customer_name", None)
        validated_data.pop("customer_phone", None)
        validated_data.pop("customer_instagram_user_id", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()
        self._save_usage(instance, stock_rows, packaging_rows, catalog_rows)
        return instance


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        exclude = ["branch"]


class LeadStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeadStatus
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at"]


class AuditLogSerializer(serializers.ModelSerializer):
    user_detail = UserSerializer(source="user", read_only=True)
    class Meta:
        model = AuditLog
        fields = "__all__"


class BusinessSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessSettings
        fields = "__all__"


class AISettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AISettings
        fields = "__all__"


class IntegrationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegrationSettings
        fields = "__all__"


class InstagramSettingsSerializer(serializers.ModelSerializer):
    connected = serializers.SerializerMethodField()
    account_id = serializers.SerializerMethodField()
    has_access_token = serializers.SerializerMethodField()

    class Meta:
        model = InstagramSettings
        fields = ["id", "connected", "account_id", "account_username", "has_access_token", "token_expires_at", "auto_reply_dm", "auto_reply_post_reply", "auto_reply_story_reply", "created_at", "updated_at"]

    def get_connected(self, obj) -> bool:
        return bool(self.context.get("instagram_access_token") and self.context.get("instagram_account_id"))

    def get_account_id(self, obj) -> str:
        return self.context.get("instagram_account_id", "")

    def get_has_access_token(self, obj) -> bool:
        return bool(self.context.get("instagram_access_token"))


class InstagramWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstagramWebhookEvent
        fields = "__all__"


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class UploadResponseSerializer(serializers.Serializer):
    url = serializers.URLField()
    path = serializers.CharField()


class TextRequestSerializer(serializers.Serializer):
    text = serializers.CharField()


class AIPauseRequestSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(required=False, min_value=1)
    paused_until = serializers.DateTimeField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if not attrs.get("minutes") and not attrs.get("paused_until"):
            raise serializers.ValidationError({"detail": "minutes yoki paused_until yuboring"})
        return attrs


class SendResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    text = serializers.CharField()


class SimulateResponseSerializer(serializers.Serializer):
    reply = serializers.CharField(allow_null=True)


class MovementRequestSerializer(serializers.Serializer):
    movement_type = serializers.ChoiceField(choices=StockMovement.TYPE_CHOICES)
    quantity_stems = serializers.IntegerField(min_value=1)
    quantity_bunches = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    reason = serializers.CharField(required=False, allow_blank=True)


class PackagingMovementRequestSerializer(serializers.Serializer):
    movement_type = serializers.ChoiceField(choices=PackagingMovement.TYPE_CHOICES)
    quantity = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["movement_type"] == "adjustment":
            if attrs["quantity"] == 0:
                raise serializers.ValidationError({"quantity": "0 bo‘lmasligi kerak"})
        elif attrs["quantity"] < 1:
            raise serializers.ValidationError({"quantity": "Musbat son kiriting"})
        return attrs


class MiniAppInitSerializer(serializers.Serializer):
    init_data = serializers.CharField(required=False, allow_blank=True)


class MiniAppLineSerializer(serializers.Serializer):
    stock_batch = serializers.IntegerField(required=False)
    catalog_item = serializers.IntegerField(required=False)
    quantity_stems = serializers.IntegerField(required=False, min_value=1)
    quantity = serializers.IntegerField(required=False, min_value=1)


class MiniAppQuoteSerializer(serializers.Serializer):
    init_data = serializers.CharField(required=False, allow_blank=True)
    arrangement_type = serializers.ChoiceField(choices=["bouquet", "basket", "catalog"])
    items = MiniAppLineSerializer(many=True, required=False)
    packaging = serializers.IntegerField(required=False, allow_null=True)
    request_text = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        arrangement_type = attrs.get("arrangement_type")
        items = attrs.get("items") or []
        request_text = (attrs.get("request_text") or "").strip()
        if arrangement_type == "catalog":
            if not items:
                raise serializers.ValidationError({"items": "Katalog buyurtmasi uchun catalog_item yuboring"})
            if any(not row.get("catalog_item") for row in items):
                raise serializers.ValidationError({"items": "Mini app katalogda faqat catalog_item ishlatiladi"})
        elif not request_text:
            raise serializers.ValidationError({"request_text": "Yasatish uchun mijoz yozgan matn kerak"})
        attrs["items"] = items
        attrs["request_text"] = request_text
        return attrs


class MiniAppLeadSerializer(MiniAppQuoteSerializer):
    name = serializers.CharField(max_length=160)
    phone = serializers.CharField(max_length=30)
    note = serializers.CharField(required=False, allow_blank=True)


class EuroFlowersTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        data["permissions"] = permission_matrix(self.user)
        return data
