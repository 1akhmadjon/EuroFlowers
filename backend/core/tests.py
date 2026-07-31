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
from django.db.models import F
from django.test import TestCase, override_settings
from django.utils import timezone
import requests
from rest_framework.test import APIClient
from .models import AISettings, AuditLog, BusinessSettings, CatalogComposition, CatalogHistory, CatalogItem, CatalogMaterialUsage, Conversation, Customer, FloristAttendance, FloristProfile, FloristSalaryEntry, FloristVolumeRate, Flower, FlowerVariant, IntegrationSettings, Lead, LeadCatalogUsage, LeadStatus, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockDelivery, Branch, CatalogTransfer, FloristDayOff, FloristStockBalance, FloristStockIssue, StockBatch, StockMovement, Supplier, SupplierPayment, UserProfile
from .serializers import CatalogItemSerializer, ConversationSerializer, FloristProfileSerializer, FloristSalaryEntrySerializer, FloristVolumeRateSerializer, PackagingSerializer, StockBatchSerializer, permission_matrix
from .inventory_services import deduct_catalog_stock, mark_catalog_sold, sync_catalog_financials
from .services import AI_FOLLOW_UP_DELAY_SECONDS, ai_catalog_rows, ai_flower_variant_rows, ai_reply, ai_stock_rows, ai_tool_definitions, calculate_custom_arrangement_price, create_ai_reply_for_conversation, execute_ai_tool, mini_app_custom_quote_ai, mini_app_quote_note, normalize_phone, process_pending_customer_reply, process_stalled_conversation_follow_up, stock_batch_ai_row
from .tasks import process_conversation_follow_up, process_delayed_instagram_reply, process_delayed_telegram_reply
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

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_context_exposes_already_known_lead_fields(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-known", name="Ahmad", phone="+998901112233")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="borib olaman")
        Lead.objects.create(customer=customer, conversation=conversation, request_uz="2 pochka", fulfillment="pickup", desired_date="2026-07-30", desired_time="15:00")
        payload = {"reply": "Yaxshi", "detected_language": "uz", "customer_name": None, "phone": None, "lead_ready": False, "lead_request": None, "arrangement_type": None, "estimated_price": None, "handoff": False, "catalog_items": [], "stock_items": []}
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="r1")
            ai_reply(conversation)
        context = json.loads(client.responses.create.call_args.kwargs["input"][0]["content"].split("REAL_CONTEXT_JSON:\n", 1)[1])
        known = context["conversation"]["already_known"]
        self.assertTrue(known["name"])
        self.assertTrue(known["phone"])
        self.assertEqual(known["fulfillment"], "pickup")
        self.assertTrue(known["desired_date"])
        self.assertTrue(known["desired_time"])
        self.assertEqual(context["conversation"]["open_lead"]["fulfillment"], "pickup")

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_context_marks_returning_customer(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-return", name="Ahmad", phone="+998901112233")
        conversation = Conversation.objects.create(customer=customer)
        Lead.objects.create(customer=customer, request_uz="oldingi buyurtma")
        conversation.messages.create(sender="customer", text="salom")
        payload = {"reply": "Assalomu alaykum, Ahmad", "detected_language": "uz", "customer_name": None, "phone": None, "lead_ready": False, "lead_request": None, "arrangement_type": None, "estimated_price": None, "handoff": False, "catalog_items": [], "stock_items": []}
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="r2")
            ai_reply(conversation)
        context = json.loads(client.responses.create.call_args.kwargs["input"][0]["content"].split("REAL_CONTEXT_JSON:\n", 1)[1])
        self.assertTrue(context["customer"]["is_returning"])
        self.assertEqual(context["customer"]["previous_orders_count"], 1)
        self.assertEqual(context["customer"]["name"], "Ahmad")

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_context_exposes_last_delivery_address(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-addr", name="Ahmad", phone="+998901112233")
        Lead.objects.create(customer=customer, request_uz="oldingi", delivery_address="Xadra 9")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="salom")
        payload = {"reply": "ok", "detected_language": "uz", "customer_name": None, "phone": None, "lead_ready": False, "lead_request": None, "arrangement_type": None, "estimated_price": None, "handoff": False, "catalog_items": [], "stock_items": []}
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="r3")
            ai_reply(conversation)
        context = json.loads(client.responses.create.call_args.kwargs["input"][0]["content"].split("REAL_CONTEXT_JSON:\n", 1)[1])
        self.assertEqual(context["customer"]["last_delivery_address"], "Xadra 9")

    def test_cyrillic_latin_roundtrip(self):
        from .services import cyrillic_to_latin, latin_to_cyrillic, detect_text_script
        self.assertEqual(cyrillic_to_latin("Ассалому Алайкум"), "Assalomu Alaykum")
        self.assertEqual(cyrillic_to_latin("Етказиб бериш керакми"), "Yetkazib berish kerakmi")
        self.assertEqual(cyrillic_to_latin("силада нечпул"), "silada nechpul")
        self.assertEqual(latin_to_cyrillic("Yetkazib berish kerakmi"), "Етказиб бериш керакми")
        self.assertEqual(latin_to_cyrillic("Rahmat, kuningiz xayrli o'tsin"), "Раҳмат, кунингиз хайрли ўтсин")
        self.assertEqual(latin_to_cyrillic("Sizga qanday gul kerak edi?"), "Сизга қандай гул керак эди?")
        self.assertEqual(latin_to_cyrillic("Florist haqi taxminan 50 000 so'm"), "Флорист ҳақи тахминан 50 000 сўм")

    def test_transliteration_protects_links_and_brands(self):
        from .services import latin_to_cyrillic
        self.assertIn("https://yandex.uz/maps/-/CTfQ6TMD", latin_to_cyrillic("Manzil https://yandex.uz/maps/-/CTfQ6TMD"))
        self.assertIn("Next Mall", latin_to_cyrillic("Next Mall dan keyin"))
        self.assertIn("EuroFlowers", latin_to_cyrillic("EuroFlowers Premium gul do'koni"))

    def test_detect_text_script_separates_uzbek_cyrillic_from_russian(self):
        from .services import detect_text_script
        self.assertEqual(detect_text_script("qanaqa gullar bor"), "latin")
        self.assertEqual(detect_text_script("канака гулла бор"), "uz_cyril")
        self.assertEqual(detect_text_script("Ассалому алайкум"), "uz_cyril")
        self.assertEqual(detect_text_script("какие цветы есть"), "ru")
        self.assertEqual(detect_text_script("сколько стоит букет"), "ru")

    @override_settings(OPENAI_API_KEY="test-key")
    def test_cyrillic_customer_gets_cyrillic_reply_from_latin_model_output(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-cyr")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="канака гулла бор")
        payload = {"reply": "Skladimizda hozir quyidagi gullar bor", "detected_language": "uz", "customer_name": None, "phone": None, "lead_ready": False, "lead_request": None, "arrangement_type": None, "estimated_price": None, "handoff": False, "catalog_items": [], "stock_items": []}
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="rc")
            result = ai_reply(conversation)
        sent = client.responses.create.call_args.kwargs["input"][-1]["content"]
        self.assertIn("kanaka gulla bor", sent)
        self.assertEqual(result["reply"], "Складимизда ҳозир қуйидаги гуллар бор")
        self.assertEqual(result["reply_latin"], "Skladimizda hozir quyidagi gullar bor")

    @override_settings(OPENAI_API_KEY="test-key")
    def test_russian_customer_reply_is_not_transliterated(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-ru2")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="какие цветы есть")
        payload = {"reply": "На складе сейчас есть розы", "detected_language": "ru", "customer_name": None, "phone": None, "lead_ready": False, "lead_request": None, "arrangement_type": None, "estimated_price": None, "handoff": False, "catalog_items": [], "stock_items": []}
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="rr")
            result = ai_reply(conversation)
        sent = client.responses.create.call_args.kwargs["input"][-1]["content"]
        self.assertIn("какие цветы есть", sent)
        self.assertEqual(result["reply"], "На складе сейчас есть розы")

    def test_stock_row_exposes_pochka_fields(self):
        row = stock_batch_ai_row(self.batch)
        self.assertEqual(row["stems_per_pochka"], self.batch.stems_per_bunch)
        self.assertEqual(row["price_per_pochka"], str(self.batch.sale_price_per_bunch))
        self.assertEqual(row["price_per_stem"], str(self.batch.sale_price_per_stem))

    def test_stock_search_does_not_match_term_inside_another_word(self):
        from .services import haystack_has_term
        self.assertFalse(haystack_has_term("atirgul jumila podgallan pushti", "all"))
        self.assertTrue(haystack_has_term("atirgul jumila podgallan pushti", "jumila"))
        self.assertTrue(haystack_has_term("atirgul prut oq", "oq"))
        flower = Flower.objects.create(name_uz="Atirgul2", slug="rose2")
        variant = FlowerVariant.objects.create(flower=flower, name_uz="podgallan", color_uz="Pushti")
        StockBatch.objects.create(variant=variant, batch_number="T-2", height_cm=50, stems_per_bunch=25, received_stems=100, remaining_stems=100, cost_per_stem=10000, sale_price_per_stem=15000, sale_price_per_bunch=375000)
        self.assertEqual(len(ai_stock_rows("", limit=50)), 2)

    def test_lead_tool_refuses_without_name_or_phone(self):
        customer = Customer.objects.create(instagram_user_id="ig-lead-guard")
        conversation = Conversation.objects.create(customer=customer)
        payload = {"customer_name": None, "phone": None, "request_text": "50 ta atirgul buket", "arrangement_type": "bouquet", "estimated_price": 800000, "florist_fee": 50000, "fulfillment": None, "delivery_address": None, "desired_date": None, "desired_time": None, "catalog_items": [], "stock_items": [], "note": None}
        self.assertEqual(execute_ai_tool("client_lead_create", payload, conversation)["detail"], "customer_name_required")
        payload["customer_name"] = "Ahmad"
        self.assertEqual(execute_ai_tool("client_lead_create", payload, conversation)["detail"], "phone_required")
        self.assertFalse(Lead.objects.filter(customer=customer).exists())

    def test_lead_tool_persists_fulfillment_address_and_date(self):
        customer = Customer.objects.create(instagram_user_id="ig-lead-full")
        conversation = Conversation.objects.create(customer=customer)
        payload = {"customer_name": "Ahmad", "phone": "901112233", "request_text": "50 ta Atirgul Mondial oq buket", "arrangement_type": "bouquet", "estimated_price": 800000, "florist_fee": 50000, "fulfillment": "delivery", "delivery_address": "Xadra 9", "desired_date": "2026-07-30", "desired_time": "15:00", "catalog_items": [], "stock_items": [{"batch_id": self.batch.id, "quantity_stems": 50, "quantity_bunches": 0}], "note": None}
        result = execute_ai_tool("client_lead_create", payload, conversation)
        self.assertTrue(result["ok"])
        lead = Lead.objects.get(id=result["lead_id"])
        self.assertEqual(lead.fulfillment, "delivery")
        self.assertEqual(lead.delivery_address, "Xadra 9")
        self.assertEqual(lead.desired_date.isoformat(), "2026-07-30")
        self.assertEqual(lead.desired_time, "15:00")
        self.assertEqual(lead.florist_fee, Decimal("50000"))
        self.assertNotIn("custom", lead.request_uz.lower())
        edited = execute_ai_tool("client_lead_edit", {"lead_id": lead.id, "customer_name": None, "phone": None, "request_text": None, "status": None, "arrangement_type": None, "estimated_price": None, "florist_fee": None, "fulfillment": "pickup", "delivery_address": None, "desired_date": None, "desired_time": None, "catalog_items": None, "stock_items": None, "note": None}, conversation)
        self.assertTrue(edited["ok"])
        lead.refresh_from_db()
        self.assertEqual(lead.fulfillment, "pickup")

    def test_stock_image_tool_reports_failure_instead_of_claiming_sent(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-image-fail")
        conversation = Conversation.objects.create(customer=customer)
        self.batch.image_url = "https://example.com/rose.jpg"
        self.batch.save(update_fields=["image_url", "updated_at"])
        with patch("core.services.instagram_send_image", side_effect=requests.HTTPError("400 Bad Request")):
            result = execute_ai_tool("send_stock_image", {"query": "Mondial", "batch_id": self.batch.id}, conversation)
        self.assertFalse(result["ok"])
        self.assertFalse(result["image_sent"])
        self.assertEqual(result["detail"], "send_failed")

    def test_stock_image_tool_confirms_delivery_on_success(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-image-ok")
        conversation = Conversation.objects.create(customer=customer)
        self.batch.image_url = "https://example.com/rose.jpg"
        self.batch.save(update_fields=["image_url", "updated_at"])
        with patch("core.services.instagram_send_image", return_value={"message_id": "mid-1"}):
            result = execute_ai_tool("send_stock_image", {"query": "Mondial", "batch_id": self.batch.id}, conversation)
        self.assertTrue(result["ok"])
        self.assertTrue(result["image_sent"])

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_context_uses_business_settings_not_hardcoded_values(self):
        from unittest.mock import patch
        settings_row, _ = BusinessSettings.objects.get_or_create(pk=1)
        settings_row.working_hours = {"uz": "Har kuni 09:00-22:00", "ru": "Ежедневно 09:00-22:00"}
        settings_row.shop_phone = "+998 90 000 00 00"
        settings_row.save()
        customer = Customer.objects.create(instagram_user_id="ig-ctx")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="ish vaqti qanaqa")
        payload = {"reply": "Har kuni 09:00 dan 22:00 gacha", "detected_language": "uz", "customer_name": None, "phone": None, "lead_ready": False, "lead_request": None, "arrangement_type": None, "estimated_price": None, "handoff": False, "catalog_items": [], "stock_items": []}
        with patch("core.services.OpenAI") as openai_class:
            client = openai_class.return_value
            client.responses.create.return_value = SimpleNamespace(output_text=json.dumps(payload), output=[], id="resp_ctx")
            ai_reply(conversation)
        context = client.responses.create.call_args.kwargs["input"][0]["content"]
        self.assertIn("Har kuni 09:00-22:00", context)
        self.assertIn("+998 90 000 00 00", context)
        self.assertNotIn("24/7", context)

    @override_settings(OPENAI_API_KEY="test-key")
    def test_ai_reply_is_stored_without_post_processing(self):
        from unittest.mock import patch
        customer = Customer.objects.create(instagram_user_id="ig-nopostproc")
        conversation = Conversation.objects.create(customer=customer)
        conversation.messages.create(sender="customer", text="manzil qayerda")
        original = "Manzilimiz Bobur ko'chasi 10\nNext Mall dan keyin o'ng qo'lda"
        with patch("core.services.ai_reply", return_value={"reply": original, "detected_language": "uz", "customer_name": None, "phone": None, "lead_ready": False, "lead_request": None, "arrangement_type": None, "estimated_price": None, "handoff": False, "catalog_items": [], "stock_items": []}):
            reply = create_ai_reply_for_conversation(conversation)
        self.assertEqual(reply.text, original)

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
        # Tarif aniq floristga biriktiriladi, umumiy tarif endi ishlatilmaydi
        FloristVolumeRate.objects.create(florist=florist, arrangement_type="bouquet", volume="small", default_stems=10, florist_fee=70000)
        # Florist tanlangan katalog gulni floristning qo'lidagi qoldiqdan oladi
        from .inventory_services import issue_stock_to_florist
        issue_stock_to_florist(florist, self.batch, 10, "test", self.user)
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
        from .inventory_services import issue_stock_to_florist
        issue_stock_to_florist(florist, self.batch, 12, "test", self.user)
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
        self.assertIn("working_hours_uz", kwargs["input"][0]["content"])
        self.assertIn("qanaqa gullar bor", kwargs["input"][1]["content"])
        self.assertIn("105000.00", kwargs["input"][-1]["content"])

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

    def _florist_with_history(self):
        user = User.objects.create_user("florist-stats", password="password", first_name="Dilnoza", last_name="F")
        profile = FloristProfile.objects.create(user=user, staff_type="florist", phone="+998901112233")
        bouquet = CatalogItem.objects.create(name_uz="Katta buket", arrangement_type="bouquet", volume="Katta", catalog_kind="standard", price=Decimal("900000"), quantity_total=1, status="available", florist=profile, florist_fee=Decimal("60000"))
        basket = CatalogItem.objects.create(name_uz="Mini savat", arrangement_type="basket", volume="Kichik", catalog_kind="custom", price=Decimal("400000"), quantity_total=1, status="available", florist=profile, florist_fee=Decimal("30000"))
        CatalogComposition.objects.create(catalog_item=bouquet, stock_batch=self.batch, quantity_stems=20)
        CatalogComposition.objects.create(catalog_item=basket, stock_batch=self.batch, quantity_stems=10)
        FloristSalaryEntry.objects.create(florist=profile, amount=Decimal("60000"), source="catalog", work_date="2026-07-20", catalog_item=bouquet)
        FloristSalaryEntry.objects.create(florist=profile, amount=Decimal("30000"), source="custom_catalog", work_date="2026-07-21", catalog_item=basket)
        FloristSalaryEntry.objects.create(florist=profile, amount=Decimal("15000"), source="manual", work_date="2026-07-21", note="Qo‘shimcha")
        mark_catalog_sold(bouquet, self.user)
        FloristAttendance.objects.create(florist=profile, work_date="2026-07-20")
        return profile

    def test_catalog_item_can_be_created_without_customer(self):
        response = self.client.post("/api/catalog/", {"name_uz": "Mijozsiz buket", "arrangement_type": "bouquet", "price": "300000", "quantity_total": 1, "status": "available"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data["customer"])
        self.assertIsNone(response.data["customer_detail"])

    def test_catalog_item_can_attach_existing_customer(self):
        customer = Customer.objects.create(name="Ahmad", phone="+998901112233", instagram_user_id="ig-cat-1")
        response = self.client.post("/api/catalog/", {"name_uz": "Ahmad buketi", "arrangement_type": "bouquet", "price": "300000", "quantity_total": 1, "status": "available", "customer": customer.id}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["customer"], customer.id)
        self.assertEqual(response.data["customer_detail"]["name"], "Ahmad")
        self.assertEqual(Customer.objects.count(), 1)

    def test_catalog_item_creates_new_customer_from_name_and_phone(self):
        response = self.client.post("/api/catalog/", {"name_uz": "Yangi mijoz buketi", "arrangement_type": "basket", "catalog_kind": "custom", "price": "450000", "quantity_total": 1, "customer_name": "Dilnoza", "customer_phone": "901119988"}, format="json")
        self.assertEqual(response.status_code, 201)
        customer = Customer.objects.get(name="Dilnoza")
        self.assertEqual(customer.phone, "+998901119988")
        self.assertTrue(customer.instagram_user_id.startswith("manual:"))
        self.assertEqual(response.data["customer_detail"]["phone"], "+998901119988")

    def test_catalog_item_reuses_customer_by_phone(self):
        existing = Customer.objects.create(name="", phone="+998901119988", instagram_user_id="ig-cat-2")
        response = self.client.post("/api/catalog/", {"name_uz": "Takror mijoz", "arrangement_type": "bouquet", "price": "200000", "quantity_total": 1, "status": "available", "customer_name": "Dilnoza", "customer_phone": "+998901119988"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["customer"], existing.id)
        existing.refresh_from_db()
        self.assertEqual(existing.name, "Dilnoza")
        self.assertEqual(Customer.objects.count(), 1)

    def test_catalog_item_customer_can_be_changed_and_cleared(self):
        first = Customer.objects.create(name="Birinchi", phone="+998901110001", instagram_user_id="ig-cat-3")
        item = CatalogItem.objects.create(name_uz="O‘zgaruvchi", arrangement_type="bouquet", price=Decimal("200000"), quantity_total=1, status="available", customer=first)
        changed = self.client.patch(f"/api/catalog/{item.id}/", {"customer_name": "Ikkinchi", "customer_phone": "901110002"}, format="json")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.data["customer_detail"]["name"], "Ikkinchi")
        cleared = self.client.patch(f"/api/catalog/{item.id}/", {"customer": None}, format="json")
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.data["customer"])

    def test_catalog_can_be_filtered_by_customer(self):
        customer = Customer.objects.create(name="Filtr", phone="+998901110003", instagram_user_id="ig-cat-4")
        CatalogItem.objects.create(name_uz="Filtr buketi", arrangement_type="bouquet", price=Decimal("200000"), quantity_total=1, status="available", customer=customer)
        CatalogItem.objects.create(name_uz="Boshqa", arrangement_type="bouquet", price=Decimal("200000"), quantity_total=1, status="available")
        response = self.client.get(f"/api/catalog/?customer={customer.id}")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name_uz"], "Filtr buketi")

    def test_florist_stats_endpoint_returns_full_breakdown(self):
        profile = self._florist_with_history()
        response = self.client.get(f"/api/florists/{profile.id}/stats/")
        self.assertEqual(response.status_code, 200)
        data = response.data
        summary = data["summary"]
        self.assertEqual(Decimal(summary["salary_total"]), Decimal("105000.00"))
        self.assertEqual(Decimal(summary["catalog_salary_total"]), Decimal("90000.00"))
        self.assertEqual(Decimal(summary["manual_salary_total"]), Decimal("15000.00"))
        self.assertEqual(summary["catalog_count"], 2)
        self.assertEqual(summary["bouquet_count"], 1)
        self.assertEqual(summary["basket_count"], 1)
        self.assertEqual(summary["standard_count"], 1)
        self.assertEqual(summary["custom_count"], 1)
        self.assertEqual(summary["attendance_days"], 1)
        self.assertEqual(summary["sold_quantity"], 1)
        self.assertEqual(Decimal(summary["sale_revenue"]), Decimal("900000.00"))
        self.assertEqual(Decimal(summary["avg_fee_per_item"]), Decimal("45000.00"))
        self.assertEqual({row["arrangement_type"] for row in data["by_arrangement"]}, {"bouquet", "basket"})
        self.assertEqual({(row["arrangement_type"], row["volume"]) for row in data["by_volume"]}, {("bouquet", "Katta"), ("basket", "Kichik")})
        self.assertEqual({row["source"] for row in data["by_source"]}, {"catalog", "custom_catalog", "manual"})
        self.assertEqual(len(data["by_day"]), 2)
        sold_row = next(row for row in data["salary_entries"] if row["catalog_name"] == "Katta buket")
        self.assertEqual(sold_row["arrangement_label"], "Buket")
        self.assertEqual(sold_row["volume"], "Katta")
        self.assertEqual(Decimal(sold_row["amount"]), Decimal("60000.00"))
        self.assertEqual(Decimal(sold_row["sale_revenue"]), Decimal("900000.00"))
        self.assertTrue(sold_row["is_sold"])
        unsold_row = next(row for row in data["salary_entries"] if row["catalog_name"] == "Mini savat")
        self.assertFalse(unsold_row["is_sold"])
        self.assertEqual(Decimal(unsold_row["sale_revenue"]), Decimal("0"))

    def test_florist_stats_respects_date_range(self):
        profile = self._florist_with_history()
        response = self.client.get(f"/api/florists/{profile.id}/stats/?date_from=2026-07-21&date_to=2026-07-21")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["salary_entries_count"], 2)
        self.assertEqual(Decimal(response.data["summary"]["salary_total"]), Decimal("45000.00"))
        self.assertEqual(response.data["period"]["date_from"], "2026-07-21")

    def test_florist_me_dashboard_returns_own_stats(self):
        profile = self._florist_with_history()
        PagePermission.objects.create(user=profile.user, page="florists", can_view=True, can_control=False)
        UserProfile.objects.update_or_create(user=profile.user, defaults={"role": "florist"})
        client = APIClient()
        client.force_authenticate(profile.user)
        response = client.get("/api/florists/me/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["florist"]["id"], profile.id)
        self.assertEqual(Decimal(response.data["summary"]["salary_total"]), Decimal("105000.00"))
        self.assertTrue(response.data["salary_entries"])

    def test_florist_excel_export_has_all_sheets(self):
        import io
        from openpyxl import load_workbook
        profile = self._florist_with_history()
        response = self.client.get(f"/api/exports/florist/?florist={profile.id}&date_from=2026-07-01&date_to=2026-07-31")
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(io.BytesIO(response.content))
        self.assertEqual(workbook.sheetnames, ["Ish haqi tarixi", "Umumiy", "Kunlar bo‘yicha", "Hajm bo‘yicha", "Manba bo‘yicha", "Keldi-ketdi"])
        header = [cell.value for cell in workbook["Ish haqi tarixi"][1]]
        self.assertIn("Sotuvdan tushgan", header)
        self.assertIn("Floristga qo‘shilgan", header)
        self.assertIn("Buket yoki savat", header)

    def test_supplier_with_payments_is_archived_not_deleted(self):
        supplier = Supplier.objects.create(name="To‘lovli postavshik")
        SupplierPayment.objects.create(supplier=supplier, amount=Decimal("100000"), paid_at="2026-07-29", method="cash")
        SupplierPayment.objects.create(supplier=supplier, amount=Decimal("50000"), paid_at="2026-07-30", method="card")
        response = self.client.delete(f"/api/suppliers/{supplier.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["archived"])
        self.assertFalse(response.data["deleted"])
        self.assertFalse(response.data["is_active"])
        self.assertEqual(response.data["id"], supplier.id)
        self.assertEqual(response.data["blocked_by"], [{"model": "supplierpayment", "label": "To‘lovlar", "count": 2}])
        self.assertIn("To‘lovlar (2 ta)", response.data["detail"])
        supplier.refresh_from_db()
        self.assertFalse(supplier.is_active)
        self.assertTrue(AuditLog.objects.filter(action="supplier_archived").exists())

    def test_supplier_without_relations_is_deleted_with_204(self):
        supplier = Supplier.objects.create(name="Bo‘sh postavshik")
        response = self.client.delete(f"/api/suppliers/{supplier.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Supplier.objects.filter(id=supplier.id).exists())
        self.assertTrue(AuditLog.objects.filter(action="supplier_deleted").exists())

    def test_supplier_with_only_batches_is_deleted_and_batches_lose_supplier(self):
        """StockBatch.supplier SET_NULL, shuning uchun faqat partiyasi bor postavshik
        o'chib ketadi va partiyalar egasiz qoladi. Hozirgi xatti-harakat shunday."""
        supplier = Supplier.objects.create(name="Partiyali postavshik")
        batch = StockBatch.objects.create(variant=self.batch.variant, supplier=supplier, batch_number="ARCH-1", height_cm=50, stems_per_bunch=25, received_stems=10, remaining_stems=10, cost_per_stem=1000, sale_price_per_stem=2000, sale_price_per_bunch=50000)
        response = self.client.delete(f"/api/suppliers/{supplier.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Supplier.objects.filter(id=supplier.id).exists())
        batch.refresh_from_db()
        self.assertIsNone(batch.supplier_id)

    def test_supplier_with_payment_and_batches_is_archived_and_keeps_batches(self):
        supplier = Supplier.objects.create(name="To‘lovli va partiyali")
        batch = StockBatch.objects.create(variant=self.batch.variant, supplier=supplier, batch_number="ARCH-2", height_cm=50, stems_per_bunch=25, received_stems=10, remaining_stems=10, cost_per_stem=1000, sale_price_per_stem=2000, sale_price_per_bunch=50000)
        SupplierPayment.objects.create(supplier=supplier, amount=Decimal("100000"), paid_at="2026-07-29", method="cash")
        response = self.client.delete(f"/api/suppliers/{supplier.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["archived"])
        batch.refresh_from_db()
        self.assertEqual(batch.supplier_id, supplier.id)

    def _sell_catalog(self, price="6700000", quantity=1, sold_at=None):
        item = CatalogItem.objects.create(name_uz="Sotuv buketi", arrangement_type="bouquet", price=Decimal(price), quantity_total=quantity, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=5)
        payload = {"quantity": quantity}
        if sold_at:
            payload["sold_at"] = sold_at
        response = self.client.post(f"/api/catalog/{item.id}/sell/", payload, format="json")
        self.assertEqual(response.status_code, 200)
        return item

    def test_dashboard_revenue_includes_catalog_sales(self):
        self._sell_catalog()
        response = self.client.get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(Decimal(data["revenue_today"]), Decimal("6700000.00"))
        self.assertEqual(Decimal(data["catalog_sales_revenue_today"]), Decimal("6700000.00"))
        self.assertEqual(Decimal(data["lead_revenue_today"]), Decimal("0"))
        self.assertEqual(data["catalog_sales_orders_today"], 1)
        self.assertEqual(data["orders_today"], 1)

    def test_dashboard_revenue_matches_accounting_total_sales(self):
        self._sell_catalog()
        dashboard = self.client.get("/api/dashboard/").data
        accounting = self.client.get("/api/accounting/").data
        self.assertEqual(Decimal(dashboard["catalog_sales_revenue_today"]), Decimal(accounting["summary"]["total_sales"]))

    def test_analytics_summary_and_daily_stats_include_catalog(self):
        self._sell_catalog()
        response = self.client.get("/api/analytics/")
        self.assertEqual(response.status_code, 200)
        summary = response.data["summary"]
        self.assertEqual(Decimal(summary["revenue"]), Decimal("6700000.00"))
        self.assertEqual(Decimal(summary["catalog_sales_revenue"]), Decimal("6700000.00"))
        self.assertEqual(Decimal(summary["lead_revenue"]), Decimal("0"))
        self.assertEqual(summary["catalog_sales_orders"], 1)
        today = timezone.localdate().isoformat()
        row = next(day for day in response.data["daily_stats"] if day["date"] == today)
        self.assertEqual(Decimal(row["catalog_revenue"]), Decimal("6700000.00"))
        self.assertEqual(row["catalog_orders"], 1)
        self.assertEqual(row["catalog_quantity"], 1)
        self.assertEqual(Decimal(row["revenue"]), Decimal("6700000.00"))

    def test_dashboard_daily_stats_include_catalog_series(self):
        self._sell_catalog()
        response = self.client.get("/api/dashboard/")
        today = timezone.localdate().isoformat()
        row = next(day for day in response.data["daily_stats"] if day["date"] == today)
        self.assertEqual(Decimal(row["catalog_revenue"]), Decimal("6700000.00"))
        self.assertEqual(row["catalog_orders"], 1)

    def test_top_catalog_items_and_revenue_by_source_use_catalog_sales(self):
        item = self._sell_catalog()
        response = self.client.get("/api/analytics/")
        top = response.data["top_catalog_items"]
        self.assertTrue(top)
        self.assertEqual(top[0]["catalog_item_id"], item.id)
        self.assertEqual(Decimal(top[0]["revenue"]), Decimal("6700000.00"))
        self.assertTrue(response.data["recent_top_catalog_items"])
        sources = {row["source"]: row for row in response.data["revenue_by_source"]}
        self.assertIn("catalog", sources)
        self.assertEqual(Decimal(sources["catalog"]["revenue"]), Decimal("6700000.00"))
        self.assertEqual(sources["catalog"]["source_label"], "Katalogdan sotuv")

    def test_catalog_sale_accepts_historical_sold_at(self):
        item = self._sell_catalog(sold_at="2026-07-10T12:00:00+05:00")
        item.refresh_from_db()
        self.assertEqual(item.sold_at.isoformat()[:10], "2026-07-10")
        history = CatalogHistory.objects.get(catalog_item=item, action="sold")
        self.assertEqual(timezone.localtime(history.created_at).date().isoformat(), "2026-07-10")
        response = self.client.get("/api/analytics/?from=2026-07-01&to=2026-07-31")
        row = next(day for day in response.data["daily_stats"] if day["date"] == "2026-07-10")
        self.assertEqual(Decimal(row["catalog_revenue"]), Decimal("6700000.00"))

    def test_stock_movement_accepts_historical_created_at(self):
        response = self.client.post(f"/api/stock-batches/{self.batch.id}/movement/", {"movement_type": "waste", "quantity_stems": 3, "reason": "test", "created_at": "2026-07-05T10:00:00+05:00"}, format="json")
        self.assertEqual(response.status_code, 200)
        movement = StockMovement.objects.get(id=response.data["id"])
        self.assertEqual(timezone.localtime(movement.created_at).date().isoformat(), "2026-07-05")

    def test_lead_accepts_historical_created_at(self):
        response = self.client.post("/api/leads/", {"customer_name": "Tarixiy", "customer_phone": "901110009", "request_uz": "Test", "created_at": "2026-07-03T09:00:00+05:00"}, format="json")
        self.assertEqual(response.status_code, 201)
        lead = Lead.objects.get(id=response.data["id"])
        self.assertEqual(timezone.localtime(lead.created_at).date().isoformat(), "2026-07-03")

    def test_packaging_movement_accepts_historical_created_at(self):
        packaging = Packaging.objects.create(packaging_type="wrap", name_uz="Test qog‘oz", cost_price=Decimal("1000"), sale_price=Decimal("2000"), quantity=50)
        response = self.client.post(f"/api/materials/{packaging.id}/movement/", {"movement_type": "in", "quantity": 10, "created_at": "2026-07-04T08:00:00+05:00"}, format="json")
        self.assertEqual(response.status_code, 200)
        movement = PackagingMovement.objects.get(id=response.data["id"])
        self.assertEqual(timezone.localtime(movement.created_at).date().isoformat(), "2026-07-04")

    def _florist(self, username="fl-stock"):
        user = User.objects.create_user(username, password="p", first_name="Stock")
        return FloristProfile.objects.create(user=user, staff_type="florist")

    def test_issue_stock_to_florist_moves_balance(self):
        florist = self._florist()
        before = self.batch.remaining_stems
        response = self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 30, "reason": "Ish uchun"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["kind_label"], "Chiqarildi")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, before - 30)
        balance = FloristStockBalance.objects.get(florist=florist, batch=self.batch)
        self.assertEqual(balance.remaining_stems, 30)
        self.assertTrue(StockMovement.objects.filter(reference_type="florist_issue").exists())

    def test_issue_rejects_more_than_stock(self):
        florist = self._florist()
        response = self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 99999}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("qolgan", response.data["detail"])

    def test_florist_can_return_unused_stock(self):
        florist = self._florist()
        self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 30}, format="json")
        self.batch.refresh_from_db()
        before = self.batch.remaining_stems
        response = self.client.post("/api/florist-stock-issues/return/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 10, "kind": "return"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, before + 10)
        self.assertEqual(FloristStockBalance.objects.get(florist=florist, batch=self.batch).remaining_stems, 20)

    def test_florist_waste_does_not_return_to_stock(self):
        florist = self._florist()
        self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 30}, format="json")
        self.batch.refresh_from_db()
        before = self.batch.remaining_stems
        response = self.client.post("/api/florist-stock-issues/return/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 5, "kind": "waste", "reason": "so‘ldi"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, before)
        self.assertEqual(FloristStockBalance.objects.get(florist=florist, batch=self.batch).remaining_stems, 25)

    def test_return_rejects_more_than_florist_has(self):
        florist = self._florist()
        self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 10}, format="json")
        response = self.client.post("/api/florist-stock-issues/return/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 50}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_catalog_with_florist_consumes_florist_stock_not_warehouse(self):
        florist = self._florist()
        self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 30}, format="json")
        self.batch.refresh_from_db()
        stock_before = self.batch.remaining_stems
        response = self.client.post("/api/catalog/", {"name_uz": "Florist buketi", "arrangement_type": "bouquet", "price": "300000", "quantity_total": 1, "status": "available", "florist": florist.id, "composition": [{"stock_batch": self.batch.id, "quantity_stems": 20}]}, format="json")
        self.assertEqual(response.status_code, 201)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, stock_before)
        self.assertEqual(FloristStockBalance.objects.get(florist=florist, batch=self.batch).remaining_stems, 10)

    def test_catalog_with_florist_rejects_when_florist_lacks_stock(self):
        florist = self._florist()
        self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 5}, format="json")
        response = self.client.post("/api/catalog/", {"name_uz": "Yetmaydi", "arrangement_type": "bouquet", "price": "300000", "quantity_total": 1, "status": "available", "florist": florist.id, "composition": [{"stock_batch": self.batch.id, "quantity_stems": 20}]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_catalog_without_florist_still_uses_warehouse(self):
        before = self.batch.remaining_stems
        response = self.client.post("/api/catalog/", {"name_uz": "Skladdan", "arrangement_type": "bouquet", "price": "300000", "quantity_total": 1, "status": "available", "composition": [{"stock_batch": self.batch.id, "quantity_stems": 10}]}, format="json")
        self.assertEqual(response.status_code, 201)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, before - 10)

    def test_florist_stock_balance_list_filters_by_florist(self):
        florist = self._florist()
        self.client.post("/api/florist-stock-issues/issue/", {"florist": florist.id, "batch": self.batch.id, "quantity_stems": 12}, format="json")
        response = self.client.get(f"/api/florist-stock-balances/?florist={florist.id}")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["remaining_stems"], 12)
        self.assertEqual(response.data["results"][0]["batch_detail"]["batch_number"], self.batch.batch_number)

    def test_florist_day_off_crud(self):
        florist = self._florist()
        response = self.client.post("/api/florist-days-off/", {"florist": florist.id, "work_date": "2026-08-02", "kind": "weekend", "note": "Yakshanba"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["kind_label"], "Dam kuni")
        listed = self.client.get(f"/api/florist-days-off/?florist={florist.id}")
        self.assertEqual(listed.data["count"], 1)
        duplicate = self.client.post("/api/florist-days-off/", {"florist": florist.id, "work_date": "2026-08-02", "kind": "sick"}, format="json")
        self.assertEqual(duplicate.status_code, 400)

    def _parkent(self):
        return Branch.objects.get(name="Parkent filiali")

    def _main_catalog(self, quantity=5, price="300000"):
        item = CatalogItem.objects.create(name_uz="Filial buketi", arrangement_type="bouquet", price=Decimal(price), quantity_total=quantity, quantity_stock_deducted=quantity, status="available")
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=4)
        sync_catalog_financials(item)
        return item

    def test_transfer_moves_part_of_catalog_to_branch(self):
        item = self._main_catalog(quantity=5, price="300000")
        parkent = self._parkent()
        response = self.client.post(f"/api/catalog/{item.id}/transfer/", {"branch": parkent.id, "quantity": 2, "price": "450000", "note": "Parkentga"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["quantity"], 2)
        self.assertEqual(Decimal(response.data["source_price"]), Decimal("300000.00"))
        self.assertEqual(Decimal(response.data["target_price"]), Decimal("450000.00"))
        item.refresh_from_db()
        self.assertEqual(item.quantity_total, 3)
        target = CatalogItem.objects.get(id=response.data["target_item"])
        self.assertEqual(target.branch_id, parkent.id)
        self.assertEqual(target.quantity_total, 2)
        self.assertEqual(target.price, Decimal("450000.00"))
        self.assertEqual(target.source_price, Decimal("300000.00"))
        self.assertEqual(target.source_item_id, item.id)

    def test_transfer_does_not_touch_warehouse_stock(self):
        item = self._main_catalog()
        self.batch.refresh_from_db()
        before = self.batch.remaining_stems
        self.client.post(f"/api/catalog/{item.id}/transfer/", {"branch": self._parkent().id, "quantity": 2}, format="json")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, before)

    def test_transfer_rejects_more_than_available(self):
        item = self._main_catalog(quantity=2)
        response = self.client.post(f"/api/catalog/{item.id}/transfer/", {"branch": self._parkent().id, "quantity": 5}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("atigi 2 dona", response.data["detail"])

    def test_branch_catalogs_do_not_mix(self):
        item = self._main_catalog()
        parkent = self._parkent()
        self.client.post(f"/api/catalog/{item.id}/transfer/", {"branch": parkent.id, "quantity": 2}, format="json")
        # asosiy filial admini faqat asosiy katalogni ko'radi
        main_list = self.client.get("/api/catalog/")
        names = {row["id"] for row in main_list.data["results"]}
        self.assertIn(item.id, names)
        self.assertEqual(len(names), 1)
        # filial foydalanuvchisi faqat o'z filialini ko'radi
        user = User.objects.create_user("parkent-user", password="p")
        UserProfile.objects.create(user=user, role="operator", branch=parkent)
        PagePermission.objects.create(user=user, page="catalog", can_view=True, can_control=True)
        client = APIClient()
        client.force_authenticate(user)
        branch_list = client.get("/api/catalog/")
        self.assertEqual(branch_list.data["count"], 1)
        self.assertEqual(branch_list.data["results"][0]["branch_name"], "Parkent filiali")

    def test_branch_user_cannot_create_catalog(self):
        parkent = self._parkent()
        user = User.objects.create_user("parkent-creator", password="p")
        UserProfile.objects.create(user=user, role="operator", branch=parkent)
        PagePermission.objects.create(user=user, page="catalog", can_view=True, can_control=True)
        client = APIClient()
        client.force_authenticate(user)
        response = client.post("/api/catalog/", {"name_uz": "Yangi", "arrangement_type": "bouquet", "price": "100000", "quantity_total": 1}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_branch_can_change_price_then_sell_with_discount(self):
        item = self._main_catalog(quantity=3, price="300000")
        parkent = self._parkent()
        transfer = self.client.post(f"/api/catalog/{item.id}/transfer/", {"branch": parkent.id, "quantity": 2, "price": "450000"}, format="json")
        target_id = transfer.data["target_item"]
        user = User.objects.create_user("parkent-seller", password="p")
        UserProfile.objects.create(user=user, role="operator", branch=parkent)
        PagePermission.objects.create(user=user, page="catalog", can_view=True, can_control=True)
        client = APIClient()
        client.force_authenticate(user)
        # narxni yana o'zgartiradi
        changed = client.patch(f"/api/catalog/{target_id}/", {"price": "500000"}, format="json")
        self.assertEqual(changed.status_code, 200)
        # keyin chegirma bilan sotadi
        sold = client.post(f"/api/catalog/{target_id}/sell/", {"quantity": 1, "sale_price": "420000", "discount_reason": "Doimiy mijoz"}, format="json")
        self.assertEqual(sold.status_code, 200)
        history = CatalogHistory.objects.get(catalog_item_id=target_id, action="sold")
        self.assertEqual(history.listed_unit_price, Decimal("500000.00"))
        self.assertEqual(history.sold_unit_price, Decimal("420000.00"))
        self.assertEqual(history.discount_amount, Decimal("80000.00"))
        self.assertEqual(history.discount_reason, "Doimiy mijoz")

    def test_branch_report_counts_transfers_sales_and_discounts(self):
        item = self._main_catalog(quantity=5, price="300000")
        parkent = self._parkent()
        transfer = self.client.post(f"/api/catalog/{item.id}/transfer/", {"branch": parkent.id, "quantity": 3, "price": "450000"}, format="json")
        target = CatalogItem.objects.get(id=transfer.data["target_item"])
        mark_catalog_sold(target, self.user, quantity=1)
        mark_catalog_sold(target, self.user, quantity=1, sale_price=Decimal("400000"), discount_reason="Aksiya")
        response = self.client.get("/api/branch-report/")
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.data["branches"] if r["branch_name"] == "Parkent filiali")
        self.assertEqual(row["received_quantity"], 3)
        self.assertEqual(row["sold_quantity"], 2)
        self.assertEqual(Decimal(row["sold_revenue"]), Decimal("850000.00"))
        self.assertEqual(Decimal(row["source_value"]), Decimal("600000.00"))
        self.assertEqual(Decimal(row["markup_total"]), Decimal("250000.00"))
        self.assertEqual(row["discounted_sales_count"], 1)
        self.assertEqual(row["discounted_quantity"], 1)
        self.assertEqual(Decimal(row["discount_total"]), Decimal("50000.00"))

    def _sold_in_both_branches(self):
        """Asosiy filialda 1 ta, Parkentda 1 ta sotuv qoldiradi."""
        item = self._main_catalog(quantity=4, price="300000")
        parkent = self._parkent()
        transfer = self.client.post(f"/api/catalog/{item.id}/transfer/", {"branch": parkent.id, "quantity": 2, "price": "500000"}, format="json")
        target = CatalogItem.objects.get(id=transfer.data["target_item"])
        mark_catalog_sold(item, self.user, quantity=1, payment_type="cash")
        mark_catalog_sold(target, self.user, quantity=1, payment_type="card")
        return item, target, parkent

    def _branch_row(self, response, name):
        return next(row for row in response.data["by_branch"] if row["branch_name"] == name)

    def test_accounting_total_includes_branch_sales(self):
        # umumiy yig'indi ikkala filialni qamraydi
        self._sold_in_both_branches()
        response = self.client.get("/api/accounting/")
        self.assertEqual(Decimal(response.data["summary"]["total_sales"]), Decimal("800000.00"))
        self.assertEqual(response.data["summary"]["sales_count"], 2)
        self.assertEqual(response.data["summary"]["total_quantity"], 2)

    def test_accounting_splits_total_by_branch(self):
        # umumiyga qo'shilsa ham qaysi filialdan qanchaligi aniq ko'rinadi
        self._sold_in_both_branches()
        response = self.client.get("/api/accounting/")
        main = self._branch_row(response, "Toshkent (asosiy filial)")
        parkent = self._branch_row(response, "Parkent filiali")
        self.assertEqual(Decimal(main["total_sales"]), Decimal("300000.00"))
        self.assertEqual(Decimal(parkent["total_sales"]), Decimal("500000.00"))
        self.assertEqual(Decimal(main["share_percent"]), Decimal("37.50"))
        self.assertEqual(Decimal(parkent["share_percent"]), Decimal("62.50"))
        self.assertTrue(main["is_main"])
        self.assertFalse(parkent["is_main"])
        # filial qatorlari yig'indisi umumiyga teng
        self.assertEqual(
            sum(Decimal(row["total_sales"]) for row in response.data["by_branch"]),
            Decimal(response.data["summary"]["total_sales"]),
        )

    def test_accounting_splits_cash_and_card_per_branch(self):
        # naqd va karta har filialda alohida chiqadi
        self._sold_in_both_branches()
        response = self.client.get("/api/accounting/")
        main = self._branch_row(response, "Toshkent (asosiy filial)")
        parkent = self._branch_row(response, "Parkent filiali")
        self.assertEqual(Decimal(main["cash_total"]), Decimal("300000.00"))
        self.assertEqual(main["cash_count"], 1)
        self.assertEqual(Decimal(main["card_total"]), Decimal("0"))
        self.assertEqual(Decimal(parkent["card_total"]), Decimal("500000.00"))
        self.assertEqual(parkent["card_count"], 1)
        self.assertEqual(Decimal(parkent["cash_total"]), Decimal("0"))
        self.assertEqual(Decimal(response.data["summary"]["cash_total"]), Decimal("300000.00"))
        self.assertEqual(Decimal(response.data["summary"]["card_total"]), Decimal("500000.00"))

    def test_accounting_counts_sold_flower_stems_per_branch(self):
        # filialga o'tkazilganda tarkib nusxalanadi, shuning uchun gul donasi u yerda ham sanaladi
        self._sold_in_both_branches()
        response = self.client.get("/api/accounting/")
        main = self._branch_row(response, "Toshkent (asosiy filial)")
        parkent = self._branch_row(response, "Parkent filiali")
        self.assertEqual(main["flower_stems"], 4)
        self.assertEqual(parkent["flower_stems"], 4)
        self.assertEqual(response.data["summary"]["flower_stems"], 8)

    def test_accounting_branch_filter_narrows_report(self):
        _, _, parkent = self._sold_in_both_branches()
        only_main = self.client.get("/api/accounting/?branch=main")
        self.assertEqual(Decimal(only_main.data["summary"]["total_sales"]), Decimal("300000.00"))
        self.assertEqual(only_main.data["branch_filter"]["mode"], "main")
        only_branch = self.client.get(f"/api/accounting/?branch={parkent.id}")
        self.assertEqual(Decimal(only_branch.data["summary"]["total_sales"]), Decimal("500000.00"))
        self.assertEqual(only_branch.data["branch_filter"]["branch_name"], "Parkent filiali")
        self.assertEqual(len(only_branch.data["by_branch"]), 1)

    def test_accounting_history_rows_carry_branch(self):
        self._sold_in_both_branches()
        response = self.client.get("/api/accounting/")
        branches = sorted(row["branch_name"] for row in response.data["history"])
        self.assertEqual(branches, ["Parkent filiali", "Toshkent (asosiy filial)"])

    def test_branch_accounting_and_dashboard_are_separate(self):
        # filial foydalanuvchisi faqat o'z filialini ko'radi va buni kengaytira olmaydi
        _, _, parkent = self._sold_in_both_branches()
        user = User.objects.create_user("parkent-acc", password="p")
        UserProfile.objects.create(user=user, role="operator", branch=parkent)
        for page in ["catalog", "dashboard"]:
            PagePermission.objects.create(user=user, page=page, can_view=True, can_control=True)
        client = APIClient()
        client.force_authenticate(user)
        branch_acc = client.get("/api/accounting/?branch=all")
        self.assertEqual(Decimal(branch_acc.data["summary"]["total_sales"]), Decimal("500000.00"))
        self.assertEqual(len(branch_acc.data["by_branch"]), 1)
        branch_dash = client.get("/api/dashboard/")
        self.assertEqual(Decimal(branch_dash.data["catalog_sales_revenue_today"]), Decimal("500000.00"))

    def test_accounting_waste_stays_with_main_branch(self):
        # chiqit faqat asosiy skladda bo'ladi, filial hisobiga tushmaydi
        self._sold_in_both_branches()
        StockMovement.objects.create(batch=self.batch, movement_type="waste", quantity_stems=-5, quantity_bunches=Decimal("0.5"), reason="Qurib qoldi")
        response = self.client.get("/api/accounting/")
        main = self._branch_row(response, "Toshkent (asosiy filial)")
        parkent = self._branch_row(response, "Parkent filiali")
        self.assertEqual(main["waste_stems"], 5)
        self.assertEqual(parkent["waste_stems"], 0)
        self.assertEqual(response.data["summary"]["waste_stems"], 5)
        branch_only = self.client.get(f"/api/accounting/?branch={self._parkent().id}")
        self.assertEqual(branch_only.data["summary"]["waste_stems"], 0)

    def test_volume_rate_requires_florist(self):
        response = self.client.post("/api/florist-volume-rates/", {"arrangement_type": "bouquet", "volume": "M", "default_stems": 25, "florist_fee": "60000"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("florist", response.data)

    def test_volume_rate_is_per_florist(self):
        user_a = User.objects.create_user("fl-a", password="p")
        user_b = User.objects.create_user("fl-b", password="p")
        a = FloristProfile.objects.create(user=user_a, staff_type="florist")
        b = FloristProfile.objects.create(user=user_b, staff_type="florist")
        for profile, fee, stems in [(a, "60000", 25), (b, "90000", 35)]:
            response = self.client.post("/api/florist-volume-rates/", {"florist": profile.id, "arrangement_type": "bouquet", "volume": "M", "default_stems": stems, "florist_fee": fee}, format="json")
            self.assertEqual(response.status_code, 201)
        item_a = CatalogItem.objects.create(name_uz="A buket", arrangement_type="bouquet", volume="M", florist=a, price=Decimal("500000"), quantity_total=1, status="available")
        item_b = CatalogItem.objects.create(name_uz="B buket", arrangement_type="bouquet", volume="M", florist=b, price=Decimal("500000"), quantity_total=1, status="available")
        from .inventory_services import apply_volume_rate
        self.assertEqual(apply_volume_rate(item_a).florist_salary_amount, Decimal("60000"))
        self.assertEqual(apply_volume_rate(item_b).florist_salary_amount, Decimal("90000"))

    def test_catalog_detail_returns_unit_profit(self):
        item = CatalogItem.objects.create(name_uz="Foyda buket", arrangement_type="bouquet", price=Decimal("300000"), quantity_total=2, status="available", florist_fee=Decimal("20000"))
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=5)
        sync_catalog_financials(item)
        response = self.client.get(f"/api/catalog/{item.id}/")
        self.assertEqual(response.status_code, 200)
        profit = response.data["profit"]
        # 2 dona uchun tannarx: 2*5*10000 + 2*20000 = 140000, bittasiga 70000
        self.assertEqual(Decimal(profit["unit_cost"]), Decimal("70000.00"))
        self.assertEqual(Decimal(profit["unit_price"]), Decimal("300000.00"))
        self.assertEqual(Decimal(profit["unit_profit"]), Decimal("230000.00"))
        self.assertEqual(Decimal(profit["total_potential_profit"]), Decimal("460000.00"))
        self.assertEqual(Decimal(profit["realized_profit"]), Decimal("0.00"))

    def test_florist_own_dashboard_hides_sales_and_profit(self):
        profile = self._florist_with_history()
        UserProfile.objects.update_or_create(user=profile.user, defaults={"role": "florist"})
        PagePermission.objects.create(user=profile.user, page="florists", can_view=True, can_control=False)
        client = APIClient()
        client.force_authenticate(profile.user)
        response = client.get("/api/florists/me/dashboard/")
        self.assertEqual(response.status_code, 200)
        summary = response.data["summary"]
        # Florist faqat nechta yasagani va qancha ish haqi olganini ko'radi
        self.assertIn("salary_total", summary)
        self.assertIn("catalog_count", summary)
        self.assertNotIn("sale_revenue", summary)
        self.assertNotIn("sold_quantity", summary)
        self.assertNotIn("unsold_quantity", summary)
        for row in response.data["salary_entries"]:
            self.assertNotIn("sale_revenue", row)
            self.assertNotIn("listed_price", row)
            self.assertNotIn("is_sold", row)
        for row in response.data["by_volume"]:
            self.assertNotIn("sale_revenue", row)

    def test_admin_florist_stats_still_shows_sales(self):
        profile = self._florist_with_history()
        response = self.client.get(f"/api/florists/{profile.id}/stats/")
        self.assertIn("sale_revenue", response.data["summary"])
        self.assertIn("sold_quantity", response.data["summary"])

    def test_supplier_payment_crud_and_rollups(self):
        supplier = Supplier.objects.create(name="Gul Import")
        variant = self.batch.variant
        StockBatch.objects.create(variant=variant, supplier=supplier, batch_number="SUP-1", height_cm=50, stems_per_bunch=25, received_stems=100, remaining_stems=100, cost_per_stem=Decimal("7000"), sale_price_per_stem=15000, sale_price_per_bunch=375000)
        StockBatch.objects.create(variant=variant, supplier=supplier, batch_number="SUP-2", height_cm=50, stems_per_bunch=25, received_stems=50, remaining_stems=50, cost_per_stem=Decimal("8000"), sale_price_per_stem=16000, sale_price_per_bunch=400000)
        created = self.client.post("/api/supplier-payments/", {"supplier": supplier.id, "amount": "500000.00", "paid_at": "2026-07-29", "method": "cash", "note": "Iyul oyi uchun"}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["method_label"], "Naqd")
        self.assertEqual(created.data["supplier_detail"]["name"], "Gul Import")
        self.client.post("/api/supplier-payments/", {"supplier": supplier.id, "amount": "200000.00", "paid_at": "2026-07-30", "method": "transfer"}, format="json")
        listed = self.client.get(f"/api/supplier-payments/?supplier={supplier.id}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["count"], 2)
        detail = self.client.get(f"/api/suppliers/{supplier.id}/")
        self.assertEqual(detail.status_code, 200)
        # 100*7000 + 50*8000 = 1 100 000
        self.assertEqual(Decimal(detail.data["purchase_total"]), Decimal("1100000.00"))
        self.assertEqual(Decimal(detail.data["paid_total"]), Decimal("700000.00"))
        self.assertEqual(Decimal(detail.data["outstanding"]), Decimal("400000.00"))
        self.assertEqual(str(detail.data["last_payment_at"]), "2026-07-30")

    def test_supplier_without_payments_reports_zero_paid(self):
        supplier = Supplier.objects.create(name="Yangi Postavshik")
        response = self.client.get(f"/api/suppliers/{supplier.id}/")
        self.assertEqual(Decimal(response.data["purchase_total"]), Decimal("0"))
        self.assertEqual(Decimal(response.data["paid_total"]), Decimal("0"))
        self.assertEqual(Decimal(response.data["outstanding"]), Decimal("0"))

    def test_supplier_payment_rejects_non_positive_amount(self):
        supplier = Supplier.objects.create(name="Test Postavshik")
        response = self.client.post("/api/supplier-payments/", {"supplier": supplier.id, "amount": "0", "paid_at": "2026-07-29", "method": "cash"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_accounting_summary_splits_cost_and_reports_waste(self):
        packaging = Packaging.objects.create(packaging_type="wrap", name_uz="Qog‘oz", cost_price=Decimal("5000"), sale_price=Decimal("9000"), quantity=100)
        item = CatalogItem.objects.create(name_uz="Hisob buket", arrangement_type="bouquet", price=Decimal("300000"), quantity_total=1, status="available", florist_fee=Decimal("20000"))
        CatalogComposition.objects.create(catalog_item=item, stock_batch=self.batch, quantity_stems=10)
        CatalogMaterialUsage.objects.create(catalog_item=item, packaging=packaging, quantity=2)
        sync_catalog_financials(item)
        mark_catalog_sold(item, self.user)
        StockMovement.objects.create(batch=self.batch, movement_type="waste", quantity_stems=-4, reason="so‘lgan")
        response = self.client.get("/api/accounting/")
        self.assertEqual(response.status_code, 200)
        summary = response.data["summary"]
        self.assertEqual(Decimal(summary["flower_cost_total"]), Decimal("100000.00"))
        self.assertEqual(Decimal(summary["material_cost_total"]), Decimal("10000.00"))
        self.assertEqual(Decimal(summary["florist_fee_cost_total"]), Decimal("20000.00"))
        self.assertEqual(Decimal(summary["cost_total"]), Decimal("130000.00"))
        self.assertEqual(Decimal(summary["waste_cost_total"]), Decimal("40000.00"))
        self.assertEqual(summary["waste_stems"], 4)
        row = response.data["history"][0]
        self.assertEqual(Decimal(row["flower_cost"]) + Decimal(row["material_cost"]) + Decimal(row["florist_fee_cost"]), Decimal(row["cost_total"]))

    def test_stock_movement_exposes_cost_value(self):
        StockMovement.objects.create(batch=self.batch, movement_type="waste", quantity_stems=-3, reason="chiqit")
        response = self.client.get("/api/stock-movements/")
        self.assertEqual(response.status_code, 200)
        row = response.data["results"][0]
        self.assertEqual(Decimal(row["cost_value"]), Decimal("30000.00"))
        self.assertEqual(Decimal(row["sale_value"]), Decimal("60000.00"))

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

    def test_branches_endpoint_lists_seeded_branches(self):
        response = self.client.get("/api/branches/")
        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.data["results"]}
        self.assertIn("Asosiy filial", names)
        self.assertIn("Parkent filiali", names)

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
        self.assertIsNone(created.profile.branch_id)
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

    def test_florist_can_only_see_own_profile_and_salary(self):
        florist_user = User.objects.create_user("own-florist", password="password", first_name="Ali")
        other_user = User.objects.create_user("other-florist", password="password", first_name="Vali")
        UserProfile.objects.create(user=florist_user, role="florist")
        UserProfile.objects.create(user=other_user, role="florist")
        own_profile = FloristProfile.objects.create(user=florist_user, staff_type="florist")
        other_profile = FloristProfile.objects.create(user=other_user, staff_type="florist")
        FloristSalaryEntry.objects.create(florist=own_profile, amount=Decimal("100000"), work_date=timezone.localdate(), source="manual")
        FloristSalaryEntry.objects.create(florist=other_profile, amount=Decimal("200000"), work_date=timezone.localdate(), source="manual")
        PagePermission.objects.create(user=florist_user, page="florists", can_view=True, can_control=False)
        self.client.force_authenticate(florist_user)
        response = self.client.get("/api/florists/")
        self.assertEqual(response.status_code, 200)
        profile_ids = [row["id"] for row in response.json()["results"]]
        self.assertEqual(profile_ids, [own_profile.id])
        response = self.client.get("/api/florist-salary/")
        self.assertEqual(response.status_code, 200)
        salary_florist_ids = [row["florist"] for row in response.json()["results"]]
        self.assertEqual(salary_florist_ids, [own_profile.id])

    def test_notification_mark_read_marks_single_notification(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="notifications", can_view=True, can_control=True)
        first = Notification.objects.create(notification_type="lead", title_uz="First", title_ru="First", body_uz="", body_ru="")
        second = Notification.objects.create(notification_type="lead", title_uz="Second", title_ru="Second", body_uz="", body_ru="")
        self.client.force_authenticate(self.user)
        response = self.client.post(f"/api/notifications/{first.id}/mark-read/")
        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_read)
        self.assertFalse(second.is_read)

    def test_lead_status_api_does_not_expose_name_ru(self):
        UserProfile.objects.create(user=self.user, role="admin")
        PagePermission.objects.create(user=self.user, page="crm", can_view=True, can_control=True)
        LeadStatus.objects.create(key="test-status", name_uz="Test status", color="#111111", order=1)
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/lead-statuses/")
        self.assertEqual(response.status_code, 200)
        row = response.json()["results"][0]
        self.assertIn("name_uz", row)
        self.assertNotIn("name_ru", row)

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

    def _florist_with_leftover(self, issued=100, per_item=25, items=3, quantity_total=1):
        """Skladdan gul olib, standart bo'yicha katalog yasagan florist."""
        self._leftover_seq = getattr(self, "_leftover_seq", 0) + 1
        user = User.objects.create_user(f"fl-leftover-{self._leftover_seq}", password="p")
        profile = FloristProfile.objects.create(user=user, staff_type="florist")
        # bir nechta florist ketma-ket sinalganda skladda gul tugab qolmasin
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + issued)
        self.batch.refresh_from_db()
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": issued}, format="json")
        made = []
        for index in range(items):
            response = self.client.post("/api/catalog/", {
                "name_uz": f"Buket {index + 1}", "arrangement_type": "bouquet", "volume": "M",
                "florist": profile.id, "price": "500000", "quantity_total": quantity_total, "status": "available",
                "composition": [{"stock_batch": self.batch.id, "quantity_stems": per_item}],
            }, format="json")
            self.assertEqual(response.status_code, 201, response.json())
            made.append(CatalogItem.objects.get(id=response.json()["id"]))
        return profile, made

    def _balance(self, profile):
        row = FloristStockBalance.objects.filter(florist=profile, batch=self.batch).first()
        return row.remaining_stems if row else 0

    def test_leftover_splits_evenly_into_catalog_items(self):
        # 100 dona olindi, standart 25 dan 3 ta buket = 75. Qolgani 25, uchga bo'linsa 8+8+9
        profile, made = self._florist_with_leftover(issued=100, per_item=25, items=3)
        self.assertEqual(self._balance(profile), 25)
        response = self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile.id}, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(self._balance(profile), 0)
        stems = sorted(CatalogComposition.objects.filter(catalog_item__in=made).values_list("quantity_stems", flat=True))
        self.assertEqual(stems, [33, 33, 34])
        self.assertEqual(sum(stems), 100)

    def test_leftover_gives_extra_to_one_item_when_not_divisible(self):
        # 90 olindi, 25 dan 3 ta = 75, qolgani 15 -> 5+5+5 teng bo'linadi
        profile, made = self._florist_with_leftover(issued=90, per_item=25, items=3)
        self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile.id}, format="json")
        self.assertEqual(sorted(CatalogComposition.objects.filter(catalog_item__in=made).values_list("quantity_stems", flat=True)), [30, 30, 30])
        # 92 olinganda qolgani 17 -> 5+6+6, kimdir bittaga ko'p oladi
        profile2, made2 = self._florist_with_leftover(issued=92, per_item=25, items=3, quantity_total=1)
        self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile2.id}, format="json")
        stems = sorted(CatalogComposition.objects.filter(catalog_item__in=made2).values_list("quantity_stems", flat=True))
        self.assertEqual(stems, [30, 31, 31])
        self.assertEqual(sum(stems), 92)
        self.assertEqual(self._balance(profile2), 0)

    def test_leftover_counts_units_not_items(self):
        # 2 donadan 2 ta katalog = 4 dona buket. Bo'lish katalog emas, dona hisobida borishi kerak
        profile, made = self._florist_with_leftover(issued=92, per_item=23, items=2, quantity_total=2)
        self.assertEqual(self._balance(profile), 0)
        issued = self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 8}, format="json")
        self.assertEqual(issued.status_code, 201, issued.json())
        self.assertEqual(self._balance(profile), 8)
        self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile.id}, format="json")
        # 4 dona buket, 8 gul -> har donaga +2, ya'ni tarkib 23 -> 25
        self.assertEqual(sorted(CatalogComposition.objects.filter(catalog_item__in=made).values_list("quantity_stems", flat=True)), [25, 25])
        self.assertEqual(self._balance(profile), 0)

    def test_leftover_raises_costs_including_sold_items(self):
        profile, made = self._florist_with_leftover(issued=100, per_item=25, items=3)
        mark_catalog_sold(made[0], self.user)
        before = CatalogItem.objects.get(pk=made[0].pk).calculated_cost_price
        self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile.id}, format="json")
        after = CatalogItem.objects.get(pk=made[0].pk).calculated_cost_price
        self.assertGreater(after, before)
        # hisob-kitobdagi tannarx ham o'sadi
        report = self.client.get("/api/accounting/")
        self.assertGreater(Decimal(report.data["summary"]["cost_total"]), Decimal("0"))

    def test_leftover_blocked_when_no_catalog_uses_the_flower(self):
        user = User.objects.create_user("fl-no-catalog", password="p")
        profile = FloristProfile.objects.create(user=user, staff_type="florist")
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 10}, format="json")
        response = self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile.id}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("katalog topilmadi", response.data["detail"])
        self.assertEqual(self._balance(profile), 10)

    def test_adjust_preview_changes_nothing(self):
        profile, made = self._florist_with_leftover(issued=100, per_item=25, items=3)
        response = self.client.get(f"/api/florist-stock-balances/adjust-preview/?florist={profile.id}")
        self.assertEqual(response.status_code, 200)
        row = response.data["batches"][0]
        self.assertEqual(row["florist_stems_now"], 25)
        self.assertEqual(sorted(item["change_per_item"] for item in row["items"]), [8, 8, 9])
        self.assertEqual(self._balance(profile), 25)
        self.assertEqual(sorted(CatalogComposition.objects.filter(catalog_item__in=made).values_list("quantity_stems", flat=True)), [25, 25, 25])

    def test_reverse_returns_stems_from_catalog_to_florist(self):
        # standart 25 edi, florist 23 tadan ishlatgan: 3 buketdan 6 dona ortdi
        profile, made = self._florist_with_leftover(issued=75, per_item=25, items=3)
        self.assertEqual(self._balance(profile), 0)
        response = self.client.post("/api/florist-stock-balances/adjust/", {
            "florist": profile.id, "batch": self.batch.id, "direction": "to_florist", "quantity_stems": 6,
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(sorted(CatalogComposition.objects.filter(catalog_item__in=made).values_list("quantity_stems", flat=True)), [23, 23, 23])
        self.assertEqual(self._balance(profile), 6)

    def test_reverse_lowers_catalog_cost(self):
        profile, made = self._florist_with_leftover(issued=75, per_item=25, items=3)
        before = CatalogItem.objects.get(pk=made[0].pk).calculated_cost_price
        self.client.post("/api/florist-stock-balances/adjust/", {
            "florist": profile.id, "batch": self.batch.id, "direction": "to_florist", "quantity_stems": 6,
        }, format="json")
        after = CatalogItem.objects.get(pk=made[0].pk).calculated_cost_price
        self.assertLess(after, before)

    def test_reverse_requires_batch_and_quantity(self):
        profile, _ = self._florist_with_leftover(issued=75, per_item=25, items=3)
        no_batch = self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile.id, "direction": "to_florist", "quantity_stems": 3}, format="json")
        self.assertEqual(no_batch.status_code, 400)
        self.assertIn("batch", no_batch.data)
        no_quantity = self.client.post("/api/florist-stock-balances/adjust/", {"florist": profile.id, "batch": self.batch.id, "direction": "to_florist"}, format="json")
        self.assertEqual(no_quantity.status_code, 400)
        self.assertIn("quantity_stems", no_quantity.data)

    def test_reverse_rejects_more_than_catalog_holds(self):
        profile, made = self._florist_with_leftover(issued=75, per_item=25, items=3)
        response = self.client.post("/api/florist-stock-balances/adjust/", {
            "florist": profile.id, "batch": self.batch.id, "direction": "to_florist", "quantity_stems": 500,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("yetmayapti", response.data["detail"])
        # hech narsa o'zgarmadi
        self.assertEqual(sorted(CatalogComposition.objects.filter(catalog_item__in=made).values_list("quantity_stems", flat=True)), [25, 25, 25])
        self.assertEqual(self._balance(profile), 0)

    def test_florist_catalog_checks_florist_balance_not_warehouse(self):
        # gul floristning qo'liga chiqarilgach skladda qolmaydi, lekin katalog qo'shilishi kerak
        user = User.objects.create_user("fl-empty-warehouse", password="p")
        profile = FloristProfile.objects.create(user=user, staff_type="florist")
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 100}, format="json")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, 0)
        response = self.client.post("/api/catalog/", {
            "name_uz": "Sklad bo‘sh buket", "arrangement_type": "bouquet", "florist": profile.id,
            "price": "500000", "quantity_total": 1, "status": "available",
            "composition": [{"stock_batch": self.batch.id, "quantity_stems": 25}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(FloristStockBalance.objects.get(florist=profile, batch=self.batch).remaining_stems, 75)

    def test_florist_catalog_rejected_when_florist_has_too_few(self):
        user = User.objects.create_user("fl-short", password="p")
        profile = FloristProfile.objects.create(user=user, staff_type="florist")
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 10}, format="json")
        response = self.client.post("/api/catalog/", {
            "name_uz": "Ko‘p gulli buket", "arrangement_type": "bouquet", "florist": profile.id,
            "price": "500000", "quantity_total": 1, "status": "available",
            "composition": [{"stock_batch": self.batch.id, "quantity_stems": 25}],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("floristdagi gul yetarli emas", response.data["detail"])

    def _florist_with_rates(self, name):
        user = User.objects.create_user(name, password="p")
        profile = FloristProfile.objects.create(user=user, staff_type="florist")
        for volume, stems in [("S", 15), ("M", 25), ("L", 40)]:
            FloristVolumeRate.objects.create(florist=profile, arrangement_type="bouquet", volume=volume, default_stems=stems, florist_fee=Decimal("50000"))
        return profile

    def _make_sized_catalog(self, profile, volume, count=1, quantity_total=1):
        made = []
        for index in range(count):
            response = self.client.post("/api/catalog/", {
                "name_uz": f"{volume} buket {index + 1}", "arrangement_type": "bouquet", "volume": volume,
                "florist": profile.id, "price": "500000", "quantity_total": quantity_total, "status": "available",
            }, format="json")
            self.assertEqual(response.status_code, 201, response.json())
            made.append(CatalogItem.objects.get(id=response.json()["id"]))
        return made

    def test_catalog_without_stems_needs_only_volume(self):
        # florist katalogga faqat hajmni yozadi, gul soni so'ralmaydi
        profile = self._florist_with_rates("fl-size-only")
        made = self._make_sized_catalog(profile, "M")
        self.assertEqual(made[0].composition.count(), 0)
        self.assertEqual(made[0].volume, "M")

    def test_florist_catalog_requires_volume(self):
        profile = self._florist_with_rates("fl-no-volume")
        response = self.client.post("/api/catalog/", {
            "name_uz": "Hajmsiz", "arrangement_type": "bouquet",
            "florist": profile.id, "price": "500000", "quantity_total": 1,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("volume", response.data)

    def test_closing_issue_shares_stems_by_volume_standard(self):
        # 600 dona chiqarildi, 3 ta S + 2 ta M + 1 ta L yasaldi
        profile = self._florist_with_rates("fl-close-1")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 600)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 600}, format="json")
        small = self._make_sized_catalog(profile, "S", count=3)
        medium = self._make_sized_catalog(profile, "M", count=2)
        large = self._make_sized_catalog(profile, "L", count=1)
        response = self.client.post("/api/florist-stock-balances/close-issue/", {"florist": profile.id, "batch": self.batch.id}, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.data["shared_stems"], 600)
        stems = {item.id: item.composition.first().quantity_stems for item in small + medium + large}
        # ulush: S=15/135, M=25/135, L=40/135
        self.assertEqual(sorted(stems[item.id] for item in small), [66, 67, 67])
        self.assertEqual(sorted(stems[item.id] for item in medium), [111, 111])
        self.assertEqual(stems[large[0].id], 178)
        self.assertEqual(sum(stems.values()), 600)
        self.assertEqual(FloristStockBalance.objects.get(florist=profile, batch=self.batch).remaining_stems, 0)

    def test_closing_issue_returns_leftover_to_warehouse_first(self):
        profile = self._florist_with_rates("fl-close-2")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 100)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 100}, format="json")
        self.batch.refresh_from_db()
        warehouse_before = self.batch.remaining_stems
        made = self._make_sized_catalog(profile, "M", count=2)
        response = self.client.post("/api/florist-stock-balances/close-issue/", {
            "florist": profile.id, "batch": self.batch.id, "return_stems": 20,
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.data["returned_stems"], 20)
        self.assertEqual(response.data["shared_stems"], 80)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, warehouse_before + 20)
        self.assertEqual(sorted(item.composition.first().quantity_stems for item in made), [40, 40])
        self.assertEqual(FloristStockBalance.objects.get(florist=profile, batch=self.batch).remaining_stems, 0)

    def test_closing_issue_counts_units_inside_one_catalog(self):
        profile = self._florist_with_rates("fl-close-3")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 200)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 200}, format="json")
        made = self._make_sized_catalog(profile, "M", count=1, quantity_total=4)
        self.client.post("/api/florist-stock-balances/close-issue/", {"florist": profile.id, "batch": self.batch.id}, format="json")
        # 4 dona buket, 200 gul -> har donaga 50
        self.assertEqual(made[0].composition.first().quantity_stems, 50)
        self.assertEqual(FloristStockBalance.objects.get(florist=profile, batch=self.batch).remaining_stems, 0)

    def test_closing_issue_sets_catalog_cost(self):
        profile = self._florist_with_rates("fl-close-4")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 100)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 100}, format="json")
        made = self._make_sized_catalog(profile, "M", count=2)
        before = CatalogItem.objects.get(pk=made[0].pk).calculated_cost_price
        self.client.post("/api/florist-stock-balances/close-issue/", {"florist": profile.id, "batch": self.batch.id}, format="json")
        after = CatalogItem.objects.get(pk=made[0].pk).calculated_cost_price
        self.assertGreater(after, before)

    def test_closing_issue_needs_volume_rate(self):
        user = User.objects.create_user("fl-close-norate", password="p")
        profile = FloristProfile.objects.create(user=user, staff_type="florist")
        FloristVolumeRate.objects.create(florist=profile, arrangement_type="bouquet", volume="M", default_stems=25, florist_fee=Decimal("50000"))
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 100)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 100}, format="json")
        self._make_sized_catalog(profile, "M")
        self._make_sized_catalog(profile, "XL")
        response = self.client.post("/api/florist-stock-balances/close-issue/", {"florist": profile.id, "batch": self.batch.id}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("hajm tarifi belgilanmagan", response.data["detail"])
        self.assertEqual(FloristStockBalance.objects.get(florist=profile, batch=self.batch).remaining_stems, 100)

    def test_closing_issue_without_catalog_is_rejected(self):
        profile = self._florist_with_rates("fl-close-5")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 50)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 50}, format="json")
        response = self.client.post("/api/florist-stock-balances/close-issue/", {"florist": profile.id, "batch": self.batch.id}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("guli yozilmagan katalog yo‘q", response.data["detail"])

    def test_closing_issue_can_return_everything(self):
        profile = self._florist_with_rates("fl-close-6")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 30)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 30}, format="json")
        self.batch.refresh_from_db()
        warehouse_before = self.batch.remaining_stems
        response = self.client.post("/api/florist-stock-balances/close-issue/", {
            "florist": profile.id, "batch": self.batch.id, "return_stems": 30,
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.data["shared_stems"], 0)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_stems, warehouse_before + 30)

    def test_close_issue_preview_changes_nothing(self):
        profile = self._florist_with_rates("fl-close-7")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 100)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 100}, format="json")
        made = self._make_sized_catalog(profile, "M", count=2)
        response = self.client.get(f"/api/florist-stock-balances/close-issue-preview/?florist={profile.id}&batch={self.batch.id}&return_stems=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["share_stems"], 80)
        self.assertEqual(sorted(row["stems_per_item"] for row in response.data["items"]), [40, 40])
        self.assertEqual(FloristStockBalance.objects.get(florist=profile, batch=self.batch).remaining_stems, 100)
        self.assertEqual(sum(item.composition.count() for item in made), 0)

    def test_closing_issue_rejects_return_bigger_than_held(self):
        profile = self._florist_with_rates("fl-close-8")
        StockBatch.objects.filter(pk=self.batch.pk).update(remaining_stems=F("remaining_stems") + 40)
        self.client.post("/api/florist-stock-issues/issue/", {"florist": profile.id, "batch": self.batch.id, "quantity_stems": 40}, format="json")
        response = self.client.post("/api/florist-stock-balances/close-issue/", {
            "florist": profile.id, "batch": self.batch.id, "return_stems": 500,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(FloristStockBalance.objects.get(florist=profile, batch=self.batch).remaining_stems, 40)

    def test_delivery_is_created_then_flowers_added_into_it(self):
        supplier = Supplier.objects.create(name="Golland Flowers")
        created = self.client.post("/api/stock-deliveries/", {
            "number": "7", "received_at": "2026-08-01", "supplier": supplier.id, "note": "Chorshanba yuki",
        }, format="json")
        self.assertEqual(created.status_code, 201, created.json())
        delivery_id = created.json()["id"]
        flower = Flower.objects.create(name_uz="Lola partiya", slug="lola-partiya")
        first = FlowerVariant.objects.create(flower=flower, name_uz="Qizil nav", color_uz="Qizil")
        second = FlowerVariant.objects.create(flower=flower, name_uz="Sariq nav", color_uz="Sariq")
        for variant in [first, second]:
            response = self.client.post("/api/stock-batches/", {
                "delivery": delivery_id, "variant": variant.id, "height_cm": 50,
                "stems_per_bunch": 25, "received_stems": 100,
                "cost_per_bunch": "250000", "sale_price_per_bunch": "500000",
            }, format="json")
            self.assertEqual(response.status_code, 201, response.json())
            # partiya raqami, sanasi va postavshigi partiyadan olinadi
            self.assertEqual(response.json()["batch_number"], "7")
            self.assertEqual(response.json()["received_at"], "2026-08-01")
            self.assertEqual(response.json()["supplier"], supplier.id)
        listed = self.client.get(f"/api/stock-deliveries/{delivery_id}/")
        self.assertEqual(listed.data["batch_count"], 2)
        self.assertEqual(listed.data["total_stems"], 200)
        inside = self.client.get(f"/api/stock-deliveries/{delivery_id}/batches/")
        self.assertEqual(len(inside.data), 2)

    def test_bunch_price_fills_stem_price_automatically(self):
        response = self.client.post("/api/stock-batches/", {
            "batch_number": "AUTO-1", "variant": self.batch.variant_id, "height_cm": 50,
            "stems_per_bunch": 25, "received_stems": 50,
            "cost_per_bunch": "25000", "sale_price_per_bunch": "50000",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(Decimal(response.json()["cost_per_stem"]), Decimal("1000.00"))
        self.assertEqual(Decimal(response.json()["sale_price_per_stem"]), Decimal("2000.00"))

    def test_stem_price_is_rounded_to_hundred(self):
        # 24 950 / 25 = 998 -> 1 000,  26 500 / 25 = 1 060 -> 1 100
        response = self.client.post("/api/stock-batches/", {
            "batch_number": "AUTO-2", "variant": self.batch.variant_id, "height_cm": 50,
            "stems_per_bunch": 25, "received_stems": 50,
            "cost_per_bunch": "24950", "sale_price_per_bunch": "26500",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(Decimal(response.json()["cost_per_stem"]), Decimal("1000.00"))
        self.assertEqual(Decimal(response.json()["sale_price_per_stem"]), Decimal("1100.00"))
        # pochka narxi kiritilgani o'zgarmay saqlanadi
        self.assertEqual(Decimal(response.json()["cost_per_bunch"]), Decimal("24950.00"))

    def test_stem_price_fills_bunch_price_backwards(self):
        response = self.client.post("/api/stock-batches/", {
            "batch_number": "AUTO-3", "variant": self.batch.variant_id, "height_cm": 50,
            "stems_per_bunch": 10, "received_stems": 50,
            "cost_per_stem": "3000", "sale_price_per_stem": "5000",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(Decimal(response.json()["cost_per_bunch"]), Decimal("30000.00"))
        self.assertEqual(Decimal(response.json()["sale_price_per_bunch"]), Decimal("50000.00"))

    def test_batch_without_delivery_gets_one_automatically(self):
        response = self.client.post("/api/stock-batches/", {
            "batch_number": "AUTO-4", "variant": self.batch.variant_id, "height_cm": 50,
            "stems_per_bunch": 10, "received_stems": 20,
            "cost_per_stem": "1000", "sale_price_per_stem": "2000", "sale_price_per_bunch": "20000",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.json())
        self.assertIsNotNone(response.json()["delivery"])
        self.assertEqual(response.json()["delivery_detail"]["number"], "AUTO-4")

    def test_same_delivery_reused_for_same_number_and_date(self):
        payload = {
            "batch_number": "AUTO-5", "variant": self.batch.variant_id, "height_cm": 50,
            "received_at": "2026-08-01", "stems_per_bunch": 10, "received_stems": 20,
            "cost_per_stem": "1000", "sale_price_per_stem": "2000", "sale_price_per_bunch": "20000",
        }
        one = self.client.post("/api/stock-batches/", payload, format="json")
        two = self.client.post("/api/stock-batches/", payload, format="json")
        self.assertEqual(one.status_code, 201)
        self.assertEqual(two.status_code, 201)
        self.assertEqual(one.json()["delivery"], two.json()["delivery"])
        self.assertEqual(StockDelivery.objects.filter(number="AUTO-5").count(), 1)

    def test_stock_batch_number_can_repeat(self):
        # bir xil raqamli partiyalar turli gul va turli kunlarda kelaveradi
        flower = Flower.objects.create(name_uz="Xrizantema API", slug="xrizantema-api")
        first = FlowerVariant.objects.create(flower=flower, name_uz="Oq", color_uz="Oq")
        second = FlowerVariant.objects.create(flower=flower, name_uz="Sariq", color_uz="Sariq")
        payload = {
            "batch_number": "1",
            "height_cm": 50,
            "stems_per_bunch": 5,
            "received_stems": 20,
            "cost_per_stem": "10000.00",
            "sale_price_per_stem": "20000.00",
            "sale_price_per_bunch": "100000.00",
        }
        one = self.client.post("/api/stock-batches/", {**payload, "variant": first.id}, format="json")
        self.assertEqual(one.status_code, 201, one.json())
        two = self.client.post("/api/stock-batches/", {**payload, "variant": second.id}, format="json")
        self.assertEqual(two.status_code, 201, two.json())
        self.assertEqual(StockBatch.objects.filter(batch_number="1").count(), 2)
        # ayni gulga ayni raqam bilan yana qo'shsa ham to'sib qo'yilmaydi
        three = self.client.post("/api/stock-batches/", {**payload, "variant": first.id}, format="json")
        self.assertEqual(three.status_code, 201, three.json())
        self.assertEqual(StockBatch.objects.filter(batch_number="1").count(), 3)

    @override_settings(OPENAI_API_KEY="")
    def test_mini_app_custom_quote_ai_returns_final_price_note(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={"default_florist_fee": Decimal("50000")})
        result = mini_app_custom_quote_ai("10 ta atirgul buketga", "bouquet")
        self.assertEqual(result["ai_note"], mini_app_quote_note(result["estimated_price"]))
        self.assertEqual(result["ai_note"], "Taxminiy narx 50 000 so'm. Operatorlarimiz aloqaga chiqib, sizga batafsil ma'lumot berishadi.")

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
        # orders endi lead va katalog sotuvini jamlaydi: 1 won lead + 1 katalog sotuvi
        self.assertEqual(data["summary"]["orders"], 2)
        self.assertEqual(data["summary"]["lead_orders"], 1)
        self.assertEqual(data["summary"]["catalog_sales_orders"], 1)
        self.assertEqual(Decimal(str(data["summary"]["lead_revenue"])), Decimal("600000.00"))
        self.assertEqual(Decimal(str(data["summary"]["catalog_sales_revenue"])), Decimal("250000.00"))
        self.assertEqual(Decimal(str(data["summary"]["revenue"])), Decimal("850000.00"))
        self.assertEqual(data["top_selling_flowers"][0]["name_uz"], "Atirgul API")
        self.assertEqual(data["top_selling_flowers"][0]["stems"], 8)
        self.assertEqual(data["top_catalog_items"][0]["catalog_item__name_uz"], "Analytics buket")
        # katalogdan 1 ta + lead orqali 2 ta
        self.assertEqual(data["top_catalog_items"][0]["quantity"], 3)
        self.assertEqual(data["recent_top_catalog_items"][0]["catalog_item__name_uz"], "Analytics buket")
        self.assertEqual(data["recent_top_catalog_items"][0]["orders"], 2)
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
