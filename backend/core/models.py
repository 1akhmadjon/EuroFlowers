from decimal import Decimal
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Branch(TimeStampedModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class UserProfile(TimeStampedModel):
    ROLE_CHOICES = [
        ("developer", "Developer"),
        ("admin", "Administrator"),
        ("operator", "Operator"),
        ("florist", "Florist"),
        ("warehouse", "Skladchi"),
        ("content", "Kontent menejer"),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="operator")
    branches = models.ManyToManyField(Branch, blank=True, related_name="staff")
    language = models.CharField(max_length=2, choices=[("uz", "O‘zbek"), ("ru", "Русский")], default="uz")


class PagePermission(TimeStampedModel):
    DEVELOPER_ONLY_PAGES = ("ai_settings", "integrations", "audit")
    PAGE_CHOICES = [
        ("dashboard", "Dashboard"),
        ("inventory", "Sklad"),
        ("catalog", "Katalog"),
        ("crm", "CRM"),
        ("customers", "Mijozlar"),
        ("conversations", "Instagram inbox"),
        ("social_posts", "Postlar"),
        ("notifications", "Bildirishnomalar"),
        ("settings", "Sozlamalar"),
        ("ai_settings", "AI sozlamalari"),
        ("integrations", "Integratsiyalar"),
        ("users", "Jamoa"),
        ("mini_app", "Mini app"),
        ("audit", "Audit"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="page_permissions")
    page = models.CharField(max_length=40, choices=PAGE_CHOICES)
    can_view = models.BooleanField(default=False)
    can_control = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "page"], name="unique_user_page_permission")]
        ordering = ["user_id", "page"]


class Flower(TimeStampedModel):
    name_uz = models.CharField(max_length=120)
    name_ru = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    season_start_month = models.PositiveSmallIntegerField(null=True, blank=True)
    season_end_month = models.PositiveSmallIntegerField(null=True, blank=True)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name_uz


class FlowerVariant(TimeStampedModel):
    flower = models.ForeignKey(Flower, on_delete=models.PROTECT, related_name="variants")
    name_uz = models.CharField(max_length=120)
    name_ru = models.CharField(max_length=120)
    color_uz = models.CharField(max_length=80)
    color_ru = models.CharField(max_length=80)
    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    default_stems_per_bunch = models.PositiveIntegerField(default=10)
    minimum_sale_stems = models.PositiveIntegerField(default=1)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.flower.name_uz} · {self.name_uz} · {self.color_uz}"


class StockBatch(TimeStampedModel):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="stock_batches")
    variant = models.ForeignKey(FlowerVariant, on_delete=models.PROTECT, related_name="batches")
    batch_number = models.CharField(max_length=40)
    received_at = models.DateField(default=timezone.localdate)
    height_cm = models.PositiveIntegerField()
    height_from_cm = models.PositiveIntegerField(null=True, blank=True)
    height_to_cm = models.PositiveIntegerField(null=True, blank=True)
    stems_per_bunch = models.PositiveIntegerField(default=10)
    received_stems = models.PositiveIntegerField()
    remaining_stems = models.PositiveIntegerField()
    cost_per_stem = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    sale_price_per_stem = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    sale_price_per_bunch = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    minimum_sale_stems = models.PositiveIntegerField(default=1)
    image_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "batch_number"], name="unique_branch_batch")]
        ordering = ["received_at", "id"]

    @property
    def remaining_bunches(self):
        return self.remaining_stems // self.stems_per_bunch

    @property
    def height_label(self):
        if self.height_from_cm and self.height_to_cm and self.height_from_cm != self.height_to_cm:
            return f"{self.height_from_cm}-{self.height_to_cm} sm"
        height = self.height_from_cm or self.height_to_cm or self.height_cm
        return f"{height} sm"

    @property
    def stock_value(self):
        return self.remaining_stems * self.sale_price_per_stem

    def __str__(self):
        return f"{self.batch_number} · {self.variant}"


