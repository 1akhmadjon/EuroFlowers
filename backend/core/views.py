from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, F, Max, Q, Sum
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce, TruncDate
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import django_filters
from urllib.parse import parse_qsl
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, parser_classes, permission_classes
from rest_framework import serializers
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from .models import AISettings, AuditLog, Branch, BusinessSettings, CatalogComposition, CatalogItem, Conversation, Customer, Flower, FlowerVariant, InstagramSettings, InstagramWebhookEvent, IntegrationSettings, Lead, LeadCatalogUsage, LeadStatus, LeadStockUsage, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement
from .permissions import RolePermission, has_page_permission
from .serializers import AISettingsSerializer, AIPauseRequestSerializer, AuditLogSerializer, BranchSerializer, BusinessSettingsSerializer, CatalogItemSerializer, ConversationSerializer, CustomerSerializer, EuroFlowersTokenObtainPairSerializer, FlowerSerializer, FlowerVariantSerializer, InstagramSettingsSerializer, InstagramWebhookEventSerializer, IntegrationSettingsSerializer, LeadColumnReorderSerializer, LeadMoveSerializer, LeadSerializer, LeadStatusSerializer, MiniAppInitSerializer, MiniAppLeadSerializer, MiniAppQuoteSerializer, MovementRequestSerializer, NotificationSerializer, PackagingMovementRequestSerializer, PackagingMovementSerializer, PackagingSerializer, PagePermissionSerializer, SendResponseSerializer, SimulateResponseSerializer, SocialPostSerializer, StockBatchSerializer, StockMovementSerializer, TextRequestSerializer, UploadResponseSerializer, UploadSerializer, UserSerializer, UserWriteSerializer
from .services import apply_packaging_movement, apply_stock_movement, deduct_catalog_stock, deduct_lead_stock, instagram_send, mark_catalog_sold, normalize_phone, process_customer_message, resolve_instagram_event, restore_lead_stock


class CreatedAtRangeFilter(django_filters.FilterSet):
    created_at = django_filters.DateTimeFromToRangeFilter()


class LeadFilter(CreatedAtRangeFilter):
    class Meta:
        model = Lead
        fields = ["branch", "status", "arrangement_type", "assigned_to", "social_post", "created_at"]


def schedule_lead_recall(lead):
    if not lead.recall_at or lead.recall_sent_at or lead.status == "lost":
        return
    from .tasks import process_lead_recall
    eta = lead.recall_at if lead.recall_at > timezone.now() else None
    if eta:
        process_lead_recall.apply_async(args=[lead.id], eta=eta)
    else:
        process_lead_recall.delay(lead.id)


def next_lead_sort_order(branch, status_value):
    current = Lead.objects.filter(branch=branch, status=status_value).aggregate(value=Max("sort_order"))["value"] or Decimal("0")
    return current + Decimal("1000")


def lead_sort_order_between(before, after, branch, status_value):
    if before and before.branch_id != branch.id:
        raise serializers.ValidationError({"before": "Lead boshqa filialga tegishli"})
    if after and after.branch_id != branch.id:
        raise serializers.ValidationError({"after": "Lead boshqa filialga tegishli"})
    if before and before.status != status_value:
        raise serializers.ValidationError({"before": "Lead statusi yangi column bilan mos emas"})
    if after and after.status != status_value:
        raise serializers.ValidationError({"after": "Lead statusi yangi column bilan mos emas"})
    if before and after:
        return (before.sort_order + after.sort_order) / Decimal("2")
    if before:
        return before.sort_order + Decimal("1000")
    if after:
        return after.sort_order - Decimal("1000")
    return next_lead_sort_order(branch, status_value)


class StockMovementFilter(CreatedAtRangeFilter):
    class Meta:
        model = StockMovement
        fields = ["batch", "movement_type", "created_at"]


class PackagingMovementFilter(CreatedAtRangeFilter):
    class Meta:
        model = PackagingMovement
        fields = ["packaging", "movement_type", "created_at"]


class ConversationFilter(CreatedAtRangeFilter):
    class Meta:
        model = Conversation
        fields = ["branch", "status", "assigned_to", "created_at"]


class AuditLogFilter(CreatedAtRangeFilter):
    class Meta:
        model = AuditLog
        fields = ["action", "entity_type", "user", "created_at"]


class PagePermissionFilter(django_filters.FilterSet):
    permission_page = django_filters.ChoiceFilter(field_name="page", choices=PagePermission.PAGE_CHOICES)

    class Meta:
        model = PagePermission
        fields = ["user", "permission_page", "can_view", "can_control"]


class EuroFlowersTokenObtainPairView(TokenObtainPairView):
    serializer_class = EuroFlowersTokenObtainPairSerializer


def forbidden():
    return Response({"detail": "Sizda bu sahifa uchun ruxsat yo‘q."}, status=status.HTTP_403_FORBIDDEN)


class ScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]

    def branch_ids(self):
        profile = getattr(self.request.user, "profile", None)
        if self.request.user.is_superuser or not profile:
            return None
        return list(profile.branches.values_list("id", flat=True))

    def get_queryset(self):
        queryset = super().get_queryset()
        if not queryset.query.order_by and not queryset.model._meta.ordering:
            queryset = queryset.order_by("id")
        branch_ids = self.branch_ids()
        if branch_ids is None:
            return queryset
        field_names = {field.name for field in queryset.model._meta.fields}
        if "branch" in field_names:
            return queryset.filter(branch_id__in=branch_ids)
        if queryset.model is Branch:
            return queryset.filter(id__in=branch_ids)
        return queryset


class BranchViewSet(ScopedViewSet):
    permission_page = "settings"
    write_roles = ["admin"]
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    search_fields = ["name", "code", "address"]


class UserViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]
    permission_page = "users"
    queryset = User.objects.select_related("profile").prefetch_related("profile__branches", "page_permissions").order_by("id")
    serializer_class = UserSerializer
    search_fields = ["username", "first_name", "last_name", "email"]
    filterset_fields = ["is_active", "profile__role"]

    def get_serializer_class(self):
        if self.request.method in ["POST", "PUT", "PATCH"]:
            return UserWriteSerializer
        return UserSerializer

    def get_developer_role(self):
        return getattr(getattr(self.request.user, "profile", None), "role", None) == "developer"

    def create(self, request, *args, **kwargs):
        if request.data.get("role") == "developer" and not self.get_developer_role():
            return forbidden()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if request.data.get("role") == "developer" and not self.get_developer_role():
            return forbidden()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if request.data.get("role") == "developer" and not self.get_developer_role():
            return forbidden()
        return super().partial_update(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        role = getattr(getattr(self.request.user, "profile", None), "role", None)
        if role != "developer":
            queryset = queryset.exclude(profile__role="developer")
        return queryset

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        user.is_active = False
        user.save(update_fields=["is_active"])
        return Response(UserSerializer(user).data)


class FlowerViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = Flower.objects.prefetch_related("variants").all()
    serializer_class = FlowerSerializer
    search_fields = ["name_uz", "name_ru"]


class FlowerVariantViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = FlowerVariant.objects.select_related("flower").all()
    serializer_class = FlowerVariantSerializer
    filterset_fields = ["flower", "is_active"]
    search_fields = ["name_uz", "name_ru", "color_uz", "color_ru", "flower__name_uz", "flower__name_ru"]


class StockBatchViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = StockBatch.objects.select_related("branch", "variant__flower").all()
    serializer_class = StockBatchSerializer
    filterset_fields = ["branch", "variant", "height_cm", "is_active"]
    search_fields = ["batch_number", "variant__flower__name_uz", "variant__flower__name_ru", "variant__name_uz", "variant__color_uz"]
    ordering_fields = ["received_at", "remaining_stems", "sale_price_per_stem", "height_cm"]

    def get_queryset(self):
        queryset = super().get_queryset()
        branch_ids = self.branch_ids()
        return queryset.filter(branch_id__in=branch_ids) if branch_ids is not None else queryset

    def perform_create(self, serializer):
        batch = serializer.save()
        StockMovement.objects.create(batch=batch, movement_type="in", quantity_stems=batch.received_stems, quantity_bunches=batch.received_stems / batch.stems_per_bunch, reason="Partiya kirimi", performed_by=self.request.user)
        AuditLog.objects.create(user=self.request.user, action="stock_received", entity_type="StockBatch", entity_id=str(batch.id), after={"received_stems": batch.received_stems})

    def destroy(self, request, *args, **kwargs):
        batch = self.get_object()
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            batch.is_active = False
            batch.save(update_fields=["is_active", "updated_at"])
            return Response({"detail": "Bu partiyada sklad tarixi bor. Partiya o‘chirilmadi, is_active=false qilib arxivlandi.", "id": batch.id, "is_active": batch.is_active})

    @extend_schema(request=MovementRequestSerializer, responses=StockMovementSerializer)
    @action(detail=True, methods=["post"])
    def movement(self, request, pk=None):
        batch = self.get_object()
        serializer = MovementRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            movement = apply_stock_movement(batch, serializer.validated_data["movement_type"], serializer.validated_data["quantity_stems"], serializer.validated_data.get("reason", ""), request.user)
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(StockMovementSerializer(movement).data)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [RolePermission]
    permission_page = "inventory"
    queryset = StockMovement.objects.select_related("batch__variant__flower", "performed_by").all()
    serializer_class = StockMovementSerializer
    filterset_class = StockMovementFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        profile = getattr(self.request.user, "profile", None)
        if self.request.user.is_superuser or not profile:
            return queryset
        return queryset.filter(batch__branch__in=profile.branches.all())


class PackagingViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = Packaging.objects.select_related("branch").all()
    serializer_class = PackagingSerializer
    filterset_fields = ["branch", "packaging_type", "is_active"]
    search_fields = ["name_uz", "name_ru"]

    def perform_create(self, serializer):
        packaging = serializer.save()
        if packaging.quantity:
            movement = PackagingMovement.objects.create(packaging=packaging, movement_type="in", quantity=packaging.quantity, reason="Qadoq/savat kirimi", performed_by=self.request.user)
            AuditLog.objects.create(user=self.request.user, action="packaging_received", entity_type="Packaging", entity_id=str(packaging.id), after={"quantity": packaging.quantity, "movement": movement.id})

    def perform_update(self, serializer):
        before = serializer.instance.quantity
        packaging = serializer.save()
        if "quantity" in serializer.validated_data and packaging.quantity != before:
            delta = packaging.quantity - before
            movement = PackagingMovement.objects.create(packaging=packaging, movement_type="adjustment", quantity=delta, reason="Qadoq/savat qoldig‘i tahrirlandi", performed_by=self.request.user)
            AuditLog.objects.create(user=self.request.user, action="packaging_adjusted", entity_type="Packaging", entity_id=str(packaging.id), before={"quantity": before}, after={"quantity": packaging.quantity, "movement": movement.id})

    @extend_schema(request=PackagingMovementRequestSerializer, responses=PackagingMovementSerializer)
    @action(detail=True, methods=["post"])
    def movement(self, request, pk=None):
        packaging = self.get_object()
        serializer = PackagingMovementRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            movement = apply_packaging_movement(packaging, serializer.validated_data["movement_type"], serializer.validated_data["quantity"], serializer.validated_data.get("reason", ""), request.user)
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PackagingMovementSerializer(movement).data)


class PackagingMovementViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [RolePermission]
    permission_page = "inventory"
    queryset = PackagingMovement.objects.select_related("packaging__branch", "performed_by").all()
    serializer_class = PackagingMovementSerializer
    filterset_class = PackagingMovementFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        profile = getattr(self.request.user, "profile", None)
        if self.request.user.is_superuser or not profile:
            return queryset
        return queryset.filter(packaging__branch__in=profile.branches.all())


class CatalogItemViewSet(ScopedViewSet):
    permission_page = "catalog"
    write_roles = ["admin", "florist", "content", "warehouse"]
    queryset = CatalogItem.objects.select_related("branch", "social_post").prefetch_related("composition__stock_batch__variant__flower").all()
    serializer_class = CatalogItemSerializer
    filterset_fields = ["branch", "status", "arrangement_type"]
    search_fields = ["name_uz", "name_ru", "description_uz", "description_ru"]

    def get_queryset(self):
        queryset = super().get_queryset()
        branch_ids = self.branch_ids()
        return queryset.filter(branch_id__in=branch_ids) if branch_ids is not None else queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(request=None, responses=CatalogItemSerializer)
    @action(detail=True, methods=["post"])
    def sell(self, request, pk=None):
        try:
            item = mark_catalog_sold(self.get_object(), request.user, request.data.get("quantity", 1))
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(item).data)

    @extend_schema(request=None, responses=CatalogItemSerializer)
    @action(detail=True, methods=["post"])
    def deduct_stock(self, request, pk=None):
        try:
            quantity = request.data.get("quantity")
            item = deduct_catalog_stock(self.get_object(), request.user, quantity)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(item).data)


