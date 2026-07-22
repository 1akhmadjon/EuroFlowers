from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import Branch, CatalogComposition, CatalogItem, Conversation, Customer, Flower, FlowerVariant, IntegrationSettings, Lead, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement, UserProfile
from .serializers import permission_matrix
from .services import create_ai_reply_for_conversation, deduct_catalog_stock, mark_catalog_sold, normalize_phone, process_pending_customer_reply, resolve_instagram_event, resolve_telegram_update
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

    def test_catalog_partial_sales_deduct_composition_per_quantity(self):
        item = CatalogItem.objects.create(branch=self.branch, name_uz="Qizil set", name_ru="Красный сет", arrangement_type="bouquet", price=900000, quantity_total=10, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=3)
        mark_catalog_sold(item, self.user, quantity=3)
        item.refresh_from_db()
        self.assertEqual(item.quantity_sold, 3)
        deduct_catalog_stock(item, self.user)
        self.batch.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 91)
        self.assertEqual(item.quantity_stock_deducted, 3)
        self.assertEqual(item.status, "available")

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

    def test_ai_direct_stems_request_creates_lead_without_stock_deduction_items(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-test-3", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="3 pochka atirgulni o‘zini olaman")
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value={
            "reply": "Operatorimiz aniqlashtirib aloqaga chiqadi.",
            "detected_language": "uz",
            "customer_name": "Ahmad",
            "phone": "+998901234567",
            "lead_ready": True,
            "lead_request": "Mijoz gulni dona/pochka holida olmoqchi, operator aniqlashtirishi kerak.",
            "arrangement_type": "stems",
            "estimated_price": 1000000,
            "handoff": False,
            "catalog_items": [],
            "stock_items": [{"batch_id": self.batch.id, "quantity_stems": 75, "quantity_bunches": 3}],
        }):
            reply = create_ai_reply_for_conversation(conversation)
        lead = Lead.objects.get(customer=customer)
        self.assertTrue(reply.metadata["lead_ready"])
        self.assertIsNone(lead.estimated_price)
        self.assertEqual(lead.stock_usage.count(), 0)
        self.assertIn("dona/pochka", lead.request_uz)

    def test_pending_customer_reply_debounces_to_latest_message(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-debounce", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        first = conversation.messages.create(sender="customer", text="katalog")
        second = conversation.messages.create(sender="customer", text="rasmlari bormi")
        from unittest.mock import patch
        with patch("core.services.create_ai_reply_for_conversation", side_effect=lambda conv: Message.objects.create(conversation=conv, sender="ai", text="Javob")) as mocked:
            self.assertIsNone(process_pending_customer_reply(conversation.id, first.id))
            reply = process_pending_customer_reply(conversation.id, second.id)
            self.assertIsNotNone(reply)
            self.assertIsNone(process_pending_customer_reply(conversation.id, second.id))
        conversation.refresh_from_db()
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(conversation.ai_replied_to_message_id, second.id)

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

    def test_instagram_media_links_are_saved_in_chat_message(self):
        SocialPost.objects.create(branch=self.branch, post_type="story", title_uz="Story", title_ru="Story", is_active=True)
        payload = {
            "entry": [{
                "messaging": [{
                    "sender": {"id": "ig-user-1"},
                    "recipient": {"id": "ig-business"},
                    "message": {
                        "mid": "mid-link-1",
                        "text": "shu qancha",
                        "attachments": [{
                            "type": "ig_story",
                            "payload": {
                                "story_media_id": "story-1",
                                "story_media_url": "https://lookaside.fbsbx.com/ig_messaging_cdn/story.jpg",
                            },
                        }],
                    },
                }],
            }],
        }
        jobs = resolve_instagram_event(payload)
        self.assertEqual(len(jobs), 1)
        message = Message.objects.get(instagram_message_id="mid-link-1")
        self.assertIn("Story link: https://lookaside.fbsbx.com/ig_messaging_cdn/story.jpg", message.text)
        self.assertEqual(message.metadata["attachments"][0]["kind"], "story")
        self.assertEqual(message.metadata["attachments"][0]["url"], "https://lookaside.fbsbx.com/ig_messaging_cdn/story.jpg")

    def test_unknown_instagram_media_clears_previous_post_context(self):
        old_post = SocialPost.objects.create(branch=self.branch, post_type="reel", media_id="old-pion", title_uz="Pion buket", title_ru="Pion", is_active=True)
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-user-2")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch, social_post=old_post)
        payload = {
            "entry": [{
                "messaging": [{
                    "sender": {"id": "ig-user-2"},
                    "recipient": {"id": "ig-business"},
                    "message": {
                        "mid": "mid-link-2",
                        "text": "shu qancha",
                        "attachments": [{
                            "type": "share",
                            "payload": {"url": "https://www.instagram.com/reel/UNKNOWN123/"},
                        }],
                    },
                }],
            }],
        }
        jobs = resolve_instagram_event(payload)
        self.assertEqual(len(jobs), 1)
        conversation.refresh_from_db()
        message = Message.objects.get(instagram_message_id="mid-link-2")
        self.assertIsNone(conversation.social_post)
        self.assertIn("bazadagi story/post/reel katalogiga bog‘lanmagan", message.text)

    def test_telegram_voice_link_is_saved_in_chat_message(self):
        SocialPost.objects.create(branch=self.branch, post_type="post", title_uz="Test post", title_ru="Test post", is_active=True)
        IntegrationSettings.objects.update_or_create(pk=1, defaults={"telegram_bot_token": "test-token"})
        payload = {
            "update_id": 1002,
            "message": {
                "message_id": 78,
                "chat": {"id": 555},
                "from": {"id": 1000, "first_name": "Ali"},
                "voice": {"file_id": "voice-file-id"},
            },
        }
        from unittest.mock import patch

        class MockResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "result": {"file_path": "voice/file.ogg"}}

        with patch("core.services.requests.post", return_value=MockResponse()):
            jobs = resolve_telegram_update(payload)
        self.assertEqual(len(jobs), 1)
        message = Message.objects.get(instagram_message_id="telegram:555:78")
        self.assertIn("Voice message: https://api.telegram.org/file/bottest-token/voice/file.ogg", message.text)
        self.assertEqual(message.metadata["attachments"][0]["kind"], "voice")


