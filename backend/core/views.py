from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder
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
from .branching import default_branch
from .models import AISettings, AuditLog, Branch, BusinessSettings, CatalogComposition, CatalogItem, Conversation, Customer, FloristAttendance, FloristProfile, FloristSalaryEntry, FloristVolumeRate, Flower, FlowerVariant, InstagramSettings, InstagramWebhookEvent, IntegrationSettings, Lead, LeadCatalogUsage, LeadStatus, LeadStockUsage, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement, Supplier
from .permissions import RolePermission, has_page_permission
from .serializers import AISettingsSerializer, AIPauseRequestSerializer, AuditLogSerializer, BranchSerializer, BusinessSettingsSerializer, CatalogItemSerializer, ConversationSerializer, CustomerSerializer, EuroFlowersTokenObtainPairSerializer, FloristAttendanceSerializer, FloristProfileSerializer, FloristSalaryEntrySerializer, FloristVolumeRateSerializer, FlowerSerializer, FlowerVariantSerializer, InstagramSettingsSerializer, InstagramWebhookEventSerializer, IntegrationSettingsSerializer, LeadColumnReorderSerializer, LeadMoveSerializer, LeadSerializer, LeadStatusSerializer, MiniAppInitSerializer, MiniAppLeadSerializer, MiniAppQuoteSerializer, MovementRequestSerializer, NotificationSerializer, PackagingMovementRequestSerializer, PackagingMovementSerializer, PackagingSerializer, PagePermissionSerializer, SendResponseSerializer, SimulateResponseSerializer, SocialPostSerializer, StockBatchSerializer, StockMovementSerializer, SupplierSerializer, TextRequestSerializer, UploadResponseSerializer, UploadSerializer, UserSerializer, UserWriteSerializer
from .inventory_services import apply_packaging_movement, apply_stock_movement, deduct_catalog_stock, deduct_lead_stock, mark_catalog_sold, restore_catalog_inventory, restore_lead_stock
from .platform_services import instagram_send, telegram_send
from .services import mini_app_custom_quote_ai, normalize_phone, process_customer_message


class CreatedAtRangeFilter(django_filters.FilterSet):
    created_at = django_filters.DateTimeFromToRangeFilter()


class LeadFilter(CreatedAtRangeFilter):
    class Meta:
        model = Lead
        fields = ["status", "arrangement_type", "assigned_to", "social_post", "created_at"]


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
    supplier = django_filters.ModelChoiceFilter(field_name="batch__supplier", queryset=Supplier.objects.all())

    class Meta:
        model = StockMovement
        fields = ["batch", "supplier", "movement_type", "created_at"]


class PackagingMovementFilter(CreatedAtRangeFilter):
    class Meta:
        model = PackagingMovement
        fields = ["packaging", "movement_type", "created_at"]


class StockBatchFilter(CreatedAtRangeFilter):
    class Meta:
        model = StockBatch
        fields = ["variant", "supplier", "height_cm", "height_from_cm", "height_to_cm", "is_active", "created_at"]


class FloristAttendanceFilter(CreatedAtRangeFilter):
    class Meta:
        model = FloristAttendance
        fields = ["florist", "work_date", "source", "created_at"]


class FloristSalaryEntryFilter(CreatedAtRangeFilter):
    class Meta:
        model = FloristSalaryEntry
        fields = ["florist", "source", "work_date", "created_at"]


class ConversationFilter(CreatedAtRangeFilter):
    class Meta:
        model = Conversation
        fields = ["status", "assigned_to", "created_at"]


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


def json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def instance_snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        key = field.name
        value = getattr(instance, field.attname)
        data[key] = value
    if isinstance(instance, Lead):
        data["catalog_usage"] = [{"catalog_item": row.catalog_item_id, "catalog_name": row.catalog_item.name_uz, "quantity": row.quantity} for row in instance.catalog_usage.select_related("catalog_item")]
        data["stock_usage"] = [{"stock_batch": row.stock_batch_id, "batch_number": row.stock_batch.batch_number, "quantity_stems": row.quantity_stems, "quantity_bunches": row.quantity_bunches} for row in instance.stock_usage.select_related("stock_batch")]
        data["packaging_usage"] = [{"packaging": row.packaging_id, "packaging_name": row.packaging.name_uz, "quantity": row.quantity} for row in instance.packaging_usage.select_related("packaging")]
    if isinstance(instance, User):
        profile = getattr(instance, "profile", None)
        data["profile"] = {"role": profile.role, "language": profile.language} if profile else None
        data["permissions"] = [{"page": row.page, "can_view": row.can_view, "can_control": row.can_control} for row in instance.page_permissions.order_by("page")]
    return json_safe(data)


def changed_snapshot(before, after):
    keys = sorted(set(before) | set(after))
    before_changed = {}
    after_changed = {}
    for key in keys:
        if before.get(key) != after.get(key):
            before_changed[key] = before.get(key)
            after_changed[key] = after.get(key)
    return before_changed, after_changed


def write_audit(user, action, instance, before=None, after=None):
    if isinstance(instance, AuditLog):
        return None
    return AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        entity_type=instance.__class__.__name__,
        entity_id=str(getattr(instance, "id", "")),
        before=json_safe(before or {}),
        after=json_safe(after or {}),
    )


class ScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [RolePermission]

    def branch_ids(self):
        return None

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

    def perform_create(self, serializer):
        instance = serializer.save()
        write_audit(self.request.user, f"{instance.__class__.__name__.lower()}_created", instance, before={}, after=instance_snapshot(instance))

    def perform_update(self, serializer):
        before = instance_snapshot(serializer.instance)
        instance = serializer.save()
        after = instance_snapshot(instance)
        before_changed, after_changed = changed_snapshot(before, after)
        if before_changed or after_changed:
            write_audit(self.request.user, f"{instance.__class__.__name__.lower()}_updated", instance, before=before_changed, after=after_changed)

    def perform_destroy(self, instance):
        before = instance_snapshot(instance)
        try:
            instance.delete()
            write_audit(self.request.user, f"{instance.__class__.__name__.lower()}_deleted", instance, before=before, after={})
        except ProtectedError:
            field_names = {field.name for field in instance._meta.fields}
            if "is_active" not in field_names:
                raise
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])
            before_changed, after_changed = changed_snapshot(before, instance_snapshot(instance))
            write_audit(self.request.user, f"{instance.__class__.__name__.lower()}_archived", instance, before=before_changed, after=after_changed)


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
        response = super().create(request, *args, **kwargs)
        if response.status_code < 400:
            user = User.objects.filter(id=response.data.get("id")).first()
            if user:
                write_audit(request.user, "user_created", user, before={}, after=instance_snapshot(user))
        return response

    def update(self, request, *args, **kwargs):
        if request.data.get("role") == "developer" and not self.get_developer_role():
            return forbidden()
        user = self.get_object()
        before = instance_snapshot(user)
        response = super().update(request, *args, **kwargs)
        if response.status_code < 400:
            user.refresh_from_db()
            before_changed, after_changed = changed_snapshot(before, instance_snapshot(user))
            if before_changed or after_changed:
                write_audit(request.user, "user_updated", user, before=before_changed, after=after_changed)
        return response

    def partial_update(self, request, *args, **kwargs):
        if request.data.get("role") == "developer" and not self.get_developer_role():
            return forbidden()
        user = self.get_object()
        before = instance_snapshot(user)
        response = super().partial_update(request, *args, **kwargs)
        if response.status_code < 400:
            user.refresh_from_db()
            before_changed, after_changed = changed_snapshot(before, instance_snapshot(user))
            if before_changed or after_changed:
                write_audit(request.user, "user_updated", user, before=before_changed, after=after_changed)
        return response

    def get_queryset(self):
        queryset = super().get_queryset()
        role = getattr(getattr(self.request.user, "profile", None), "role", None)
        if role != "developer":
            queryset = queryset.exclude(profile__role="developer")
        return queryset

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        before = instance_snapshot(user)
        user.is_active = False
        user.save(update_fields=["is_active"])
        before_changed, after_changed = changed_snapshot(before, instance_snapshot(user))
        write_audit(request.user, "user_deactivated", user, before=before_changed, after=after_changed)
        return Response(UserSerializer(user).data)


class FlowerViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = Flower.objects.prefetch_related("variants").all()
    serializer_class = FlowerSerializer
    search_fields = ["name_uz"]

    def perform_destroy(self, instance):
        before = instance_snapshot(instance)
        try:
            instance.delete()
            write_audit(self.request.user, "flower_deleted", instance, before=before, after={})
        except ProtectedError:
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])
            archived_variants = list(instance.variants.filter(is_active=True).values_list("id", flat=True))
            instance.variants.filter(is_active=True).update(is_active=False, updated_at=timezone.now())
            after = instance_snapshot(instance)
            after["archived_variants"] = archived_variants
            before_changed, after_changed = changed_snapshot(before, after)
            write_audit(self.request.user, "flower_archived", instance, before=before_changed, after=after_changed)


class FlowerVariantViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = FlowerVariant.objects.select_related("flower").all()
    serializer_class = FlowerVariantSerializer
    filterset_fields = ["flower", "is_active"]
    search_fields = ["name_uz", "color_uz", "flower__name_uz"]

    def perform_destroy(self, instance):
        before = instance_snapshot(instance)
        try:
            instance.delete()
            write_audit(self.request.user, "flowervariant_deleted", instance, before=before, after={})
        except ProtectedError:
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])
            before_changed, after_changed = changed_snapshot(before, instance_snapshot(instance))
            write_audit(self.request.user, "flowervariant_archived", instance, before=before_changed, after=after_changed)


class SupplierViewSet(ScopedViewSet):
    permission_page = "suppliers"
    write_roles = ["admin", "warehouse"]
    queryset = Supplier.objects.annotate(batches_count=Count("stock_batches", distinct=True), total_received_stems=Coalesce(Sum("stock_batches__received_stems"), 0)).all()
    serializer_class = SupplierSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name", "phone", "notes"]


class FloristVolumeRateViewSet(ScopedViewSet):
    permission_page = "florists"
    write_roles = ["admin"]
    queryset = FloristVolumeRate.objects.select_related("branch").all()
    serializer_class = FloristVolumeRateSerializer
    filterset_fields = ["branch", "arrangement_type", "volume", "is_active"]