class CustomerViewSet(ScopedViewSet):
    permission_page = "customers"
    write_roles = ["admin", "operator"]
    queryset = Customer.objects.select_related("branch").annotate(purchases_count=Count("leads", filter=Q(leads__status="won")), total_spent=Coalesce(Sum("leads__estimated_price", filter=Q(leads__status="won")), Decimal("0"))).all()
    serializer_class = CustomerSerializer
    filterset_fields = ["branch", "language", "is_blocked"]
    search_fields = ["name", "phone", "instagram_username"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("include_incomplete") == "true":
            return queryset
        return queryset.exclude(Q(name="") | Q(phone=""))


class LeadStatusViewSet(ScopedViewSet):
    permission_page = "crm"
    write_roles = ["admin", "operator"]
    queryset = LeadStatus.objects.all()
    serializer_class = LeadStatusSerializer
    filterset_fields = ["is_active"]
    search_fields = ["key", "name_uz", "name_ru"]
    ordering_fields = ["order", "created_at"]


class LeadViewSet(ScopedViewSet):
    permission_page = "crm"
    write_roles = ["admin", "operator"]
    queryset = Lead.objects.select_related("customer", "branch", "assigned_to", "social_post").all()
    serializer_class = LeadSerializer
    filterset_class = LeadFilter
    search_fields = ["customer__name", "customer__phone", "request_uz", "request_ru"]
    ordering_fields = ["sort_order", "created_at", "estimated_price"]

    def perform_create(self, serializer):
        with transaction.atomic():
            extra = {}
            if "sort_order" not in serializer.validated_data:
                branch = serializer.validated_data.get("branch")
                status_value = serializer.validated_data.get("status", "new")
                if branch:
                    extra["sort_order"] = next_lead_sort_order(branch, status_value)
            lead = serializer.save(**extra)
            if lead.status == "won":
                try:
                    deduct_lead_stock(lead, self.request.user)
                    serializer.instance.refresh_from_db()
                except ValueError as exc:
                    raise serializers.ValidationError({"detail": str(exc)})
            transaction.on_commit(lambda lead_id=lead.id: schedule_lead_recall(Lead.objects.get(id=lead_id)))

    @extend_schema(request=LeadMoveSerializer, responses=LeadSerializer)
    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        lead = self.get_object()
        serializer = LeadMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_value = serializer.validated_data.get("status") or lead.status
        if not LeadStatus.objects.filter(key=status_value).exists():
            return Response({"status": "Bunday lead statusi mavjud emas"}, status=status.HTTP_400_BAD_REQUEST)
        before = serializer.validated_data.get("before")
        after = serializer.validated_data.get("after")
        sort_order = serializer.validated_data.get("sort_order")
        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(pk=lead.pk)
            before_status = lead.status
            if sort_order is None:
                sort_order = lead_sort_order_between(before, after, lead.branch, status_value)
            lead.status = status_value
            lead.sort_order = sort_order
            lead.save(update_fields=["status", "sort_order", "updated_at"])
            if lead.status == "won" and before_status != "won":
                try:
                    deduct_lead_stock(lead, self.request.user)
                    lead.refresh_from_db()
                except ValueError as exc:
                    raise serializers.ValidationError({"detail": str(exc)})
            elif before_status == "won" and lead.status != "won":
                restore_lead_stock(lead, self.request.user)
                lead.refresh_from_db()
        return Response(LeadSerializer(lead, context={"request": request}).data)

    @extend_schema(request=LeadColumnReorderSerializer, responses=inline_serializer(name="LeadColumnReorderResponse", fields={"updated": serializers.IntegerField()}))
    @action(detail=False, methods=["post"], url_path="reorder-column")
    def reorder_column(self, request):
        serializer = LeadColumnReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_value = serializer.validated_data["status"]
        if not LeadStatus.objects.filter(key=status_value).exists():
            return Response({"status": "Bunday lead statusi mavjud emas"}, status=status.HTTP_400_BAD_REQUEST)
        lead_ids = serializer.validated_data["lead_ids"]
        if len(lead_ids) != len(set(lead_ids)):
            return Response({"lead_ids": "Lead id takrorlanmasligi kerak"}, status=status.HTTP_400_BAD_REQUEST)
        branch = serializer.validated_data.get("branch")
        scoped_queryset = self.get_queryset()
        leads = list(scoped_queryset.filter(id__in=lead_ids))
        if len(leads) != len(set(lead_ids)):
            return Response({"lead_ids": "Lead topilmadi yoki sizda ruxsat yo‘q"}, status=status.HTTP_400_BAD_REQUEST)
        if leads:
            branch = branch or leads[0].branch
        if not branch:
            return Response({"branch": "Bo‘sh column tartibi uchun branch kerak"}, status=status.HTTP_400_BAD_REQUEST)
        if any(lead.branch_id != branch.id for lead in leads):
            return Response({"lead_ids": "Bitta column tartibi faqat bitta filial leadlari bilan yuboriladi"}, status=status.HTTP_400_BAD_REQUEST)
        target_existing_ids = set(scoped_queryset.filter(branch=branch, status=status_value).values_list("id", flat=True))
        incoming_ids = set(lead_ids)
        missing_ids = target_existing_ids - incoming_ids
        if missing_ids:
            return Response({"lead_ids": "Target column lead_ids to‘liq yuborilishi kerak", "missing_ids": sorted(missing_ids)}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            for index, lead_id in enumerate(lead_ids, start=1):
                lead = Lead.objects.select_for_update().get(id=lead_id)
                before_status = lead.status
                lead.status = status_value
                lead.sort_order = Decimal(index * 1000)
                lead.save(update_fields=["status", "sort_order", "updated_at"])
                if lead.status == "won" and before_status != "won":
                    try:
                        deduct_lead_stock(lead, self.request.user)
                    except ValueError as exc:
                        raise serializers.ValidationError({"detail": str(exc)})
                elif before_status == "won" and lead.status != "won":
                    restore_lead_stock(lead, self.request.user)
        return Response({"updated": len(lead_ids)})

    def perform_update(self, serializer):
        with transaction.atomic():
            before_status = serializer.instance.status
            lead = serializer.save()
            if lead.status == "won" and before_status != "won":
                try:
                    deduct_lead_stock(lead, self.request.user)
                    serializer.instance.refresh_from_db()
                except ValueError as exc:
                    raise serializers.ValidationError({"detail": str(exc)})
            elif before_status == "won" and lead.status != "won":
                restore_lead_stock(lead, self.request.user)
                serializer.instance.refresh_from_db()
            transaction.on_commit(lambda lead_id=lead.id: schedule_lead_recall(Lead.objects.get(id=lead_id)))


class SocialPostViewSet(ScopedViewSet):
    permission_page = "social_posts"
    write_roles = ["admin", "content"]
    queryset = SocialPost.objects.select_related("branch").prefetch_related("catalog_items__composition__stock_batch__variant__flower", "leads__customer", "leads__catalog_usage__catalog_item").annotate(reply_count=Count("conversations", distinct=True), lead_count=Count("leads", distinct=True)).all()
    serializer_class = SocialPostSerializer
    filterset_fields = ["branch", "post_type", "is_targeted", "is_active"]
    search_fields = ["title_uz", "title_ru", "media_id", "permalink"]


class ConversationViewSet(ScopedViewSet):
    permission_page = "conversations"
    write_roles = ["admin", "operator"]
    queryset = Conversation.objects.select_related("customer", "branch", "social_post", "assigned_to").prefetch_related("messages").all()
    serializer_class = ConversationSerializer
    filterset_class = ConversationFilter
    search_fields = ["customer__name", "customer__instagram_username", "messages__text"]

    @extend_schema(request=TextRequestSerializer, responses=SendResponseSerializer)
    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        conversation = self.get_object()
        text = request.data.get("text", "").strip()
        if not text:
            return Response({"detail": "Xabar bo‘sh"}, status=status.HTTP_400_BAD_REQUEST)
        message = conversation.messages.create(sender="operator", text=text)
        instagram_send(conversation.customer.instagram_user_id, text)
        conversation.last_message_at = timezone.now()
        conversation.ai_paused_until = timezone.now() + timedelta(minutes=15)
        conversation.ai_pause_reason = "operator_message"
        conversation.assigned_to = request.user
        conversation.save(update_fields=["last_message_at", "ai_paused_until", "ai_pause_reason", "assigned_to", "updated_at"])
        return Response({"id": message.id, "text": message.text})

    @extend_schema(request=None, responses=ConversationSerializer)
    @action(detail=True, methods=["post"])
    def handoff(self, request, pk=None):
        conversation = self.get_object()
        conversation.status = "operator"
        conversation.assigned_to = request.user
        conversation.save(update_fields=["status", "assigned_to", "updated_at"])
        return Response(self.get_serializer(conversation).data)

    @extend_schema(request=None, responses=ConversationSerializer)
    @action(detail=True, methods=["post"])
    def resume_ai(self, request, pk=None):
        conversation = self.get_object()
        conversation.status = "ai"
        conversation.assigned_to = None
        conversation.ai_paused_until = None
        conversation.ai_pause_reason = ""
        conversation.save(update_fields=["status", "assigned_to", "ai_paused_until", "ai_pause_reason", "updated_at"])
        return Response(self.get_serializer(conversation).data)

    @extend_schema(request=AIPauseRequestSerializer, responses=ConversationSerializer)
    @action(detail=True, methods=["post"])
    def pause_ai(self, request, pk=None):
        conversation = self.get_object()
        serializer = AIPauseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("paused_until"):
            paused_until = serializer.validated_data["paused_until"]
        else:
            paused_until = timezone.now() + timedelta(minutes=serializer.validated_data["minutes"])
        conversation.ai_paused_until = paused_until
        conversation.ai_pause_reason = serializer.validated_data.get("reason", "manual")
        conversation.assigned_to = request.user
        conversation.save(update_fields=["ai_paused_until", "ai_pause_reason", "assigned_to", "updated_at"])
        return Response(self.get_serializer(conversation).data)

    @extend_schema(request=TextRequestSerializer, responses=SimulateResponseSerializer)
    @action(detail=True, methods=["post"])
    def simulate(self, request, pk=None):
        serializer = TextRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply = process_customer_message(self.get_object(), serializer.validated_data["text"])
        return Response({"reply": reply.text if reply else None})


class NotificationViewSet(ScopedViewSet):
    permission_page = "notifications"
    queryset = Notification.objects.select_related("branch").all()
    serializer_class = NotificationSerializer
    filterset_fields = ["branch", "notification_type", "is_read"]
    write_roles = ["admin", "operator", "florist", "warehouse", "content"]
    http_method_names = ["get", "post", "head", "options"]

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read", "updated_at"])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"])
    def read_all(self, request):
        queryset = self.filter_queryset(self.get_queryset()).filter(is_read=False)
        count = queryset.update(is_read=True)
        return Response({"updated": count})


