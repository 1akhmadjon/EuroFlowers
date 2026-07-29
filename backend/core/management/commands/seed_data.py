from decimal import Decimal
import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import AISettings, BusinessSettings, CatalogComposition, CatalogItem, Conversation, Customer, Flower, FlowerVariant, InstagramSettings, IntegrationSettings, Lead, LeadStatus, Message, Notification, Packaging, PagePermission, SocialPost, StockBatch, StockMovement, UserProfile


class Command(BaseCommand):
    def handle(self, *args, **options):
        seed_password = os.getenv("SEED_PASSWORD") or os.getenv("ADMIN_PASSWORD") or "change-me-seed-password"
        admin, _ = User.objects.get_or_create(username="admin", defaults={"first_name": "Akmal", "last_name": "Karimov", "email": "admin@euroflowers.uz", "is_staff": True, "is_superuser": True})
        admin.set_password(seed_password)
        admin.save()
        UserProfile.objects.get_or_create(user=admin, defaults={"role": "admin", "language": "uz"})
        developer, _ = User.objects.get_or_create(username="developer", defaults={"first_name": "Dev", "last_name": "EuroFlowers", "email": "dev@euroflowers.uz", "is_staff": True})
        developer.set_password(seed_password)
        developer.save()
        developer_profile, _ = UserProfile.objects.get_or_create(user=developer, defaults={"role": "developer", "language": "uz"})
        if developer_profile.role != "developer":
            developer_profile.role = "developer"
            developer_profile.save(update_fields=["role", "updated_at"])
        BusinessSettings.objects.get_or_create(pk=1)
        AISettings.objects.get_or_create(pk=1)
        IntegrationSettings.objects.get_or_create(pk=1)
        InstagramSettings.objects.get_or_create(pk=1, defaults={"account_username": "euroflowers.uz"})
        for key, name_uz, color, order in [
            ("new", "Yangi", "#2563eb", 10),
            ("qualified", "Aniqlangan", "#7c3aed", 20),
            ("contacted", "Aloqa qilindi", "#f59e0b", 30),
            ("won", "Sotildi", "#16a34a", 40),
            ("lost", "Yo‘qotildi", "#dc2626", 50),
        ]:
            LeadStatus.objects.update_or_create(key=key, defaults={"name_uz": name_uz, "color": color, "order": order, "is_active": True})
        for page, _ in PagePermission.PAGE_CHOICES:
            if page not in PagePermission.DEVELOPER_ONLY_PAGES:
                PagePermission.objects.update_or_create(user=admin, page=page, defaults={"can_view": True, "can_control": True})
            PagePermission.objects.update_or_create(user=developer, page=page, defaults={"can_view": True, "can_control": True})

        flower, _ = Flower.objects.update_or_create(slug="gortenziya", defaults={"name_uz": "Gortenziya", "description_uz": "Premium gortenziya assortimenti"})
        variant, _ = FlowerVariant.objects.update_or_create(flower=flower, name_uz="Premium Blue", color_uz="Moviy", defaults={"default_stems_per_bunch": 5, "minimum_sale_stems": 1, "description_uz": "Moviy premium gortenziya"})
        batch, created = StockBatch.objects.update_or_create(batch_number="EF-DEMO-1", defaults={"variant": variant, "received_at": timezone.localdate(), "height_cm": 50, "stems_per_bunch": 5, "received_stems": 50, "remaining_stems": 50, "cost_per_stem": 65000, "sale_price_per_stem": 105000, "sale_price_per_bunch": 500000, "minimum_sale_stems": 1})
        if created:
            StockMovement.objects.create(batch=batch, movement_type="in", quantity_stems=50, quantity_bunches=Decimal("10"), reason="Boshlang‘ich seed kirimi", performed_by=admin)
        Packaging.objects.update_or_create(packaging_type="basket", name_uz="Oq premium savat", defaults={"size": "M", "capacity_min_stems": 15, "capacity_max_stems": 35, "cost_price": 120000, "sale_price": 180000, "quantity": 12})
        post, _ = SocialPost.objects.update_or_create(media_id="demo-post-1", defaults={"post_type": "post", "permalink": "https://www.instagram.com/euroflowers.uz/", "title_uz": "Demo katalog", "title_ru": "Demo katalog", "description_uz": "Demo post", "description_ru": "Demo post", "price": 750000, "flower_count": 15, "is_active": True})
        item, _ = CatalogItem.objects.update_or_create(name_uz="Moviy demo buket", defaults={"description_uz": "Demo katalog guli", "arrangement_type": "bouquet", "price": 750000, "status": "available", "social_post": post, "created_by": admin})
        item.composition.all().delete()
        CatalogComposition.objects.create(catalog_item=item, stock_batch=batch, quantity_stems=7, quantity_bunches=Decimal("1.40"))
        customer, _ = Customer.objects.update_or_create(instagram_user_id="demo-customer", defaults={"name": "Aziza", "phone": "+998901234567", "language": "uz"})
        conversation, _ = Conversation.objects.get_or_create(customer=customer, status="ai", defaults={"social_post": post})
        if not conversation.messages.exists():
            Message.objects.create(conversation=conversation, sender="customer", text="Assalomu alaykum, demo buket narxi qancha?")
            Message.objects.create(conversation=conversation, sender="ai", text="Assalomu alaykum, demo buket 750 000 so‘m.")
        Lead.objects.get_or_create(customer=customer, request_uz="Demo katalog buyurtma", defaults={"conversation": conversation, "social_post": post, "status": "new", "arrangement_type": "catalog", "estimated_price": 750000})
        Notification.objects.get_or_create(notification_type="lead", reference_type="catalog_item", reference_id=item.id, defaults={"title_uz": "Demo lead", "title_ru": "Demo lead", "body_uz": "Demo data tayyor", "body_ru": "Demo data tayyor"})
        self.stdout.write(self.style.SUCCESS("EuroFlowers seed ma’lumotlari tayyor"))
