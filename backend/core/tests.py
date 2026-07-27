from decimal import Decimal
from datetime import timedelta
import json
import tempfile
from types import SimpleNamespace
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
import requests
from rest_framework.test import APIClient
from .models import AISettings, AuditLog, Branch, CatalogComposition, CatalogHistory, CatalogItem, CatalogMaterialUsage, Conversation, Customer, FloristProfile, FloristSalaryEntry, FloristVolumeRate, Flower, FlowerVariant, IntegrationSettings, Lead, LeadCatalogUsage, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement, UserProfile
from .serializers import CatalogItemSerializer, ConversationSerializer, FloristProfileSerializer, FloristSalaryEntrySerializer, FloristVolumeRateSerializer, PackagingSerializer, StockBatchSerializer, permission_matrix
from .inventory_services import deduct_catalog_stock, mark_catalog_sold
from .services import ai_flower_variant_rows, ai_reply, ai_stock_rows, ai_tool_definitions, create_ai_reply_for_conversation, detect_customer_reply_script, execute_ai_tool, normalize_phone, process_pending_customer_reply
from .tasks import process_delayed_instagram_reply, process_delayed_telegram_reply, split_location_reply
from .webhook_services import resolve_instagram_event, resolve_telegram_update


class BusinessRulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", password="password")
        self.branch = Branch.objects.create(name="Test", code="TEST")
        flower = Flower.objects.create(name_uz="Atirgul", slug="rose")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Mondial", color_uz="Oq")
        self.batch = StockBatch.objects.create(branch=self.branch, variant=variant, batch_number="T-1", height_cm=60, stems_per_bunch=20, received_stems=100, remaining_stems=100, cost_per_stem=20000, sale_price_per_stem=30000, sale_price_per_bunch=580000)
        self.item = CatalogItem.objects.create(branch=self.branch, name_uz="Oq buket", arrangement_type="bouquet", price=500000)
        CatalogComposition.objects.create(catalog_item=self.item, stock_batch=self.batch, quantity_stems=15)

    def test_selling_does_not_automatically_deduct_stock(self):
        mark_catalog_sold(self.item, self.user)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 100)
        self.assertFalse(Notification.objects.filter(notification_type="stock_pending", reference_id=self.item.id).exists())

    def test_manual_deduction_is_atomic_and_once_only(self):
        mark_catalog_sold(self.item, self.user)
        deduct_catalog_stock(self.item, self.user)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 85)
        with self.assertRaises(ValueError):
            deduct_catalog_stock(self.item, self.user)

    def test_catalog_partial_sales_deduct_composition_per_quantity(self):
        item = CatalogItem.objects.create(branch=self.branch, name_uz="Qizil set", arrangement_type="bouquet", price=900000, quantity_total=10, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=3)
        mark_catalog_sold(item, self.user, quantity=3)
        item.refresh_from_db()
        self.assertEqual(item.quantity_sold, 3)
        deduct_catalog_stock(item, self.user, quantity=3)
        self.batch.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 91)
        self.assertEqual(item.quantity_stock_deducted, 3)
        self.assertEqual(item.status, "available")

    def test_catalog_discounted_sale_requires_reason_and_creates_history(self):
        item = CatalogItem.objects.create(branch=self.branch, name_uz="Skidka buket", arrangement_type="bouquet", price=500000, quantity_total=2, status="available")
        with self.assertRaises(ValueError):
            mark_catalog_sold(item, self.user, quantity=1, sale_price=450000)
        mark_catalog_sold(item, self.user, quantity=1, sale_price=450000, discount_reason="Doimiy mijoz")
        item.refresh_from_db()
        self.assertEqual(item.quantity_sold, 1)
        history = CatalogHistory.objects.get(catalog_item=item, action="sold")
        self.assertEqual(history.listed_unit_price, Decimal("500000.00"))
        self.assertEqual(history.sold_unit_price, Decimal("450000.00"))
        self.assertEqual(history.discount_amount, Decimal("50000.00"))
        self.assertEqual(history.discount_percent, Decimal("10.00"))
        self.assertEqual(history.discount_reason, "Doimiy mijoz")

    def test_custom_catalog_deducts_inventory_and_creates_salary_from_volume_rate(self):
        florist_user = User.objects.create_user("florist", password="password", first_name="Ali")
        florist = FloristProfile.objects.create(user=florist_user, branch=self.branch, staff_type="florist")
        FloristVolumeRate.objects.create(branch=self.branch, arrangement_type="bouquet", volume="small", default_stems=10, florist_fee=70000)
        packaging = Packaging.objects.create(branch=self.branch, packaging_type="wrap", name_uz="Test qogoz", cost_price=10000, sale_price=20000, quantity=5)
        serializer = CatalogItemSerializer(data={
            "name_uz": "Custom buket",
            "arrangement_type": "bouquet",
            "catalog_kind": "custom",
            "volume": "small",
            "florist": florist.id,
            "price": "250000.00",
            "discount_reason": "Doimiy mijozga chegirma",
            "quantity_total": 1,
            "composition": [{"stock_batch": self.batch.id, "quantity_stems": 10, "quantity_bunches": "0.50"}],
            "materials": [{"packaging": packaging.id, "quantity": 1}],
        }, context={"request": SimpleNamespace(user=self.user)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        item = serializer.save()
        self.batch.refresh_from_db()
        packaging.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(item.status, "sold")
        self.assertEqual(item.quantity_sold, 1)
        self.assertEqual(self.batch.remaining_stems, 90)
        self.assertEqual(packaging.quantity, 4)
        self.assertEqual(item.florist_fee, Decimal("50000.00"))
        self.assertEqual(item.florist_salary_amount, Decimal("70000.00"))
        self.assertEqual(item.calculated_component_price, Decimal("370000.00"))
        self.assertEqual(item.calculated_cost_price, Decimal("260000.00"))
        self.assertEqual(item.discount_amount, Decimal("120000.00"))
        self.assertEqual(item.discount_percent, Decimal("32.43"))
        salary = FloristSalaryEntry.objects.get(catalog_item=item)
        self.assertEqual(salary.amount, Decimal("70000.00"))
        self.assertEqual(salary.florist, florist)
        sold_history = CatalogHistory.objects.get(catalog_item=item, action="sold")
        self.assertEqual(sold_history.discount_amount, Decimal("120000.00"))
        self.assertEqual(sold_history.discount_reason, "Doimiy mijozga chegirma")

    def test_custom_catalog_accepts_custom_volume_and_manual_salary_amount(self):
        florist_user = User.objects.create_user("custom-florist", password="password", first_name="Ali")
        florist = FloristProfile.objects.create(user=florist_user, branch=self.branch, staff_type="florist")
        serializer = CatalogItemSerializer(data={
            "name_uz": "Juda katta custom buket",
            "arrangement_type": "bouquet",
            "catalog_kind": "custom",
            "volume": "extra katta 120 dona",
            "florist": florist.id,
            "price": "450000.00",
            "florist_fee": "80000.00",
            "florist_salary_amount": "125000.00",
            "quantity_total": 1,
            "composition": [{"stock_batch": self.batch.id, "quantity_stems": 10, "quantity_bunches": "0.50"}],
        }, context={"request": SimpleNamespace(user=self.user)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        item = serializer.save()
        item.refresh_from_db()
        self.assertEqual(item.volume, "extra katta 120 dona")
        self.assertEqual(item.florist_fee, Decimal("80000.00"))
        self.assertEqual(item.florist_salary_amount, Decimal("125000.00"))
        self.assertEqual(item.calculated_component_price, Decimal("380000.00"))
        salary = FloristSalaryEntry.objects.get(catalog_item=item)
        self.assertEqual(salary.amount, Decimal("125000.00"))

    def test_stock_batch_serializer_returns_fractional_remaining_bunches(self):
        self.batch.remaining_stems = 85
        self.batch.save(update_fields=["remaining_stems", "updated_at"])
        data = StockBatchSerializer(self.batch).data
        self.assertEqual(data["remaining_bunches"], "4.25")
        self.assertEqual(data["remaining_bunches_label"], "4.25 pochka")

    def test_florist_profile_accepts_precise_coordinates_and_nested_volume_rates(self):
        florist_user = User.objects.create_user("precise-florist", password="password", first_name="Ali")
        serializer = FloristProfileSerializer(data={
            "user": florist_user.id,
            "staff_type": "florist",
            "daily_pay": "150000.00",
            "shop_latitude": "41.31108123",
            "shop_longitude": "69.24056234",
            "volume_rates": [
                {"arrangement_type": "bouquet", "volume": "small", "default_stems": 15, "florist_fee": "50000.00"},
                {"arrangement_type": "basket", "volume": "large", "default_stems": 45, "florist_fee": "120000.00"},
            ],
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        profile = serializer.save()
        self.assertEqual(profile.branch, self.branch)
        self.assertEqual(profile.daily_pay, Decimal("0"))
        self.assertEqual(profile.volume_rates.count(), 2)
        self.assertTrue(profile.volume_rates.filter(arrangement_type="basket", volume="large", florist_fee=Decimal("120000.00")).exists())
        self.assertNotIn("branch", FloristProfileSerializer(profile).data)
        rate = profile.volume_rates.first()
        self.assertNotIn("branch", FloristVolumeRateSerializer(rate).data)

    def test_apprentice_daily_salary_update_requires_reason(self):
        apprentice_user = User.objects.create_user("apprentice", password="password", first_name="Vali")
        apprentice = FloristProfile.objects.create(user=apprentice_user, branch=self.branch, staff_type="apprentice", daily_pay=100000)
        entry = FloristSalaryEntry.objects.create(florist=apprentice, source="daily", amount=100000, work_date=timezone.localdate(), note="Kunlik")
        serializer = FloristSalaryEntrySerializer(entry, data={"amount": "120000.00"}, partial=True)
        self.assertFalse(serializer.is_valid())
        serializer = FloristSalaryEntrySerializer(entry, data={"amount": "120000.00", "reason": "Qo‘shimcha smena"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = serializer.save()
        self.assertEqual(updated.amount, Decimal("120000.00"))
        self.assertIn("Qo‘shimcha smena", updated.note)

    def test_packaging_serializer_accepts_image_and_returns_quantity_label(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                serializer = PackagingSerializer(data={
                    "packaging_type": "other",
                    "name_uz": "Shokolad",
                    "cost_price": "10000.00",
                    "sale_price": "20000.00",
                    "quantity": 12,
                    "image": SimpleUploadedFile("shokolad.jpg", b"image-bytes", content_type="image/jpeg"),
                })
                self.assertTrue(serializer.is_valid(), serializer.errors)
                packaging = serializer.save(branch=self.branch)
                data = PackagingSerializer(packaging).data
                self.assertEqual(data["quantity_label"], "12 dona")
                self.assertTrue(data["image_url"].endswith("shokolad.jpg"))

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("90 123-45-67"), "+998901234567")
        self.assertEqual(normalize_phone("+998 90 123 45 67"), "+998901234567")
        self.assertEqual(normalize_phone("998901234567"), "+998901234567")
        self.assertEqual(normalize_phone("+998 ** *** ** 67"), "")
        self.assertEqual(normalize_phone("+99867"), "")
        self.assertEqual(normalize_phone("67"), "")

    def test_detect_customer_reply_script_distinguishes_uzbek_cyrillic_from_russian(self):
        self.assertEqual(detect_customer_reply_script("гортензия кере"), "uz_cyril")
        self.assertEqual(detect_customer_reply_script("силада сотаслами ози"), "uz_cyril")
        self.assertEqual(detect_customer_reply_script("сколько стоит гортензия"), "ru")

    def test_ai_catalog_generic_query_returns_available_items(self):
        from .services import ai_catalog_rows
        self.item.status = "available"
        self.item.save(update_fields=["status", "updated_at"])
        for query in ["vitrina", "vitrinada qanaqa gulla bor", "katalogdagi tayyor mahsulotlar"]:
            rows = ai_catalog_rows(query)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name_uz"], "Oq buket")

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

    def test_ai_single_catalog_image_question_is_auto_sent_and_rewritten(self):
        self.item.status = "available"
        self.item.arrangement_type = "basket"
        self.item.image_url = "https://example.com/oq-buket.jpg"
        self.item.save(update_fields=["status", "arrangement_type", "image_url", "updated_at"])
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-single-catalog")
        post = SocialPost.objects.create(branch=self.branch, post_type="story", media_id="story-oq-buket", title_uz="Oq buket", title_ru="White bouquet", price=500000, is_active=True)
        self.item.social_post = post
        self.item.save(update_fields=["social_post", "updated_at"])
        conversation = Conversation.objects.create(customer=customer, branch=self.branch, social_post=post)
        conversation.messages.create(sender="customer", text="bu nechpul")
        payload = {
            "reply": "Savatga yasalgan vitrinadagi gullarimiz\n\n1. Oq buket\n2.\nQaysini tanlaysiz, rasmni ko'rsataman?",
            "detected_language": "uz",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": "basket",
            "estimated_price": "500000",
            "handoff": False,
            "catalog_items": [{"catalog_name": "Oq buket", "quantity": 1}],
            "stock_items": [],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload), patch("core.services.instagram_send_image", return_value={"ok": True}) as image_mock:
            reply = create_ai_reply_for_conversation(conversation)
        image_mock.assert_called_once_with("ig-single-catalog", "https://example.com/oq-buket.jpg")
        self.assertIn("Oq buket savat", reply.text)
        self.assertIn("Narxi 500 000 so'm", reply.text)
        self.assertNotIn("Katalogimizda hozir faqat", reply.text)
        self.assertNotIn("Qaysini tanlaysiz", reply.text)
        self.assertNotIn("rasmni", reply.text.lower())

    def test_ai_single_catalog_filter_uses_catalog_only_wording(self):
        self.item.status = "available"
        self.item.arrangement_type = "basket"
        self.item.image_url = "https://example.com/oq-buket.jpg"
        self.item.save(update_fields=["status", "arrangement_type", "image_url", "updated_at"])
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-single-catalog-filter")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="savat katalog")
        payload = {
            "reply": "Savatga yasalgan vitrinadagi gullarimiz\n\n1. Oq buket\n2.\nQaysini tanlaysiz, rasmni ko'rsataman?",
            "detected_language": "uz",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": "basket",
            "estimated_price": "500000",
            "handoff": False,
            "catalog_items": [],
            "stock_items": [],
            "tool_results": [{"name": "get_catalog", "arguments": {"arrangement_type": "basket"}, "output": {"catalog": [{"name_uz": "Oq buket", "price": "500000.00"}]}}],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload), patch("core.services.instagram_send_image", return_value={"ok": True}) as image_mock:
            reply = create_ai_reply_for_conversation(conversation)
        image_mock.assert_called_once_with("ig-single-catalog-filter", "https://example.com/oq-buket.jpg")
        self.assertIn("Katalogimizda hozir faqat Oq buket savat bor ekan", reply.text)
        self.assertIn("Narxi 500 000 so'm", reply.text)
        self.assertNotIn("Qaysini tanlaysiz", reply.text)
        self.assertNotIn("rasmni", reply.text.lower())

    def test_ai_multiple_selected_catalog_images_are_sent(self):
        self.item.status = "available"
        self.item.image_url = "https://example.com/oq-buket.jpg"
        self.item.save(update_fields=["status", "image_url", "updated_at"])
        second = CatalogItem.objects.create(branch=self.branch, name_uz="Pushti buket", arrangement_type="bouquet", price=600000, status="available", image_url="https://example.com/pushti-buket.jpg")
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-multi-catalog")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="shu ikkalasini rasmini yuboring")
        payload = {
            "reply": "Rasmlarini ko'rsataman.",
            "detected_language": "uz",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": "catalog",
            "estimated_price": None,
            "handoff": False,
            "catalog_items": [{"catalog_name": self.item.name_uz, "quantity": 1}, {"catalog_name": second.name_uz, "quantity": 1}],
            "stock_items": [],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload), patch("core.services.instagram_send_image", return_value={"ok": True}) as image_mock:
            reply = create_ai_reply_for_conversation(conversation)
        self.assertEqual(image_mock.call_count, 2)
        image_mock.assert_any_call("ig-multi-catalog", "https://example.com/oq-buket.jpg")
        image_mock.assert_any_call("ig-multi-catalog", "https://example.com/pushti-buket.jpg")
        self.assertEqual(reply.metadata["tool_results"][-1]["name"], "send_catalog_images")

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_reply_sends_context_conversation_and_allowed_tools(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-tools", name="Ahmad")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="qanaqa gullar bor")
        conversation.messages.create(sender="ai", text="Skladimizda bor", metadata={"tool_results": [{"name": "get_stock", "output": {"stock": [{"price_per_stem": "105000.00"}]}}]})
        payload = {
            "reply": "Katalogdagi gullarni ko‘rib beraman.",
            "detected_language": "uz",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": None,
            "estimated_price": None,
            "handoff": False,
            "catalog_items": [],
            "stock_items": [],
        }
        from unittest.mock import patch
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="resp_1")
            result = ai_reply(conversation)
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual({tool["name"] for tool in kwargs["tools"]}, {"client_leads_get", "client_lead_create", "client_lead_edit", "get_catalog", "get_stock", "get_flower_variant_info", "send_catalog_image", "send_catalog_images"})
        self.assertTrue(kwargs["parallel_tool_calls"] is False)
        self.assertEqual(result["reply"], payload["reply"])
        self.assertEqual(kwargs["instructions"], AISettings.objects.get(pk=1).system_prompt)
        self.assertTrue(kwargs["input"][0]["content"].startswith("REAL_CONTEXT_JSON:"))
        self.assertTrue(kwargs["input"][1]["content"].startswith("LANGUAGE_CONTROL:"))
        self.assertIn("qanaqa gullar bor", kwargs["input"][2]["content"])
        self.assertIn("105000.00", kwargs["input"][-1]["content"])

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_reply_adds_greeting_control_for_first_batched_greeting(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-greeting-batch")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="Ассалому Алайкум")
        conversation.messages.create(sender="customer", text="гортензия кере")
        payload = {
            "reply": "Ассалому алайкум! Складимизда гортензия бор.",
            "detected_language": "uz",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": None,
            "estimated_price": None,
            "handoff": False,
            "catalog_items": [],
            "stock_items": [],
        }
        from unittest.mock import patch
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="resp_1")
            result = ai_reply(conversation)
        kwargs = client.responses.create.call_args.kwargs
        self.assertIn("Uzbek Cyrillic", kwargs["input"][1]["content"])
        self.assertTrue(kwargs["input"][2]["content"].startswith("GREETING_CONTROL:"))
        self.assertIn("Ассалому Алайкум", kwargs["input"][3]["content"])
        self.assertEqual(result["detected_language"], "uz")

    def test_ai_tool_definitions_are_whitelisted(self):
        self.assertEqual({tool["name"] for tool in ai_tool_definitions()}, {"client_leads_get", "client_lead_create", "client_lead_edit", "get_catalog", "get_stock", "get_flower_variant_info", "send_catalog_image", "send_catalog_images"})

    def test_get_catalog_tool_filters_baskets(self):
        basket = CatalogItem.objects.create(branch=self.branch, name_uz="Oq savat", arrangement_type="basket", price=700000, status="available")
        self.item.status = "available"
        self.item.save(update_fields=["status", "updated_at"])
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="telegram:11")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        result = execute_ai_tool("get_catalog", {"query": "", "arrangement_type": "basket"}, conversation)
        names = {row["name_uz"] for row in result["catalog"]}
        self.assertIn(basket.name_uz, names)
        self.assertNotIn(self.item.name_uz, names)

    def test_get_stock_tool_does_not_return_baskets_when_flower_is_missing(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="telegram:13")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        result = execute_ai_tool("get_stock", {"query": "gortenziya"}, conversation)
        self.assertEqual(result, {"stock": []})

    def test_client_lead_create_tool_creates_customer_lead_and_usage(self):
        self.item.status = "available"
        self.item.save(update_fields=["status", "updated_at"])
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="telegram:10")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        result = execute_ai_tool("client_lead_create", {
            "customer_name": "Ahmad",
            "phone": "901234567",
            "request_text": "Oq buket 1 dona, kelib olish",
            "arrangement_type": "catalog",
            "estimated_price": 500000,
            "catalog_items": [{"catalog_name": "Oq buket", "quantity": 1}],
            "stock_items": [],
            "note": None,
        }, conversation)
        self.assertTrue(result["ok"])
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Ahmad")
        self.assertEqual(customer.phone, "+998901234567")
        lead = Lead.objects.get(id=result["lead_id"])
        self.assertEqual(lead.request_uz, "Oq buket 1 dona, kelib olish")
        self.assertEqual(lead.source, "telegram")
        self.assertTrue(LeadCatalogUsage.objects.filter(lead=lead, catalog_item=self.item, quantity=1).exists())

    def test_client_lead_edit_infers_pickup_when_request_text_missing(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="telegram:12", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        lead = Lead.objects.create(customer=customer, branch=self.branch, conversation=conversation, request_uz="Pion buketi 1 dona katalogdan. kelib olish yoki yetkazib berish tanlanmagan.")
        conversation.messages.create(sender="customer", text="borib olaman")
        result = execute_ai_tool("client_lead_edit", {
            "lead_id": lead.id,
            "customer_name": "Ahmad",
            "phone": "901234567",
            "request_text": None,
            "status": None,
            "arrangement_type": None,
            "estimated_price": None,
            "catalog_items": None,
            "stock_items": None,
            "note": None,
        }, conversation)
        self.assertTrue(result["ok"])
        lead.refresh_from_db()
        self.assertIn("Mijoz kelib olib ketadi.", lead.request_uz)

    def test_ai_stock_rows_matches_long_price_query(self):
        rows = ai_stock_rows("Mondial oq atirgul narxi va mavjudlik 10 dona", limit=10)
        self.assertTrue(any(row["batch_id"] == self.batch.id for row in rows))

    def test_ai_stock_rows_excludes_variants_without_stock_from_general_list(self):
        flower = Flower.objects.create(name_uz="Gortenziya", slug="gortenziya")
        FlowerVariant.objects.create(flower=flower, name_uz="Snowball", color_uz="Oq")
        FlowerVariant.objects.create(flower=flower, name_uz="Limelight", color_uz="Yashil")
        rows = ai_stock_rows("gortenziya", limit=10)
        self.assertFalse(any(row["variant_uz"] in {"Snowball", "Limelight"} for row in rows))

    def test_ai_stock_rows_include_full_variant_display_names(self):
        flower = Flower.objects.create(name_uz="Gortenziya", slug="gortenziya-display")
        golland = FlowerVariant.objects.create(flower=flower, name_uz="Gortenziya Golland", color_uz="Moviy")
        kolumbiya = FlowerVariant.objects.create(flower=flower, name_uz="Gortenziya Kolumbiya", color_uz="Moviy")
        StockBatch.objects.create(branch=self.branch, variant=golland, batch_number="GOL-1", height_cm=50, stems_per_bunch=5, received_stems=20, remaining_stems=20, cost_per_stem=70000, sale_price_per_stem=105000, sale_price_per_bunch=500000)
        StockBatch.objects.create(branch=self.branch, variant=kolumbiya, batch_number="KOL-1", height_cm=50, stems_per_bunch=5, received_stems=20, remaining_stems=20, cost_per_stem=35000, sale_price_per_stem=60000, sale_price_per_bunch=285000)
        rows = ai_stock_rows("gortenziya kok", limit=10)
        rows_by_name = {row["display_name_uz"]: row for row in rows}
        self.assertIn("Gortenziya Golland Moviy", rows_by_name)
        self.assertIn("Gortenziya Kolumbiya Moviy", rows_by_name)
        self.assertEqual(rows_by_name["Gortenziya Golland Moviy"]["price_per_stem"], "105000.00")
        self.assertEqual(rows_by_name["Gortenziya Kolumbiya Moviy"]["price_per_stem"], "60000.00")
        self.assertTrue(all(row["color_uz"] == "Moviy" for row in rows if row["flower_uz"] == "Gortenziya"))

    def test_ai_flower_variant_rows_can_show_specific_variant_without_stock(self):
        flower = Flower.objects.create(name_uz="Gortenziya", slug="gortenziya")
        FlowerVariant.objects.create(flower=flower, name_uz="Snowball", color_uz="Oq")
        rows = ai_flower_variant_rows("gortenziya snow ball", limit=10)
        self.assertTrue(any(row["variant_uz"] == "Snowball" and row["active_stock"] == [] for row in rows))

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

    def test_delayed_reply_waits_until_latest_message_is_old_enough(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="telegram:444", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        message = conversation.messages.create(sender="customer", text="salom")
        from unittest.mock import patch
        with patch("core.tasks.process_delayed_telegram_reply.apply_async") as schedule_mock, patch("core.tasks.process_pending_customer_reply") as reply_mock:
            result = process_delayed_telegram_reply(conversation.id, message.id, "444")
        self.assertIsNone(result)
        schedule_mock.assert_called_once()
        reply_mock.assert_not_called()

    def test_pending_customer_reply_handles_empty_social_post(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-no-post", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch, social_post=None)
        message = conversation.messages.create(sender="customer", text="salom")
        from unittest.mock import patch
        with patch("core.services.create_ai_reply_for_conversation", side_effect=lambda conv: Message.objects.create(conversation=conv, sender="ai", text="Javob")):
            reply = process_pending_customer_reply(conversation.id, message.id)
        self.assertIsNotNone(reply)
        conversation.refresh_from_db()
        self.assertEqual(conversation.ai_replied_to_message_id, message.id)

    def test_conversation_serializer_exposes_ai_active_and_clears_expired_pause(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-pause")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch, ai_paused_until=timezone.now() - timedelta(minutes=1), ai_pause_reason="operator_message")
        data = ConversationSerializer(conversation).data
        conversation.refresh_from_db()
        self.assertTrue(data["ai_is_active"])
        self.assertIsNone(data["ai_paused_until"])
        self.assertEqual(conversation.ai_pause_reason, "")

    def test_delayed_instagram_reply_does_not_show_typing_when_ai_paused(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="ig-paused")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch, ai_paused_until=timezone.now() + timedelta(minutes=15), ai_pause_reason="operator_message")
        message = conversation.messages.create(sender="customer", text="salom")
        from unittest.mock import patch
        with patch("core.tasks.instagram_sender_action") as typing_mock, patch("core.tasks.process_pending_customer_reply") as reply_mock:
            result = process_delayed_instagram_reply(conversation.id, message.id, "ig-paused")
        self.assertIsNone(result)
        typing_mock.assert_not_called()
        reply_mock.assert_not_called()

    def test_delayed_telegram_reply_does_not_auto_send_catalog_image(self):
        self.item.status = "available"
        self.item.image_url = "https://example.com/oq-buket.jpg"
        self.item.save(update_fields=["status", "image_url", "updated_at"])
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="telegram:123", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        customer_message = conversation.messages.create(sender="customer", text="oq buket rasmi")
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(seconds=8))
        reply_message = Message.objects.create(conversation=conversation, sender="ai", text="Mana rasmi", metadata={"catalog_items": [{"catalog_id": self.item.id, "quantity": 1}]})
        from unittest.mock import patch
        with patch("core.tasks.process_pending_customer_reply", return_value=reply_message), patch("core.tasks.telegram_sender_action", return_value={"ok": True}), patch("core.tasks.telegram_send", return_value={"ok": True}) as text_mock:
            result = process_delayed_telegram_reply(conversation.id, customer_message.id, "555")
        self.assertEqual(result, reply_message.id)
        text_mock.assert_called_once_with("555", "Mana rasmi")

    def test_delayed_telegram_reply_skips_context_image_after_tool_image(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="telegram:123", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        customer_message = conversation.messages.create(sender="customer", text="oq buket rasmi")
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(seconds=8))
        reply_message = Message.objects.create(conversation=conversation, sender="ai", text="Rasmini yubordim.", metadata={"catalog_items": [{"catalog_id": self.item.id, "quantity": 1}], "image_tool_results": [{"image_sent": True, "catalog_id": self.item.id}]})
        from unittest.mock import patch
        with patch("core.tasks.process_pending_customer_reply", return_value=reply_message), patch("core.tasks.telegram_sender_action", return_value={"ok": True}), patch("core.tasks.telegram_send", return_value={"ok": True}):
            result = process_delayed_telegram_reply(conversation.id, customer_message.id, "555")
        self.assertEqual(result, reply_message.id)

    def test_location_reply_splits_into_two_messages(self):
        text = "Manzillarimiz:\n\n1. Ул. Мукими 1\nhttps://yandex.uz/maps/-/CTVJzD4O\n\n2. 1-й квартал, 1, массив Чиланзар, Чиланзарский район, Ташкент\nhttps://yandex.uz/maps/-/CTVJfPoq\n\nQaysi manzilga yo‘l ko‘rsatib beray?"
        messages = split_location_reply(text)
        self.assertEqual(len(messages), 2)
        self.assertIn("CTVJzD4O", messages[0])
        self.assertIn("CTVJfPoq", messages[1])
        self.assertIn("Qaysi manzilga", messages[1])

    def test_new_location_reply_splits_after_confirmation(self):
        text = "Rahmat, buyurtmangiz qabul qilindi.\n\nManzil: Bobur ko‘chasi 10\nhttps://yandex.uz/maps/-/CTfQ6TMD\nIsh vaqti: 24/7"
        messages = split_location_reply(text)
        self.assertEqual(messages[0], "Rahmat, buyurtmangiz qabul qilindi.")
        self.assertIn("Bobur", messages[1])
        self.assertIn("24/7", messages[1])

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
        self.assertEqual(customer.name, "")
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

    def test_instagram_story_reply_links_catalog_by_active_story_asset_url(self):
        post = SocialPost.objects.create(
            branch=self.branch,
            post_type="story",
            media_id="story-share-3946136376066774555",
            permalink="https://www.instagram.com/stories/extra_teest/3946136376066774555/",
            story_share_id="3946136376066774555",
            webhook_story_id="17900000000000000",
            title_uz="Pion buketi",
            title_ru="Pion bouquet",
            price=800000,
            is_active=True,
        )
        CatalogItem.objects.create(
            branch=self.branch,
            social_post=post,
            name_uz="Pion buketi",
            arrangement_type="bouquet",
            price=800000,
            status="available",
        )
        payload = {
            "entry": [{
                "messaging": [{
                    "sender": {"id": "ig-user-story-asset"},
                    "recipient": {"id": "ig-business"},
                    "message": {
                        "mid": "mid-story-asset-1",
                        "text": "shu nechpul",
                        "reply_to": {
                            "story": {
                                "url": "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=18151925590500461&signature=test",
                            }
                        },
                    },
                }],
            }],
        }
        from unittest.mock import patch

        with patch("core.webhook_services.find_active_story_by_media_url", return_value={
            "id": "18151925590500461",
            "media_url": "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=18151925590500461&signature=live",
            "permalink": "https://www.instagram.com/stories/extra_teest/3946136376066774555/",
        }):
            jobs = resolve_instagram_event(payload)
        self.assertEqual(len(jobs), 1)
        conversation = Conversation.objects.get(customer__instagram_user_id="ig-user-story-asset")
        message = Message.objects.get(instagram_message_id="mid-story-asset-1")
        post.refresh_from_db()
        self.assertEqual(conversation.social_post_id, post.id)
        self.assertIn("18151925590500461", post.webhook_story_id)
        self.assertNotIn("bazadagi story/post/reel katalogiga bog‘lanmagan", message.text)

    def test_instagram_story_reply_creates_social_post_from_catalog_story_url(self):
        item = CatalogItem.objects.create(
            branch=self.branch,
            name_uz="Pushti atirgul buketi",
            arrangement_type="bouquet",
            price=500000,
            status="available",
            instagram_story_url="https://www.instagram.com/stories/extra_teest/3948457236253594433/",
        )
        payload = {
            "entry": [{
                "messaging": [{
                    "sender": {"id": "ig-user-catalog-story"},
                    "recipient": {"id": "ig-business"},
                    "message": {
                        "mid": "mid-catalog-story-1",
                        "text": "narxi qancha",
                        "attachments": [{
                            "type": "ig_story",
                            "payload": {
                                "story_media_id": "18151925590500461",
                                "story_media_url": "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=18151925590500461&signature=test",
                            },
                        }],
                    },
                }],
            }],
        }
        from unittest.mock import patch

        with patch("core.webhook_services.find_active_story_by_media_url", return_value={
            "id": "18151925590500461",
            "media_url": "https://scontent.example/story.jpg",
            "permalink": "https://www.instagram.com/stories/extra_teest/3948457236253594433/",
        }):
            jobs = resolve_instagram_event(payload)
        self.assertEqual(len(jobs), 1)
        item.refresh_from_db()
        conversation = Conversation.objects.get(customer__instagram_user_id="ig-user-catalog-story")
        message = Message.objects.get(instagram_message_id="mid-catalog-story-1")
        self.assertIsNotNone(item.social_post_id)
        self.assertEqual(conversation.social_post_id, item.social_post_id)
        self.assertNotIn("bazadagi story/post/reel katalogiga bog‘lanmagan", message.text)

    def test_instagram_lookaside_base_url_does_not_match_other_story(self):
        SocialPost.objects.create(
            branch=self.branch,
            post_type="story",
            media_id="18151925590500461",
            permalink="https://www.instagram.com/stories/extra_teest/3948457236253594433/",
            story_share_id="3948457236253594433",
            webhook_story_id="18151925590500461",
            webhook_story_url="https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=18151925590500461&signature=old",
            title_uz="Gortenziya Mix",
            title_ru="Hydrangea Mix",
            price=1500000,
            is_active=True,
        )
        payload = {
            "entry": [{
                "messaging": [{
                    "sender": {"id": "ig-user-other-story"},
                    "recipient": {"id": "ig-business"},
                    "message": {
                        "mid": "mid-other-story-1",
                        "text": "buni narxi qancha",
                        "attachments": [{
                            "type": "ig_story",
                            "payload": {
                                "story_media_id": "18090487133179534",
                                "story_media_url": "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=18090487133179534&signature=new",
                            },
                        }],
                    },
                }],
            }],
        }
        from unittest.mock import patch

        with patch("core.webhook_services.find_active_story_by_media_url", return_value=None):
            jobs = resolve_instagram_event(payload)
        self.assertEqual(len(jobs), 1)
        conversation = Conversation.objects.get(customer__instagram_user_id="ig-user-other-story")
        message = Message.objects.get(instagram_message_id="mid-other-story-1")
        self.assertIsNone(conversation.social_post_id)
        self.assertIn("bazadagi story/post/reel katalogiga bog‘lanmagan", message.text)

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

        with patch("core.platform_services.requests.post", return_value=MockResponse()):
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
        flower = Flower.objects.create(name_uz="Atirgul API", slug="rose-api")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Freedom", color_uz="Qizil")
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

    def test_customer_delete_archives_when_leads_exist(self):
        customer = Customer.objects.create(branch=self.branch, instagram_user_id="delete-me", name="Ahmad", phone="+998901234567")
        Lead.objects.create(customer=customer, branch=self.branch, request_uz="Test buyurtma")
        response = self.client.delete(f"/api/customers/{customer.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["archived"])
        customer.refresh_from_db()
        self.assertEqual(customer.name, "")
        self.assertEqual(customer.phone, "")
        self.assertEqual(customer.instagram_user_id, f"deleted:{customer.id}")
        response = self.client.get("/api/customers/")
        ids = [row["id"] for row in response.json()["results"]]
        self.assertNotIn(customer.id, ids)

    def test_social_post_response_includes_lead_ids(self):
        post = SocialPost.objects.create(branch=self.branch, post_type="story", title_uz="Story buket", title_ru="Story bouquet", is_active=True)
        customer = Customer.objects.create(branch=self.branch, name="Madina", phone="+998901234567", instagram_user_id="ig-lead")
        lead = Lead.objects.create(customer=customer, branch=self.branch, social_post=post, status="won", request_uz="Storydagi buket", arrangement_type="catalog", estimated_price=400000)
        item = CatalogItem.objects.create(branch=self.branch, social_post=post, name_uz="Qizil buket", arrangement_type="bouquet", price=400000, quantity_total=4, status="available")
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
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 88)

    def test_social_post_custom_catalog_merges_multiple_flower_payloads_into_one_item(self):
        flower = Flower.objects.create(name_uz="Pion API", slug="pion-api")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Sarah", color_uz="Pushti")
        second_batch = StockBatch.objects.create(branch=self.branch, variant=variant, batch_number="API-2", height_cm=55, stems_per_bunch=10, received_stems=80, remaining_stems=80, cost_per_stem=50000, sale_price_per_stem=80000, sale_price_per_bunch=800000)
        response = self.client.post("/api/social-posts/", {
            "branch": self.branch.id,
            "post_type": "post",
            "media_id": "api-post-custom-merge",
            "title_uz": "Custom",
            "title_ru": "Custom",
            "price": "0.00",
            "is_active": True,
            "catalog_items": [
                {
                    "name_uz": "Atirgul custom",
                    "arrangement_type": "bouquet",
                    "catalog_kind": "custom",
                    "price": "120000.00",
                    "florist_salary_amount": "40000.00",
                    "discount_reason": "Custom jamlangan skidka",
                    "quantity_total": 1,
                    "composition": [{"stock_batch": self.batch.id, "quantity_stems": 4, "quantity_bunches": "0.20"}],
                },
                {
                    "name_uz": "Pion custom",
                    "arrangement_type": "bouquet",
                    "catalog_kind": "custom",
                    "price": "240000.00",
                    "florist_salary_amount": "60000.00",
                    "discount_reason": "Custom jamlangan skidka",
                    "quantity_total": 1,
                    "composition": [{"stock_batch": second_batch.id, "quantity_stems": 3, "quantity_bunches": "0.30"}],
                },
            ],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        post = SocialPost.objects.get(media_id="api-post-custom-merge")
        self.assertEqual(post.catalog_items.count(), 1)
        item = post.catalog_items.get()
        self.assertEqual(item.catalog_kind, "custom")
        self.assertEqual(item.status, "sold")
        self.assertEqual(item.price, Decimal("360000.00"))
        self.assertEqual(item.florist_salary_amount, Decimal("100000.00"))
        self.assertEqual(item.composition.count(), 2)
        self.assertTrue(item.composition.filter(stock_batch=self.batch, quantity_stems=4).exists())
        self.assertTrue(item.composition.filter(stock_batch=second_batch, quantity_stems=3).exists())

    def test_catalog_create_deducts_flowers_and_materials_then_delete_restores_unsold(self):
        material = Packaging.objects.create(branch=self.branch, packaging_type="wrap", name_uz="Koreya qogoz", quantity=10, sale_price=50000)
        payload = {
            "name_uz": "Materialli buket",
            "arrangement_type": "bouquet",
            "price": "450000.00",
            "quantity_total": 3,
            "status": "available",
            "composition": [{"stock_batch": self.batch.id, "quantity_stems": 4}],
            "materials": [{"packaging": material.id, "quantity": 2}],
        }
        response = self.client.post("/api/catalog/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        item_id = response.json()["id"]
        self.batch.refresh_from_db()
        material.refresh_from_db()
        item = CatalogItem.objects.get(id=item_id)
        self.assertEqual(self.batch.remaining_stems, 88)
        self.assertEqual(material.quantity, 4)
        self.assertEqual(item.quantity_stock_deducted, 3)
        self.assertTrue(CatalogMaterialUsage.objects.filter(catalog_item=item, packaging=material, quantity=2).exists())
        response = self.client.patch(f"/api/catalog/{item_id}/", {"quantity_total": 2}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        material.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 92)
        self.assertEqual(material.quantity, 6)
        self.assertEqual(item.quantity_stock_deducted, 2)
        response = self.client.delete(f"/api/catalog/{item_id}/")
        self.assertEqual(response.status_code, 204)
        self.batch.refresh_from_db()
        material.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 100)
        self.assertEqual(material.quantity, 10)

    def test_catalog_create_merges_duplicate_composition_and_material_rows(self):
        material = Packaging.objects.create(branch=self.branch, packaging_type="wrap", name_uz="Dubl qogoz", quantity=20, sale_price=50000)
        response = self.client.post("/api/catalog/", {
            "name_uz": "Dubl rows buket",
            "arrangement_type": "bouquet",
            "price": "450000.00",
            "quantity_total": 2,
            "status": "available",
            "composition": [
                {"stock_batch": self.batch.id, "quantity_stems": 2, "quantity_bunches": "0.10"},
                {"stock_batch": self.batch.id, "quantity_stems": 3, "quantity_bunches": "0.15"},
            ],
            "materials": [
                {"packaging": material.id, "quantity": 1},
                {"packaging": material.id, "quantity": 2},
            ],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        item = CatalogItem.objects.get(id=response.json()["id"])
        self.assertEqual(item.composition.count(), 1)
        self.assertEqual(item.materials.count(), 1)
        self.assertEqual(item.composition.get().quantity_stems, 5)
        self.assertEqual(item.composition.get().quantity_bunches, Decimal("0.25"))
        self.assertEqual(item.materials.get().quantity, 3)
        self.batch.refresh_from_db()
        material.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 90)
        self.assertEqual(material.quantity, 14)

    def test_catalog_sell_api_accepts_discounted_price_with_reason(self):
        item = CatalogItem.objects.create(branch=self.branch, name_uz="API skidka buket", arrangement_type="bouquet", price=500000, quantity_total=2, status="available")
        response = self.client.post(f"/api/catalog/{item.id}/sell/", {"quantity": 1, "sale_price": "450000.00"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"/api/catalog/{item.id}/sell/", {"quantity": 1, "sale_price": "450000.00", "discount_reason": "VIP mijoz"}, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        item.refresh_from_db()
        self.assertEqual(item.quantity_sold, 1)
        history = item.history.get(action="sold")
        self.assertEqual(history.discount_amount, Decimal("50000.00"))
        self.assertEqual(history.discount_reason, "VIP mijoz")

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

    def test_operator_send_uses_telegram_for_telegram_conversation(self):
        customer = Customer.objects.create(branch=self.branch, name="Telegram", phone="+998901234568", instagram_user_id="telegram:123")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        conversation.messages.create(sender="customer", text="Salom", instagram_message_id="telegram:555:77")
        from unittest.mock import patch
        with patch("core.views.telegram_send", return_value={"ok": True}) as telegram_mock, patch("core.views.instagram_send", return_value={"ok": True}) as instagram_mock:
            response = self.client.post(f"/api/conversations/{conversation.id}/send/", {"text": "Javob"}, format="json")
        self.assertEqual(response.status_code, 200)
        telegram_mock.assert_called_once_with("555", "Javob")
        instagram_mock.assert_not_called()

    def test_operator_send_records_failed_delivery_when_instagram_rejects(self):
        customer = Customer.objects.create(branch=self.branch, name="Instagram", phone="+998901234567", instagram_user_id="ig-source")
        conversation = Conversation.objects.create(customer=customer, branch=self.branch)
        response_obj = requests.Response()
        response_obj.status_code = 403
        response_obj._content = b'{"error":{"message":"Forbidden"}}'
        error = requests.HTTPError("403 Client Error", response=response_obj)
        from unittest.mock import patch
        with patch("core.views.instagram_send", side_effect=error):
            response = self.client.post(f"/api/conversations/{conversation.id}/send/", {"text": "Javob"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["delivery_status"], "failed")
        self.assertEqual(response.json()["platform_status"], 403)
        message = conversation.messages.get(sender="operator", text="Javob")
        self.assertEqual(message.metadata["delivery_status"], "failed")

    def test_branches_endpoint_is_registered(self):
        response = self.client.get("/api/branches/")
        self.assertEqual(response.status_code, 200)

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

    def test_single_branch_mode_hides_branch_fields_and_defaults_branch(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="users", can_view=True, can_control=True)
        response = self.client.post("/api/users/", {
            "username": "branch-default",
            "password": "Password123!",
            "role": "operator",
            "permissions": [{"page": "conversations", "can_view": True, "can_control": True}]
        }, format="json")
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username="branch-default")
        self.assertIn(self.branch, list(created.profile.branches.all()))
        self.assertNotIn("branches", response.json()["profile"])
        response = self.client.post("/api/packaging/", {"packaging_type": "basket", "name_uz": "Branchsiz savat", "quantity": 1, "sale_price": "100000.00"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("branch", response.json())
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("net_profit", response.json())
        self.assertIn("batch_inventory_stats", response.json())
        self.assertIn("florist_production_stats", response.json())
        self.assertNotIn("branch_stock", response.json())

    def test_user_can_change_own_password(self):
        UserProfile.objects.create(user=self.user, role="admin")
        response = self.client.post("/api/me/change-password/", {
            "old_password": "wrong",
            "new_password": "NewPassword123!",
            "new_password_confirm": "NewPassword123!",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/me/change-password/", {
            "old_password": "password",
            "new_password": "NewPassword123!",
            "new_password_confirm": "NewPassword123!",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPassword123!"))

    def test_florist_notifications_are_targeted(self):
        UserProfile.objects.create(user=self.user, role="admin")
        florist_user = User.objects.create_user("target-florist", password="password", first_name="Ali")
        UserProfile.objects.create(user=florist_user, role="florist")
        FloristProfile.objects.create(user=florist_user, branch=self.branch, staff_type="florist")
        PagePermission.objects.create(user=florist_user, page="notifications", can_view=True, can_control=False)
        Notification.objects.create(branch=self.branch, notification_type="lead", title_uz="Global", title_ru="Global", body_uz="Global", body_ru="Global")
        target = Notification.objects.create(branch=self.branch, target_user=florist_user, notification_type="florist_salary", title_uz="Target", title_ru="Target", body_uz="Target", body_ru="Target")
        self.client.force_authenticate(florist_user)
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()["results"]]
        self.assertEqual(ids, [target.id])
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        titles = [row["title_uz"] for row in response.json()["results"]]
        self.assertIn("Global", titles)
        self.assertNotIn("Target", titles)

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
        response = self.client.post("/api/packaging/", {"branch": branch.id, "packaging_type": "basket", "name_uz": "API savat", "quantity": 4, "sale_price": "90000.00"}, format="json")
        self.assertEqual(response.status_code, 201)
        api_packaging = Packaging.objects.get(id=response.json()["id"])
        self.assertTrue(PackagingMovement.objects.filter(packaging=api_packaging, movement_type="in", quantity=4).exists())
        response = self.client.patch(f"/api/packaging/{api_packaging.id}/", {"quantity": 6}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PackagingMovement.objects.filter(packaging=api_packaging, movement_type="adjustment", quantity=2).exists())
        packaging = Packaging.objects.create(branch=branch, packaging_type="basket", name_uz="Test savat", quantity=10, sale_price=100000)
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

    def test_stock_batch_create_calculates_received_stems_from_bunches(self):
        flower = Flower.objects.create(name_uz="Gortenziya API", slug="gortenziya-api")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Golland", color_uz="Moviy")
        response = self.client.post("/api/stock-batches/", {
            "variant": variant.id,
            "batch_number": "BUNCH-1",
            "height_cm": 50,
            "stems_per_bunch": 5,
            "received_bunches": "3.00",
            "cost_per_stem": "50000.00",
            "sale_price_per_stem": "80000.00",
            "sale_price_per_bunch": "400000.00",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["received_stems"], 15)
        self.assertEqual(response.json()["remaining_stems"], 15)
        self.assertEqual(response.json()["remaining_bunches"], "3.00")

    def test_mini_app_lead_history_returns_customer_orders(self):
        branch = Branch.objects.create(name="Mini", code="MINI")
        CatalogItem.objects.create(branch=branch, name_uz="Mini katalog", arrangement_type="bouquet", price=250000, status="available")
        init_data = 'user={"id":777,"first_name":"Ali"}'
        payload = {"init_data": init_data, "branch": branch.id, "arrangement_type": "basket", "request_text": "7 ta gortenziya savatga", "name": "Ali", "phone": "901234567", "note": "Bugun kerak"}
        from unittest.mock import patch
        quote = {"branch": branch, "lines": [{"type": "custom_text", "request_text": "7 ta gortenziya savatga"}], "packaging": None, "florist_fee": "50000", "estimated_price": "750000", "price_is_estimate": True, "ai_note": "Taxminiy narx"}
        with patch("core.views.mini_app_custom_quote_ai", return_value=quote):
            response = APIClient().post("/api/mini-app/leads/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        lead = Lead.objects.get(source="mini_app")
        self.assertEqual(lead.customer.instagram_user_id, "miniapp:777")
        self.assertEqual(lead.customer.phone, "+998901234567")
        self.assertEqual(lead.details["lines"][0]["request_text"], "7 ta gortenziya savatga")
        self.assertTrue(lead.details["price_is_estimate"])
        response = APIClient().get("/api/mini-app/me/", {"init_data": init_data})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["customer"]["name"], "Ali")
        self.assertEqual(len(data["orders"]), 1)
        self.assertEqual(data["orders"][0]["details"]["lines"][0]["type"], "custom_text")
        response = APIClient().get("/api/mini-app/leads/", {"init_data": init_data})
        self.assertEqual(response.status_code, 200)
        leads_data = response.json()
        self.assertEqual(leads_data["customer"]["name"], "Ali")
        self.assertEqual(len(leads_data["orders"]), 1)
        self.assertEqual(leads_data["orders"][0]["status"], "new")
        response = APIClient().get("/api/mini-app/catalog/", {"init_data": init_data, "branch": branch.id})
        self.assertEqual(response.status_code, 200)
        catalog_data = response.json()
        self.assertEqual(len(catalog_data["orders"]), 1)
        self.assertNotIn("stock", catalog_data)
        self.assertNotIn("packaging", catalog_data)
        self.assertEqual(catalog_data["catalog"][0]["name_uz"], "Mini katalog")

    def test_catalog_create_rejects_short_stock_for_total_quantity(self):
        payload = {
            "branch": self.branch.id,
            "name_uz": "Kop buket",
            "arrangement_type": "bouquet",
            "price": "100000.00",
            "status": "available",
            "quantity_total": 40,
            "composition": [{"stock_batch": self.batch.id, "quantity_stems": 3}],
        }
        response = self.client.post("/api/catalog/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIsInstance(data["detail"], str)
        self.assertIn("Katalogni saqlash uchun sklad qoldig'i yetarli emas.", data["detail"])
        self.assertIn("Gul: Atirgul API Freedom Qizil", data["detail"])
        self.assertIn("Kerak: 120 dona", data["detail"])
        self.assertIn("Bor: 100 dona", data["detail"])
        self.assertIn("Yetmayapti: 20 dona", data["detail"])

    def test_flower_delete_archives_when_variants_exist(self):
        flower = Flower.objects.create(name_uz="Liliya", slug="lily-delete")
        FlowerVariant.objects.create(flower=flower, name_uz="Oriental", color_uz="Oq")
        response = self.client.delete(f"/api/flowers/{flower.id}/")
        self.assertEqual(response.status_code, 204)
        flower.refresh_from_db()
        self.assertFalse(flower.is_active)
        self.assertFalse(flower.variants.first().is_active)
        self.assertTrue(AuditLog.objects.filter(action="flower_archived", entity_id=str(flower.id)).exists())

    def test_flower_variant_delete_archives_when_stock_batches_exist(self):
        variant_id = self.batch.variant_id
        response = self.client.delete(f"/api/flower-variants/{variant_id}/")
        self.assertEqual(response.status_code, 204)
        variant = FlowerVariant.objects.get(id=variant_id)
        self.assertFalse(variant.is_active)
        self.assertTrue(AuditLog.objects.filter(action="flowervariant_archived", entity_id=str(variant_id)).exists())

    def test_stock_batch_accepts_height_range_without_height_cm(self):
        payload = {
            "variant": self.batch.variant_id,
            "batch_number": "API-RANGE-1",
            "height_from_cm": 50,
            "height_to_cm": 60,
            "stems_per_bunch": 20,
            "received_stems": 40,
            "remaining_stems": 40,
            "cost_per_stem": "10000.00",
            "sale_price_per_stem": "20000.00",
            "sale_price_per_bunch": "400000.00",
            "minimum_sale_stems": 5,
        }
        response = self.client.post("/api/stock-batches/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["height_cm"], 50)
        self.assertEqual(data["height_from_cm"], 50)
        self.assertEqual(data["height_to_cm"], 60)
        self.assertEqual(data["height_label"], "50-60 sm")

    def test_ai_stock_rows_include_variant_description(self):
        variant = self.batch.variant
        variant.description_uz = "Premium import gortenziya, boshi yirikroq va rangi to‘qroq."
        variant.description_ru = "Премиальная импортная гортензия."
        variant.save(update_fields=["description_uz", "description_ru", "updated_at"])
        rows = ai_stock_rows("premium import")
        self.assertEqual(rows[0]["description_uz"], "Premium import gortenziya, boshi yirikroq va rangi to‘qroq.")
        self.assertEqual(rows[0]["description_ru"], "Премиальная импортная гортензия.")

    def test_ai_stock_rows_include_height_range_label(self):
        self.batch.height_from_cm = 50
        self.batch.height_to_cm = 60
        self.batch.save(update_fields=["height_from_cm", "height_to_cm", "updated_at"])
        rows = ai_stock_rows("Freedom")
        self.assertEqual(rows[0]["height_label"], "50-60 sm")
        self.assertEqual(rows[0]["height_from_cm"], 50)
        self.assertEqual(rows[0]["height_to_cm"], 60)

    def test_manual_lead_create_customer_and_deducts_stock_when_won(self):
        packaging = Packaging.objects.create(branch=self.branch, packaging_type="basket", name_uz="Lead savat", quantity=2, sale_price=50000)
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
        item = CatalogItem.objects.create(branch=self.branch, name_uz="Catalog buket", arrangement_type="bouquet", price=300000, quantity_total=3, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=5)
        deduct_catalog_stock(item, self.user)
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
        self.assertEqual(self.batch.remaining_stems, 85)
        self.assertEqual(item.quantity_sold, 2)
        self.assertEqual(item.quantity_stock_deducted, 3)
        response = self.client.post(f"/api/leads/{lead_id}/move/", {"status": "new"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 85)
        self.assertEqual(item.quantity_sold, 0)
        self.assertEqual(item.quantity_stock_deducted, 3)
        self.assertEqual(item.status, "available")

    def test_catalog_lead_stock_returns_when_won_is_deleted(self):
        item = CatalogItem.objects.create(branch=self.branch, name_uz="Delete buket", arrangement_type="bouquet", price=300000, quantity_total=3, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=5)
        deduct_catalog_stock(item, self.user)
        payload = {
            "branch": self.branch.id,
            "status": "new",
            "request_uz": "Delete lead",
            "arrangement_type": "catalog",
            "customer_name": "Sardor",
            "customer_phone": "901234001",
            "catalog_usage_input": [{"catalog_item": item.id, "quantity": 2}],
        }
        response = self.client.post("/api/leads/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        lead_id = response.json()["id"]
        response = self.client.patch(f"/api/leads/{lead_id}/", {"status": "won"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.batch.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 85)
        self.assertEqual(item.quantity_sold, 2)
        response = self.client.delete(f"/api/leads/{lead_id}/")
        self.assertEqual(response.status_code, 204)
        self.batch.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 85)
        self.assertEqual(item.quantity_sold, 0)
        self.assertEqual(item.quantity_stock_deducted, 3)
        self.assertFalse(Lead.objects.filter(id=lead_id).exists())
        audit = AuditLog.objects.filter(action="lead_deleted", entity_id=str(lead_id)).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.before["status"], "won")
        self.assertEqual(audit.after["restored_before_delete"]["stock_deducted_at"], None)

    def test_audit_hides_developer_logs_from_non_developer(self):
        UserProfile.objects.create(user=self.user, role="admin")
        developer = User.objects.create_user("developer-audit", password="password")
        UserProfile.objects.create(user=developer, role="developer")
        AuditLog.objects.create(user=developer, action="secret", entity_type="AISettings", entity_id="1")
        AuditLog.objects.create(user=self.user, action="visible", entity_type="Lead", entity_id="1")
        response = self.client.get("/api/audit/")
        self.assertEqual(response.status_code, 403)

    def test_analytics_and_dashboard_include_top_selling_flowers(self):
        item = CatalogItem.objects.create(branch=self.branch, name_uz="Analytics buket", arrangement_type="bouquet", price=300000, quantity_total=5, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=4)
        customer = Customer.objects.create(branch=self.branch, name="Analytics", phone="+998901234501", instagram_user_id="analytics")
        lead = Lead.objects.create(customer=customer, branch=self.branch, status="won", request_uz="Analytics lead", arrangement_type="catalog", estimated_price=600000, source="instagram")
        LeadCatalogUsage.objects.create(lead=lead, catalog_item=item, quantity=2)
        mark_catalog_sold(item, self.user, quantity=1, sale_price=250000, discount_reason="Analytics skidka")
        response = self.client.get("/api/analytics/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["daily_stats"]), 30)
        self.assertEqual(data["summary"]["orders"], 1)
        self.assertEqual(data["top_selling_flowers"][0]["name_uz"], "Atirgul API")
        self.assertEqual(data["top_selling_flowers"][0]["stems"], 8)
        self.assertEqual(data["top_catalog_items"][0]["catalog_item__name_uz"], "Analytics buket")
        self.assertEqual(data["top_catalog_items"][0]["quantity"], 2)
        self.assertEqual(data["recent_top_catalog_items"][0]["catalog_item__name_uz"], "Analytics buket")
        self.assertEqual(data["recent_top_catalog_items"][0]["orders"], 1)
        self.assertEqual(data["summary"]["discounted_catalog_sales_count"], 1)
        self.assertEqual(data["summary"]["discounted_catalog_quantity"], 1)
        self.assertEqual(Decimal(str(data["summary"]["discounted_catalog_amount"])), Decimal("50000.00"))
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["top_selling_flowers"][0]["stems"], 8)
        self.assertEqual(Decimal(str(response.json()["discounted_catalog_amount"])), Decimal("50000.00"))

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

    def test_leads_can_be_paginated_by_status_for_kanban_column(self):
        customer = Customer.objects.create(branch=self.branch, name="Kanban page", phone="+998901234569", instagram_user_id="kanban-page")
        first = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="First", sort_order=Decimal("1000"))
        second = Lead.objects.create(customer=customer, branch=self.branch, status="new", request_uz="Second", sort_order=Decimal("2000"))
        Lead.objects.create(customer=customer, branch=self.branch, status="qualified", request_uz="Other column", sort_order=Decimal("1000"))
        response = self.client.get("/api/leads/", {"status": "new", "page": 1, "page_size": 1})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["id"], first.id)
        self.assertTrue(data["next"])
        response = self.client.get("/api/leads/", {"status": "new", "page": 2, "page_size": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], second.id)
        response = self.client.get("/api/leads/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["count"], 3)

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