class AuditLogViewSet(ScopedViewSet):
    permission_page = "audit"
    queryset = AuditLog.objects.select_related("user").all()
    serializer_class = AuditLogSerializer
    filterset_class = AuditLogFilter
    write_roles = []
    http_method_names = ["get", "head", "options"]


class PagePermissionViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]
    permission_page = "users"
    queryset = PagePermission.objects.select_related("user").all()
    serializer_class = PagePermissionSerializer
    filterset_class = PagePermissionFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        role = getattr(getattr(self.request.user, "profile", None), "role", None)
        if role != "developer":
            queryset = queryset.exclude(page__in=PagePermission.DEVELOPER_ONLY_PAGES).exclude(user__profile__role="developer")
        return queryset


class InstagramWebhookEventViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [RolePermission]
    permission_page = "integrations"
    queryset = InstagramWebhookEvent.objects.all()
    serializer_class = InstagramWebhookEventSerializer
    filterset_fields = ["event_type", "sender_id", "message_id", "media_id", "story_id"]
    search_fields = ["text", "story_url", "media_id", "story_id", "sender_id", "message_id"]


@extend_schema(responses=UserSerializer)
@api_view(["GET"])
def me(request):
    return Response(UserSerializer(request.user).data)


@extend_schema(responses=inline_serializer(
    name="Dashboard",
    fields={
        "active_leads": serializers.IntegerField(),
        "new_leads_today": serializers.IntegerField(),
        "available_catalog": serializers.IntegerField(),
        "pending_deductions": serializers.IntegerField(),
        "unread_notifications": serializers.IntegerField(),
        "ai_conversations": serializers.IntegerField(),
        "operator_conversations": serializers.IntegerField(),
        "stock_stems": serializers.IntegerField(),
        "low_stock": serializers.IntegerField(),
        "lead_pipeline": serializers.ListField(child=serializers.DictField()),
        "branch_stock": serializers.ListField(child=serializers.DictField()),
        "recent_leads": LeadSerializer(many=True),
        "recent_notifications": NotificationSerializer(many=True),
        "revenue_today": serializers.DecimalField(max_digits=14, decimal_places=2),
        "orders_today": serializers.IntegerField(),
        "revenue_7d": serializers.DecimalField(max_digits=14, decimal_places=2),
        "conversion_rate": serializers.FloatField(),
        "period": serializers.DictField(),
        "period_revenue": serializers.DecimalField(max_digits=14, decimal_places=2),
        "period_orders": serializers.IntegerField(),
        "period_leads": serializers.IntegerField(),
        "period_customers": serializers.IntegerField(),
        "period_conversations": serializers.IntegerField(),
        "daily_stats": serializers.ListField(child=serializers.DictField()),
        "top_selling_flowers": serializers.ListField(child=serializers.DictField()),
        "florist_revenue": serializers.DecimalField(max_digits=14, decimal_places=2),
        "flowers_sold_stems": serializers.IntegerField(),
    },
))
@api_view(["GET"])
def dashboard(request):
    if not has_page_permission(request.user, "dashboard", False):
        return forbidden()
    today = timezone.localdate()
    week_start = today - timedelta(days=6)
    period_start, period_end = dashboard_period(request)
    stock = StockBatch.objects.filter(is_active=True)
    stock_movements = StockMovement.objects.all()
    leads = Lead.objects.all()
    customers = Customer.objects.all()
    catalog = CatalogItem.objects.all()
    notifications = Notification.objects.all()
    conversations = Conversation.objects.all()
    profile = getattr(request.user, "profile", None)
    if not request.user.is_superuser and profile:
        branches = profile.branches.all()
        stock = stock.filter(branch__in=branches)
        stock_movements = stock_movements.filter(batch__branch__in=branches)
        leads = leads.filter(branch__in=branches)
        customers = customers.filter(branch__in=branches)
        catalog = catalog.filter(branch__in=branches)
        notifications = notifications.filter(branch__in=branches)
        conversations = conversations.filter(branch__in=branches)
    won_leads = leads.filter(status="won")
    period_leads = apply_created_range(leads, period_start, period_end)
    period_customers = apply_created_range(customers, period_start, period_end)
    period_conversations = apply_created_range(conversations, period_start, period_end)
    period_won_leads = apply_updated_range(won_leads, period_start, period_end)
    period_stock_out = apply_created_range(stock_movements.filter(movement_type="out", quantity_stems__lt=0), period_start, period_end)
    flowers_sold = period_stock_out.aggregate(value=Coalesce(Sum("quantity_stems"), 0))["value"] or 0
    leads_total = leads.count()
    conversations_total = conversations.count()
    conversion_base = conversations_total or leads_total
    data = {
        "active_leads": leads.exclude(status__in=["won", "lost"]).count(),
        "new_leads_today": leads.filter(created_at__date=today).count(),
        "orders_today": won_leads.filter(updated_at__date=today).count(),
        "revenue_today": won_leads.filter(updated_at__date=today).aggregate(value=Coalesce(Sum("estimated_price"), Decimal("0")))["value"],
        "revenue_7d": won_leads.filter(updated_at__date__gte=week_start).aggregate(value=Coalesce(Sum("estimated_price"), Decimal("0")))["value"],
        "period": {"from": period_start, "to": period_end},
        "period_orders": period_won_leads.count(),
        "period_revenue": period_won_leads.aggregate(value=Coalesce(Sum("estimated_price"), Decimal("0")))["value"],
        "period_leads": period_leads.count(),
        "period_customers": period_customers.count(),
        "period_conversations": period_conversations.count(),
        "daily_stats": dashboard_daily_stats(period_leads, period_conversations, period_start, period_end),
        "top_selling_flowers": top_selling_flowers(period_won_leads)[:5],
        "florist_revenue": period_won_leads.aggregate(value=Coalesce(Sum("florist_fee"), Decimal("0")))["value"],
        "flowers_sold_stems": abs(int(flowers_sold)),
        "conversion_rate": round((won_leads.count() / conversion_base) * 100, 2) if conversion_base else 0,
        "available_catalog": catalog.filter(status="available").count(),
        "pending_deductions": catalog.filter(quantity_sold__gt=F("quantity_stock_deducted")).count(),
        "unread_notifications": notifications.filter(is_read=False).count(),
        "ai_conversations": conversations.filter(status="ai").count(),
        "operator_conversations": conversations.filter(status="operator").count(),
        "stock_stems": stock.aggregate(value=Coalesce(Sum("remaining_stems"), 0))["value"],
        "low_stock": stock.filter(remaining_stems__lte=F("minimum_sale_stems")).count(),
        "lead_pipeline": list(leads.values("status").annotate(count=Count("id")).order_by("status")),
        "branch_stock": list(stock.values("branch__id", "branch__name").annotate(stems=Sum("remaining_stems"), batches=Count("id")).order_by("branch__name")),
        "recent_leads": LeadSerializer(leads.select_related("customer", "branch")[:6], many=True).data,
        "recent_notifications": NotificationSerializer(notifications.filter(is_read=False)[:6], many=True).data,
    }
    return Response(data)


