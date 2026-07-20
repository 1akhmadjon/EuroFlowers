from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from .models import Branch, CatalogComposition, CatalogItem, Conversation, Customer, Flower, FlowerVariant, Lead, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, UserProfile
from .serializers import permission_matrix
from .services import create_ai_reply_for_conversation, deduct_catalog_stock, mark_catalog_sold, normalize_phone, resolve_telegram_update
from .tasks import split_location_reply


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
        self.assertEqual(normalize_phone("+998 90 123 45 67"), "+998901234567")
        self.assertEqual(normalize_phone("998901234567"), "+998901234567")
        self.assertEqual(normalize_phone("+998 ** *** ** 67"), "")
        self.assertEqual(normalize_phone("+99867"), "")
        self.assertEqual(normalize_phone("67"), "")

    def test_ai_lead_requires_valid_customer_phone(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-test")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="buyurtma")
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value={"reply": "Qabul qilindi", "detected_language": "uz", "customer_name": "Ahmad", "phone": "+998 ** *** ** 67", "lead_ready": True, "lead_request": "Test lead", "arrangement_type": "bouquet", "estimated_price": 100000, "handoff": False}):
            reply = create_ai_reply_for_conversation(conversation)
        customer.refresh_from_db()
        self.assertEqual(customer.phone, "")
        self.assertFalse(reply.metadata["lead_ready"])
        self.assertFalse(Lead.objects.filter(customer=customer).exists())

    def test_ai_lead_requires_customer_name(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-test-2", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="buyurtma")
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value={"reply": "Qabul qilindi", "detected_language": "uz", "customer_name": None, "phone": "+998901234567", "lead_ready": True, "lead_request": "Test lead", "arrangement_type": "bouquet", "estimated_price": 100000, "handoff": False}):
            reply = create_ai_reply_for_conversation(conversation)
        self.assertFalse(reply.metadata["lead_ready"])
        self.assertIn("ismingiz", reply.text.lower())
        self.assertFalse(Lead.objects.filter(customer=customer).exists())

    def test_location_reply_splits_into_two_messages(self):
        text = "Manzillarimiz:\n\n1. Ул. Мукими 1\nhttps://yandex.uz/maps/-/CTVJzD4O\n\n2. 1-й квартал, 1, массив Чиланзар, Чиланзарский район, Ташкент\nhttps://yandex.uz/maps/-/CTVJfPoq\n\nQaysi manzilga yo‘l ko‘rsatib beray?"
        messages = split_location_reply(text)
        self.assertEqual(len(messages), 2)
        self.assertIn("CTVJzD4O", messages[0])
        self.assertIn("CTVJfPoq", messages[1])
        self.assertIn("Qaysi manzilga", messages[1])

    def test_telegram_update_creates_conversation_message_once(self):
        SocialPost.objects.create(branch=self.branch, post_type="post", title_uz="Test post", title_ru="Test post", is_active=True)
        payload = {
            "update_id": 1001,
            "message": {
                "message_id": 77,
                "text": "Assalomu alaykum",
                "chat": {"id": 555},
                "from": {"id": 999, "first_name": "Ali", "last_name": "Valiyev"},
            },
        }
        jobs = resolve_telegram_update(payload)
        self.assertEqual(len(jobs), 1)
        customer = Customer.objects.get(instagram_user_id="telegram:999")
        self.assertEqual(customer.name, "Ali Valiyev")
        self.assertTrue(Conversation.objects.filter(customer=customer).exists())
        self.assertTrue(Message.objects.filter(conversation__customer=customer, text="Assalomu alaykum", instagram_message_id="telegram:555:77").exists())
        self.assertEqual(resolve_telegram_update(payload), [])


