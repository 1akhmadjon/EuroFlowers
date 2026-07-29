from decimal import Decimal
from datetime import timedelta
import json
import importlib
from pathlib import Path
import tempfile
import zipfile
from types import SimpleNamespace
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
import requests
from rest_framework.test import APIClient
from .models import AISettings, AuditLog, BusinessSettings, CatalogComposition, CatalogHistory, CatalogItem, CatalogMaterialUsage, Conversation, Customer, FloristAttendance, FloristProfile, FloristSalaryEntry, FloristVolumeRate, Flower, FlowerVariant, IntegrationSettings, Lead, LeadCatalogUsage, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement, UserProfile
from .serializers import CatalogItemSerializer, ConversationSerializer, FloristProfileSerializer, FloristSalaryEntrySerializer, FloristVolumeRateSerializer, PackagingSerializer, StockBatchSerializer, permission_matrix
from .inventory_services import deduct_catalog_stock, mark_catalog_sold
from .services import AI_FOLLOW_UP_DELAY_SECONDS, ai_catalog_rows, ai_flower_variant_rows, ai_reply, ai_stock_rows, ai_tool_definitions, calculate_custom_arrangement_price, create_ai_reply_for_conversation, detect_customer_reply_script, execute_ai_tool, normalize_phone, process_pending_customer_reply, process_stalled_conversation_follow_up, stock_batch_ai_row
from .tasks import process_conversation_follow_up, process_delayed_instagram_reply, process_delayed_telegram_reply, split_location_reply
from .webhook_services import resolve_instagram_event, resolve_telegram_update
from .backup_services import backup_command_matches, backup_caption, create_media_backup


class BusinessRulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", password="password")
        flower = Flower.objects.create(name_uz="Atirgul", slug="rose")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Mondial", color_uz="Oq")
        self.batch = StockBatch.objects.create(variant=variant, batch_number="T-1", height_cm=60, stems_per_bunch=20, received_stems=100, remaining_stems=100, cost_per_stem=20000, sale_price_per_stem=30000, sale_price_per_bunch=580000)
        self.item = CatalogItem.objects.create(name_uz="Oq buket", arrangement_type="bouquet", price=500000)
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
        item = CatalogItem.objects.create(name_uz="Qizil set", arrangement_type="bouquet", price=900000, quantity_total=10, status="available")
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
        item = CatalogItem.objects.create(name_uz="Skidka buket", arrangement_type="bouquet", price=500000, quantity_total=2, status="available")
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

    def test_ai_stock_rows_return_first_available_batch_per_variant(self):
        StockBatch.objects.create(variant=self.batch.variant, batch_number="T-2", height_cm=60, stems_per_bunch=20, received_stems=200, remaining_stems=200, cost_per_stem=21000, sale_price_per_stem=35000, sale_price_per_bunch=680000, received_at=timezone.localdate() + timedelta(days=1))
        rows = ai_stock_rows("atirgul mondial", limit=10)
        batch_ids = [row["batch_id"] for row in rows]
        self.assertIn(self.batch.id, batch_ids)
        self.assertNotIn(StockBatch.objects.get(batch_number="T-2").id, batch_ids)

    def test_ai_stock_rows_treats_whitespace_query_as_all_stock(self):
        rows = ai_stock_rows(" ", limit=10)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["batch_id"], self.batch.id)

    def test_ai_stock_rows_treats_generic_query_as_all_stock(self):
        rows = ai_stock_rows("текущие цветы", limit=10)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["batch_id"], self.batch.id)

    def test_ai_stock_rows_include_cyrillic_display_names(self):
        rows = ai_stock_rows("atirgul", limit=10)
        self.assertTrue(rows)
        self.assertIn("display_name_uz_cyril", rows[0])
        self.assertIn("Атиргул", rows[0]["display_name_uz_cyril"])

    def test_ai_catalog_rows_treats_whitespace_query_as_all_catalog(self):
        self.item.status = "available"
        self.item.save(update_fields=["status", "updated_at"])
        rows = ai_catalog_rows(" ", limit=10)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["name_uz"], self.item.name_uz)

    def test_custom_catalog_deducts_inventory_and_creates_salary_from_volume_rate(self):
        florist_user = User.objects.create_user("florist", password="password", first_name="Ali")
        florist = FloristProfile.objects.create(user=florist_user, staff_type="florist")
        FloristVolumeRate.objects.create(arrangement_type="bouquet", volume="small", default_stems=10, florist_fee=70000)
        packaging = Packaging.objects.create(packaging_type="wrap", name_uz="Test qogoz", cost_price=10000, sale_price=20000, quantity=5)
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
        florist = FloristProfile.objects.create(user=florist_user, staff_type="florist")
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
            "shop_latitude": "41.31108123456789",
            "shop_longitude": "69.24056234567891",
            "volume_rates": [
                {"arrangement_type": "bouquet", "volume": "small", "default_stems": 15, "florist_fee": "50000.00"},
                {"arrangement_type": "basket", "volume": "large", "default_stems": 45, "florist_fee": "120000.00"},
            ],
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        profile = serializer.save()
        self.assertEqual(profile.daily_pay, Decimal("0"))
        self.assertEqual(profile.shop_latitude, Decimal("41.3110812346"))
        self.assertEqual(profile.shop_longitude, Decimal("69.2405623457"))
        self.assertEqual(profile.volume_rates.count(), 2)
        self.assertTrue(profile.volume_rates.filter(arrangement_type="basket", volume="large", florist_fee=Decimal("120000.00")).exists())
        self.assertNotIn("branch", FloristProfileSerializer(profile).data)
        rate = profile.volume_rates.first()
        self.assertNotIn("branch", FloristVolumeRateSerializer(rate).data)

    def test_apprentice_daily_salary_update_requires_reason(self):
        apprentice_user = User.objects.create_user("apprentice", password="password", first_name="Vali")
        apprentice = FloristProfile.objects.create(user=apprentice_user, staff_type="apprentice", daily_pay=100000)
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
                packaging = serializer.save()
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
        CatalogItem.objects.create(name_uz="Sotilgan buket", arrangement_type="bouquet", price=500000, status="available", quantity_total=1, quantity_sold=1)
        CatalogItem.objects.create(name_uz="Arxiv buket", arrangement_type="bouquet", price=500000, status="archived")
        for query in ["vitrina", "vitrinada qanaqa gulla bor", "katalogdagi tayyor mahsulotlar"]:
            rows = ai_catalog_rows(query)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name_uz"], "Oq buket")

    def test_recent_catalog_item_ignores_unavailable_metadata(self):
        from .services import recent_catalog_item_for_conversation
        customer = Customer.objects.create(instagram_user_id="ig-recent-catalog")
        conversation = Conversation.objects.create(customer=customer)
        sold = CatalogItem.objects.create(name_uz="Eski katalog", arrangement_type="bouquet", price=500000, status="available", quantity_total=1, quantity_sold=1)
        conversation.messages.create(sender="ai", text="Eski rasm", metadata={"catalog_items": [{"catalog_id": sold.id, "quantity": 1}]})
        self.assertIsNone(recent_catalog_item_for_conversation(conversation))

    @override_settings(BACKUP_TELEGRAM_GROUP_ID="-1003718639311", BACKUP_TELEGRAM_THREAD_ID="1542", BACKUP_TELEGRAM_COMMAND="/eurodan_backup_tashachi")
    def test_backup_command_matches_only_configured_group_thread(self):
        payload = {"message": {"text": "/eurodan_backup_tashachi", "chat": {"id": -1003718639311}, "message_thread_id": 1542}}
        self.assertTrue(backup_command_matches(payload))
        self.assertFalse(backup_command_matches({"message": {"text": "/eurodan_backup_tashachi", "chat": {"id": -1003718639311}, "message_thread_id": 999}}))
        self.assertFalse(backup_command_matches({"message": {"text": "/start", "chat": {"id": -1003718639311}, "message_thread_id": 1542}}))

    def test_backup_media_zip_is_created_separately(self):
        with tempfile.TemporaryDirectory() as media_root, tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(media_root) / "catalog" / "test.jpg"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"image-bytes")
            with override_settings(MEDIA_ROOT=media_root):
                zip_path = create_media_backup(temp_dir)
            self.assertEqual(zip_path.name, "euroflowers_media_images.zip")
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.namelist(), ["catalog/test.jpg"])

    def test_backup_caption_uses_markdown_and_escapes_filename(self):
        caption = backup_caption(Path("euroflowers_media_images.zip"), "manual:test_user", 3, 3)
        self.assertIn("📦 *EuroFlowers backup*", caption)
        self.assertIn("Media rasmlar ZIP", caption)
        self.assertIn("euroflowers\\_media\\_images\\.zip", caption)
        self.assertIn("manual:test\\_user", caption)

    def test_ai_lead_requires_valid_customer_phone(self):
        customer = Customer.objects.create(instagram_user_id="ig-test")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="buyurtma")
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value={"reply": "Qabul qilindi", "detected_language": "uz", "customer_name": "Ahmad", "phone": "+998 ** *** ** 67", "lead_ready": True, "lead_request": "Test lead", "arrangement_type": "bouquet", "estimated_price": 100000, "handoff": False}):
            reply = create_ai_reply_for_conversation(conversation)
        customer.refresh_from_db()
        self.assertEqual(customer.phone, "")
        self.assertFalse(reply.metadata["lead_ready"])
        self.assertFalse(Lead.objects.filter(customer=customer).exists())

    def test_ai_lead_requires_customer_name(self):
        customer = Customer.objects.create(instagram_user_id="ig-test-2", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer)
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
        customer = Customer.objects.create(instagram_user_id="ig-single-catalog")
        post = SocialPost.objects.create(post_type="story", media_id="story-oq-buket", title_uz="Oq buket", title_ru="White bouquet", price=500000, is_active=True)
        self.item.social_post = post
        self.item.save(update_fields=["social_post", "updated_at"])
        conversation = Conversation.objects.create(customer=customer, social_post=post)
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
        customer = Customer.objects.create(instagram_user_id="ig-single-catalog-filter")
        conversation = Conversation.objects.create(customer=customer)
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
        second = CatalogItem.objects.create(name_uz="Pushti buket", arrangement_type="bouquet", price=600000, status="available", image_url="https://example.com/pushti-buket.jpg")
        customer = Customer.objects.create(instagram_user_id="ig-multi-catalog")
        conversation = Conversation.objects.create(customer=customer)
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

    def test_ai_stock_list_reply_removes_image_offer(self):
        self.batch.image_url = "https://example.com/mondial.jpg"
        self.batch.save(update_fields=["image_url", "updated_at"])
        customer = Customer.objects.create(instagram_user_id="ig-stock-list")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="qanaqa gullar bor")
        payload = {
            "reply": "Skladimizda Atirgul Mondial bor\n\nQaysi biridan buket yoki savat yasaymiz yoki rasmni ko‘rmoqchimisiz?",
            "detected_language": "uz",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": None,
            "estimated_price": None,
            "handoff": False,
            "catalog_items": [],
            "stock_items": [{"batch_id": self.batch.id, "quantity_stems": 0, "quantity_bunches": 0}],
            "tool_results": [{"name": "get_stock", "arguments": {"query": "all"}, "output": {"stock": [{"batch_id": self.batch.id, "display_name_uz": "Atirgul Mondial Oq", "has_image": True}]}}],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload), patch("core.services.instagram_send_image") as image_mock:
            reply = create_ai_reply_for_conversation(conversation)
        image_mock.assert_not_called()
        self.assertNotIn("rasmni", reply.text.lower())
        self.assertIn("Qaysi biridan buket yoki savat yasaymiz?", reply.text)

    def test_ai_stock_false_negative_is_overridden_for_russian(self):
        customer = Customer.objects.create(instagram_user_id="ig-stock-ru")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="какие цветы есть")
        payload = {
            "reply": "Сейчас в витрине готовых цветов нет.",
            "detected_language": "ru",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": None,
            "estimated_price": None,
            "handoff": False,
            "catalog_items": [],
            "stock_items": [],
            "tool_results": [{"name": "get_stock", "arguments": {"query": "текущие цветы"}, "output": {"stock": [stock_batch_ai_row(self.batch)]}}],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload):
            reply = create_ai_reply_for_conversation(conversation)
        self.assertIn("Сейчас в складе есть такие цветы", reply.text)
        self.assertIn("Атиргул", reply.text)
        self.assertNotIn("витрине", reply.text.lower())
        self.assertNotIn("нет", reply.text.lower())

    def test_ai_stock_false_negative_is_overridden_for_uzbek_cyrillic(self):
        customer = Customer.objects.create(instagram_user_id="ig-stock-cyril")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="канака гулла бор")
        payload = {
            "reply": "Ҳозир витринада тайёр гуллар рўйхати бўш экан.",
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
            "tool_results": [{"name": "get_stock", "arguments": {"query": "канака гулла"}, "output": {"stock": [stock_batch_ai_row(self.batch)]}}],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload):
            reply = create_ai_reply_for_conversation(conversation)
        self.assertIn("Ҳозир складимизда қуйидаги гуллар бор", reply.text)
        self.assertIn("Атиргул", reply.text)
        self.assertNotIn("Atirgul", reply.text)
        self.assertNotIn("бўш", reply.text.lower())

    def test_ai_stock_generic_empty_tool_result_falls_back_to_all_stock(self):
        customer = Customer.objects.create(instagram_user_id="ig-stock-generic-empty")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="какие цветы есть")
        payload = {
            "reply": "Сейчас в витрине готовых цветов нет.",
            "detected_language": "ru",
            "customer_name": None,
            "phone": None,
            "lead_ready": False,
            "lead_request": None,
            "arrangement_type": None,
            "estimated_price": None,
            "handoff": False,
            "catalog_items": [],
            "stock_items": [],
            "tool_results": [{"name": "get_stock", "arguments": {"query": "популярные цветы"}, "output": {"stock": []}}],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload):
            reply = create_ai_reply_for_conversation(conversation)
        self.assertIn("Сейчас в складе есть такие цветы", reply.text)
        self.assertIn("Атиргул", reply.text)
        self.assertNotIn("витрине", reply.text.lower())

    def test_ai_stock_specific_empty_tool_result_is_clean_not_found_reply(self):
        customer = Customer.objects.create(instagram_user_id="ig-stock-specific-empty")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="gortenziya bormi")
        payload = {
            "reply": "Qaysi rangini xohlaysiz?",
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
            "tool_results": [{"name": "get_stock", "arguments": {"query": "gortenziya"}, "output": {"stock": []}}],
        }
        from unittest.mock import patch
        with patch("core.services.ai_reply", return_value=payload):
            reply = create_ai_reply_for_conversation(conversation)
        self.assertEqual(reply.text, "Hozir skladimizda gortenziya qolmagan ekan.")
        self.assertNotIn("Qaysi rangini", reply.text)

    def test_ai_stock_image_request_sends_recent_stock_image_when_tool_missing(self):
        self.batch.image_url = "https://example.com/mondial.jpg"
        self.batch.save(update_fields=["image_url", "updated_at"])
        customer = Customer.objects.create(instagram_user_id="telegram:44")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="qanaqa gullar bor")
        conversation.messages.create(sender="ai", text="Skladimizda Atirgul Mondial bor", metadata={"tool_results": [{"name": "get_stock", "arguments": {"query": "all"}, "output": {"stock": [{"batch_id": self.batch.id, "display_name_uz": "Atirgul Mondial Oq", "has_image": True}]}}]})
        conversation.messages.create(sender="customer", text="rasm korsatchi")
        payload = {
            "reply": "Rasmni yubordim.",
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
        with patch("core.services.ai_reply", return_value=payload), patch("core.services.telegram_send_image", return_value={"ok": True}) as image_mock:
            reply = create_ai_reply_for_conversation(conversation)
        image_mock.assert_called_once_with("44", "https://example.com/mondial.jpg")
        self.assertEqual(reply.metadata["tool_results"][-1]["name"], "send_stock_image")
        self.assertIn("Atirgul Mondial Oq rasmi", reply.text)
        self.assertIn("Shu guldan nechta dona qilib buket yoki savat yasaymiz?", reply.text)

    def test_pickup_reply_updates_lead_and_removes_internal_status_words(self):
        customer = Customer.objects.create(instagram_user_id="telegram:45", name="Ahmad", phone="+998901112233")
        conversation = Conversation.objects.create(customer=customer)
        lead = Lead.objects.create(customer=customer, conversation=conversation, request_uz="70 ta Atirgul prut oq buket. kelib olish yoki yetkazib berish tanlanmagan.")
        conversation.messages.create(sender="customer", text="borib olaman")
        payload = {
            "reply": "Rahmat. Siz kelib olib ketasiz deb qayd etildi. Buyurtma saqlanmoqda.",
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
        with patch("core.services.ai_reply", return_value=payload):
            reply = create_ai_reply_for_conversation(conversation)
        lead.refresh_from_db()
        self.assertIn("Mijoz kelib olib ketadi.", lead.request_uz)
        self.assertIn("Manzilimiz", reply.text)
        self.assertIn("Telefon +998 88 009 33 30", reply.text)
        self.assertNotIn("qayd", reply.text.lower())
        self.assertNotIn("saql", reply.text.lower())
        self.assertEqual(reply.metadata["tool_results"][-1]["name"], "client_lead_edit")

    def test_location_request_returns_clean_shop_contact_only(self):
        customer = Customer.objects.create(instagram_user_id="telegram:46", name="Ahmad", phone="+998901112233")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="manzil tashang")
        payload = {
            "reply": "Manzilimiz Bobur ko'chasi. Rahmat, buyurtma saqlanmoqda.",
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
        with patch("core.services.ai_reply", return_value=payload):
            reply = create_ai_reply_for_conversation(conversation)
        self.assertIn("Manzilimiz", reply.text)
        self.assertIn("Telefon +998 88 009 33 30", reply.text)
        self.assertIn("Ish vaqti 24/7", reply.text)
        self.assertNotIn("Rahmat", reply.text)
        self.assertNotIn("saql", reply.text.lower())

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_reply_sends_context_conversation_and_allowed_tools(self):
        customer = Customer.objects.create(instagram_user_id="ig-tools", name="Ahmad")
        conversation = Conversation.objects.create(customer=customer)
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
        self.assertEqual({tool["name"] for tool in kwargs["tools"]}, {"client_leads_get", "client_lead_create", "client_lead_edit", "get_catalog", "get_stock", "get_flower_variant_info", "calculate_custom_arrangement_price", "send_catalog_image", "send_catalog_images", "send_stock_image", "send_stock_images"})
        self.assertTrue(kwargs["parallel_tool_calls"] is False)
        self.assertEqual(result["reply"], payload["reply"])
        self.assertEqual(kwargs["instructions"], AISettings.objects.get(pk=1).system_prompt)
        self.assertTrue(kwargs["input"][0]["content"].startswith("REAL_CONTEXT_JSON:"))
        self.assertIn("shop_phone", kwargs["input"][0]["content"])
        self.assertTrue(kwargs["input"][1]["content"].startswith("LANGUAGE_CONTROL:"))
        self.assertIn("qanaqa gullar bor", kwargs["input"][2]["content"])
        self.assertIn("105000.00", kwargs["input"][-1]["content"])

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_reply_adds_greeting_control_for_first_batched_greeting(self):
        customer = Customer.objects.create(instagram_user_id="ig-greeting-batch")
        conversation = Conversation.objects.create(customer=customer)
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
        self.assertEqual({tool["name"] for tool in ai_tool_definitions()}, {"client_leads_get", "client_lead_create", "client_lead_edit", "get_catalog", "get_stock", "get_flower_variant_info", "calculate_custom_arrangement_price", "send_catalog_image", "send_catalog_images", "send_stock_image", "send_stock_images"})

    def test_get_catalog_tool_filters_baskets(self):
        basket = CatalogItem.objects.create(name_uz="Oq savat", arrangement_type="basket", price=700000, status="available")
        self.item.status = "available"
        self.item.save(update_fields=["status", "updated_at"])
        customer = Customer.objects.create(instagram_user_id="telegram:11")
        conversation = Conversation.objects.create(customer=customer)
        result = execute_ai_tool("get_catalog", {"query": "", "arrangement_type": "basket"}, conversation)
        names = {row["name_uz"] for row in result["catalog"]}
        self.assertIn(basket.name_uz, names)
        self.assertNotIn(self.item.name_uz, names)

    def test_get_stock_tool_does_not_return_baskets_when_flower_is_missing(self):
        customer = Customer.objects.create(instagram_user_id="telegram:13")
        conversation = Conversation.objects.create(customer=customer)
        result = execute_ai_tool("get_stock", {"query": "gortenziya"}, conversation)
        self.assertEqual(result, {"stock": []})

    def test_calculate_custom_arrangement_price_is_deterministic(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={"default_florist_fee": Decimal("50000")})
        self.batch.sale_price_per_stem = Decimal("15000")
        self.batch.remaining_stems = 100
        self.batch.save(update_fields=["sale_price_per_stem", "remaining_stems", "updated_at"])
        result = calculate_custom_arrangement_price([{"batch_id": self.batch.id, "quantity_stems": 50, "quantity_bunches": 0}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["flower_subtotal"], "750000")
        self.assertEqual(result["florist_fee"], "50000")
        self.assertEqual(result["total"], "800000")
        self.assertIn("Jami taxminan 800 000 so'm", result["display_summary_uz"]["total"])

    def test_calculate_custom_arrangement_price_handles_multiple_flowers(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={"default_florist_fee": Decimal("50000")})
        self.batch.sale_price_per_stem = Decimal("15000")
        self.batch.remaining_stems = 100
        self.batch.save(update_fields=["sale_price_per_stem", "remaining_stems", "updated_at"])
        second = StockBatch.objects.create(variant=self.batch.variant, batch_number="T-PRUT", height_cm=60, stems_per_bunch=20, received_stems=100, remaining_stems=100, cost_per_stem=10000, sale_price_per_stem=15000, sale_price_per_bunch=300000)
        customer = Customer.objects.create(instagram_user_id="telegram:calc")
        conversation = Conversation.objects.create(customer=customer)
        result = execute_ai_tool("calculate_custom_arrangement_price", {
            "stock_items": [
                {"batch_id": self.batch.id, "quantity_stems": 10, "quantity_bunches": 0},
                {"batch_id": second.id, "quantity_stems": 10, "quantity_bunches": 0},
            ],
        }, conversation)
        self.assertTrue(result["ok"])
        self.assertEqual(result["flower_subtotal"], "300000")
        self.assertEqual(result["total"], "350000")
        self.assertEqual(len(result["lines"]), 2)

    def test_send_stock_image_tool_sends_flower_image(self):
        self.batch.image_url = "https://example.com/freedom.jpg"
        self.batch.save(update_fields=["image_url", "updated_at"])
        customer = Customer.objects.create(instagram_user_id="telegram:13")
        conversation = Conversation.objects.create(customer=customer)
        from unittest.mock import patch
        with patch("core.services.telegram_send_image", return_value={"ok": True}) as image_mock:
            result = execute_ai_tool("send_stock_image", {"query": "Mondial", "batch_id": None}, conversation)
        self.assertTrue(result["ok"])
        self.assertEqual(result["image_url"], "https://example.com/freedom.jpg")
        image_mock.assert_called_once_with("13", "https://example.com/freedom.jpg")

    def test_client_lead_create_tool_creates_customer_lead_and_usage(self):
        self.item.status = "available"
        self.item.save(update_fields=["status", "updated_at"])
        customer = Customer.objects.create(instagram_user_id="telegram:10")
        conversation = Conversation.objects.create(customer=customer)
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
        customer = Customer.objects.create(instagram_user_id="telegram:12", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer)
        lead = Lead.objects.create(customer=customer, conversation=conversation, request_uz="Pion buketi 1 dona katalogdan. kelib olish yoki yetkazib berish tanlanmagan.")
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
        StockBatch.objects.create(variant=golland, batch_number="GOL-1", height_cm=50, stems_per_bunch=5, received_stems=20, remaining_stems=20, cost_per_stem=70000, sale_price_per_stem=105000, sale_price_per_bunch=500000)
        StockBatch.objects.create(variant=kolumbiya, batch_number="KOL-1", height_cm=50, stems_per_bunch=5, received_stems=20, remaining_stems=20, cost_per_stem=35000, sale_price_per_stem=60000, sale_price_per_bunch=285000)
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
        customer = Customer.objects.create(instagram_user_id="ig-debounce", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer)
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
        customer = Customer.objects.create(instagram_user_id="telegram:444", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer)
        message = conversation.messages.create(sender="customer", text="salom")
        from unittest.mock import patch
        with patch("core.tasks.process_delayed_telegram_reply.apply_async") as schedule_mock, patch("core.tasks.process_pending_customer_reply") as reply_mock:
            result = process_delayed_telegram_reply(conversation.id, message.id, "444")
        self.assertIsNone(result)
        schedule_mock.assert_called_once()
        reply_mock.assert_not_called()

    def test_pending_customer_reply_handles_empty_social_post(self):
        customer = Customer.objects.create(instagram_user_id="ig-no-post", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer, social_post=None)
        message = conversation.messages.create(sender="customer", text="salom")
        from unittest.mock import patch
        with patch("core.services.create_ai_reply_for_conversation", side_effect=lambda conv: Message.objects.create(conversation=conv, sender="ai", text="Javob")):
            reply = process_pending_customer_reply(conversation.id, message.id)
        self.assertIsNotNone(reply)
        conversation.refresh_from_db()
        self.assertEqual(conversation.ai_replied_to_message_id, message.id)

    def test_conversation_serializer_exposes_ai_active_and_clears_expired_pause(self):
        customer = Customer.objects.create(instagram_user_id="ig-pause")
        conversation = Conversation.objects.create(customer=customer, ai_paused_until=timezone.now() - timedelta(minutes=1), ai_pause_reason="operator_message")
        data = ConversationSerializer(conversation).data
        conversation.refresh_from_db()
        self.assertTrue(data["ai_is_active"])
        self.assertIsNone(data["ai_paused_until"])
        self.assertEqual(conversation.ai_pause_reason, "")

    def test_delayed_instagram_reply_does_not_show_typing_when_ai_paused(self):
        customer = Customer.objects.create(instagram_user_id="ig-paused")
        conversation = Conversation.objects.create(customer=customer, ai_paused_until=timezone.now() + timedelta(minutes=15), ai_pause_reason="operator_message")
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
        customer = Customer.objects.create(instagram_user_id="telegram:123", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer)
        customer_message = conversation.messages.create(sender="customer", text="oq buket rasmi")
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(seconds=8))
        reply_message = Message.objects.create(conversation=conversation, sender="ai", text="Mana rasmi", metadata={"catalog_items": [{"catalog_id": self.item.id, "quantity": 1}]})
        from unittest.mock import patch
        with patch("core.tasks.process_pending_customer_reply", return_value=reply_message), patch("core.tasks.telegram_sender_action", return_value={"ok": True}), patch("core.tasks.telegram_send", return_value={"ok": True}) as text_mock, patch("core.tasks.process_conversation_follow_up.apply_async") as follow_up_schedule:
            result = process_delayed_telegram_reply(conversation.id, customer_message.id, "555")
        self.assertEqual(result, reply_message.id)
        text_mock.assert_called_once_with("555", "Mana rasmi")
        follow_up_schedule.assert_called_once_with(args=[conversation.id, reply_message.id], countdown=AI_FOLLOW_UP_DELAY_SECONDS)

    def test_delayed_telegram_reply_skips_context_image_after_tool_image(self):
        customer = Customer.objects.create(instagram_user_id="telegram:123", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer)
        customer_message = conversation.messages.create(sender="customer", text="oq buket rasmi")
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(seconds=8))
        reply_message = Message.objects.create(conversation=conversation, sender="ai", text="Rasmini yubordim.", metadata={"catalog_items": [{"catalog_id": self.item.id, "quantity": 1}], "image_tool_results": [{"image_sent": True, "catalog_id": self.item.id}]})
        from unittest.mock import patch
        with patch("core.tasks.process_pending_customer_reply", return_value=reply_message), patch("core.tasks.telegram_sender_action", return_value={"ok": True}), patch("core.tasks.telegram_send", return_value={"ok": True}), patch("core.tasks.process_conversation_follow_up.apply_async") as follow_up_schedule:
            result = process_delayed_telegram_reply(conversation.id, customer_message.id, "555")
        self.assertEqual(result, reply_message.id)
        follow_up_schedule.assert_called_once_with(args=[conversation.id, reply_message.id], countdown=AI_FOLLOW_UP_DELAY_SECONDS)

    def test_follow_up_waits_thirty_minutes_after_last_ai_message(self):
        customer = Customer.objects.create(instagram_user_id="ig-follow-up")
        conversation = Conversation.objects.create(customer=customer)
        customer_message = conversation.messages.create(sender="customer", text="katalog narxlari")
        ai_message = Message.objects.create(conversation=conversation, sender="ai", text="Oq buket\nNarxi 500 000 so'm")
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(minutes=30))
        Message.objects.filter(id=ai_message.id).update(created_at=timezone.now() - timedelta(minutes=29))
        from unittest.mock import patch
        with patch("core.services.ai_follow_up_decision") as decision_mock:
            result = process_stalled_conversation_follow_up(conversation.id, ai_message.id)
        self.assertIsNone(result)
        decision_mock.assert_not_called()

    def test_follow_up_skips_when_lead_exists(self):
        customer = Customer.objects.create(instagram_user_id="ig-follow-up-lead", name="Ahmad", phone="+998901234567")
        conversation = Conversation.objects.create(customer=customer)
        customer_message = conversation.messages.create(sender="customer", text="oq buket olaman Ahmad 901234567")
        ai_message = Message.objects.create(conversation=conversation, sender="ai", text="Buyurtmangiz qabul qilindi", metadata={"lead_created_id": 10})
        Lead.objects.create(customer=customer, conversation=conversation, request_uz="Oq buket", arrangement_type="catalog")
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(minutes=32))
        Message.objects.filter(id=ai_message.id).update(created_at=timezone.now() - timedelta(minutes=31))
        from unittest.mock import patch
        with patch("core.services.ai_follow_up_decision") as decision_mock:
            result = process_stalled_conversation_follow_up(conversation.id, ai_message.id)
        self.assertIsNone(result)
        decision_mock.assert_not_called()

    def test_follow_up_creates_ai_message_from_decision(self):
        customer = Customer.objects.create(instagram_user_id="ig-follow-up-create")
        conversation = Conversation.objects.create(customer=customer)
        customer_message = conversation.messages.create(sender="customer", text="shu savat nechpul")
        ai_message = Message.objects.create(conversation=conversation, sender="ai", text="Gortenziya Mix savat\nNarxi 900 000 so'm", metadata={"catalog_items": [{"catalog_name": "Gortenziya Mix", "quantity": 1}]})
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(minutes=32))
        Message.objects.filter(id=ai_message.id).update(created_at=timezone.now() - timedelta(minutes=31))
        from unittest.mock import patch
        with patch("core.services.ai_follow_up_decision", return_value={"send_follow_up": True, "message": "Hurmatli mijoz, budjetingiz qancha edi?", "reason": "price_shown"}):
            follow_up = process_stalled_conversation_follow_up(conversation.id, ai_message.id)
        self.assertIsNotNone(follow_up)
        self.assertEqual(follow_up.sender, "ai")
        self.assertTrue(follow_up.metadata["follow_up"])
        self.assertIn("budjetingiz", follow_up.text)

    def test_follow_up_task_sends_instagram_message(self):
        customer = Customer.objects.create(instagram_user_id="ig-follow-up-send")
        conversation = Conversation.objects.create(customer=customer)
        customer_message = conversation.messages.create(sender="customer", text="katalog")
        ai_message = Message.objects.create(conversation=conversation, sender="ai", text="Narxi 500 000 so'm")
        Message.objects.filter(id=customer_message.id).update(created_at=timezone.now() - timedelta(minutes=32))
        Message.objects.filter(id=ai_message.id).update(created_at=timezone.now() - timedelta(minutes=31))
        from unittest.mock import patch
        with patch("core.services.ai_follow_up_decision", return_value={"send_follow_up": True, "message": "Budjetingiz qancha edi?", "reason": "stalled"}), patch("core.tasks.instagram_send", return_value={"ok": True}) as send_mock:
            result = process_conversation_follow_up(conversation.id, ai_message.id)
        self.assertIsNotNone(result)
        send_mock.assert_called_once_with("ig-follow-up-send", "Budjetingiz qancha edi?")

    def test_discount_negotiation_prompt_rule_requires_lead_creation(self):
        migration = importlib.import_module("core.migrations.0038_ai_prompt_discount_negotiation")
        rule = migration.DISCOUNT_NEGOTIATION_PROMPT_RULE
        self.assertIn("Arzonlashtirish va budjetga mos variant qoidasi", rule)
        self.assertIn("client_lead_create", rule)
        self.assertIn("Mijoz 200 000 so'mlik katalog buketni 150 000 so'mga so'radi", rule)

    def test_stock_image_prompt_rule_asks_quantity_before_date(self):
        migration = importlib.import_module("core.migrations.0041_ai_prompt_stock_image_and_discount_flow")
        rule = migration.STOCK_IMAGE_AND_DISCOUNT_FLOW_RULE
        self.assertIn("send_stock_image", rule)
        self.assertIn("sizga qachonga kerak edi", rule)
        self.assertIn("Shu guldan nechta dona qilib buket yoki savat yasaymiz", rule)
        self.assertIn("Gullarimiz hamyonbop narxlarda", rule)
        self.assertIn("client_lead_create", rule)

    def test_stock_list_and_calculation_prompt_rule(self):
        migration = importlib.import_module("core.migrations.0042_ai_prompt_stock_list_and_calculation_flow")
        rule = migration.STOCK_LIST_AND_CALCULATION_FLOW_RULE
        self.assertIn("Qaysi turini ko'rgingiz keladi", rule)
        self.assertIn("Qaysi biridan buket yoki savat yasaymiz", rule)
        self.assertIn("javob juda qisqa bo'lsin", rule)
        self.assertIn("quantity_stems x sale_price_per_stem", rule)
        self.assertIn("150 000 + 150 000 + 50 000 = 350 000", rule)
        self.assertIn("Hech qachon shu holatni 550 000", rule)

    def test_public_reply_boundaries_prompt_rule(self):
        migration = importlib.import_module("core.migrations.0043_ai_prompt_public_reply_boundaries")
        rule = migration.PUBLIC_REPLY_BOUNDARIES_RULE
        self.assertIn("lead, CRM, tizimga yozish", rule)
        self.assertIn("leadga qo'shsam bo'ladimi", rule)
        self.assertIn("Manzil javobining oxiriga", rule)
        self.assertIn("Rahmat, tez orada operatorlarimiz", rule)
        self.assertIn("Ko'pi bilan 3-5 qator", rule)
        self.assertIn("Jami taxminan 350 000 so'm", rule)

    def test_deterministic_price_tool_prompt_rule(self):
        migration = importlib.import_module("core.migrations.0044_ai_prompt_deterministic_price_tool")
        rule = migration.DETERMINISTIC_PRICE_TOOL_RULE
        self.assertIn("Custom buket yoki savat narxini hech qachon o'zing hisoblama", rule)
        self.assertIn("calculate_custom_arrangement_price", rule)
        self.assertIn("quantity_stems 50", rule)
        self.assertIn("775 000", rule)
        self.assertIn("errors qaytarsa, narx aytma", rule)

    def test_natural_sales_flow_prompt_rule(self):
        migration = importlib.import_module("core.migrations.0045_ai_prompt_natural_sales_flow")
        rule = migration.NATURAL_SALES_FLOW_RULE
        self.assertIn("qattiq shablon emas", rule)
        self.assertIn("50 dona guldan bitta buket", rule)
        self.assertIn("50 dona bitta buketmi yoki 50 ta buket kerakmi", rule)
        self.assertIn("darhol get_stock va calculate_custom_arrangement_price", rule)
        self.assertIn("Sizga qachonga kerak edi?", rule)
        self.assertIn("Yetkazib berish kerakmi yoki kelib olib ketasizmi", rule)
        self.assertIn("Tushunarli", rule)

    def test_clean_ai_prompt_replacement_contains_core_rules(self):
        migration = importlib.import_module("core.migrations.0046_replace_ai_prompt_clean_version")
        prompt = migration.CLEAN_EUROFLOWERS_AI_PROMPT
        self.assertIn("EUROFLOWERS PREMIUM gul do'konining Instagram va Telegramdagi AI sotuv menejerisan", prompt)
        self.assertIn("Custom narxni hech qachon o'zing hisoblama", prompt)
        self.assertIn("calculate_custom_arrangement_price", prompt)
        self.assertIn("lead_ready doim false", prompt)
        self.assertIn("REAL_CONTEXT_JSON.business", prompt)
        self.assertIn("shop_phone", prompt)
        self.assertNotIn("# EUROFLOWERS PREMIUM", prompt)

    def test_legacy_ai_prompt_restore_reverts_clean_prompt(self):
        migration = importlib.import_module("core.migrations.0047_restore_legacy_ai_prompt")
        prompt = migration.LEGACY_EUROFLOWERS_AI_PROMPT
        self.assertIn("Sen EuroFlowers Premium gul do‘konining Instagram va Telegramdagi AI sotuvchisian", prompt)
        self.assertIn("Deterministik custom narx hisoblash qoidasi", prompt)
        self.assertIn("Tabiiy sotuv muloqoti qoidasi", prompt)
        self.assertIn("calculate_custom_arrangement_price", prompt)
        self.assertNotIn("EUROFLOWERS PREMIUM gul do'konining Instagram va Telegramdagi AI sotuv menejerisan", prompt)

    def test_quality_reassurance_prompt_rule(self):
        migration = importlib.import_module("core.migrations.0048_ai_prompt_quality_reassurance")
        rule = migration.QUALITY_REASSURANCE_PROMPT_RULE
        self.assertIn("Sifat, yangi gul va obyom bo'yicha aniq javob qoidasi", rule)
        self.assertIn("Ko'nglingiz xotirjam bo'lsin", rule)
        self.assertIn("so'lib qolgan gullar bilan hech qachon buket yoki savat yasalmaydi", rule)
        self.assertIn("o'zingdan generate qilma", rule)

    def test_stock_image_tool_required_prompt_rule(self):
        migration = importlib.import_module("core.migrations.0049_ai_prompt_stock_image_tool_required")
        rule = migration.STOCK_IMAGE_TOOL_REQUIRED_RULE
        self.assertIn("Sklad rasmi bo'yicha qat'iy qoida", rule)
        self.assertIn("rasmni ko'rmoqchimisiz", rule)
        self.assertIn("send_stock_image", rule)
        self.assertIn("Tool chaqirmasdan hech qachon", rule)

    def test_stock_language_pickup_prompt_rule(self):
        migration = importlib.import_module("core.migrations.0050_ai_prompt_stock_language_pickup_rules")
        rule = migration.STOCK_LANGUAGE_PICKUP_PROMPT_RULE
        self.assertIn("Stock mavjudlik, til va pickup aniqligi", rule)
        self.assertIn("majburiy get_stock chaqir", rule)
        self.assertIn("display_name_uz_cyril", rule)
        self.assertIn("vitrinada yo'q", rule)
        self.assertIn("qayd etildi", rule)

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
        SocialPost.objects.create(post_type="post", title_uz="Test post", title_ru="Test post", is_active=True)
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
        SocialPost.objects.create(post_type="story", title_uz="Story", title_ru="Story", is_active=True)
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
        old_post = SocialPost.objects.create(post_type="reel", media_id="old-pion", title_uz="Pion buket", title_ru="Pion", is_active=True)
        customer = Customer.objects.create(instagram_user_id="ig-user-2")
        conversation = Conversation.objects.create(customer=customer, social_post=old_post)
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
        SocialPost.objects.create(post_type="post", title_uz="Test post", title_ru="Test post", is_active=True)
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
        flower = Flower.objects.create(name_uz="Atirgul API", slug="rose-api")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="Freedom", color_uz="Qizil")
        self.batch = StockBatch.objects.create(variant=variant, batch_number="API-1", height_cm=60, stems_per_bunch=20, received_stems=100, remaining_stems=100, cost_per_stem=10000, sale_price_per_stem=20000, sale_price_per_bunch=400000)

    def test_dashboard_requires_authentication(self):
        response = APIClient().get("/api/dashboard/")
        self.assertEqual(response.status_code, 401)

    def test_dashboard_includes_daily_chart_stats_for_default_month(self):
        customer = Customer.objects.create(name="Chart User", phone="+998901234567", instagram_user_id="chart-user")
        conversation = Conversation.objects.create(customer=customer)
        lead = Lead.objects.create(customer=customer, conversation=conversation, request_uz="Chart lead", arrangement_type="catalog")
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
        Customer.objects.create(instagram_user_id="placeholder")
        Customer.objects.create(instagram_user_id="complete", name="Ahmad", phone="+998901234567")
        response = self.client.get("/api/customers/")
        self.assertEqual(response.status_code, 200)
        ids = [row["instagram_user_id"] for row in response.json()["results"]]
        self.assertIn("complete", ids)
        self.assertNotIn("placeholder", ids)
        response = self.client.get("/api/customers/?include_incomplete=true")
        ids = [row["instagram_user_id"] for row in response.json()["results"]]
        self.assertIn("placeholder", ids)

    def test_customer_delete_archives_when_leads_exist(self):
        customer = Customer.objects.create(instagram_user_id="delete-me", name="Ahmad", phone="+998901234567")
        Lead.objects.create(customer=customer, request_uz="Test buyurtma")
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
        post = SocialPost.objects.create(post_type="story", title_uz="Story buket", title_ru="Story bouquet", is_active=True)
        customer = Customer.objects.create(name="Madina", phone="+998901234567", instagram_user_id="ig-lead")
        lead = Lead.objects.create(customer=customer, social_post=post, status="won", request_uz="Storydagi buket", arrangement_type="catalog", estimated_price=400000)
        item = CatalogItem.objects.create(social_post=post, name_uz="Qizil buket", arrangement_type="bouquet", price=400000, quantity_total=4, status="available")
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
        second_batch = StockBatch.objects.create(variant=variant, batch_number="API-2", height_cm=55, stems_per_bunch=10, received_stems=80, remaining_stems=80, cost_per_stem=50000, sale_price_per_stem=80000, sale_price_per_bunch=800000)
        response = self.client.post("/api/social-posts/", {
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
        material = Packaging.objects.create(packaging_type="wrap", name_uz="Koreya qogoz", quantity=10, sale_price=50000)
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
        material = Packaging.objects.create(packaging_type="wrap", name_uz="Dubl qogoz", quantity=20, sale_price=50000)
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
        item = CatalogItem.objects.create(name_uz="API skidka buket", arrangement_type="bouquet", price=500000, quantity_total=2, status="available")
        response = self.client.post(f"/api/catalog/{item.id}/sell/", {"quantity": 1, "sale_price": "450000.00"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"/api/catalog/{item.id}/sell/", {"quantity": 1, "sale_price": "450000.00", "discount_reason": "VIP mijoz", "payment_type": "card"}, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        item.refresh_from_db()
        self.assertEqual(item.quantity_sold, 1)
        history = item.history.get(action="sold")
        self.assertEqual(history.discount_amount, Decimal("50000.00"))
        self.assertEqual(history.discount_reason, "VIP mijoz")
        self.assertEqual(history.snapshot["payment_type"], "card")

    def test_conversation_response_includes_source(self):
        instagram_customer = Customer.objects.create(name="Instagram", phone="+998901234567", instagram_user_id="ig-source")
        telegram_customer = Customer.objects.create(name="Telegram", phone="+998901234568", instagram_user_id="telegram:123")
        instagram_conversation = Conversation.objects.create(customer=instagram_customer)
        telegram_conversation = Conversation.objects.create(customer=telegram_customer)
        response = self.client.get(f"/api/conversations/{instagram_conversation.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "instagram")
        self.assertEqual(response.json()["source_label"], "Instagram")
        response = self.client.get(f"/api/conversations/{telegram_conversation.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "telegram")
        self.assertEqual(response.json()["source_label"], "Telegram")

    def test_operator_send_uses_telegram_for_telegram_conversation(self):
        customer = Customer.objects.create(name="Telegram", phone="+998901234568", instagram_user_id="telegram:123")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="Salom", instagram_message_id="telegram:555:77")
        from unittest.mock import patch
        with patch("core.views.telegram_send", return_value={"ok": True}) as telegram_mock, patch("core.views.instagram_send", return_value={"ok": True}) as instagram_mock:
            response = self.client.post(f"/api/conversations/{conversation.id}/send/", {"text": "Javob"}, format="json")
        self.assertEqual(response.status_code, 200)
        telegram_mock.assert_called_once_with("555", "Javob")
        instagram_mock.assert_not_called()

    def test_operator_send_records_failed_delivery_when_instagram_rejects(self):
        customer = Customer.objects.create(name="Instagram", phone="+998901234567", instagram_user_id="ig-source")
        conversation = Conversation.objects.create(customer=customer)
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

    def test_branches_endpoint_is_removed(self):
        response = self.client.get("/api/branches/")
        self.assertEqual(response.status_code, 404)

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
        self.assertIn("audit", admin_permissions)

    def test_admin_cannot_grant_developer_only_permissions(self):
        UserProfile.objects.create(user=self.user, role="admin")
        operator = User.objects.create_user("limited", password="password")
        UserProfile.objects.create(user=operator, role="operator")
        response = self.client.post("/api/permissions/", {"user": operator.id, "page": "ai_settings", "can_view": True, "can_control": True}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/users/", {"username": "bad-user", "password": "password", "role": "operator", "permissions": [{"page": "integrations", "can_view": True, "can_control": True}]}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/api/audit/")
        self.assertEqual(response.status_code, 200)

    def test_branchless_mode_hides_branch_fields(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="users", can_view=True, can_control=True)
        response = self.client.post("/api/users/", {
            "username": "branchless-user",
            "password": "Password123!",
            "role": "operator",
            "permissions": [{"page": "conversations", "can_view": True, "can_control": True}]
        }, format="json")
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username="branchless-user")
        self.assertFalse(hasattr(created.profile, "branches"))
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
        FloristProfile.objects.create(user=florist_user, staff_type="florist")
        PagePermission.objects.create(user=florist_user, page="notifications", can_view=True, can_control=False)
        Notification.objects.create(notification_type="lead", title_uz="Global", title_ru="Global", body_uz="Global", body_ru="Global")
        target = Notification.objects.create(target_user=florist_user, notification_type="florist_salary", title_uz="Target", title_ru="Target", body_uz="Target", body_ru="Target")
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

    def test_admin_gets_notification_when_florist_checks_in(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="notifications", can_view=True, can_control=False)
        florist_user = User.objects.create_user("checkin-florist", password="password", first_name="Ali")
        UserProfile.objects.create(user=florist_user, role="florist")
        FloristProfile.objects.create(user=florist_user, staff_type="florist")
        PagePermission.objects.create(user=florist_user, page="attendance", can_view=True, can_control=True)
        self.client.force_authenticate(florist_user)
        response = self.client.post("/api/florist-attendance/check-in/", {"checked_at": "2026-07-27T09:00:00+05:00"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/notifications/?notification_type=attendance")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("ishga keldi" in row["title_uz"] for row in response.json()["results"]))

    def test_florist_check_in_accepts_precise_mobile_location(self):
        florist_user = User.objects.create_user("precise-location", password="password", first_name="Ali")
        UserProfile.objects.create(user=florist_user, role="florist")
        FloristProfile.objects.create(user=florist_user, staff_type="florist", shop_latitude=Decimal("41.2954351234"), shop_longitude=Decimal("69.2503551234"))
        PagePermission.objects.create(user=florist_user, page="attendance", can_view=True, can_control=True)
        self.client.force_authenticate(florist_user)
        response = self.client.post("/api/florist-attendance/check-in/", {
            "checked_at": "2026-07-27T09:00:00+05:00",
            "latitude": "41.29543512345678",
            "longitude": "69.25035512345678",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["check_in_latitude"], "41.2954351235")
        self.assertEqual(response.json()["check_in_longitude"], "69.2503551235")

    def test_admin_does_not_see_developer_notifications(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="notifications", can_view=True, can_control=False)
        developer = User.objects.create_user("hidden-developer", password="password", first_name="Developer")
        UserProfile.objects.create(user=developer, role="developer")
        developer_profile = FloristProfile.objects.create(user=developer, staff_type="florist")
        attendance = FloristAttendance.objects.create(florist=developer_profile, work_date=timezone.localdate(), check_in_at=timezone.now())
        Notification.objects.create(notification_type="attendance", title_uz="Developer ishga keldi", title_ru="Developer ishga keldi", body_uz="Developer ishga keldi", body_ru="Developer ishga keldi", reference_type="attendance", reference_id=attendance.id)
        Notification.objects.create(target_user=developer, notification_type="attendance", title_uz="Developer target", title_ru="Developer target", body_uz="Developer target", body_ru="Developer target")
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.status_code, 200)
        titles = [row["title_uz"] for row in response.json()["results"]]
        self.assertFalse(any("Developer" in title for title in titles))

    def test_developer_check_in_does_not_create_visible_notifications(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="notifications", can_view=True, can_control=False)
        developer = User.objects.create_user("checkin-developer", password="password", first_name="Developer")
        UserProfile.objects.create(user=developer, role="developer")
        FloristProfile.objects.create(user=developer, staff_type="florist")
        PagePermission.objects.create(user=developer, page="attendance", can_view=True, can_control=True)
        self.client.force_authenticate(developer)
        response = self.client.post("/api/florist-attendance/check-in/", {"checked_at": "2026-07-27T09:00:00+05:00"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.filter(title_uz__icontains="Developer").exists())
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/notifications/?notification_type=attendance")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

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
        response = self.client.post("/api/packaging/", {"packaging_type": "basket", "name_uz": "API savat", "quantity": 4, "sale_price": "90000.00"}, format="json")
        self.assertEqual(response.status_code, 201)
        api_packaging = Packaging.objects.get(id=response.json()["id"])
        self.assertTrue(PackagingMovement.objects.filter(packaging=api_packaging, movement_type="in", quantity=4).exists())
        response = self.client.patch(f"/api/packaging/{api_packaging.id}/", {"quantity": 6}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PackagingMovement.objects.filter(packaging=api_packaging, movement_type="adjustment", quantity=2).exists())
        packaging = Packaging.objects.create(packaging_type="basket", name_uz="Test savat", quantity=10, sale_price=100000)
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
        CatalogItem.objects.create(name_uz="Mini katalog", arrangement_type="bouquet", price=250000, status="available")
        init_data = 'user={"id":777,"first_name":"Ali"}'
        payload = {"init_data": init_data, "arrangement_type": "basket", "request_text": "7 ta gortenziya savatga", "name": "Ali", "phone": "901234567", "note": "Bugun kerak"}
        from unittest.mock import patch
        quote = {"lines": [{"type": "custom_text", "request_text": "7 ta gortenziya savatga"}], "packaging": None, "florist_fee": "50000", "estimated_price": "750000", "price_is_estimate": True, "ai_note": "Taxminiy narx"}
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
        response = APIClient().get("/api/mini-app/catalog/", {"init_data": init_data})
        self.assertEqual(response.status_code, 200)
        catalog_data = response.json()
        self.assertEqual(len(catalog_data["orders"]), 1)
        self.assertNotIn("stock", catalog_data)
        self.assertNotIn("packaging", catalog_data)
        self.assertEqual(catalog_data["catalog"][0]["name_uz"], "Mini katalog")

    def test_catalog_create_rejects_short_stock_for_total_quantity(self):
        payload = {
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

    def test_ai_stock_rows_include_image_url(self):
        self.batch.image_url = "https://example.com/freedom.jpg"
        self.batch.save(update_fields=["image_url", "updated_at"])
        rows = ai_stock_rows("Freedom")
        self.assertTrue(rows[0]["has_image"])
        self.assertEqual(rows[0]["image_url"], "https://example.com/freedom.jpg")

    def test_manual_lead_create_customer_and_deducts_stock_when_won(self):
        packaging = Packaging.objects.create(packaging_type="basket", name_uz="Lead savat", quantity=2, sale_price=50000)
        payload = {
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
        item = CatalogItem.objects.create(name_uz="Catalog buket", arrangement_type="bouquet", price=300000, quantity_total=3, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=5)
        deduct_catalog_stock(item, self.user)
        payload = {
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
        item = CatalogItem.objects.create(name_uz="Delete buket", arrangement_type="bouquet", price=300000, quantity_total=3, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=5)
        deduct_catalog_stock(item, self.user)
        payload = {
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
        PagePermission.objects.create(user=self.user, page="audit", can_view=True, can_control=False)
        developer = User.objects.create_user("developer-audit", password="password")
        UserProfile.objects.create(user=developer, role="developer")
        AuditLog.objects.create(user=developer, action="secret", entity_type="AISettings", entity_id="1")
        AuditLog.objects.create(user=self.user, action="visible", entity_type="Lead", entity_id="1")
        response = self.client.get("/api/audit/")
        self.assertEqual(response.status_code, 200)
        actions = [row["action"] for row in response.json()["results"]]
        self.assertIn("visible", actions)
        self.assertNotIn("secret", actions)
        self.assertEqual(response.json()["results"][0]["action_label"], "Visible")

    def test_audit_can_filter_by_user_without_exposing_developer_logs(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="audit", can_view=True, can_control=False)
        operator = User.objects.create_user("operator-audit", password="password", first_name="Operator")
        UserProfile.objects.create(user=operator, role="operator")
        developer = User.objects.create_user("developer-filter-audit", password="password")
        UserProfile.objects.create(user=developer, role="developer")
        AuditLog.objects.create(user=self.user, action="admin_action", entity_type="Lead", entity_id="1")
        AuditLog.objects.create(user=operator, action="operator_action", entity_type="Lead", entity_id="2")
        AuditLog.objects.create(user=developer, action="developer_action", entity_type="AISettings", entity_id="3")
        response = self.client.get(f"/api/audit/?user={operator.id}")
        self.assertEqual(response.status_code, 200)
        actions = [row["action"] for row in response.json()["results"]]
        self.assertEqual(actions, ["operator_action"])
        self.assertEqual(response.json()["results"][0]["actor_name"], "Operator")
        response = self.client.get(f"/api/audit/?user_id={self.user.id}")
        self.assertEqual(response.status_code, 200)
        actions = [row["action"] for row in response.json()["results"]]
        self.assertEqual(actions, ["admin_action"])
        response = self.client.get(f"/api/audit/?user={developer.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_analytics_and_dashboard_include_top_selling_flowers(self):
        item = CatalogItem.objects.create(name_uz="Analytics buket", arrangement_type="bouquet", price=300000, quantity_total=5, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=4)
        customer = Customer.objects.create(name="Analytics", phone="+998901234501", instagram_user_id="analytics")
        lead = Lead.objects.create(customer=customer, status="won", request_uz="Analytics lead", arrangement_type="catalog", estimated_price=600000, source="instagram")
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
        response = self.client.get("/api/accounting/")
        self.assertEqual(response.status_code, 200)
        accounting = response.json()
        self.assertEqual(accounting["summary"]["discounted_sales_count"], 1)
        self.assertEqual(accounting["summary"]["total_quantity"], 1)
        self.assertEqual(accounting["history"][0]["catalog_name"], "Analytics buket")
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["top_selling_flowers"][0]["stems"], 8)
        self.assertEqual(Decimal(str(response.json()["discounted_catalog_amount"])), Decimal("50000.00"))

    def test_lead_move_keeps_kanban_position_between_two_leads(self):
        customer = Customer.objects.create(name="Kanban", phone="+998901234567", instagram_user_id="kanban")
        first = Lead.objects.create(customer=customer, status="new", request_uz="First", sort_order=Decimal("1000"))
        moving = Lead.objects.create(customer=customer, status="new", request_uz="Moving", sort_order=Decimal("2000"))
        last = Lead.objects.create(customer=customer, status="new", request_uz="Last", sort_order=Decimal("3000"))
        response = self.client.post(f"/api/leads/{moving.id}/move/", {"status": "new", "before": first.id, "after": last.id}, format="json")
        self.assertEqual(response.status_code, 200)
        moving.refresh_from_db()
        self.assertEqual(moving.sort_order, Decimal("2000.000000"))
        ids = list(Lead.objects.filter(status="new").order_by("sort_order").values_list("id", flat=True))
        self.assertEqual(ids, [first.id, moving.id, last.id])

    def test_leads_can_be_paginated_by_status_for_kanban_column(self):
        customer = Customer.objects.create(name="Kanban page", phone="+998901234569", instagram_user_id="kanban-page")
        first = Lead.objects.create(customer=customer, status="new", request_uz="First", sort_order=Decimal("1000"))
        second = Lead.objects.create(customer=customer, status="new", request_uz="Second", sort_order=Decimal("2000"))
        Lead.objects.create(customer=customer, status="qualified", request_uz="Other column", sort_order=Decimal("1000"))
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
        customer = Customer.objects.create(name="Kanban full", phone="+998901234568", instagram_user_id="kanban-full")
        first = Lead.objects.create(customer=customer, status="new", request_uz="First", sort_order=Decimal("1000"))
        second = Lead.objects.create(customer=customer, status="new", request_uz="Second", sort_order=Decimal("2000"))
        third = Lead.objects.create(customer=customer, status="new", request_uz="Third", sort_order=Decimal("3000"))
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
