from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView
from .views import AuditLogViewSet, BranchViewSet, CatalogItemViewSet, ConversationViewSet, CustomerViewSet, EuroFlowersTokenObtainPairView, FlowerVariantViewSet, FlowerViewSet, InstagramWebhookEventViewSet, LeadViewSet, NotificationViewSet, PackagingMovementViewSet, PackagingViewSet, PagePermissionViewSet, SocialPostViewSet, StockBatchViewSet, StockMovementViewSet, UserViewSet, ai_settings, business_settings, dashboard, integrations_settings, instagram_status, instagram_webhook, me, mini_app_catalog, mini_app_lead, mini_app_me, mini_app_quote, telegram_webhook, upload_file

router = DefaultRouter()
router.register("branches", BranchViewSet)
router.register("users", UserViewSet)
router.register("permissions", PagePermissionViewSet)
router.register("flowers", FlowerViewSet)
router.register("flower-variants", FlowerVariantViewSet)
router.register("stock-batches", StockBatchViewSet)
router.register("stock-movements", StockMovementViewSet)
router.register("packaging", PackagingViewSet)
router.register("packaging-movements", PackagingMovementViewSet)
router.register("materials", PackagingViewSet, basename="materials")
router.register("material-movements", PackagingMovementViewSet, basename="material-movements")
router.register("catalog", CatalogItemViewSet)
router.register("customers", CustomerViewSet)
router.register("leads", LeadViewSet)
router.register("social-posts", SocialPostViewSet)
router.register("conversations", ConversationViewSet)
router.register("instagram/events", InstagramWebhookEventViewSet)
router.register("notifications", NotificationViewSet)
router.register("audit", AuditLogViewSet)

urlpatterns = [
    path("auth/token/", EuroFlowersTokenObtainPairView.as_view()),
    path("auth/token/refresh/", TokenRefreshView.as_view()),
    path("auth/token/blacklist/", TokenBlacklistView.as_view()),
    path("me/", me),
    path("dashboard/", dashboard),
    path("settings/", business_settings),
    path("ai/settings/", ai_settings),
    path("integrations/", integrations_settings),
    path("instagram/status/", instagram_status),
    path("instagram/webhook/", instagram_webhook),
    path("telegram/webhook/", telegram_webhook),
    path("mini-app/catalog/", mini_app_catalog),
    path("mini-app/me/", mini_app_me),
    path("mini-app/quote/", mini_app_quote),
    path("mini-app/leads/", mini_app_lead),
    path("uploads/", upload_file),
    path("", include(router.urls)),
]
