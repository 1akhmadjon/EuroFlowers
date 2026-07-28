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


class UserProfile(TimeStampedModel):
    ROLE_CHOICES = [
        ("developer", "Developer"),
        ("admin", "Administrator"),
        ("operator", "Operator"),
        ("florist", "Florist"),
        ("apprentice", "Shogird"),
        ("supervisor", "Nazoratchi"),
        ("warehouse", "Skladchi"),
        ("content", "Kontent menejer"),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="operator")
    language = models.CharField(max_length=2, choices=[("uz", "O‘zbek"), ("ru", "Русский")], default="uz")


class PagePermission(TimeStampedModel):
    DEVELOPER_ONLY_PAGES = ("ai_settings", "integrations")
    PAGE_CHOICES = [
        ("dashboard", "Dashboard"),
        ("inventory", "Sklad"),
        ("catalog", "Katalog"),
        ("crm", "CRM"),
        ("customers", "Mijozlar"),
        ("conversations", "Instagram inbox"),
        ("social_posts", "Postlar"),
        ("notifications", "Bildirishnomalar"),
        ("suppliers", "Postavshiklar"),
        ("florists", "Floristlar"),
        ("attendance", "Ish vaqti"),
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
    slug = models.SlugField(unique=True, null=True, blank=True)
    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    season_start_month = models.PositiveSmallIntegerField(null=True, blank=True)
    season_end_month = models.PositiveSmallIntegerField(null=True, blank=True)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.slug == "":
            self.slug = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name_uz


class FlowerVariant(TimeStampedModel):
    flower = models.ForeignKey(Flower, on_delete=models.PROTECT, related_name="variants")
    name_uz = models.CharField(max_length=120)
    color_uz = models.CharField(max_length=80)
    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    default_stems_per_bunch = models.PositiveIntegerField(default=10)
    minimum_sale_stems = models.PositiveIntegerField(default=1)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.flower.name_uz} · {self.name_uz} · {self.color_uz}"


class Supplier(TimeStampedModel):
    name = models.CharField(max_length=160)
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class StockBatch(TimeStampedModel):
    variant = models.ForeignKey(FlowerVariant, on_delete=models.PROTECT, related_name="batches")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_batches")
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
        constraints = [models.UniqueConstraint(fields=["batch_number"], name="unique_batch_number")]
        ordering = ["received_at", "id"]

    @property
    def remaining_bunches(self):
        if not self.stems_per_bunch:
            return Decimal("0.00")
        return (Decimal(self.remaining_stems) / Decimal(self.stems_per_bunch)).quantize(Decimal("0.01"))

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
    TYPE_CHOICES = [("wrap", "Buket qog‘ozi"), ("basket", "Savat"), ("box", "Quti"), ("other", "Boshqalar")]
    packaging_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    name_uz = models.CharField(max_length=120)
    size = models.CharField(max_length=40, blank=True)
    capacity_min_stems = models.PositiveIntegerField(default=1)
    capacity_max_stems = models.PositiveIntegerField(default=999)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    quantity = models.PositiveIntegerField(default=0)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    @property
    def quantity_label(self):
        return f"{self.quantity} dona"


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


class FloristProfile(TimeStampedModel):
    STAFF_CHOICES = [("florist", "Florist"), ("apprentice", "Shogird")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="florist_profile")
    staff_type = models.CharField(max_length=20, choices=STAFF_CHOICES, default="florist")
    phone = models.CharField(max_length=30, blank=True)
    daily_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    work_start_time = models.TimeField(null=True, blank=True)
    work_end_time = models.TimeField(null=True, blank=True)
    shop_latitude = models.DecimalField(max_digits=16, decimal_places=10, null=True, blank=True)
    shop_longitude = models.DecimalField(max_digits=16, decimal_places=10, null=True, blank=True)
    arrival_radius_meters = models.PositiveIntegerField(default=50)
    departure_radius_meters = models.PositiveIntegerField(default=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["user__first_name", "user__username"]

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class FloristAttendance(TimeStampedModel):
    SOURCE_CHOICES = [("mobile", "Mobile"), ("manual", "Qo‘lda")]
    florist = models.ForeignKey(FloristProfile, on_delete=models.CASCADE, related_name="attendance")
    work_date = models.DateField(default=timezone.localdate)
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    check_in_latitude = models.DecimalField(max_digits=16, decimal_places=10, null=True, blank=True)
    check_in_longitude = models.DecimalField(max_digits=16, decimal_places=10, null=True, blank=True)
    check_out_latitude = models.DecimalField(max_digits=16, decimal_places=10, null=True, blank=True)
    check_out_longitude = models.DecimalField(max_digits=16, decimal_places=10, null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="mobile")
    note = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["florist", "work_date"], name="unique_florist_attendance_date")]
        ordering = ["-work_date", "-id"]