class FloristProfileViewSet(ScopedViewSet):
    permission_page = "florists"
    write_roles = ["admin", "supervisor"]
    queryset = FloristProfile.objects.select_related("user", "branch").annotate(salary_total=Coalesce(Sum("salary_entries__amount"), Decimal("0")), catalog_count=Count("catalog_items", distinct=True)).all()
    serializer_class = FloristProfileSerializer
    filterset_fields = ["staff_type", "branch", "is_active"]
    search_fields = ["user__first_name", "user__last_name", "user__username", "phone"]

    @action(detail=False, methods=["get"], url_path="me")
    def me_profile(self, request):
        profile = FloristProfile.objects.select_related("user", "branch").filter(user=request.user).first()
        if not profile:
            return Response({"detail": "Florist profili topilmadi"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(profile).data)

    @action(detail=False, methods=["get"], url_path="me/dashboard")
    def me_dashboard(self, request):
        profile = FloristProfile.objects.filter(user=request.user).first()
        if not profile:
            return Response({"detail": "Florist profili topilmadi"}, status=status.HTTP_404_NOT_FOUND)
        start, end = dashboard_period(request)
        salary = apply_created_range(profile.salary_entries.all(), start, end)
        attendance = apply_created_range(profile.attendance.all(), start, end)
        catalog = apply_created_range(profile.catalog_items.all(), start, end)
        return Response({
            "florist": FloristProfileSerializer(profile).data,
            "period": {"from": start, "to": end},
            "salary_total": salary.aggregate(value=Coalesce(Sum("amount"), Decimal("0")))["value"],
            "salary_entries_count": salary.count(),
            "catalog_count": catalog.count(),
            "custom_catalog_count": catalog.filter(catalog_kind="custom").count(),
            "attendance_days": attendance.count(),
            "latest_salary_entries": FloristSalaryEntrySerializer(salary.select_related("catalog_item", "florist__user")[:20], many=True).data,
            "latest_attendance": FloristAttendanceSerializer(attendance[:20], many=True).data,
        })


class FloristAttendanceViewSet(ScopedViewSet):
    permission_page = "attendance"
    write_roles = ["admin", "supervisor", "florist", "apprentice"]
    queryset = FloristAttendance.objects.select_related("florist__user", "florist__branch").all()
    serializer_class = FloristAttendanceSerializer
    filterset_class = FloristAttendanceFilter

    def get_queryset(self):
        queryset = super().get_queryset()
        if has_page_permission(self.request.user, "attendance", True):
            return queryset
        profile = FloristProfile.objects.filter(user=self.request.user).first()
        return queryset.filter(florist=profile) if profile else queryset.none()

    def _profile_from_request(self, request):
        florist_id = request.data.get("florist")
        if florist_id and has_page_permission(request.user, "attendance", True):
            profile = FloristProfile.objects.filter(id=florist_id).first()
        else:
            profile = FloristProfile.objects.filter(user=request.user).first()
        if not profile:
            raise serializers.ValidationError({"florist": "Florist profili topilmadi"})
        return profile

    @action(detail=False, methods=["post"], url_path="check-in")
    def check_in(self, request):
        profile = self._profile_from_request(request)
        checked_at = parse_datetime(request.data.get("checked_at") or "") or timezone.now()
        if timezone.is_naive(checked_at):
            checked_at = timezone.make_aware(checked_at)
        work_date = timezone.localtime(checked_at).date()
        row, _ = FloristAttendance.objects.get_or_create(florist=profile, work_date=work_date, defaults={"source": request.data.get("source") or "mobile"})
        row.check_in_at = row.check_in_at or checked_at
        row.check_in_latitude = request.data.get("latitude") or row.check_in_latitude
        row.check_in_longitude = request.data.get("longitude") or row.check_in_longitude
        row.source = request.data.get("source") or row.source
        row.note = request.data.get("note") or row.note
        row.save(update_fields=["check_in_at", "check_in_latitude", "check_in_longitude", "source", "note", "updated_at"])
        if profile.staff_type == "apprentice" and profile.daily_pay:
            FloristSalaryEntry.objects.update_or_create(florist=profile, source="daily", attendance=row, defaults={"amount": profile.daily_pay, "work_date": work_date, "note": "Shogird kunlik ish haqi", "created_by": request.user})
        return Response(self.get_serializer(row).data)

    @action(detail=False, methods=["post"], url_path="check-out")
    def check_out(self, request):
        profile = self._profile_from_request(request)
        checked_at = parse_datetime(request.data.get("checked_at") or "") or timezone.now()
        if timezone.is_naive(checked_at):
            checked_at = timezone.make_aware(checked_at)
        work_date = timezone.localtime(checked_at).date()
        row, _ = FloristAttendance.objects.get_or_create(florist=profile, work_date=work_date, defaults={"source": request.data.get("source") or "mobile"})
        row.check_out_at = checked_at
        row.check_out_latitude = request.data.get("latitude") or row.check_out_latitude
        row.check_out_longitude = request.data.get("longitude") or row.check_out_longitude
        row.source = request.data.get("source") or row.source
        row.note = request.data.get("note") or row.note
        row.save(update_fields=["check_out_at", "check_out_latitude", "check_out_longitude", "source", "note", "updated_at"])
        return Response(self.get_serializer(row).data)


class FloristSalaryEntryViewSet(ScopedViewSet):
    permission_page = "florists"
    write_roles = ["admin", "supervisor"]
    queryset = FloristSalaryEntry.objects.select_related("florist__user", "catalog_item", "attendance", "created_by").all()
    serializer_class = FloristSalaryEntrySerializer
    filterset_class = FloristSalaryEntryFilter

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class StockBatchViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = StockBatch.objects.select_related("branch", "variant__flower", "supplier").all()
    serializer_class = StockBatchSerializer
    filterset_class = StockBatchFilter
    search_fields = ["batch_number", "variant__flower__name_uz", "variant__name_uz", "variant__color_uz", "supplier__name", "supplier__phone"]
    ordering_fields = ["received_at", "remaining_stems", "sale_price_per_stem", "height_cm", "height_from_cm", "height_to_cm"]

    def perform_create(self, serializer):
        batch = serializer.save()
        StockMovement.objects.create(batch=batch, movement_type="in", quantity_stems=batch.received_stems, quantity_bunches=batch.received_stems / batch.stems_per_bunch, reason="Partiya kirimi", performed_by=self.request.user)
        AuditLog.objects.create(user=self.request.user, action="stock_received", entity_type="StockBatch", entity_id=str(batch.id), after={"received_stems": batch.received_stems})
        if batch.supplier_id:
            title = "Yangi gul kirimi"
            body = f"{batch.supplier.name} postavshikdan {batch.variant.flower.name_uz} {batch.variant.name_uz} {batch.variant.color_uz} keldi. Partiya: {batch.batch_number}. Miqdor: {batch.received_stems} dona."
            Notification.objects.create(branch=batch.branch, notification_type="supplier_stock", title_uz=title, title_ru=title, body_uz=body, body_ru=body, reference_type="stock_batch", reference_id=batch.id)
            integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
            group_chat_id = integration.telegram_group_chat_id or settings.TELEGRAM_GROUP_CHAT_ID
            if group_chat_id:
                try:
                    telegram_send(group_chat_id, f"{title}\n{body}")
                except Exception as exc:
                    print(f"SUPPLIER_STOCK_TELEGRAM_FAILED batch={batch.id} error={exc}", flush=True)

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
            movement = apply_stock_movement(batch, serializer.validated_data["movement_type"], serializer.validated_data.get("quantity_stems"), serializer.validated_data.get("reason", ""), request.user, serializer.validated_data.get("quantity_bunches"))
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(StockMovementSerializer(movement).data)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [RolePermission]
    permission_page = "inventory"
    queryset = StockMovement.objects.select_related("batch__variant__flower", "performed_by").all()
    serializer_class = StockMovementSerializer
    filterset_class = StockMovementFilter


class PackagingViewSet(ScopedViewSet):
    permission_page = "inventory"
    write_roles = ["admin", "warehouse"]
    queryset = Packaging.objects.select_related("branch").all()
    serializer_class = PackagingSerializer
    filterset_fields = ["packaging_type", "is_active"]
    search_fields = ["name_uz"]

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


class CatalogItemViewSet(ScopedViewSet):
    permission_page = "catalog"
    write_roles = ["admin", "florist", "content", "warehouse"]
    queryset = CatalogItem.objects.select_related("branch", "social_post").prefetch_related("composition__stock_batch__variant__flower", "materials__packaging").all()
    serializer_class = CatalogItemSerializer
    filterset_fields = ["status", "arrangement_type"]
    search_fields = ["name_uz", "description_uz", "description_ru"]

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

    def perform_destroy(self, instance):
        with transaction.atomic():
            item = CatalogItem.objects.select_for_update().get(pk=instance.pk)
            before = instance_snapshot(item)
            restore_catalog_inventory(item, self.request.user)
            item.refresh_from_db()
            try:
                item.delete()
                write_audit(self.request.user, "catalog_deleted", item, before=before, after={})
            except ProtectedError:
                item.status = "archived"
                item.save(update_fields=["status", "updated_at"])
                write_audit(self.request.user, "catalog_archived", item, before=before, after=instance_snapshot(item))


class CustomerViewSet(ScopedViewSet):
    permission_page = "customers"
    write_roles = ["admin", "operator"]
    queryset = Customer.objects.select_related("branch").annotate(purchases_count=Count("leads", filter=Q(leads__status="won")), total_spent=Coalesce(Sum("leads__estimated_price", filter=Q(leads__status="won")), Decimal("0"))).all()
    serializer_class = CustomerSerializer
    filterset_fields = ["language", "is_blocked"]
    search_fields = ["name", "phone", "instagram_username"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("include_incomplete") == "true":
            return queryset
        return queryset.exclude(Q(name="") | Q(phone=""))

    def destroy(self, request, *args, **kwargs):
        customer = self.get_object()
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            before = instance_snapshot(customer)
            customer.name = ""
            customer.phone = ""
            customer.instagram_username = ""
            customer.instagram_user_id = f"deleted:{customer.id}"
            customer.notes = (customer.notes + "\n" if customer.notes else "") + "Client arxivlandi. Lead tarixi saqlandi."
            customer.is_blocked = True
            customer.save(update_fields=["name", "phone", "instagram_username", "instagram_user_id", "notes", "is_blocked", "updated_at"])
            before_changed, after_changed = changed_snapshot(before, instance_snapshot(customer))
            write_audit(self.request.user, "customer_archived", customer, before=before_changed, after=after_changed)
            return Response({"detail": "Client arxivlandi. Lead tarixi saqlandi.", "id": customer.id, "archived": True})


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
    ordering = ["status", "sort_order", "-created_at", "id"]

    def perform_create(self, serializer):
        with transaction.atomic():
            extra = {}
            if "sort_order" not in serializer.validated_data:
                branch = serializer.validated_data.get("branch") or default_branch()
                status_value = serializer.validated_data.get("status", "new")
                extra["sort_order"] = next_lead_sort_order(branch, status_value)
            lead = serializer.save(**extra)
            write_audit(self.request.user, "lead_created", lead, before={}, after=instance_snapshot(lead))
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
            before_snapshot = instance_snapshot(lead)
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
            after_snapshot = instance_snapshot(lead)
            before_changed, after_changed = changed_snapshot(before_snapshot, after_snapshot)
            if before_changed or after_changed:
                write_audit(self.request.user, "lead_moved", lead, before=before_changed, after=after_changed)
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
        scoped_queryset = self.get_queryset()
        leads = list(scoped_queryset.filter(id__in=lead_ids))
        if len(leads) != len(set(lead_ids)):
            return Response({"lead_ids": "Lead topilmadi yoki sizda ruxsat yo‘q"}, status=status.HTTP_400_BAD_REQUEST)
        target_existing_ids = set(scoped_queryset.filter(status=status_value).values_list("id", flat=True))
        incoming_ids = set(lead_ids)
        missing_ids = target_existing_ids - incoming_ids
        if missing_ids:
            return Response({"lead_ids": "Target column lead_ids to‘liq yuborilishi kerak", "missing_ids": sorted(missing_ids)}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            for index, lead_id in enumerate(lead_ids, start=1):
                lead = Lead.objects.select_for_update().get(id=lead_id)
                before_snapshot = instance_snapshot(lead)
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
                lead.refresh_from_db()
                after_snapshot = instance_snapshot(lead)
                before_changed, after_changed = changed_snapshot(before_snapshot, after_snapshot)
                if before_changed or after_changed:
                    write_audit(self.request.user, "lead_reordered", lead, before=before_changed, after=after_changed)
        return Response({"updated": len(lead_ids)})

    def perform_update(self, serializer):
        with transaction.atomic():
            before_snapshot = instance_snapshot(serializer.instance)
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
            lead.refresh_from_db()
            after_snapshot = instance_snapshot(lead)
            before_changed, after_changed = changed_snapshot(before_snapshot, after_snapshot)
            if before_changed or after_changed:
                write_audit(self.request.user, "lead_updated", lead, before=before_changed, after=after_changed)
            transaction.on_commit(lambda lead_id=lead.id: schedule_lead_recall(Lead.objects.get(id=lead_id)))

    def perform_destroy(self, instance):
        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(pk=instance.pk)
            before_snapshot = instance_snapshot(lead)
            if lead.stock_deducted_at:
                restore_lead_stock(lead, self.request.user)
                lead.refresh_from_db()
            after_restore_snapshot = instance_snapshot(lead)
            write_audit(self.request.user, "lead_deleted", lead, before=before_snapshot, after={"restored_before_delete": after_restore_snapshot})
            lead.delete()


class SocialPostViewSet(ScopedViewSet):
    permission_page = "social_posts"
    write_roles = ["admin", "content"]
    queryset = SocialPost.objects.select_related("branch").prefetch_related("catalog_items__composition__stock_batch__variant__flower", "leads__customer", "leads__catalog_usage__catalog_item").annotate(reply_count=Count("conversations", distinct=True), lead_count=Count("leads", distinct=True)).all()
    serializer_class = SocialPostSerializer
    filterset_fields = ["post_type", "is_targeted", "is_active"]
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
        external_id = conversation.customer.instagram_user_id
        if external_id.startswith("telegram:"):
            telegram_message = conversation.messages.filter(instagram_message_id__startswith="telegram:").order_by("-created_at", "-id").first()
            parts = telegram_message.instagram_message_id.split(":") if telegram_message else []
            chat_id = parts[1] if len(parts) >= 3 else external_id.removeprefix("telegram:")
            telegram_send(chat_id, text)
        else:
            instagram_send(external_id, text)
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
    filterset_fields = ["notification_type", "is_read"]
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

    def get_queryset(self):
        queryset = super().get_queryset()
        role = getattr(getattr(self.request.user, "profile", None), "role", None)
        if role != "developer":
            queryset = queryset.exclude(user__profile__role="developer")
        return queryset


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

    def perform_create(self, serializer):
        permission = serializer.save()
        write_audit(self.request.user, "pagepermission_created", permission, before={}, after=instance_snapshot(permission))

    def perform_update(self, serializer):
        before = instance_snapshot(serializer.instance)
        permission = serializer.save()
        before_changed, after_changed = changed_snapshot(before, instance_snapshot(permission))
        if before_changed or after_changed:
            write_audit(self.request.user, "pagepermission_updated", permission, before=before_changed, after=after_changed)

    def perform_destroy(self, instance):
        before = instance_snapshot(instance)
        write_audit(self.request.user, "pagepermission_deleted", instance, before=before, after={})
        instance.delete()


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
    won_leads = leads.filter(status="won")
    period_leads = apply_created_range(leads, period_start, period_end)
    period_customers = apply_created_range(customers, period_start, period_end)
    period_conversations = apply_created_range(conversations, period_start, period_end)
    period_won_leads = apply_updated_range(won_leads, period_start, period_end)
    period_stock_out = apply_created_range(stock_movements.filter(movement_type="out", quantity_stems__lt=0), period_start, period_end)
    flowers_sold = period_stock_out.aggregate(value=Coalesce(Sum("quantity_stems"), 0))["value"] or 0
    period_catalog_sold = apply_updated_range(catalog.filter(quantity_sold__gt=0), period_start, period_end)
    catalog_financials = catalog_sale_financials(period_catalog_sold)
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
        "florist_salary_total": apply_created_range(FloristSalaryEntry.objects.all(), period_start, period_end).aggregate(value=Coalesce(Sum("amount"), Decimal("0")))["value"],
        "flowers_sold_stems": abs(int(flowers_sold)),
        "catalog_revenue": catalog_financials["revenue"],
        "catalog_cost": catalog_financials["cost"],
        "catalog_discount": catalog_financials["discount"],
        "net_profit": catalog_financials["profit"],
        "batch_inventory_stats": batch_inventory_stats(period_start, period_end)[:10],
        "florist_production_stats": florist_production_stats(period_start, period_end)[:10],
        "conversion_rate": round((won_leads.count() / conversion_base) * 100, 2) if conversion_base else 0,
        "available_catalog": catalog.filter(status="available").count(),
        "pending_deductions": catalog.filter(quantity_sold__gt=F("quantity_stock_deducted")).count(),
        "unread_notifications": notifications.filter(is_read=False).count(),
        "ai_conversations": conversations.filter(status="ai").count(),
        "operator_conversations": conversations.filter(status="operator").count(),
        "stock_stems": stock.aggregate(value=Coalesce(Sum("remaining_stems"), 0))["value"],
        "low_stock": stock.filter(remaining_stems__lte=F("minimum_sale_stems")).count(),
        "lead_pipeline": list(leads.values("status").annotate(count=Count("id")).order_by("status")),
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
    period_leads = apply_created_range(leads, period_start, period_end)
    period_conversations = apply_created_range(conversations, period_start, period_end)
    period_customers = apply_created_range(customers, period_start, period_end)
    won_leads = leads.filter(status="won")
    period_won_leads = apply_updated_range(won_leads, period_start, period_end)
    period_stock_out = apply_created_range(stock_movements.filter(movement_type="out", quantity_stems__lt=0), period_start, period_end)
    flowers_sold = period_stock_out.aggregate(value=Coalesce(Sum("quantity_stems"), 0))["value"] or 0
    period_catalog_sold = apply_updated_range(CatalogItem.objects.filter(quantity_sold__gt=0), period_start, period_end)
    catalog_financials = catalog_sale_financials(period_catalog_sold)
    data = {
        "period": {"from": period_start, "to": period_end},
        "summary": {
            "leads": period_leads.count(),
            "customers": period_customers.count(),
            "conversations": period_conversations.count(),
            "orders": period_won_leads.count(),
            "revenue": period_won_leads.aggregate(value=Coalesce(Sum("estimated_price"), Decimal("0")))["value"],
            "florist_revenue": period_won_leads.aggregate(value=Coalesce(Sum("florist_fee"), Decimal("0")))["value"],
            "florist_salary_total": apply_created_range(FloristSalaryEntry.objects.all(), period_start, period_end).aggregate(value=Coalesce(Sum("amount"), Decimal("0")))["value"],
            "flowers_sold_stems": abs(int(flowers_sold)),
            "catalog_revenue": catalog_financials["revenue"],
            "catalog_cost": catalog_financials["cost"],
            "catalog_discount": catalog_financials["discount"],
            "net_profit": catalog_financials["profit"],
            "conversion_rate": round((period_won_leads.count() / (period_conversations.count() or period_leads.count())) * 100, 2) if (period_conversations.count() or period_leads.count()) else 0,
        },
        "daily_stats": analytics_daily_stats(period_leads, period_conversations, period_won_leads, period_start, period_end),
        "top_selling_flowers": top_selling_flowers(period_won_leads),
        "top_catalog_items": top_catalog_items(period_won_leads),
        "recent_top_catalog_items": recent_top_catalog_items(period_won_leads),
        "batch_inventory_stats": batch_inventory_stats(period_start, period_end),
        "florist_production_stats": florist_production_stats(period_start, period_end),
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
        "stock_batch__variant__color_uz",
    ).annotate(stems=Coalesce(Sum("quantity_stems"), 0), bunches=Coalesce(Sum("quantity_bunches"), Decimal("0")))
    for row in stock_rows:
        key = (row["stock_batch__variant__flower_id"], row["stock_batch__variant__color_uz"] or "")
        rows.setdefault(key, {"flower_id": row["stock_batch__variant__flower_id"], "name_uz": row["stock_batch__variant__flower__name_uz"], "color_uz": row["stock_batch__variant__color_uz"], "stems": 0, "bunches": Decimal("0")})
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
        rows.setdefault(key, {"flower_id": flower.id, "name_uz": flower.name_uz, "color_uz": variant.color_uz, "stems": 0, "bunches": Decimal("0")})
        rows[key]["stems"] += int(composition.quantity_stems * quantity)
        rows[key]["bunches"] += composition.quantity_bunches * quantity
    return sorted([dict(row, bunches=str(row["bunches"])) for row in rows.values()], key=lambda row: row["stems"], reverse=True)[:20]


def top_catalog_items(won_leads):
    return list(LeadCatalogUsage.objects.filter(lead__in=won_leads).select_related("catalog_item").values("catalog_item_id", "catalog_item__name_uz", "catalog_item__arrangement_type", "catalog_item__image_url").annotate(quantity=Coalesce(Sum("quantity"), 0), orders=Count("lead", distinct=True), revenue=Coalesce(Sum("lead__estimated_price"), Decimal("0")), last_sold_at=Max("lead__updated_at")).order_by("-quantity", "-last_sold_at")[:20])


def recent_top_catalog_items(won_leads):
    return list(LeadCatalogUsage.objects.filter(lead__in=won_leads).select_related("catalog_item").values("catalog_item_id", "catalog_item__name_uz", "catalog_item__arrangement_type", "catalog_item__image_url").annotate(quantity=Coalesce(Sum("quantity"), 0), orders=Count("lead", distinct=True), revenue=Coalesce(Sum("lead__estimated_price"), Decimal("0")), last_sold_at=Max("lead__updated_at")).order_by("-last_sold_at", "-quantity")[:20])


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


def catalog_sale_financials(queryset):
    revenue = Decimal("0")
    cost = Decimal("0")
    florist_salary = Decimal("0")
    discount = Decimal("0")
    for item in queryset:
        sold = Decimal(item.quantity_sold or 0)
        total = Decimal(item.quantity_total or 1)
        ratio = sold / total if total else Decimal("0")
        revenue += Decimal(item.price or 0) * sold
        cost += Decimal(item.calculated_cost_price or 0) * ratio
        florist_salary += Decimal(item.florist_fee or 0) * sold
        discount += Decimal(item.discount_amount or 0) * ratio
    return {"revenue": revenue, "cost": cost, "florist_salary": florist_salary, "discount": discount, "profit": revenue - cost}


def batch_inventory_stats(start, end):
    movements = apply_created_range(StockMovement.objects.select_related("batch__variant__flower", "batch__supplier").filter(Q(reference_type="catalog_item") | Q(movement_type="waste")), start, end)
    catalog_ids = [row.reference_id for row in movements if row.reference_type == "catalog_item" and row.reference_id]
    catalog_kinds = {item.id: item.catalog_kind for item in CatalogItem.objects.filter(id__in=catalog_ids)}
    rows = {}
    for movement in movements:
        batch = movement.batch
        key = batch.id
        variant = batch.variant
        row = rows.setdefault(key, {
            "batch_id": batch.id,
            "batch_number": batch.batch_number,
            "supplier_id": batch.supplier_id,
            "supplier_name": batch.supplier.name if batch.supplier_id else "",
            "flower": variant.flower.name_uz,
            "variant": variant.name_uz,
            "color": variant.color_uz,
            "standard_catalog_stems": 0,
            "custom_catalog_stems": 0,
            "waste_stems": 0,
            "total_out_stems": 0,
        })
        stems = abs(int(movement.quantity_stems or 0))
        if movement.movement_type == "waste":
            row["waste_stems"] += stems
        elif movement.reference_type == "catalog_item":
            if catalog_kinds.get(movement.reference_id) == "custom":
                row["custom_catalog_stems"] += stems
            else:
                row["standard_catalog_stems"] += stems
        if movement.quantity_stems < 0:
            row["total_out_stems"] += stems
    return sorted(rows.values(), key=lambda row: row["total_out_stems"], reverse=True)


def florist_production_stats(start, end):
    catalog = apply_created_range(CatalogItem.objects.select_related("florist__user").filter(florist__isnull=False), start, end)
    salary = apply_created_range(FloristSalaryEntry.objects.select_related("florist__user"), start, end)
    salary_by_florist = {row["florist_id"]: row["amount"] for row in salary.values("florist_id").annotate(amount=Coalesce(Sum("amount"), Decimal("0")))}
    rows = {}
    for item in catalog:
        profile = item.florist
        row = rows.setdefault(profile.id, {
            "florist_id": profile.id,
            "name": profile.user.get_full_name() or profile.user.username,
            "staff_type": profile.staff_type,
            "standard_bouquets": 0,
            "standard_baskets": 0,
            "custom_bouquets": 0,
            "custom_baskets": 0,
            "catalog_total": 0,
            "salary_total": salary_by_florist.get(profile.id, Decimal("0")),
        })
        row["catalog_total"] += 1
        key = f"{item.catalog_kind}_{'baskets' if item.arrangement_type == 'basket' else 'bouquets'}"
        if key in row:
            row[key] += 1
    return sorted(rows.values(), key=lambda row: row["catalog_total"], reverse=True)


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
        "arrangement_type": row.arrangement_type,
        "request": row.request_uz or row.request_ru,
        "estimated_price": row.estimated_price,
        "details": row.details or {},
    } for row in rows]


def mini_app_branch(branch_id=None):
    return default_branch()


def mini_app_quote_payload(data):
    branch = mini_app_branch(data.get("branch"))
    if data["arrangement_type"] in ["bouquet", "basket"]:
        quote = mini_app_custom_quote_ai(data["request_text"], data["arrangement_type"])
        quote["branch"] = branch
        return quote
    total = Decimal("0")
    lines = []
    for item in data["items"]:
        if item.get("catalog_item"):
            catalog = CatalogItem.objects.filter(id=item["catalog_item"], status="available").first()
            if not catalog:
                raise ValueError("Katalog guli topilmadi")
            quantity = item.get("quantity") or 1
            line_total = catalog.price * quantity
            total += line_total
            lines.append({"type": "catalog", "id": catalog.id, "name_uz": catalog.name_uz, "quantity": quantity, "unit_price": str(catalog.price), "total": str(line_total)})
            continue
        raise ValueError("Mini app katalogda faqat catalog_item ishlatiladi")
    return {"branch": branch, "lines": lines, "packaging": None, "florist_fee": str(Decimal("0")), "estimated_price": str(total), "price_is_estimate": False, "ai_note": ""}


def mini_app_request_text(arrangement_type, quote, note=""):
    labels = {"bouquet": "Buket", "basket": "Savat", "stems": "Donalab gul", "catalog": "Tayyor katalog"}
    lines = [f"Mini app buyurtma: {labels.get(arrangement_type, arrangement_type)}"]
    for row in quote["lines"]:
        if row["type"] == "catalog":
            lines.append(f"- {row['name_uz']}: {row['quantity']} ta, {row['total']} so‘m")
        elif row["type"] == "stock":
            name = f"{row['flower_uz']} {row['variant_uz']} {row['color_uz']}".strip()
            lines.append(f"- {name}: {row['quantity_stems']} dona, {row['total']} so‘m")
        elif row["type"] == "custom_text":
            lines.append(f"- Mijoz matni: {row['request_text']}")
    if quote.get("packaging"):
        packaging = quote["packaging"]
        lines.append(f"- Savat/qadoq: {packaging['name_uz']} ({packaging.get('size', '')}), {packaging['sale_price']} so‘m")
    if Decimal(str(quote["florist_fee"])) > 0:
        lines.append(f"- Florist xizmati: {quote['florist_fee']} so‘m")
    total_label = "Jami taxminan" if quote.get("price_is_estimate") else "Jami"
    lines.append(f"{total_label}: {quote['estimated_price']} so‘m")
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


@extend_schema(parameters=[MiniAppInitSerializer], responses=inline_serializer(name="MiniAppCatalog", fields={"catalog": CatalogItemSerializer(many=True), "customer": CustomerSerializer(allow_null=True), "orders": serializers.ListField(child=serializers.DictField())}))
@api_view(["GET"])
@permission_classes([AllowAny])
def mini_app_catalog(request):
    try:
        identity = mini_app_identity(request.query_params.get("init_data", ""))
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    catalog = CatalogItem.objects.filter(status="available").select_related("branch", "social_post")[:50]
    customer = mini_app_customer(identity)
    return Response({"catalog": CatalogItemSerializer(catalog, many=True).data, "customer": CustomerSerializer(customer).data if customer else None, "orders": mini_app_order_rows(customer)})


@extend_schema(request=MiniAppQuoteSerializer, responses=inline_serializer(name="MiniAppQuoteResponse", fields={"lines": serializers.ListField(child=serializers.DictField()), "packaging": serializers.DictField(allow_null=True), "florist_fee": serializers.CharField(), "estimated_price": serializers.CharField(), "price_is_estimate": serializers.BooleanField(), "ai_note": serializers.CharField(allow_blank=True)}))
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
    details = {"lines": quote["lines"], "packaging": quote["packaging"], "florist_fee": quote["florist_fee"], "estimated_price": quote["estimated_price"], "price_is_estimate": quote["price_is_estimate"], "ai_note": quote.get("ai_note", ""), "note": serializer.validated_data.get("note", "")}
    lead = Lead.objects.create(customer=customer, branch=quote["branch"], status="new", request_uz=request_text, arrangement_type=serializer.validated_data["arrangement_type"], estimated_price=quote["estimated_price"], source="mini_app", details=details)
    for row in quote["lines"]:
        if row["type"] == "catalog":
            catalog_item = CatalogItem.objects.filter(id=row["id"]).first()
            if catalog_item:
                LeadCatalogUsage.objects.create(lead=lead, catalog_item=catalog_item, quantity=row["quantity"])
        elif row["type"] == "stock":
            batch = StockBatch.objects.filter(id=row["id"]).first()
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