class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", password="password", is_superuser=True, is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.branch = Branch.objects.create(name="API Test", code="API")
        flower = Flower.objects.create(name_uz="Atirgul API", name_ru="Роза API", slug="rose-api")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Freedom", name_ru="Freedom", color_uz="Qizil", color_ru="Красный")
        self.batch = StockBatch.objects.create(branch=self.branch, variant=variant, batch_number="API-1", height_cm=60, stems_per_bunch=20, received_stems=100, remaining_stems=100, cost_per_stem=10000, sale_price_per_stem=20000, sale_price_per_bunch=400000)

    def test_dashboard_requires_authentication(self):
        response = APIClient().get("/api/dashboard/")
        self.assertEqual(response.status_code, 401)

    def test_dashboard_includes_daily_chart_stats_for_default_month(self):
        customer = Customer.objects.create(branch=self.branch, name="Chart User", phone="+998901234567", instagram_user_id="chart-user")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        lead = Lead.objects.create(customer=customer, branch=self.branch, conversation=conversation, request_uz="Chart lead", arrangement_type="catalog")
        today = timezone.localdate()
        created_at = timezone.now()
        Conversation.objects.filter(id=conversation.id).update(created_at=created_at)
        Lead.objects.filter(id=lead.id).update(created_at=created_at)
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["daily_stats"]), 30)
        self.assertEqual(data["daily_stats"][-1]["date"], today.isoformat())
        self.assertGreaterEqual(data["daily_stats"][-1]["leads"], 1)
        self.assertGreaterEqual(data["daily_stats"][-1]["conversations"], 1)

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

    def test_social_post_response_includes_lead_ids(self):
        post = SocialPost.objects.create(branch=self.branch, post_type="story", title_uz="Story buket", title_ru="Story bouquet", is_active=True)
        customer = Customer.objects.create(branch=self.branch, name="Madina", phone="+998901234567", instagram_user_id="ig-lead")
        lead = Lead.objects.create(customer=customer, branch=self.branch, social_post=post, status="won", request_uz="Storydagi buket", arrangement_type="catalog", estimated_price=400000)
        item = CatalogItem.objects.create(branch=self.branch, social_post=post, name_uz="Qizil buket", name_ru="Red bouquet", arrangement_type="bouquet", price=400000, quantity_total=4, status="available")
        lead.catalog_usage.create(catalog_item=item, quantity=1)
        response = self.client.get(f"/api/social-posts/{post.id}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["lead_count"], 1)
        self.assertEqual(data["leads"][0]["id"], lead.id)
        self.assertEqual(data["leads"][0]["customer_name"], "Madina")
        self.assertEqual(data["leads"][0]["catalog_items"][0]["id"], item.id)

    def test_social_post_create_accepts_catalog_composition(self):
        response = self.client.post("/api/social-posts/", {
            "branch": self.branch.id,
            "post_type": "post",
            "media_id": "api-post-composition",
            "title_uz": "Atirgul post",
            "title_ru": "Розовый пост",
            "price": "400000.00",
            "flower_count": 3,
            "is_active": True,
            "catalog_items": [{
                "name_uz": "Qizil atirgul buket",
                "name_ru": "Букет красных роз",
                "arrangement_type": "bouquet",
                "price": "400000.00",
                "quantity_total": 4,
                "status": "available",
                "composition": [{
                    "stock_batch": self.batch.id,
                    "quantity_stems": 3,
                    "quantity_bunches": "0.15"
                }]
            }]
        }, format="json")
        self.assertEqual(response.status_code, 201)
        post = SocialPost.objects.get(media_id="api-post-composition")
        item = post.catalog_items.get()
        composition = item.composition.get()
        self.assertEqual(item.quantity_total, 4)
        self.assertEqual(composition.stock_batch_id, self.batch.id)
        self.assertEqual(composition.quantity_stems, 3)
        self.assertEqual(response.json()["catalog_items"][0]["composition"][0]["stock_batch"], self.batch.id)

    def test_conversation_response_includes_source(self):
        instagram_customer = Customer.objects.create(branch=self.branch, name="Instagram", phone="+998901234567", instagram_user_id="ig-source")
        telegram_customer = Customer.objects.create(branch=self.branch, name="Telegram", phone="+998901234568", instagram_user_id="telegram:123")
        instagram_conversation = Conversation.objects.create(customer=instagram_customer, branch=self.branch)
        telegram_conversation = Conversation.objects.create(customer=telegram_customer, branch=self.branch)
        response = self.client.get(f"/api/conversations/{instagram_conversation.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "instagram")
        self.assertEqual(response.json()["source_label"], "Instagram")
        response = self.client.get(f"/api/conversations/{telegram_conversation.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "telegram")
        self.assertEqual(response.json()["source_label"], "Telegram")

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
        admin_permissions = {row["page"]: row for row in permission_matrix(self.user)}
        self.assertNotIn("ai_settings", admin_permissions)
        self.assertNotIn("integrations", admin_permissions)
        self.assertNotIn("audit", admin_permissions)

    def test_admin_cannot_grant_developer_only_permissions(self):
        UserProfile.objects.create(user=self.user, role="admin")
        operator = User.objects.create_user("limited", password="password")
        UserProfile.objects.create(user=operator, role="operator")
        response = self.client.post("/api/permissions/", {"user": operator.id, "page": "ai_settings", "can_view": True, "can_control": True}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/users/", {"username": "bad-user", "password": "password", "role": "operator", "permissions": [{"page": "audit", "can_view": True, "can_control": True}]}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/api/audit/")
        self.assertEqual(response.status_code, 403)

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

    def test_catalog_create_rejects_short_stock_for_total_quantity(self):
        payload = {
            "branch": self.branch.id,
            "name_uz": "Kop buket",
            "name_ru": "Много букетов",
            "arrangement_type": "bouquet",
            "price": "100000.00",
            "status": "available",
            "quantity_total": 40,
            "composition": [{"stock_batch": self.batch.id, "quantity_stems": 3}],
        }
        response = self.client.post("/api/catalog/", payload, format="json")
        self.assertEqual(response.status_code, 400)

    def test_manual_lead_create_customer_and_deducts_stock_when_won(self):
        packaging = Packaging.objects.create(branch=self.branch, packaging_type="basket", name_uz="Lead savat", name_ru="Lead basket", quantity=2, sale_price=50000)
        payload = {
            "branch": self.branch.id,
            "status": "new",
            "request_uz": "Manual lead",
            "arrangement_type": "basket",
            "estimated_price": "250000.00",
            "florist_fee": "50000.00",
            "customer_name": "Vali",
            "customer_phone": "901112233",
            "stock_usage_input": [{"stock_batch": self.batch.id, "quantity_stems": 4}],
            "packaging_usage_input": [{"packaging": packaging.id, "quantity": 1}],
        }
        response = self.client.post("/api/leads/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        lead_id = response.json()["id"]
        self.batch.refresh_from_db()
        packaging.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 100)
        self.assertEqual(packaging.quantity, 2)
        response = self.client.patch(f"/api/leads/{lead_id}/", {"status": "won"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        packaging.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 96)
        self.assertEqual(packaging.quantity, 1)
        self.assertEqual(Customer.objects.get(phone="+998901112233").leads.count(), 1)
        response = self.client.patch(f"/api/leads/{lead_id}/", {"status": "lost"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        packaging.refresh_from_db()
        lead = Lead.objects.get(id=lead_id)
        self.assertEqual(self.batch.remaining_stems, 100)
        self.assertEqual(packaging.quantity, 2)
        self.assertIsNone(lead.stock_deducted_at)
        self.assertTrue(StockMovement.objects.filter(reference_type="lead", reference_id=lead_id, movement_type="adjustment", quantity_stems=4).exists())
        self.assertTrue(PackagingMovement.objects.filter(reference_type="lead", reference_id=lead_id, movement_type="adjustment", quantity=1).exists())
        response = self.client.patch(f"/api/leads/{lead_id}/", {"status": "won"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        packaging.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 96)
        self.assertEqual(packaging.quantity, 1)

    def test_catalog_lead_stock_returns_when_won_is_reverted(self):
        item = CatalogItem.objects.create(branch=self.branch, name_uz="Catalog buket", name_ru="Catalog bouquet", arrangement_type="bouquet", price=300000, quantity_total=3, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=5)
        payload = {
            "branch": self.branch.id,
            "status": "new",
            "request_uz": "Catalog lead",
            "arrangement_type": "catalog",
            "customer_name": "Sardor",
            "customer_phone": "901234000",
            "catalog_usage_input": [{"catalog_item": item.id, "quantity": 2}],
        }
        response = self.client.post("/api/leads/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        lead_id = response.json()["id"]
        response = self.client.post(f"/api/leads/{lead_id}/move/", {"status": "won"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 90)
        self.assertEqual(item.quantity_sold, 2)
        self.assertEqual(item.quantity_stock_deducted, 2)
        response = self.client.post(f"/api/leads/{lead_id}/move/", {"status": "new"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 100)
        self.assertEqual(item.quantity_sold, 0)
        self.assertEqual(item.quantity_stock_deducted, 0)
        self.assertEqual(item.status, "available")

    def test_lead_move_keeps_kanban_position_between_two_leads(self):
        customer = Customer.objects.create(branch=self.branch, name="Kanban", phone="+998901234567", instagram_user_id="kanban")
        first = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="First", sort_order=Decimal("1000"))
        moving = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="Moving", sort_order=Decimal("2000"))
        last = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="Last", sort_order=Decimal("3000"))
        response = self.client.post(f"/api/leads/{moving.id}/move/", {"status": "new", "before": first.id, "after": last.id}, format="json")
        self.assertEqual(response.status_code, 200)
        moving.refresh_from_db()
        self.assertEqual(moving.sort_order, Decimal("2000.000000"))
        ids = list(Lead.objects.filter(status="new").order_by("sort_order").values_list("id", flat=True))
        self.assertEqual(ids, [first.id, moving.id, last.id])

    def test_lead_reorder_column_accepts_full_column_ids(self):
        customer = Customer.objects.create(branch=self.branch, name="Kanban full", phone="+998901234568", instagram_user_id="kanban-full")
        first = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="First", sort_order=Decimal("1000"))
        second = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="Second", sort_order=Decimal("2000"))
        third = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="Third", sort_order=Decimal("3000"))
        response = self.client.post("/api/leads/reorder-column/", {"status": "new", "lead_ids": [third.id, first.id, second.id]}, format="json")
        self.assertEqual(response.status_code, 200)
        ids = list(Lead.objects.filter(status="new").order_by("sort_order").values_list("id", flat=True))
        self.assertEqual(ids, [third.id, first.id, second.id])
        third.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(third.sort_order, Decimal("1000.000000"))
        self.assertEqual(first.sort_order, Decimal("2000.000000"))
        self.assertEqual(second.sort_order, Decimal("3000.000000"))
        response = self.client.post("/api/leads/reorder-column/", {"status": "new", "lead_ids": [third.id, first.id]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn(second.id, response.json()["missing_ids"])