class StockMovement(TimeStampedModel):
    TYPE_CHOICES = [
        ("in", "Kirim"),
        ("out", "Chiqim"),
        ("adjustment", "Tuzatish"),
        ("waste", "Hisobdan chiqarish"),
        ("transfer_out", "Filialdan chiqim"),
        ("transfer_in", "Filialga kirim"),
    ]
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    quantity_stems = models.IntegerField()
    quantity_bunches = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.PositiveBigIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_movements")

    class Meta:
        ordering = ["-created_at"]


class Packaging(TimeStampedModel):
    TYPE_CHOICES = [("wrap", "O‘ram"), ("basket", "Savat"), ("box", "Quti"), ("accessory", "Aksessuar")]
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="packaging")
    packaging_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    name_uz = models.CharField(max_length=120)
    name_ru = models.CharField(max_length=120)
    size = models.CharField(max_length=40, blank=True)
    capacity_min_stems = models.PositiveIntegerField(default=1)
    capacity_max_stems = models.PositiveIntegerField(default=999)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=0)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)


class PackagingMovement(TimeStampedModel):
    TYPE_CHOICES = [
        ("in", "Kirim"),
        ("out", "Chiqim"),
        ("adjustment", "Tuzatish"),
        ("waste", "Hisobdan chiqarish"),
        ("transfer_out", "Filialdan chiqim"),
        ("transfer_in", "Filialga kirim"),
    ]
    packaging = models.ForeignKey(Packaging, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    quantity = models.IntegerField()
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.PositiveBigIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="packaging_movements")

    class Meta:
        ordering = ["-created_at"]


class Customer(TimeStampedModel):
    name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=30, blank=True, db_index=True)
    language = models.CharField(max_length=2, choices=[("uz", "O‘zbek"), ("ru", "Русский")], default="uz")
    instagram_user_id = models.CharField(max_length=100, unique=True)
    instagram_username = models.CharField(max_length=120, blank=True)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    notes = models.TextField(blank=True)
    is_blocked = models.BooleanField(default=False)

    @property
    def masked_phone(self):
        digits = "".join(filter(str.isdigit, self.phone))
        if len(digits) < 4:
            return self.phone
        return f"+{digits[:3]} ** *** ** {digits[-2:]}"

    def __str__(self):
        return self.name or self.instagram_username or self.instagram_user_id


class SocialPost(TimeStampedModel):
    TYPE_CHOICES = [("post", "Post"), ("reel", "Reel"), ("story", "Story"), ("ad", "Target")]
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="social_posts")
    post_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    media_id = models.CharField(max_length=120, unique=True)
    permalink = models.URLField(blank=True)
    instagram_username = models.CharField(max_length=120, blank=True)
    story_share_id = models.CharField(max_length=120, blank=True)
    webhook_story_id = models.TextField(blank=True)
    webhook_story_url = models.TextField(blank=True)
    title_uz = models.CharField(max_length=180)
    title_ru = models.CharField(max_length=180)
    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    flower_count = models.PositiveIntegerField(default=0)
    image_url = models.URLField(blank=True)
    is_targeted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)


class CatalogItem(TimeStampedModel):
    STATUS_CHOICES = [("draft", "Qoralama"), ("available", "Sotuvda"), ("reserved", "Band"), ("sold", "Sotildi"), ("archived", "Arxiv")]
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="catalog_items")
    name_uz = models.CharField(max_length=180)
    name_ru = models.CharField(max_length=180)
    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    arrangement_type = models.CharField(max_length=20, choices=[("bouquet", "Buket"), ("basket", "Savat"), ("box", "Quti")])
    height_cm = models.PositiveIntegerField(null=True, blank=True)
    diameter_cm = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    florist_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("50000"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    image_url = models.URLField(blank=True)
    instagram_story_url = models.URLField(blank=True)
    social_post = models.ForeignKey(SocialPost, null=True, blank=True, on_delete=models.SET_NULL, related_name="catalog_items")
    quantity_total = models.PositiveIntegerField(default=1)
    quantity_sold = models.PositiveIntegerField(default=0)
    quantity_stock_deducted = models.PositiveIntegerField(default=0)
    sold_at = models.DateTimeField(null=True, blank=True)
    stock_deducted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="catalog_items")

    class Meta:
        ordering = ["-created_at"]


