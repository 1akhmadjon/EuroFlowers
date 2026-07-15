from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from .models import Branch, CatalogComposition, CatalogItem, Customer, Flower, FlowerVariant, Notification, StockBatch
from .services import deduct_catalog_stock, mark_catalog_sold, normalize_phone


class BusinessRulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", password="password")
        self.branch = Branch.objects.create(name="Test", code="TEST")
        flower = Flower.objects.create(name_uz="Atirgul", name_ru="Роза", slug="rose")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Mondial", name_ru="Mondial", color_uz="Oq", color_ru="Белый")
        self.batch = StockBatch.objects.create(branch=self.branch, variant=variant, batch_number="T-1", height_cm=60, stems_per_bunch=20, received_stems=100, remaining_stems=100, cost_per_stem=20000, sale_price_per_stem=30000, sale_price_per_bunch=580000)
        self.item = CatalogItem.objects.create(branch=self.branch, name_uz="Oq buket", name_ru="Белый букет", arrangement_type="bouquet", price=500000)
        CatalogComposition.objects.create(catalog_item=self.item, stock_batch=self.batch, quantity_stems=15)

    def test_selling_does_not_automatically_deduct_stock(self):
        mark_catalog_sold(self.item, self.user)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 100)
        self.assertTrue(Notification.objects.filter(notification_type="stock_pending", reference_id=self.item.id).exists())

    def test_manual_deduction_is_atomic_and_once_only(self):
        mark_catalog_sold(self.item, self.user)
        deduct_catalog_stock(self.item, self.user)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 85)
        with self.assertRaises(ValueError):
            deduct_catalog_stock(self.item, self.user)

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("90 123-45-67"), "+998901234567")


class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", password="password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_dashboard_requires_authentication(self):
        response = APIClient().get("/api/dashboard/")
        self.assertEqual(response.status_code, 401)

    @override_settings(INSTAGRAM_VERIFY_TOKEN="verify")
    def test_instagram_webhook_verification(self):
        response = APIClient().get("/api/instagram/webhook/?hub.verify_token=verify&hub.challenge=123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), 123)