@extend_schema(responses=inline_serializer(
    name="Analytics",
    fields={
        "period": serializers.DictField(),
        "summary": serializers.DictField(),
        "daily_stats": serializers.ListField(child=serializers.DictField()),
        "top_selling_flowers": serializers.ListField(child=serializers.DictField()),
        "top_catalog_items": serializers.ListField(child=serializers.DictField()),
        "recent_top_catalog_items": serializers.ListField(child=serializers.DictField()),
        "lead_statuses": serializers.ListField(child=serializers.DictField()),
        "arrangement_types": serializers.ListField(child=serializers.DictField()),
        "conversation_sources": serializers.ListField(child=serializers.DictField()),
        "revenue_by_source": serializers.ListField(child=serializers.DictField()),
    },
))
@api_view(["GET"])
def analytics(request):
    if not has_page_permission(request.user, "dashboard", False):
        return forbidden()
    period_start, period_end = dashboard_period(request)
    leads = Lead.objects.select_related("customer", "branch").all()
    conversations = Conversation.objects.select_related("customer", "branch").all()
    customers = Customer.objects.all()
    stock_movements = StockMovement.objects.all()
    profile = getattr(request.user, "profile", None)
    if not request.user.is_superuser and profile:
        branches = profile.branches.all()
        leads = leads.filter(branch__in=branches)
        conversations = conversations.filter(branch__in=branches)
        customers = customers.filter(branch__in=branches)
        stock_movements = stock_movements.filter(batch__branch__in=branches)
    period_leads = apply_created_range(leads, period_start, period_end)
    period_conversations = apply_created_range(conversations, period_start, period_end)
    period_customers = apply_created_range(customers, period_start, period_end)
    won_leads = leads.filter(status="won")
    period_won_leads = apply_updated_range(won_leads, period_start, period_end)
    period_stock_out = apply_created_range(stock_movements.filter(movement_type="out", quantity_stems__lt=0), period_start, period_end)
    flowers_sold = period_stock_out.aggregate(value=Coalesce(Sum("quantity_stems"), 0))["value"] or 0
    data = {
        "period": {"from": period_start, "to": period_end},
        "summary": {
            "leads": period_leads.count(),
            "customers": period_customers.count(),
            "conversations": period_conversations.count(),
            "orders": period_won_leads.count(),
            "revenue": period_won_leads.aggregate(value=Coalesce(Sum("estimated_price"), Decimal("0")))["value"],
            "florist_revenue": period_won_leads.aggregate(value=Coalesce(Sum("florist_fee"), Decimal("0")))["value"],
            "flowers_sold_stems": abs(int(flowers_sold)),
            "conversion_rate": round((period_won_leads.count() / (period_conversations.count() or period_leads.count())) * 100, 2) if (period_conversations.count() or period_leads.count()) else 0,
        },
        "daily_stats": analytics_daily_stats(period_leads, period_conversations, period_won_leads, period_start, period_end),
        "top_selling_flowers": top_selling_flowers(period_won_leads),
        "top_catalog_items": top_catalog_items(period_won_leads),
        "recent_top_catalog_items": recent_top_catalog_items(period_won_leads),
        "lead_statuses": list(period_leads.values("status").annotate(count=Count("id")).order_by("status")),
        "arrangement_types": list(period_leads.values("arrangement_type").annotate(count=Count("id")).order_by("arrangement_type")),
        "conversation_sources": conversation_source_breakdown(period_conversations),
        "revenue_by_source": revenue_by_source(period_won_leads),
    }
    return Response(data)