class CatalogComposition(TimeStampedModel):
    catalog_item = models.ForeignKey(CatalogItem, on_delete=models.CASCADE, related_name="composition")
    stock_batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="catalog_usages")
    quantity_stems = models.PositiveIntegerField()
    quantity_bunches = models.DecimalField(max_digits=10, decimal_places=2, default=0)


class Conversation(TimeStampedModel):
    STATUS_CHOICES = [("ai", "AI javob bermoqda"), ("operator", "Operatorga o‘tdi"), ("closed", "Yopildi")]
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="conversations")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="conversations")
    social_post = models.ForeignKey(SocialPost, null=True, blank=True, on_delete=models.SET_NULL, related_name="conversations")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ai")
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_conversations")
    last_message_at = models.DateTimeField(default=timezone.now)
    ai_summary = models.TextField(blank=True)
    ai_paused_until = models.DateTimeField(null=True, blank=True)
    ai_pause_reason = models.CharField(max_length=255, blank=True)
    ai_reply_started_for_message = models.ForeignKey("Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ai_reply_started_at = models.DateTimeField(null=True, blank=True)
    ai_replied_to_message = models.ForeignKey("Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-last_message_at"]


class Message(TimeStampedModel):
    SENDER_CHOICES = [("customer", "Mijoz"), ("ai", "AI"), ("operator", "Operator"), ("system", "Tizim")]
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=20, choices=SENDER_CHOICES)
    text = models.TextField()
    instagram_message_id = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]


class LeadStatus(TimeStampedModel):
    key = models.SlugField(max_length=40, unique=True)
    name_uz = models.CharField(max_length=120)
    name_ru = models.CharField(max_length=120, blank=True)
    color = models.CharField(max_length=40, default="#64748b")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.name_uz


class Lead(TimeStampedModel):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="leads")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="leads")
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads")
    social_post = models.ForeignKey(SocialPost, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads")
    status = models.CharField(max_length=40, default="new")
    request_uz = models.TextField()
    request_ru = models.TextField(blank=True)
    arrangement_type = models.CharField(max_length=20, choices=[("bouquet", "Buket"), ("basket", "Savat"), ("stems", "Donalab"), ("catalog", "Katalog")], blank=True)
    estimated_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    florist_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    desired_date = models.DateField(null=True, blank=True)
    delivery_at = models.DateTimeField(null=True, blank=True)
    recall_at = models.DateTimeField(null=True, blank=True)
    recall_sent_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads")
    source = models.CharField(max_length=30, default="instagram")
    details = models.JSONField(default=dict, blank=True)
    stock_deducted_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.DecimalField(max_digits=20, decimal_places=6, default=0, db_index=True)

    class Meta:
        ordering = ["status", "sort_order", "-created_at", "id"]


class LeadStockUsage(TimeStampedModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="stock_usage")
    stock_batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="lead_usages")
    quantity_stems = models.PositiveIntegerField()
    quantity_bunches = models.DecimalField(max_digits=10, decimal_places=2, default=0)


class LeadPackagingUsage(TimeStampedModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="packaging_usage")
    packaging = models.ForeignKey(Packaging, on_delete=models.PROTECT, related_name="lead_usages")
    quantity = models.PositiveIntegerField(default=1)


class LeadCatalogUsage(TimeStampedModel):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="catalog_usage")
    catalog_item = models.ForeignKey(CatalogItem, on_delete=models.PROTECT, related_name="lead_usages")
    quantity = models.PositiveIntegerField(default=1)


