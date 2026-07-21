from django.contrib.auth.models import User
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import AISettings, AuditLog, Branch, BusinessSettings, CatalogComposition, CatalogItem, Conversation, Customer, Flower, FlowerVariant, InstagramSettings, InstagramWebhookEvent, IntegrationSettings, Lead, LeadCatalogUsage, LeadPackagingUsage, LeadStatus, LeadStockUsage, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement, UserProfile


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = "__all__"


class UserProfileSerializer(serializers.ModelSerializer):
    branches = BranchSerializer(many=True, read_only=True)
    class Meta:
        model = UserProfile
        fields = ["role", "language", "branches"]


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
    branch_ids = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.all(), many=True, write_only=True, required=False)
    permissions = PagePermissionInputSerializer(many=True, required=False)
    password = serializers.CharField(write_only=True, required=False, min_length=6)
    profile = UserProfileSerializer(read_only=True)
    permission_matrix = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "password", "is_active", "role", "language", "branch_ids", "permissions", "profile", "permission_matrix"]

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
        branches = validated_data.pop("branch_ids", [])
        permissions = validated_data.pop("permissions", None)
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        profile = UserProfile.objects.create(user=user, role=role, language=language)
        profile.branches.set(branches)
        self.save_permissions(user, permissions)
        return user

    def update(self, instance, validated_data):
        role = validated_data.pop("role", None)
        language = validated_data.pop("language", None)
        branches = validated_data.pop("branch_ids", None)
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
        if branches is not None:
            profile.branches.set(branches)
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
    branch_detail = BranchSerializer(source="branch", read_only=True)
    remaining_bunches = serializers.IntegerField(read_only=True)
    stock_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    class Meta:
        model = StockBatch
        fields = "__all__"

    def to_internal_value(self, data):
        if getattr(self, "partial", False):
            data = data.copy()
            for key, value in list(data.items()):
                if value == "":
                    data.pop(key)
        return super().to_internal_value(data)


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
        fields = "__all__"


class PackagingMovementSerializer(serializers.ModelSerializer):
    packaging_detail = PackagingSerializer(source="packaging", read_only=True)
    performed_by_detail = UserSerializer(source="performed_by", read_only=True)

    class Meta:
        model = PackagingMovement
        fields = "__all__"
        read_only_fields = ["performed_by"]


class SocialPostSerializer(serializers.ModelSerializer):
    reply_count = serializers.IntegerField(read_only=True)
    lead_count = serializers.IntegerField(read_only=True)
    class Meta:
        model = SocialPost
        fields = "__all__"
        extra_kwargs = {"media_id": {"required": False}, "post_type": {"required": False}}

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
                    from .services import find_active_story_by_permalink
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
                from .services import find_media_by_permalink
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

    def create(self, validated_data):
        validated_data = self._fill_story_share_fields(validated_data)
        self._check_unique_media_id(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data = self._fill_story_share_fields(validated_data)
        self._check_unique_media_id(validated_data)
        return super().update(instance, validated_data)


class CatalogCompositionSerializer(serializers.ModelSerializer):
    batch_detail = StockBatchSerializer(source="stock_batch", read_only=True)
    class Meta:
        model = CatalogComposition
        fields = ["id", "stock_batch", "batch_detail", "quantity_stems", "quantity_bunches"]


class CatalogItemSerializer(serializers.ModelSerializer):
    composition = CatalogCompositionSerializer(many=True, required=False)
    branch_detail = BranchSerializer(source="branch", read_only=True)
    social_post_detail = SocialPostSerializer(source="social_post", read_only=True)
    class Meta:
        model = CatalogItem
        fields = "__all__"
        read_only_fields = ["created_by", "sold_at", "stock_deducted_at"]

    def validate(self, attrs):
        composition = attrs.get("composition")
        quantity_total = attrs.get("quantity_total", getattr(self.instance, "quantity_total", 1))
        if composition is None and self.instance:
            composition = [{"stock_batch": row.stock_batch, "quantity_stems": row.quantity_stems} for row in self.instance.composition.select_related("stock_batch")]
        if composition:
            for row in composition:
                batch = row["stock_batch"]
                needed = row["quantity_stems"] * quantity_total
                if batch.remaining_stems < needed:
                    raise serializers.ValidationError({"composition": f"{batch.batch_number} partiyada yetarli qoldiq yo‘q. Kerak: {needed}, bor: {batch.remaining_stems}"})
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
        fields = "__all__"


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = "__all__"


class ConversationSerializer(serializers.ModelSerializer):
    customer_detail = CustomerSerializer(source="customer", read_only=True)
    messages = MessageSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    class Meta:
        model = Conversation
        fields = "__all__"

    def get_last_message(self, obj) -> dict[str, Any] | None:
        message = obj.messages.last()
        return MessageSerializer(message).data if message else None


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


class LeadSerializer(serializers.ModelSerializer):
    customer_detail = CustomerSerializer(source="customer", read_only=True)
    branch_detail = BranchSerializer(source="branch", read_only=True)
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
        fields = "__all__"
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
        fields = "__all__"


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
    branch = serializers.IntegerField(required=False)


class MiniAppLineSerializer(serializers.Serializer):
    stock_batch = serializers.IntegerField(required=False)
    catalog_item = serializers.IntegerField(required=False)
    quantity_stems = serializers.IntegerField(required=False, min_value=1)
    quantity = serializers.IntegerField(required=False, min_value=1)


class MiniAppQuoteSerializer(serializers.Serializer):
    init_data = serializers.CharField(required=False, allow_blank=True)
    branch = serializers.IntegerField(required=False)
    arrangement_type = serializers.ChoiceField(choices=["bouquet", "basket", "stems", "catalog"])
    items = MiniAppLineSerializer(many=True)
    packaging = serializers.IntegerField(required=False, allow_null=True)


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