@extend_schema(request=BusinessSettingsSerializer, responses=BusinessSettingsSerializer)
@api_view(["GET", "PATCH"])
@permission_classes([AllowAny])
def business_settings(request):
    obj, _ = BusinessSettings.objects.get_or_create(pk=1)
    if request.method == "PATCH" and (not request.user or not request.user.is_authenticated):
        return Response({"detail": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)
    if request.method == "PATCH" and not has_page_permission(request.user, "settings", True):
        return forbidden()
    if request.method == "PATCH":
        serializer = BusinessSettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    return Response(BusinessSettingsSerializer(obj).data)


@extend_schema(request=InstagramSettingsSerializer, responses=InstagramSettingsSerializer)
@api_view(["GET", "PATCH"])
@permission_classes([AllowAny])
def instagram_status(request):
    obj, _ = InstagramSettings.objects.get_or_create(pk=1)
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    context = {"instagram_access_token": integration.instagram_access_token or settings.INSTAGRAM_ACCESS_TOKEN, "instagram_account_id": integration.instagram_account_id or settings.INSTAGRAM_ACCOUNT_ID}
    if request.method == "PATCH" and (not request.user or not request.user.is_authenticated):
        return Response({"detail": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)
    if request.method == "PATCH" and not has_page_permission(request.user, "settings", True):
        return forbidden()
    if request.method == "PATCH":
        serializer = InstagramSettingsSerializer(obj, data=request.data, partial=True, context=context)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    return Response(InstagramSettingsSerializer(obj, context=context).data)


def is_developer(user):
    return bool(user and user.is_authenticated and getattr(getattr(user, "profile", None), "role", None) == "developer")


def dashboard_period(request):
    start_value = request.query_params.get("from") or request.query_params.get("date_from")
    end_value = request.query_params.get("to") or request.query_params.get("date_to")
    start = parse_datetime(start_value) if start_value else None
    end = parse_datetime(end_value) if end_value else None
    if start_value and not start:
        parsed = parse_date(start_value)
        if parsed:
            start = timezone.make_aware(datetime.combine(parsed, time.min))
    if end_value and not end:
        parsed = parse_date(end_value)
        if parsed:
            end = timezone.make_aware(datetime.combine(parsed, time.max))
    if start and timezone.is_naive(start):
        start = timezone.make_aware(start)
    if end and timezone.is_naive(end):
        end = timezone.make_aware(end)
    today = timezone.localdate()
    if not end:
        end = timezone.make_aware(datetime.combine(today, time.max))
    if not start:
        end_date = timezone.localtime(end).date()
        start = timezone.make_aware(datetime.combine(end_date - timedelta(days=29), time.min))
    return start, end


def dashboard_daily_stats(leads, conversations, start, end):
    start_date = timezone.localtime(start).date()
    end_date = timezone.localtime(end).date()
    lead_counts = {
        row["day"]: row["count"]
        for row in leads.annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone())).values("day").annotate(count=Count("id"))
    }
    conversation_counts = {
        row["day"]: row["count"]
        for row in conversations.annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone())).values("day").annotate(count=Count("id"))
    }
    days = []
    current = start_date
    while current <= end_date:
        days.append({"date": current.isoformat(), "leads": lead_counts.get(current, 0), "conversations": conversation_counts.get(current, 0)})
        current += timedelta(days=1)
    return days


def analytics_daily_stats(leads, conversations, won_leads, start, end):
    start_date = timezone.localtime(start).date()
    end_date = timezone.localtime(end).date()
    lead_counts = {
        row["day"]: row["count"]
        for row in leads.annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone())).values("day").annotate(count=Count("id"))
    }
    conversation_counts = {
        row["day"]: row["count"]
        for row in conversations.annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone())).values("day").annotate(count=Count("id"))
    }
    order_rows = won_leads.annotate(day=TruncDate("updated_at", tzinfo=timezone.get_current_timezone())).values("day").annotate(count=Count("id"), revenue=Coalesce(Sum("estimated_price"), Decimal("0")))
    order_counts = {row["day"]: row["count"] for row in order_rows}
    revenue_counts = {row["day"]: row["revenue"] for row in order_rows}
    days = []
    current = start_date
    while current <= end_date:
        days.append({"date": current.isoformat(), "leads": lead_counts.get(current, 0), "conversations": conversation_counts.get(current, 0), "orders": order_counts.get(current, 0), "revenue": revenue_counts.get(current, Decimal("0"))})
        current += timedelta(days=1)
    return days


def top_selling_flowers(won_leads):
    lead_ids = list(won_leads.values_list("id", flat=True))
    rows = {}
    stock_rows = LeadStockUsage.objects.filter(lead_id__in=lead_ids).select_related("stock_batch__variant__flower").values(
        "stock_batch__variant__flower_id",
        "stock_batch__variant__flower__name_uz",
        "stock_batch__variant__flower__name_ru",
        "stock_batch__variant__color_uz",
        "stock_batch__variant__color_ru",
    ).annotate(stems=Coalesce(Sum("quantity_stems"), 0), bunches=Coalesce(Sum("quantity_bunches"), Decimal("0")))
    for row in stock_rows:
        key = (row["stock_batch__variant__flower_id"], row["stock_batch__variant__color_uz"] or "")
        rows.setdefault(key, {"flower_id": row["stock_batch__variant__flower_id"], "name_uz": row["stock_batch__variant__flower__name_uz"], "name_ru": row["stock_batch__variant__flower__name_ru"], "color_uz": row["stock_batch__variant__color_uz"], "color_ru": row["stock_batch__variant__color_ru"], "stems": 0, "bunches": Decimal("0")})
        rows[key]["stems"] += int(row["stems"] or 0)
        rows[key]["bunches"] += row["bunches"] or Decimal("0")
    catalog_usage = LeadCatalogUsage.objects.filter(lead_id__in=lead_ids).select_related("catalog_item").values("catalog_item_id").annotate(quantity=Coalesce(Sum("quantity"), 0))
    catalog_quantities = {row["catalog_item_id"]: row["quantity"] for row in catalog_usage}
    composition_rows = CatalogComposition.objects.filter(catalog_item_id__in=catalog_quantities.keys()).select_related("stock_batch__variant__flower")
    for composition in composition_rows:
        quantity = catalog_quantities.get(composition.catalog_item_id, 0)
        variant = composition.stock_batch.variant
        flower = variant.flower
        key = (flower.id, variant.color_uz or "")
        rows.setdefault(key, {"flower_id": flower.id, "name_uz": flower.name_uz, "name_ru": flower.name_ru, "color_uz": variant.color_uz, "color_ru": variant.color_ru, "stems": 0, "bunches": Decimal("0")})
        rows[key]["stems"] += int(composition.quantity_stems * quantity)
        rows[key]["bunches"] += composition.quantity_bunches * quantity
    return sorted([dict(row, bunches=str(row["bunches"])) for row in rows.values()], key=lambda row: row["stems"], reverse=True)[:20]


def top_catalog_items(won_leads):
    return list(LeadCatalogUsage.objects.filter(lead__in=won_leads).select_related("catalog_item").values("catalog_item_id", "catalog_item__name_uz", "catalog_item__name_ru", "catalog_item__arrangement_type", "catalog_item__image_url").annotate(quantity=Coalesce(Sum("quantity"), 0), orders=Count("lead", distinct=True), revenue=Coalesce(Sum("lead__estimated_price"), Decimal("0")), last_sold_at=Max("lead__updated_at")).order_by("-quantity", "-last_sold_at")[:20])


def recent_top_catalog_items(won_leads):
    return list(LeadCatalogUsage.objects.filter(lead__in=won_leads).select_related("catalog_item").values("catalog_item_id", "catalog_item__name_uz", "catalog_item__name_ru", "catalog_item__arrangement_type", "catalog_item__image_url").annotate(quantity=Coalesce(Sum("quantity"), 0), orders=Count("lead", distinct=True), revenue=Coalesce(Sum("lead__estimated_price"), Decimal("0")), last_sold_at=Max("lead__updated_at")).order_by("-last_sold_at", "-quantity")[:20])


def conversation_source_breakdown(conversations):
    rows = {"instagram": 0, "telegram": 0, "mini_app": 0}
    for conversation in conversations.select_related("customer"):
        external_id = conversation.customer.instagram_user_id if conversation.customer_id else ""
        if external_id.startswith("telegram:"):
            rows["telegram"] += 1
        elif external_id.startswith("miniapp:"):
            rows["mini_app"] += 1
        else:
            rows["instagram"] += 1
    return [{"source": key, "count": value} for key, value in rows.items()]


