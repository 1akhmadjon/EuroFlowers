from datetime import timedelta
from decimal import Decimal
import os
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import AISettings, Branch, BusinessSettings, CatalogComposition, CatalogItem, Conversation, Customer, Flower, FlowerVariant, InstagramSettings, IntegrationSettings, Lead, LeadPackagingUsage, LeadStockUsage, Message, Notification, Packaging, PackagingMovement, PagePermission, SocialPost, StockBatch, StockMovement, UserProfile


class Command(BaseCommand):
    def handle(self, *args, **options):
        seed_password = os.getenv("SEED_PASSWORD") or os.getenv("ADMIN_PASSWORD") or "change-me-seed-password"
        admin, _ = User.objects.get_or_create(username="admin", defaults={"first_name": "Akmal", "last_name": "Karimov", "email": "admin@euroflowers.uz", "is_staff": True, "is_superuser": True})
        admin.set_password(seed_password)
        admin.save()
        profile, _ = UserProfile.objects.get_or_create(user=admin, defaults={"role": "admin", "language": "uz"})
        developer, _ = User.objects.get_or_create(username="developer", defaults={"first_name": "Dev", "last_name": "EuroFlowers", "email": "dev@euroflowers.uz", "is_staff": True})
        developer.set_password(seed_password)
        developer.save()
        developer_profile, _ = UserProfile.objects.get_or_create(user=developer, defaults={"role": "developer", "language": "uz"})
        if developer_profile.role != "developer":
            developer_profile.role = "developer"
            developer_profile.save(update_fields=["role", "updated_at"])
        operator, _ = User.objects.get_or_create(username="operator", defaults={"first_name": "Madina", "last_name": "Rasulova"})
        operator.set_password(seed_password)
        operator.save()
        operator_profile, _ = UserProfile.objects.get_or_create(user=operator, defaults={"role": "operator", "language": "uz"})
        BusinessSettings.objects.get_or_create(pk=1)
        AISettings.objects.get_or_create(pk=1)
        IntegrationSettings.objects.get_or_create(pk=1)
        InstagramSettings.objects.get_or_create(pk=1, defaults={"account_username": "euroflowers.uz"})

        central, _ = Branch.objects.update_or_create(code="TASH-C", defaults={"name": "Toshkent markaziy filial", "address": "Toshkent shahri, Mirzo Ulug‘bek tumani", "phone": "+998 90 900 00 01"})
        west, _ = Branch.objects.update_or_create(code="TASH-W", defaults={"name": "Toshkent g‘arbiy filial", "address": "Toshkent shahri, Chilonzor tumani", "phone": "+998 90 900 00 02"})
        profile.branches.set([central, west])
        developer_profile.branches.set([central, west])
        operator_profile.branches.set([central])
        for page, _ in PagePermission.PAGE_CHOICES:
            PagePermission.objects.update_or_create(user=admin, page=page, defaults={"can_view": True, "can_control": True})
            PagePermission.objects.update_or_create(user=developer, page=page, defaults={"can_view": True, "can_control": True})
        operator_permissions = {
            "dashboard": (True, False),
            "crm": (True, True),
            "customers": (True, True),
            "conversations": (True, True),
            "catalog": (True, False),
            "notifications": (True, True),
        }
        for page, values in operator_permissions.items():
            PagePermission.objects.update_or_create(user=operator, page=page, defaults={"can_view": values[0], "can_control": values[1]})

        flowers = [
            ("atirgul", "Atirgul", "Роза", 1, 12, "https://images.unsplash.com/photo-1518895949257-7621c3c786d7?auto=format&fit=crop&w=900&q=85"),
            ("buta-atirgul", "Buta atirguli", "Кустовая роза", 1, 12, "https://images.unsplash.com/photo-1526047932273-341f2a7631f9?auto=format&fit=crop&w=900&q=85"),
            ("gortenziya", "Gortenziya", "Гортензия", 6, 10, "https://flores.uz/wp-content/uploads/2026/03/photo_2_2026-03-11_19-24-38.jpg"),
            ("pion", "Pion", "Пион", 4, 7, "https://images.unsplash.com/photo-1563241527-3004b7be0ffd?auto=format&fit=crop&w=900&q=85"),
            ("xrizantema", "Xrizantema", "Хризантема", 8, 11, "https://images.unsplash.com/photo-1572454591674-2739f30d2c6c?auto=format&fit=crop&w=900&q=85"),
            ("alstromeriya", "Alstromeriya", "Альстромерия", 1, 12, "https://images.unsplash.com/photo-1523438885200-e635ba2c371e?auto=format&fit=crop&w=900&q=85"),
            ("matthiola", "Matthiola", "Маттиола", 3, 8, "https://images.unsplash.com/photo-1591886960571-74d43a9d4166?auto=format&fit=crop&w=900&q=85"),
            ("gvozdika", "Gvozdika", "Гвоздика", 1, 12, "https://images.unsplash.com/photo-1495231916356-a86217efff12?auto=format&fit=crop&w=900&q=85"),
        ]
        flower_map = {}
        for slug, uz, ru, start, end, image in flowers:
            flower, _ = Flower.objects.update_or_create(slug=slug, defaults={"name_uz": uz, "name_ru": ru, "season_start_month": start, "season_end_month": end, "image_url": image, "description_uz": f"Premium {uz.lower()} assortimenti", "description_ru": f"Премиальный ассортимент: {ru.lower()}"})
            flower_map[slug] = flower

        variants = [
            ("atirgul", "Mondial", "Mondial", "Oq", "Белый", 20, 11),
            ("atirgul", "Explorer", "Explorer", "Qizil", "Красный", 20, 11),
            ("atirgul", "Hermosa", "Hermosa", "Pushti", "Розовый", 20, 11),
            ("buta-atirgul", "Bombastic", "Bombastic", "Pushti", "Розовый", 10, 5),
            ("buta-atirgul", "White O’Hara", "White O’Hara", "Krem", "Кремовый", 10, 5),
            ("gortenziya", "Premium Blue", "Premium Blue", "Moviy", "Голубой", 5, 3),
            ("gortenziya", "Verena", "Verena", "Pushti", "Розовый", 5, 3),
            ("pion", "Sarah Bernhardt", "Sarah Bernhardt", "Och pushti", "Нежно-розовый", 5, 5),
            ("xrizantema", "Baltica", "Baltica", "Oq", "Белый", 5, 5),
            ("alstromeriya", "Virginia", "Virginia", "Oq", "Белый", 10, 5),
            ("matthiola", "Katz", "Katz", "Lavanda", "Лавандовый", 10, 5),
            ("gvozdika", "Nobio", "Nobio", "Krem", "Кремовый", 20, 10),
        ]
        variant_map = {}
        for slug, uz, ru, color_uz, color_ru, bunch, minimum in variants:
            variant, _ = FlowerVariant.objects.update_or_create(flower=flower_map[slug], name_uz=uz, color_uz=color_uz, defaults={"name_ru": ru, "color_ru": color_ru, "default_stems_per_bunch": bunch, "minimum_sale_stems": minimum, "image_url": flower_map[slug].image_url})
            variant_map[(slug, uz)] = variant

        batch_rows = [
            (central, "EF-260701", "atirgul", "Mondial", 60, 20, 240, 21000, 35000, 660000),
            (central, "EF-260702", "atirgul", "Explorer", 70, 20, 180, 23000, 39000, 740000),
            (central, "EF-260703", "atirgul", "Hermosa", 50, 20, 160, 19000, 32000, 600000),
            (central, "EF-260704", "buta-atirgul", "Bombastic", 60, 10, 90, 35000, 52000, 490000),
            (central, "EF-260705", "gortenziya", "Premium Blue", 50, 5, 45, 65000, 95000, 450000),
            (central, "EF-260706", "gortenziya", "Verena", 45, 5, 35, 62000, 92000, 435000),
            (central, "EF-260707", "pion", "Sarah Bernhardt", 55, 5, 25, 70000, 110000, 525000),
            (central, "EF-260708", "xrizantema", "Baltica", 70, 5, 65, 22000, 36000, 170000),
            (central, "EF-260709", "alstromeriya", "Virginia", 70, 10, 80, 18000, 30000, 285000),
            (west, "WF-260701", "atirgul", "Mondial", 60, 20, 120, 21000, 35000, 660000),
            (west, "WF-260702", "buta-atirgul", "White O’Hara", 60, 10, 60, 37000, 55000, 520000),
            (west, "WF-260703", "matthiola", "Katz", 65, 10, 50, 24000, 39000, 370000),
        ]
        batch_map = {}
        for branch, number, slug, variant_name, height, per_bunch, stems, cost, sale, bunch_sale in batch_rows:
            variant = variant_map[(slug, variant_name)]
            batch, created = StockBatch.objects.update_or_create(branch=branch, batch_number=number, defaults={"variant": variant, "received_at": timezone.localdate() - timedelta(days=7), "height_cm": height, "stems_per_bunch": per_bunch, "received_stems": stems, "remaining_stems": stems, "cost_per_stem": cost, "sale_price_per_stem": sale, "sale_price_per_bunch": bunch_sale, "minimum_sale_stems": variant.minimum_sale_stems, "image_url": variant.image_url})
            if created:
                StockMovement.objects.create(batch=batch, movement_type="in", quantity_stems=stems, quantity_bunches=Decimal(stems) / per_bunch, reason="Boshlang‘ich seed kirimi", performed_by=admin)
            batch_map[number] = batch

        for kind, uz, ru, size, minimum, maximum, price, qty in [
            ("basket", "Oq premium savat", "Белая премиальная корзина", "M", 15, 35, 180000, 12),
            ("basket", "Katta to‘qima savat", "Большая плетёная корзина", "L", 36, 75, 320000, 7),
            ("box", "Dumaloq premium quti", "Круглая премиальная коробка", "M", 15, 45, 150000, 18),
            ("wrap", "Koreya o‘rami", "Корейская упаковка", "Universal", 1, 101, 50000, 80),
        ]:
            Packaging.objects.update_or_create(branch=central, packaging_type=kind, name_uz=uz, defaults={"name_ru": ru, "size": size, "capacity_min_stems": minimum, "capacity_max_stems": maximum, "cost_price": price * Decimal("0.65"), "sale_price": price, "quantity": qty})

        post, _ = SocialPost.objects.update_or_create(media_id="178900000000001", defaults={"branch": central, "post_type": "ad", "permalink": "https://www.instagram.com/euroflowers.uz/", "title_uz": "Blue Hydrangea Garden", "title_ru": "Blue Hydrangea Garden", "description_uz": "Moviy gortenziya, oq atirgul va xrizantemadan premium buket. 35–40 sm.", "description_ru": "Премиальный букет из голубой гортензии, белой розы и хризантемы. 35–40 см.", "price": 750000, "flower_count": 19, "image_url": flower_map["gortenziya"].image_url, "is_targeted": True})
        post2, _ = SocialPost.objects.update_or_create(media_id="178900000000002", defaults={"branch": central, "post_type": "post", "permalink": "https://www.instagram.com/euroflowers.uz/", "title_uz": "White Rose 15", "title_ru": "White Rose 15", "description_uz": "15 dona 60 sm oq Mondial atirgulidan buket.", "description_ru": "Букет из 15 белых роз Mondial высотой 60 см.", "price": 575000, "flower_count": 15, "image_url": flower_map["atirgul"].image_url})

        catalog_rows = [
            ("Moviy tong", "Голубое утро", "bouquet", 750000, flower_map["gortenziya"].image_url, [("EF-260705", 3), ("EF-260701", 11), ("EF-260708", 5)]),
            ("Pushti bulut", "Розовое облако", "bouquet", 920000, flower_map["buta-atirgul"].image_url, [("EF-260704", 15), ("EF-260706", 5)]),
            ("Oq nafislik", "Белая нежность", "basket", 1250000, flower_map["atirgul"].image_url, [("EF-260701", 25), ("EF-260709", 10)]),
            ("Pion mavsumi", "Сезон пионов", "bouquet", 1100000, flower_map["pion"].image_url, [("EF-260707", 9), ("EF-260704", 5)]),
            ("Qizil klassika", "Красная классика", "bouquet", 850000, flower_map["atirgul"].image_url, [("EF-260702", 21)]),
            ("Pastel garden", "Пастельный сад", "box", 980000, flower_map["alstromeriya"].image_url, [("EF-260703", 15), ("EF-260709", 10), ("EF-260708", 7)]),
        ]
        catalogs = []
        for index, (uz, ru, kind, price, image, composition) in enumerate(catalog_rows):
            item, _ = CatalogItem.objects.update_or_create(branch=central, name_uz=uz, defaults={"name_ru": ru, "description_uz": "Bugun tayyorlangan yangi premium kompozitsiya", "description_ru": "Свежая премиальная композиция, собранная сегодня", "arrangement_type": kind, "height_cm": 40, "diameter_cm": 38, "price": price, "status": "sold" if index == 4 else "available", "image_url": image, "social_post": post if index == 0 else None, "sold_at": timezone.now() if index == 4 else None, "created_by": admin})
            item.composition.all().delete()
            for batch_number, quantity in composition:
                CatalogComposition.objects.create(catalog_item=item, stock_batch=batch_map[batch_number], quantity_stems=quantity, quantity_bunches=Decimal(quantity) / batch_map[batch_number].stems_per_bunch)
            catalogs.append(item)
        Notification.objects.get_or_create(branch=central, notification_type="stock_pending", reference_type="catalog_item", reference_id=catalogs[4].id, defaults={"title_uz": "Qizil klassika: sklad chiqimi kutilmoqda", "title_ru": "Красная классика: ожидается списание", "body_uz": "Sotilgan kompozitsiya tarkibini skladdan chiqaring.", "body_ru": "Спишите состав проданной композиции со склада."})

        customers = [
            ("ig_10001", "Aziza", "+998901234567", "aziza_home", "uz"),
            ("ig_10002", "Мария", "+998935556677", "maria.tash", "ru"),
            ("ig_10003", "Jasur", "+998977778899", "jasur_97", "uz"),
            ("ig_10004", "Dilnoza", "", "dilnoza_a", "uz"),
        ]
        customer_map = {}
        for ig_id, name, phone, username, language in customers:
            customer, _ = Customer.objects.update_or_create(instagram_user_id=ig_id, defaults={"name": name, "phone": phone, "instagram_username": username, "language": language, "branch": central})
            customer_map[ig_id] = customer
        conversation, _ = Conversation.objects.get_or_create(customer=customer_map["ig_10001"], branch=central, status="ai", defaults={"social_post": post})
        if not conversation.messages.exists():
            Message.objects.create(conversation=conversation, sender="customer", text="Assalomu alaykum, targetdagi moviy buket narxi qancha?")
            Message.objects.create(conversation=conversation, sender="ai", text="Assalomu alaykum, Aziza! Blue Hydrangea Garden buketi 750 000 so‘m. Tarkibida moviy gortenziya, oq atirgul va xrizantema bor. Buyurtma uchun avvalgi +998 ** *** ** 67 raqamingiz hali ham amal qiladimi?")
        conversation2, _ = Conversation.objects.get_or_create(customer=customer_map["ig_10004"], branch=central, status="operator")
        if not conversation2.messages.exists():
            Message.objects.create(conversation=conversation2, sender="customer", text="3 pochka oq atirguldan buket qancha bo‘ladi?")
            Message.objects.create(conversation=conversation2, sender="ai", text="3 pochka 60 dona Mondial oq atirgul bo‘ladi. Gullar, o‘ram va florist xizmati bilan taxminiy narx 2 150 000 so‘m. Ismingiz va telefon raqamingizni qoldirsangiz, operatorimiz aniq tasdiqlaydi.")
        lead_rows = [
            (customer_map["ig_10001"], "Moviy gortenziyali buket, bugun kechga", "qualified", "bouquet", 750000, conversation),
            (customer_map["ig_10002"], "Корзина из 35 кремовых роз", "contacted", "basket", 1650000, None),
            (customer_map["ig_10003"], "21 dona qizil Explorer atirguli", "new", "stems", 819000, None),
            (customer_map["ig_10001"], "Onasi uchun pastel katalog buketi", "won", "catalog", 980000, None),
            (customer_map["ig_10004"], "3 pochka oq Mondial atirgulidan buket", "new", "bouquet", 2150000, conversation2),
        ]
        for customer, request, status, kind, price, conv in lead_rows:
            Lead.objects.get_or_create(customer=customer, request_uz=request, defaults={"branch": central, "conversation": conv, "social_post": post if conv == conversation else None, "status": status, "arrangement_type": kind, "estimated_price": price})
        self.stdout.write(self.style.SUCCESS("EuroFlowers seed ma’lumotlari tayyor"))