class Notification(TimeStampedModel):
    TYPE_CHOICES = [("stock_pending", "Sklad kamaytirilmagan"), ("low_stock", "Kam qoldiq"), ("lead", "Yangi lead"), ("handoff", "Operator kerak")]
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="notifications")
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    title_uz = models.CharField(max_length=180)
    title_ru = models.CharField(max_length=180)
    body_uz = models.TextField(blank=True)
    body_ru = models.TextField(blank=True)
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.PositiveBigIntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80, blank=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class BusinessSettings(TimeStampedModel):
    default_florist_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("50000"))
    min_sale_reminder_uz = models.CharField(max_length=255, default="Bu guldan minimal sotilish sonini mijozga ayting.")
    min_sale_reminder_ru = models.CharField(max_length=255, default="Сообщайте клиенту минимальное количество продажи для этого цветка.")
    approximate_price_wording_uz = models.CharField(max_length=255, default="Narx taxminiy, operator aniq tasdiqlab beradi.")
    approximate_price_wording_ru = models.CharField(max_length=255, default="Цена ориентировочная, оператор уточнит итоговую стоимость.")
    handoff_rules_uz = models.TextField(blank=True, default="Telefon raqam olingandan keyin lead yarating va operatorga o‘tkazing.")
    handoff_rules_ru = models.TextField(blank=True, default="После получения телефона создайте лид и передайте оператору.")
    working_hours = models.JSONField(default=dict, blank=True)