def revenue_by_source(won_leads):
    rows = list(won_leads.values("source").annotate(orders=Count("id"), revenue=Coalesce(Sum("estimated_price"), Decimal("0"))).order_by("source"))
    return [{"source": row["source"] or "unknown", "orders": row["orders"], "revenue": row["revenue"]} for row in rows]


def apply_created_range(queryset, start, end):
    if start:
        queryset = queryset.filter(created_at__gte=start)
    if end:
        queryset = queryset.filter(created_at__lte=end)
    return queryset


def apply_updated_range(queryset, start, end):
    if start:
        queryset = queryset.filter(updated_at__gte=start)
    if end:
        queryset = queryset.filter(updated_at__lte=end)
    return queryset


@extend_schema(request=AISettingsSerializer, responses=AISettingsSerializer)
@api_view(["GET", "PATCH"])
def ai_settings(request):
    if not is_developer(request.user):
        return forbidden()
    obj, _ = AISettings.objects.get_or_create(pk=1)
    if request.method == "PATCH":
        serializer = AISettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    return Response(AISettingsSerializer(obj).data)


@extend_schema(request=IntegrationSettingsSerializer, responses=IntegrationSettingsSerializer)
@api_view(["GET", "PATCH"])
def integrations_settings(request):
    if not is_developer(request.user):
        return forbidden()
    obj, _ = IntegrationSettings.objects.get_or_create(pk=1)
    if request.method == "PATCH":
        serializer = IntegrationSettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    return Response(IntegrationSettingsSerializer(obj).data)


@extend_schema(request=UploadSerializer, responses=UploadResponseSerializer)
@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload_file(request):
    if not has_page_permission(request.user, "inventory", True):
        return forbidden()
    serializer = UploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    uploaded = serializer.validated_data["file"]
    path = default_storage.save(f"uploads/{uploaded.name}", uploaded)
    media_url = default_storage.url(path)
    url = f"{settings.PUBLIC_BASE_URL}{media_url}" if settings.PUBLIC_BASE_URL else request.build_absolute_uri(media_url)
    return Response({"url": url, "path": path}, status=status.HTTP_201_CREATED)


def mini_app_identity(init_data, require_user=False):
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    bot_token = integration.telegram_bot_token
    values = dict(parse_qsl(init_data or "", keep_blank_values=True))
    if bot_token and init_data and not values.get("hash"):
        raise ValueError("Mini app init data noto‘g‘ri")
    if bot_token and values.get("hash"):
        received_hash = values.pop("hash")
        data_check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash, expected):
            raise ValueError("Mini app init data noto‘g‘ri")
    user_payload = {}
    user_id = values.get("user", "")
    if user_id:
        try:
            user_payload = json.loads(user_id)
            user_id = str(user_payload.get("id", user_id))
        except (TypeError, ValueError):
            pass
    if require_user and not user_id:
        raise ValueError("Telegram init data kerak")
    return {"user_id": (user_id[:100] or "guest"), "user": user_payload, "auth_date": values.get("auth_date", ""), "query_id": values.get("query_id", "")}


def mini_app_user(init_data):
    return mini_app_identity(init_data, require_user=True)["user_id"]


def mini_app_customer(identity):
    return Customer.objects.filter(instagram_user_id=f"miniapp:{identity['user_id']}").first()


def mini_app_order_rows(customer):
    if not customer:
        return []
    rows = Lead.objects.filter(customer=customer).select_related("branch").order_by("-created_at")[:30]
    statuses = {row.key: row.name_uz for row in LeadStatus.objects.filter(key__in=[lead.status for lead in rows])}
    return [{
        "id": row.id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "status": row.status,
        "status_label": statuses.get(row.status, row.status),
        "source": row.source,
        "branch": BranchSerializer(row.branch).data,
        "arrangement_type": row.arrangement_type,
        "request": row.request_uz or row.request_ru,
        "estimated_price": row.estimated_price,
        "details": row.details or {},
    } for row in rows]


def mini_app_branch(branch_id=None):
    branch = Branch.objects.filter(id=branch_id, is_active=True).first() if branch_id else None
    return branch or Branch.objects.filter(is_active=True).first() or Branch.objects.first()


def mini_app_quote_payload(data):
    branch = mini_app_branch(data.get("branch"))
    if not branch:
        raise ValueError("Filial topilmadi")
    business, _ = BusinessSettings.objects.get_or_create(pk=1)
    total = Decimal("0")
    lines = []
    stems_total = 0
    for item in data["items"]:
        if item.get("catalog_item"):
            catalog = CatalogItem.objects.filter(id=item["catalog_item"], branch=branch, status="available").first()
            if not catalog:
                raise ValueError("Katalog guli topilmadi")
            quantity = item.get("quantity") or 1
            line_total = catalog.price * quantity
            total += line_total
            lines.append({"type": "catalog", "id": catalog.id, "name_uz": catalog.name_uz, "name_ru": catalog.name_ru, "quantity": quantity, "unit_price": str(catalog.price), "total": str(line_total)})
            continue
        batch = StockBatch.objects.select_related("variant__flower").filter(id=item.get("stock_batch"), branch=branch, is_active=True).first()
        if not batch:
            raise ValueError("Sklad partiyasi topilmadi")
        quantity_stems = item.get("quantity_stems") or item.get("quantity") or batch.minimum_sale_stems
        if quantity_stems < batch.minimum_sale_stems:
            raise ValueError(f"{batch.variant.flower.name_uz} uchun minimal sotuv {batch.minimum_sale_stems} dona")
        if quantity_stems > batch.remaining_stems:
            raise ValueError(f"{batch.batch_number} partiyada yetarli qoldiq yo‘q")
        line_total = batch.sale_price_per_stem * quantity_stems
        stems_total += quantity_stems
        total += line_total
        lines.append({"type": "stock", "id": batch.id, "flower_uz": batch.variant.flower.name_uz, "flower_ru": batch.variant.flower.name_ru, "variant_uz": batch.variant.name_uz, "color_uz": batch.variant.color_uz, "quantity_stems": quantity_stems, "price_per_stem": str(batch.sale_price_per_stem), "total": str(line_total)})
    packaging = None
    if data["arrangement_type"] in ["bouquet", "basket"]:
        total += business.default_florist_fee
    if data["arrangement_type"] == "basket":
        packaging = Packaging.objects.filter(id=data.get("packaging"), branch=branch, is_active=True).first() if data.get("packaging") else Packaging.objects.filter(branch=branch, packaging_type="basket", is_active=True, capacity_min_stems__lte=max(stems_total, 1), capacity_max_stems__gte=max(stems_total, 1)).order_by("sale_price").first()
        if packaging:
            total += packaging.sale_price
    return {"branch": branch, "lines": lines, "packaging": PackagingSerializer(packaging).data if packaging else None, "florist_fee": str(business.default_florist_fee if data["arrangement_type"] in ["bouquet", "basket"] else Decimal("0")), "estimated_price": str(total), "price_is_estimate": True}