class FloristSalaryEntry(TimeStampedModel):
    SOURCE_CHOICES = [("catalog", "Katalog"), ("custom_catalog", "Custom katalog"), ("daily", "Kunlik"), ("manual", "Qo‘lda")]
    florist = models.ForeignKey(FloristProfile, on_delete=models.CASCADE, related_name="salary_entries")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES)
    work_date = models.DateField(default=timezone.localdate)
    catalog_item = models.ForeignKey("CatalogItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="salary_entries")
    attendance = models.ForeignKey(FloristAttendance, null=True, blank=True, on_delete=models.SET_NULL, related_name="salary_entries")
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_salary_entries")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["florist", "source", "catalog_item"], name="unique_florist_catalog_salary_entry")]
        ordering = ["-work_date", "-id"]


class FloristVolumeRate(TimeStampedModel):
    ARRANGEMENT_CHOICES = [("bouquet", "Buket"), ("basket", "Savat")]
    florist = models.ForeignKey(FloristProfile, null=True, blank=True, on_delete=models.CASCADE, related_name="volume_rates")
    arrangement_type = models.CharField(max_length=20, choices=ARRANGEMENT_CHOICES)
    volume = models.CharField(max_length=80)
    default_stems = models.PositiveIntegerField(default=0)
    florist_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["florist", "arrangement_type", "volume"], name="unique_florist_arrangement_volume_rate")]
        ordering = ["arrangement_type", "volume"]


class Customer(TimeStampedModel):
    name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=30, blank=True, db_index=True)
    language = models.CharField(max_length=2, choices=[("uz", "O‘zbek"), ("ru", "Русский")], default="uz")
    instagram_user_id = models.CharField(max_length=100, unique=True)
    instagram_username = models.CharField(max_length=120, blank=True)
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
    CATALOG_KIND_CHOICES = [("standard", "Standart"), ("custom", "Custom")]
    name_uz = models.CharField(max_length=180)
    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    note = models.TextField(blank=True)
    arrangement_type = models.CharField(max_length=20, choices=[("bouquet", "Buket"), ("basket", "Savat"), ("box", "Quti")])
    catalog_kind = models.CharField(max_length=20, choices=CATALOG_KIND_CHOICES, default="standard")
    volume = models.CharField(max_length=80, blank=True)
    florist = models.ForeignKey(FloristProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name="catalog_items")
    height_cm = models.PositiveIntegerField(null=True, blank=True)
    diameter_cm = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    florist_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("50000"))
    florist_salary_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    calculated_component_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    discount_reason = models.TextField(blank=True)
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


class CatalogMaterialUsage(TimeStampedModel):
    catalog_item = models.ForeignKey(CatalogItem, on_delete=models.CASCADE, related_name="materials")
    packaging = models.ForeignKey(Packaging, on_delete=models.PROTECT, related_name="catalog_usages")
    quantity = models.PositiveIntegerField(default=1)


class CatalogHistory(TimeStampedModel):
    ACTION_CHOICES = [("created", "Qo‘shildi"), ("updated", "O‘zgartirildi"), ("sold", "Sotildi"), ("inventory_deducted", "Sklad kamaytirildi"), ("inventory_restored", "Sklad qaytarildi")]
    catalog_item = models.ForeignKey(CatalogItem, on_delete=models.CASCADE, related_name="history")
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    quantity = models.PositiveIntegerField(default=0)
    listed_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sold_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    discount_reason = models.TextField(blank=True)
    note = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="catalog_history")

    class Meta:
        ordering = ["-created_at", "-id"]


class Conversation(TimeStampedModel):
    STATUS_CHOICES = [("ai", "AI javob bermoqda"), ("operator", "Operatorga o‘tdi"), ("closed", "Yopildi")]
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="conversations")
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
    TYPE_CHOICES = [("stock_pending", "Sklad kamaytirilmagan"), ("low_stock", "Kam qoldiq"), ("lead", "Yangi lead"), ("handoff", "Operator kerak"), ("supplier_stock", "Postavshik kirimi"), ("florist_catalog", "Florist ishi"), ("florist_salary", "Florist ish haqi"), ("attendance", "Keldi-ketdi")]
    target_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications")
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
    summary = models.CharField(max_length=255, blank=True)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80, blank=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_method = models.CharField(max_length=12, blank=True)
    request_path = models.CharField(max_length=255, blank=True)
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


class AISettings(TimeStampedModel):
    openai_model = models.CharField(max_length=80, default="gpt-5-mini")
    system_prompt = models.TextField(default="")
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