DEFAULT_AI_SYSTEM_PROMPT = """Sen EuroFlowers Premium gul do‘konining Instagram va Telegramdagi AI sotuvchisian.

Senga har safar mijoz haqidagi REAL_CONTEXT_JSON, mijoz bilan to‘liq conversation tarixi va function calling beriladi. Qarorni faqat shu system prompt, conversation va kerakli function call natijalariga qarab chiqar. Ma'lumotni o‘ylab topma.

Mijoz qaysi tilda yozsa, o‘sha tilda javob ber. O‘zbek lotin bo‘lsa lotinda, o‘zbek kiril bo‘lsa kirilda, ruscha bo‘lsa ruschada davom et. Inglizcha javob berma.

Javoblar qisqa, tabiiy va chiroyli bo‘lsin. Odatda 2-4 qator yetadi. Bitta xabarda bitta asosiy savol ber. Reply matnida qavs, qo‘shtirnoq va ikki nuqtani ishlatma. “Bitta javob bering”, “tasdiqlang”, “qabul qilamizmi” kabi mijozga yoqmaydigan iboralarni yozma. ID, service ma'lumotlari va ichki tool nomlarini mijozga yozma. Katalog ID, batch ID, lead ID ni hech qachon mijozga ko‘rsatma.

Har xabarda salomlashma. Faqat yangi suhbatda yoki 24 soatdan keyingi yangi murojaatda: “Assalomu aleykum, EuroFlowers Premium gul do‘kon AI menejeriman. Sizga qanday gul kerak edi?” deb boshlash mumkin.

Qo‘lingdagi functionlar:
client_leads_get - mijozning avvalgi lead/buyurtmalarini ko‘rish.
client_lead_create - ism va telefon olingandan keyin CRM lead yaratish.
client_lead_edit - shu mijozning leadini tahrirlash.
get_catalog - hozir sotuvdagi tayyor katalog buket/savatlarini ko‘rish.
get_stock - custom buket/savat yasatish uchun skladdagi gullarni ko‘rish.
get_flower_variant_info - gul navi, rangi, farqi, izohi va mavjud stock ma'lumotlarini ko‘rish.
send_catalog_image - mijoz tanlagan katalog mahsulotining rasmini yuborish.

Tool ishlatish qoidalari:
Katalog, stock, gul navi, rasm yoki lead haqida real ma'lumot kerak bo‘lsa, javob yozishdan oldin tegishli functionni chaqir.
“Tayyor buketlar bormi”, “qanaqa gullar bor”, “katalogni ko‘rsating”, “vitrinada nima bor” desa get_catalog chaqir va faqat nom va narx yoz. Qancha dona borligini va tarkibini mijoz so‘ramasa aytma.
Mijoz katalogdan aniq mahsulot tanlasa va rasm kerak bo‘lsa send_catalog_image chaqir. Rasm yuborilgandan keyin “rasmni yuboraymi”, “rasmini ko‘rmoqchimisiz”, “mana rasmi” deb ortiqcha yozma; mahsulot narxi va keyingi kerakli savolni qisqa yoz.
Mijoz “yasatmoqchiman”, “yig‘diraman”, “buket qilib berasizmi”, “savat qilib berasizmi” desa get_stock chaqir. Katalogga adashib o‘tma. Kerak bo‘lsa get_flower_variant_info ham chaqir.
Mijoz custom buket yoki savat narxini so‘rasa, avval get_stock chaqir yoki oldingi conversation metadata ichidagi get_stock natijasidan foydalan. price_per_stem va price_per_bunch tool natijasida bor. Narxni aytmasdan ism va raqam so‘rama.
Gulning o‘zi dona yoki pochka holida odatda sotilmaydi. Mijoz shuni so‘rasa: “Ko‘p hollarda gulning o‘zi alohida sotilmaydi, buket yoki savat qilib tayyorlab beramiz. Ism va raqamingizni yozib yuboraolasizmi? Operatorlarimiz aniq ma'lumot beradilar.” mazmunida javob ber va lead yaratish uchun kontakt ol.

Narx qoidalari:
Katalog, story, post va reel’dagi tayyor mahsulot narxi aniq. “Taxminan” demagin.
Custom buket yoki savat yasatishda narx taxminiy. Gul narxiga florist haqi 50 000 so‘mdan boshlanadi, obyomga qarab o‘zgaradi deb ayt.
Custom narx hisoblashda tool natijasidagi narxdan foydalan. Masalan 10 dona gul bo‘lsa 10 × price_per_stem + kamida 50 000 florist haqi qilib umumiy taxminiy narxni ayt. Pochka so‘ralsa price_per_bunch bilan hisobla.
Story/post/reel reply qilingan tayyor katalog mahsulotida florist haqini alohida aytma.
Chegirma, arzonlashtirish yoki “nega qimmat” desa bahslashma va uzun tushuntirma yozma: “Xohlasangiz operatorlarimiz sizga arzonroq variantlar bilan tanishtiradilar. Ism va raqamingizni yozib yuboraolasizmi?” deb javob ber.

Buyurtma qabul qilish:
Lead faqat mijoz aniq buyurtma qilmoqchi bo‘lsa va ism + telefon olingandan keyin yaratiladi.
Telefon +998 bilan ham, 90 123 45 67 kabi +998siz ham kelishi mumkin. Juda qisqa yoki tushunarsiz bo‘lsa qayta so‘ra.
Yangi mijozdan ism va raqamni so‘ra. Eski mijozda telefon bo‘lsa, maskalangan raqamni tasdiqlat.
Katalog buyurtmasida ortiqcha o‘lcham, paket, qaysi guldan yasaymiz deb so‘rama. Faqat yetkazib berishmi yoki kelib olib ketishmi, kerak bo‘lsa sana/vaqt/manzil, ism va raqamni ol.
Custom buyurtmada qaysi guldan qancha, buketmi yoki savatmi, kerak bo‘lsa rangini aniqlab ol. Ko‘p savol bermay, yetishmayotgan bitta muhim savolni ber.
Mijoz narxni so‘ragan bo‘lsa avval umumiy narxni chiroyli ko‘rsat. Keyin “Shu variantdan buyurtma qilasizmi? Ism va raqamingizni yozib yuboring, iltimos.” mazmunida yoz.
client_lead_create payloadida request_text juda aniq bo‘lsin: mahsulot nomi, soni, katalog/custom turi, buket/savat, yetkazib berish yoki kelib olish, sana/vaqt/manzil, mijoz izohi.
Lead tool orqali yaratilgandan keyin lead_ready false bo‘lsin, lekin reply’da mijozga buyurtma qabul qilinganini chiroyli ayt.

Manzil va yetkazish:
Manzil so‘ralsa: Bobur ko‘chasi 10. Lokatsiya: https://yandex.uz/maps/-/CTbofDyT. Orientir: Next Mall dan o‘tgandan keyin o‘ng qo‘lda. Ish vaqti 24/7.
Mijoz kelib olishni tanlasa, ism va raqam olingandan keyingi yakuniy xabarda manzilni alohida qatorlarda ber.
“Borib olib ketasizmi” demagin, “kelib olib ketasizmi” degin.
Yetkazib berish so‘ralsa: Toshkent bo‘yicha gullarni Yandex Dostavka orqali chiqarib yuboramiz, narxni operatorlarimiz manzilga qarab aniqlashtiradi.

Gul haqida savollar:
Mijoz gul navi, nega qimmatligi, farqi, qayerniki, rangi yoki sifati haqida so‘rasa get_flower_variant_info chaqir. Description bor bo‘lsa undan foydalan.
Mijoz gulning dona narxini so‘rasa get_stock chaqir. Variant info topilmasa ham stock tool orqali narxni tekshir.
Aniq gul turi bormi desa darrov “yo‘q” dema; avval vitrinadagi tayyor buketlardan qaraymi yoki shu guldan buket/savat yig‘dirib beraylikmi deb aniqlashtir.
Sklad haqida gapirganda “skladimizda” degin, “ombor” demagin.

Mavzudan tashqari savollar:
Gul, buket, savat, manzil, yetkazish va buyurtmadan boshqa mavzularga javob berma. Kod, siyosat, shaxsiy maslahat kabi savollarda qisqa qilib gul mavzusiga qaytar yoki operatorlarimiz bog‘lanishi uchun ism-raqam so‘ra.

JSON qaytarish:
Doim sales_reply JSON schema bo‘yicha javob qaytar.
reply - mijozga yuboriladigan matn.
detected_language - uz yoki ru.
customer_name va phone - mijoz bergan real qiymat bo‘lsa.
lead_ready faqat eski fallback uchun; tool orqali lead yaratsang false qil.
catalog_items faqat katalog mahsuloti buyurtma qilinsa catalog_name va quantity bilan.
stock_items faqat custom buyurtmada batch_id, quantity_stems, quantity_bunches bilan.
handoff odatda false."""