def mini_app_request_text(arrangement_type, quote, note=""):
    labels = {"bouquet": "Buket", "basket": "Savat", "stems": "Donalab gul", "catalog": "Tayyor katalog"}
    lines = [f"Mini app buyurtma: {labels.get(arrangement_type, arrangement_type)}"]
    for row in quote["lines"]:
        if row["type"] == "catalog":
            lines.append(f"- {row['name_uz']}: {row['quantity']} ta, {row['total']} so‘m")
        else:
            name = f"{row['flower_uz']} {row['variant_uz']} {row['color_uz']}".strip()
            lines.append(f"- {name}: {row['quantity_stems']} dona, {row['total']} so‘m")
    if quote.get("packaging"):
        packaging = quote["packaging"]
        lines.append(f"- Savat/qadoq: {packaging['name_uz']} ({packaging.get('size', '')}), {packaging['sale_price']} so‘m")
    if Decimal(str(quote["florist_fee"])) > 0:
        lines.append(f"- Florist xizmati: {quote['florist_fee']} so‘m")
    lines.append(f"Jami taxminan: {quote['estimated_price']} so‘m")
    if note:
        lines.append(f"Izoh: {note}")
    return "\n".join(lines)


@extend_schema(parameters=[MiniAppInitSerializer], responses=inline_serializer(name="MiniAppCustomer", fields={"customer": CustomerSerializer(allow_null=True), "orders": serializers.ListField(child=serializers.DictField())}))
@api_view(["GET"])
@permission_classes([AllowAny])
def mini_app_me(request):
    try:
        identity = mini_app_identity(request.query_params.get("init_data", ""), require_user=True)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    customer = mini_app_customer(identity)
    return Response({"customer": CustomerSerializer(customer).data if customer else None, "orders": mini_app_order_rows(customer)})


@extend_schema(parameters=[MiniAppInitSerializer], responses=inline_serializer(name="MiniAppCatalog", fields={"catalog": CatalogItemSerializer(many=True), "stock": StockBatchSerializer(many=True), "packaging": PackagingSerializer(many=True), "customer": CustomerSerializer(allow_null=True), "orders": serializers.ListField(child=serializers.DictField())}))
@api_view(["GET"])
@permission_classes([AllowAny])
def mini_app_catalog(request):
    try:
        identity = mini_app_identity(request.query_params.get("init_data", ""))
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    branch = mini_app_branch(request.query_params.get("branch"))
    catalog = CatalogItem.objects.filter(branch=branch, status="available").select_related("branch", "social_post")[:50]
    stock = StockBatch.objects.filter(branch=branch, is_active=True, remaining_stems__gt=0).select_related("branch", "variant__flower")[:100]
    packaging = Packaging.objects.filter(branch=branch, is_active=True)[:50]
    customer = mini_app_customer(identity)
    return Response({"catalog": CatalogItemSerializer(catalog, many=True).data, "stock": StockBatchSerializer(stock, many=True).data, "packaging": PackagingSerializer(packaging, many=True).data, "customer": CustomerSerializer(customer).data if customer else None, "orders": mini_app_order_rows(customer)})


@extend_schema(request=MiniAppQuoteSerializer, responses=inline_serializer(name="MiniAppQuoteResponse", fields={"lines": serializers.ListField(child=serializers.DictField()), "packaging": serializers.DictField(allow_null=True), "florist_fee": serializers.CharField(), "estimated_price": serializers.CharField(), "price_is_estimate": serializers.BooleanField()}))
@api_view(["POST"])
@permission_classes([AllowAny])
def mini_app_quote(request):
    serializer = MiniAppQuoteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        mini_app_user(serializer.validated_data.get("init_data", ""))
        quote = mini_app_quote_payload(serializer.validated_data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    quote.pop("branch")
    return Response(quote)


@extend_schema(request=MiniAppLeadSerializer, responses=LeadSerializer)
@api_view(["POST"])
@permission_classes([AllowAny])
def mini_app_lead(request):
    serializer = MiniAppLeadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        mini_user = mini_app_user(serializer.validated_data.get("init_data", ""))
        quote = mini_app_quote_payload(serializer.validated_data)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    phone = normalize_phone(serializer.validated_data["phone"]) or serializer.validated_data["phone"]
    customer, _ = Customer.objects.update_or_create(instagram_user_id=f"miniapp:{mini_user}", defaults={"name": serializer.validated_data["name"], "phone": phone, "branch": quote["branch"], "language": "uz"})
    request_text = mini_app_request_text(serializer.validated_data["arrangement_type"], quote, serializer.validated_data.get("note", ""))
    details = {"lines": quote["lines"], "packaging": quote["packaging"], "florist_fee": quote["florist_fee"], "estimated_price": quote["estimated_price"], "price_is_estimate": quote["price_is_estimate"], "note": serializer.validated_data.get("note", "")}
    lead = Lead.objects.create(customer=customer, branch=quote["branch"], status="new", request_uz=request_text, arrangement_type=serializer.validated_data["arrangement_type"], estimated_price=quote["estimated_price"], source="mini_app", details=details)
    for row in quote["lines"]:
        if row["type"] == "catalog":
            catalog_item = CatalogItem.objects.filter(id=row["id"], branch=quote["branch"]).first()
            if catalog_item:
                LeadCatalogUsage.objects.create(lead=lead, catalog_item=catalog_item, quantity=row["quantity"])
        elif row["type"] == "stock":
            batch = StockBatch.objects.filter(id=row["id"], branch=quote["branch"]).first()
            if batch:
                LeadStockUsage.objects.create(lead=lead, stock_batch=batch, quantity_stems=row["quantity_stems"], quantity_bunches=Decimal(row["quantity_stems"]) / Decimal(batch.stems_per_bunch))
    Notification.objects.create(branch=quote["branch"], notification_type="lead", title_uz=f"Mini app lead: {customer}", title_ru=f"Mini app лид: {customer}", body_uz=request_text, body_ru=request_text, reference_type="lead", reference_id=lead.id)
    return Response(LeadSerializer(lead).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=inline_serializer(name="InstagramWebhookPayload", fields={}),
    responses={
        200: OpenApiResponse(description="Webhook verified or event received"),
        403: OpenApiResponse(description="Invalid verify token"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def instagram_webhook(request):
    if request.method == "GET":
        integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
        verify_token = integration.instagram_verify_token or settings.INSTAGRAM_VERIFY_TOKEN
        if request.query_params.get("hub.verify_token") == verify_token:
            return Response(int(request.query_params.get("hub.challenge", "0")))
        return Response({"detail": "Invalid verify token"}, status=status.HTTP_403_FORBIDDEN)
    from .tasks import process_instagram_webhook
    process_instagram_webhook.delay(request.data)
    return Response({"status": "EVENT_RECEIVED"})


@extend_schema(request=inline_serializer(name="TelegramWebhookPayload", fields={}), responses={200: OpenApiResponse(description="Telegram event received")})
@api_view(["POST"])
@permission_classes([AllowAny])
def telegram_webhook(request):
    from .tasks import process_telegram_webhook
    process_telegram_webhook.delay(request.data)
    return Response({"status": "EVENT_RECEIVED"})