class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", password="password", is_superuser=True, is_staff=True)
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

    def test_customers_list_hides_incomplete_placeholders(self):
        branch = Branch.objects.create(name="Test", code="CUST")
        Customer.objects.create(branch=branch, instagram_user_id="placeholder")
        Customer.objects.create(branch=branch, instagram_user_id="complete", name="Ahmad", phone="+998901234567")
        response = self.client.get("/api/customers/")
        self.assertEqual(response.status_code, 200)
        ids = [row["instagram_user_id"] for row in response.json()["results"]]
        self.assertIn("complete", ids)
        self.assertNotIn("placeholder", ids)
        response = self.client.get("/api/customers/?include_incomplete=true")
        ids = [row["instagram_user_id"] for row in response.json()["results"]]
        self.assertIn("placeholder", ids)

    def test_admin_permission_matrix_uses_saved_rows(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="users", can_view=True, can_control=True)
        PagePermission.objects.create(user=self.user, page="catalog", can_view=False, can_control=False)
        response = self.client.get(f"/api/users/{self.user.id}/")
        self.assertEqual(response.status_code, 200)
        permissions = {row["page"]: row for row in response.json()["permissions"]}
        self.assertEqual(permissions["catalog"]["can_view"], False)
        self.assertEqual(permissions["catalog"]["can_control"], False)
        developer = User.objects.create_user("developer", password="password")
        UserProfile.objects.create(user=developer, role="developer")
        developer_permissions = {row["page"]: row for row in permission_matrix(developer)}
        self.assertTrue(developer_permissions["catalog"]["can_view"])
        self.assertTrue(developer_permissions["catalog"]["can_control"])

    def test_permissions_pagination_does_not_conflict_with_permission_page_filter(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="users", can_view=True, can_control=True)
        users = [self.user]
        for index in range(3):
            user = User.objects.create_user(f"operator-{index}", password="password")
            UserProfile.objects.create(user=user, role="operator")
            users.append(user)
        for user in users:
            for page, _ in PagePermission.PAGE_CHOICES:
                PagePermission.objects.get_or_create(user=user, page=page, defaults={"can_view": True, "can_control": True})
        response = self.client.get("/api/permissions/?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.json())
        response = self.client.get("/api/permissions/?permission_page=users")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(row["page"] == "users" for row in response.json()["results"]))

    def test_packaging_movement_updates_quantity(self):
        branch = Branch.objects.create(name="Packaging", code="PKG")
        response = self.client.post("/api/packaging/", {"branch": branch.id, "packaging_type": "basket", "name_uz": "API savat", "name_ru": "API корзина", "quantity": 4, "sale_price": "90000.00"}, format="json")
        self.assertEqual(response.status_code, 201)
        api_packaging = Packaging.objects.get(id=response.json()["id"])
        self.assertTrue(PackagingMovement.objects.filter(packaging=api_packaging, movement_type="in", quantity=4).exists())
        response = self.client.patch(f"/api/packaging/{api_packaging.id}/", {"quantity": 6}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PackagingMovement.objects.filter(packaging=api_packaging, movement_type="adjustment", quantity=2).exists())
        packaging = Packaging.objects.create(branch=branch, packaging_type="basket", name_uz="Test savat", name_ru="Тест корзина", quantity=10, sale_price=100000)
        response = self.client.post(f"/api/packaging/{packaging.id}/movement/", {"movement_type": "out", "quantity": 3, "reason": "test"}, format="json")
        self.assertEqual(response.status_code, 200)
        packaging.refresh_from_db()
        self.assertEqual(packaging.quantity, 7)
        self.assertTrue(PackagingMovement.objects.filter(packaging=packaging, quantity=-3).exists())
        response = self.client.post(f"/api/packaging/{packaging.id}/movement/", {"movement_type": "adjustment", "quantity": -2, "reason": "count"}, format="json")
        self.assertEqual(response.status_code, 200)
        packaging.refresh_from_db()
        self.assertEqual(packaging.quantity, 5)
        response = self.client.post(f"/api/packaging/{packaging.id}/movement/", {"movement_type": "out", "quantity": 99}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_mini_app_lead_history_returns_customer_orders(self):
        branch = Branch.objects.create(name="Mini", code="MINI")
        flower = Flower.objects.create(name_uz="Gortenziya", name_ru="Гортензия", slug="hydrangea")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Blue", name_ru="Blue", color_uz="Moviy", color_ru="Синий")
        batch = StockBatch.objects.create(branch=branch, variant=variant, batch_number="M-1", height_cm=50, stems_per_bunch=5, received_stems=50, remaining_stems=50, cost_per_stem=10000, sale_price_per_stem=20000, sale_price_per_bunch=100000, minimum_sale_stems=1)
        packaging = Packaging.objects.create(branch=branch, packaging_type="basket", name_uz="Mini savat", name_ru="Мини корзина", size="S", capacity_min_stems=1, capacity_max_stems=10, quantity=5, sale_price=120000)
        init_data = 'user={"id":777,"first_name":"Ali"}'
        payload = {"init_data": init_data, "branch": branch.id, "arrangement_type": "basket", "items": [{"stock_batch": batch.id, "quantity_stems": 3}], "packaging": packaging.id, "name": "Ali", "phone": "901234567", "note": "Bugun kerak"}
        response = APIClient().post("/api/mini-app/leads/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        lead = Lead.objects.get(source="mini_app")
        self.assertEqual(lead.customer.instagram_user_id, "miniapp:777")
        self.assertEqual(lead.customer.phone, "+998901234567")
        self.assertEqual(lead.details["lines"][0]["quantity_stems"], 3)
        response = APIClient().get("/api/mini-app/me/", {"init_data": init_data})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["customer"]["name"], "Ali")
        self.assertEqual(len(data["orders"]), 1)
        self.assertEqual(data["orders"][0]["details"]["lines"][0]["flower_uz"], "Gortenziya")
        response = APIClient().get("/api/mini-app/catalog/", {"init_data": init_data, "branch": branch.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["orders"]), 1)