class AISettings(TimeStampedModel):
    openai_model = models.CharField(max_length=80, default="gpt-5-mini")
    system_prompt = models.TextField(default=DEFAULT_AI_SYSTEM_PROMPT)
    temperature = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.20"))
    is_active = models.BooleanField(default=True)


class IntegrationSettings(TimeStampedModel):
    instagram_access_token = models.TextField(blank=True)
    instagram_account_id = models.CharField(max_length=120, blank=True)
    instagram_business_id = models.CharField(max_length=120, blank=True)
    instagram_verify_token = models.CharField(max_length=180, blank=True)
    telegram_bot_token = models.CharField(max_length=180, blank=True)
    telegram_group_chat_id = models.CharField(max_length=120, blank=True)
    extra = models.JSONField(default=dict, blank=True)


class InstagramSettings(TimeStampedModel):
    account_username = models.CharField(max_length=120, blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    auto_reply_dm = models.BooleanField(default=True)
    auto_reply_post_reply = models.BooleanField(default=True)
    auto_reply_story_reply = models.BooleanField(default=True)


class InstagramWebhookEvent(TimeStampedModel):
    event_type = models.CharField(max_length=80, blank=True)
    sender_id = models.TextField(blank=True)
    recipient_id = models.TextField(blank=True)
    message_id = models.TextField(blank=True)
    text = models.TextField(blank=True)
    media_id = models.TextField(blank=True)
    story_id = models.TextField(blank=True)
    story_url = models.TextField(blank=True)
    postback_referral = models.JSONField(default=dict, blank=True)
    extracted = models.JSONField(default=dict, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
