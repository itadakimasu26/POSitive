import uuid
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone


class Product(models.Model):
    CATEGORY_CHOICES = [
        ("Cafe", "Cafe products"),
        ("Coffee and Tea", "Coffee and tea"),
        ("Drinks", "Drinks"),
        ("Pastry", "Pastry"),
        ("Meals", "Meals"),
        ("Beverages", "Beverages"),
        ("Snacks", "Snacks and confectionery"),
        ("Canned Goods", "Canned and packaged goods"),
        ("Pantry", "Pantry and cooking essentials"),
        ("Fresh and Frozen", "Fresh and frozen goods"),
        ("Household", "Household supplies"),
        ("Clothing", "Clothing"),
        ("Footwear", "Footwear"),
        ("Fashion Accessories", "Fashion accessories"),
        ("Gadgets", "Gadgets"),
        ("Mobile Accessories", "Mobile accessories"),
        ("Electronics", "Electronics"),
        ("Pet Food", "Pet food"),
        ("Pet Supplies", "Pet supplies"),
        ("Hardware", "Hardware"),
        ("Tools", "Tools"),
        ("General Merchandise", "General merchandise"),
        ("Cosmetics", "Cosmetics"),
        ("Personal Care", "Personal care"),
        ("Other", "Other"),
    ]

    store = models.ForeignKey("StoreSettings", on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=140)
    category = models.CharField(max_length=60, default="Other")
    barcode = models.CharField(max_length=50, blank=True)
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    stock = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=10)
    picture_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Optional hosted product photo. A name-based placeholder is used when this is blank.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=Q(cost__gte=0), name="product_cost_gte_zero"),
            models.CheckConstraint(condition=Q(price__gte=0), name="product_price_gte_zero"),
            models.UniqueConstraint(fields=["store", "barcode"], name="unique_store_barcode"),
        ]

    def save(self, *args, **kwargs):
        self.category = " ".join((self.category or "Other").split()) or "Other"
        if not self.barcode:
            self.barcode = f"POS-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)
        if self.store_id:
            preset_names = set(self.store.default_product_category_names)
            ProductCategory.objects.get_or_create(
                store_id=self.store_id,
                name=self.category,
                defaults={"is_custom": self.category not in preset_names},
            )

    @property
    def is_low_stock(self):
        return self.stock <= self.low_stock_threshold

    @property
    def emoji(self):
        return {
            "Cafe": "☕", "Coffee and Tea": "🫖", "Drinks": "☕", "Beverages": "🥤", "Pastry": "🥐", "Meals": "🍽️",
            "Snacks": "🍫", "Canned Goods": "🥫", "Pantry": "🛒", "Fresh and Frozen": "❄️",
            "Household": "🧹", "Clothing": "👕", "Footwear": "👟", "Fashion Accessories": "👜",
            "Gadgets": "📱", "Mobile Accessories": "🔌", "Electronics": "💻", "Pet Food": "🐾",
            "Pet Supplies": "🐕", "Hardware": "🔩", "Tools": "🛠️", "General Merchandise": "🏪",
            "Cosmetics": "💄", "Personal Care": "🧴", "Other": "📦",
        }.get(self.category, "📦")

    @property
    def color_class(self):
        return {"Drinks": "mint", "Pastry": "amber", "Meals": "coral", "Other": "blue"}.get(self.category, "blue")

    @property
    def picture_emoji(self):
        name = self.name.casefold()
        keyword_icons = (
            (("matcha", "green tea"), "🍵"),
            (("coffee", "latte", "espresso", "cappuccino", "americano", "mocha"), "☕"),
            (("tea",), "🫖"),
            (("juice", "soda", "softdrink", "soft drink", "water", "milk", "shake"), "🥤"),
            (("croissant", "bread", "bun", "pastry"), "🥐"),
            (("cake", "cupcake"), "🍰"),
            (("cookie", "biscuit"), "🍪"),
            (("donut", "doughnut"), "🍩"),
            (("pizza",), "🍕"),
            (("burger",), "🍔"),
            (("noodle", "pasta", "spaghetti"), "🍜"),
            (("rice", "meal", "lunch", "dinner"), "🍱"),
            (("shirt", "blouse", "top", "dress", "jacket"), "👕"),
            (("shoe", "sneaker", "slipper", "sandal"), "👟"),
            (("bag", "purse", "wallet"), "👜"),
            (("phone", "mobile", "tablet"), "📱"),
            (("laptop", "computer"), "💻"),
            (("headphone", "earphone", "speaker"), "🎧"),
            (("cable", "charger", "adapter"), "🔌"),
            (("dog", "cat", "pet"), "🐾"),
            (("lipstick", "makeup", "cosmetic"), "💄"),
            (("soap", "shampoo", "lotion"), "🧴"),
            (("hammer", "tool", "drill"), "🛠️"),
        )
        for keywords, icon in keyword_icons:
            if any(keyword in name for keyword in keywords):
                return icon
        return self.emoji

    def __str__(self):
        return f"{self.name} ({self.store.business_name})"


class Sale(models.Model):
    class Source(models.TextChoices):
        ONLINE = "Online", "Online checkout"
        OFFLINE_CSV = "Offline CSV", "Offline recovery CSV"

    class OrderType(models.TextChoices):
        RETAIL = "Retail", "Retail sale"
        DINE_IN = "Dine-in", "Dine-in"
        TAKE_OUT = "Take-out", "Take-out"

    PAYMENT_CHOICES = [("Cash", "Cash"), ("GCash", "GCash"), ("Card", "Card")]
    STATUS_CHOICES = [
        ("Completed", "Completed"),
        ("Refunded", "Refunded"),
        ("Voided", "Voided"),
    ]

    receipt_number = models.CharField(max_length=40, unique=True)
    store = models.ForeignKey("StoreSettings", on_delete=models.PROTECT, related_name="sales")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales")
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.SET_NULL,
        related_name="sales",
        blank=True,
        null=True,
    )
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default="Cash")
    order_type = models.CharField(max_length=12, choices=OrderType.choices, default=OrderType.RETAIL)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    loyalty_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    service_charge_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    service_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    loyalty_points_earned = models.PositiveIntegerField(default=0)
    loyalty_points_redeemed = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Completed")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.ONLINE)
    external_order_id = models.CharField(max_length=64, blank=True)
    offline_notes = models.CharField(max_length=240, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approved_sales",
        blank=True,
        null=True,
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reversed_sales",
        blank=True,
        null=True,
    )
    reversal_reason = models.CharField(max_length=240, blank=True)
    reversed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "external_order_id"],
                condition=~Q(external_order_id=""),
                name="unique_store_offline_order_id",
            ),
        ]

    def __str__(self):
        return f"{self.receipt_number} — {self.store.business_name}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    product_name = models.CharField(max_length=140)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        self.line_total = Decimal(self.quantity) * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} × {self.product_name}"


class StoreSettings(models.Model):
    class StoreType(models.TextChoices):
        CAFE = "Cafe", "Cafe"
        GROCERY = "Grocery", "Minimart / specialty grocery"
        FASHION = "Fashion", "Clothing / footwear boutique"
        ELECTRONICS = "Electronics", "Gadget / accessory shop"
        PET_SUPPLIES = "Pet Supplies", "Pet-supply store"
        HARDWARE = "Hardware", "Hardware / general merchandise"
        BEAUTY = "Beauty", "Cosmetics / personal care retailer"
        GENERAL = "General Retail", "General retail / other"

    CATEGORY_PRESETS = {
        "Cafe": ("Coffee and Tea", "Drinks", "Pastry", "Meals", "Snacks", "Other"),
        "Grocery": (
            "Beverages", "Snacks", "Canned Goods", "Pantry", "Fresh and Frozen", "Household", "Other",
        ),
        "Fashion": ("Clothing", "Footwear", "Fashion Accessories", "Other"),
        "Electronics": ("Gadgets", "Mobile Accessories", "Electronics", "Other"),
        "Pet Supplies": ("Pet Food", "Pet Supplies", "Other"),
        "Hardware": ("Hardware", "Tools", "General Merchandise", "Other"),
        "Beauty": ("Cosmetics", "Personal Care", "Other"),
        "General Retail": ("General Merchandise", "Other"),
    }

    PLAN_CHOICES = [("Trial", "Trial"), ("Starter", "Starter"), ("Pro", "Pro")]
    PLAN_PRICES = {"Trial": Decimal("0.00"), "Starter": Decimal("399.00"), "Pro": Decimal("799.00")}
    STATUS_CHOICES = [("Active", "Active"), ("Suspended", "Suspended")]
    STAFF_LIMITS = {"Trial": 0, "Starter": 2, "Pro": 10}
    ADMIN_LIMITS = {"Trial": 1, "Starter": 1, "Pro": 3}
    TRIAL_LENGTH_DAYS = 30

    business_name = models.CharField(max_length=140, default="Sunrise Store")
    store_id = models.CharField(max_length=30, unique=True)
    store_type = models.CharField(
        max_length=24,
        choices=StoreType.choices,
        default=StoreType.GENERAL,
        help_text="Controls the product categories initially available to this store.",
    )
    contact_number = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=12,
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
    )
    service_charge_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        help_text="Cafe stores only. Percentage added to dine-in orders after discounts; take-out orders are not charged.",
    )
    active_plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="Trial")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Active")
    subscription_start = models.DateField(blank=True, null=True)
    subscription_end = models.DateField(blank=True, null=True)
    receipt_after_sale = models.BooleanField(default=True)
    low_stock_alerts = models.BooleanField(default=True)
    barcode_scanning = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["business_name", "store_id"]
        verbose_name = "store"
        verbose_name_plural = "stores"
        constraints = [
            models.CheckConstraint(
                condition=Q(tax_rate__gte=0, tax_rate__lte=100),
                name="store_tax_rate_valid",
            ),
            models.CheckConstraint(
                condition=Q(service_charge_rate__gte=0, service_charge_rate__lte=100),
                name="store_service_charge_rate_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.subscription_start and self.subscription_end and self.subscription_end < self.subscription_start:
            raise ValidationError({"subscription_end": "The subscription end date cannot be before its start date."})
        if not self.supports_dining and self.service_charge_rate:
            raise ValidationError({
                "service_charge_rate": "Dine-in service charges are available only for Cafe stores."
            })
        self._validate_multi_store_plan()

    def _validate_multi_store_plan(self):
        """Prevent a shared-login store from leaving Pro.

        Membership validation protects new assignments. This store-level guard
        protects the inverse operation: changing a Pro branch to a lower plan
        while any of its active users still belongs to another store.
        """
        if not self.pk or self.is_pro:
            return
        assigned_user_ids = self.memberships.filter(active=True).values_list("user_id", flat=True)
        if StoreMembership.objects.filter(
            user_id__in=assigned_user_ids,
            active=True,
        ).exclude(store_id=self.pk).exists():
            raise ValidationError({
                "active_plan": "This store has users assigned to other stores. Remove those shared assignments before changing from Pro."
            })

    def save(self, *args, **kwargs):
        previous_store_type = None
        if self.pk:
            previous_store_type = type(self).objects.filter(pk=self.pk).values_list("store_type", flat=True).first()
        # Dining concepts do not apply to retail categories. Normalizing here
        # also protects direct model writes that do not call full_clean().
        if not self.supports_dining:
            self.service_charge_rate = Decimal("0.00")
        self._validate_multi_store_plan()
        self.store_id = self.store_id.strip().upper()
        super().save(*args, **kwargs)
        if previous_store_type != self.store_type or not self.product_categories.exists():
            self.sync_product_categories()

    @property
    def default_product_category_names(self):
        return self.CATEGORY_PRESETS.get(self.store_type, self.CATEGORY_PRESETS[self.StoreType.GENERAL])

    def sync_product_categories(self):
        desired_names = set(self.default_product_category_names)
        for name in self.default_product_category_names:
            ProductCategory.objects.get_or_create(
                store=self,
                name=name,
                defaults={"is_custom": False},
            )
        for category in self.product_categories.filter(is_custom=False).exclude(name__in=desired_names):
            if not self.products.filter(category__iexact=category.name).exists():
                category.delete()

    @property
    def subscription_status(self):
        if self.status == "Suspended":
            return "Suspended"
        if self.subscription_end and self.subscription_end < timezone.localdate():
            return "Expired"
        return "Active"

    @property
    def is_subscription_active(self):
        return self.subscription_status == "Active"

    @property
    def days_until_expiry(self):
        if not self.subscription_end:
            return None
        return (self.subscription_end - timezone.localdate()).days

    @property
    def staff_limit(self):
        return self.STAFF_LIMITS[self.active_plan]

    @property
    def administrator_limit(self):
        return self.ADMIN_LIMITS[self.active_plan]

    @property
    def is_pro(self):
        return self.active_plan == "Pro"

    @property
    def supports_dining(self):
        """Return whether checkout should offer dine-in and take-out service."""
        return self.store_type == self.StoreType.CAFE

    @property
    def active_staff_count(self):
        return self.memberships.filter(role=StoreMembership.Role.STAFF, active=True).count()

    @property
    def active_administrator_count(self):
        return self.memberships.filter(role=StoreMembership.Role.ADMINISTRATOR, active=True).count()

    def __str__(self):
        return f"{self.business_name} ({self.store_id})"


class SubscriptionExtensionRequest(models.Model):
    class PaymentType(models.TextChoices):
        GCASH = "GCash", "GCash"
        MAYA_CARD = "Maya / card", "Maya / card"
        BANK_TRANSFER = "Bank transfer", "Bank transfer"
        CASH = "Cash", "Cash"

    class Status(models.TextChoices):
        NEW = "New", "New"
        IN_REVIEW = "In review", "In review"
        COMPLETED = "Completed", "Completed"
        DECLINED = "Declined", "Declined"

    PLAN_CHOICES = [
        ("Starter", "Starter"),
        ("Pro", "Pro"),
    ]

    store = models.ForeignKey(
        StoreSettings,
        on_delete=models.CASCADE,
        related_name="subscription_extension_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="subscription_extension_requests",
        blank=True,
        null=True,
    )
    requested_plan = models.CharField(max_length=20, choices=PLAN_CHOICES)
    payment_type = models.CharField(max_length=30, choices=PaymentType.choices)
    comments = models.TextField(blank=True, max_length=1000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    admin_notes = models.TextField(blank=True)
    email_sent_at = models.DateTimeField(blank=True, null=True)
    email_error = models.CharField(max_length=500, blank=True)
    status_email_sent_at = models.DateTimeField(blank=True, null=True)
    status_email_error = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_subscription_extension_requests",
        blank=True,
        null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    activated_at = models.DateTimeField(blank=True, null=True)
    extension_start = models.DateField(blank=True, null=True)
    extension_end = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "subscription extension request"
        verbose_name_plural = "subscription extension requests"
        constraints = [
            models.UniqueConstraint(
                fields=["store"],
                condition=Q(status__in=["New", "In review"]),
                name="one_open_subscription_extension_request_per_store",
            ),
        ]

    @staticmethod
    def one_month_end(start):
        """Return the inclusive end date of a one-calendar-month term."""
        year = start.year + (start.month // 12)
        month = (start.month % 12) + 1
        days_in_next_month = monthrange(year, month)[1]
        if start.day > days_in_next_month:
            return date(year, month, days_in_next_month)
        return date(year, month, start.day) - timedelta(days=1)

    def activate_subscription(self):
        """Apply this completed request exactly once and return whether it ran."""
        if not self.pk:
            raise ValidationError("Save the extension request before activating it.")
        if self.status != self.Status.COMPLETED:
            raise ValidationError({"status": "Only a completed request can activate a subscription."})

        with transaction.atomic():
            locked_request = type(self).objects.select_for_update().get(pk=self.pk)
            if locked_request.activated_at:
                self.activated_at = locked_request.activated_at
                self.extension_start = locked_request.extension_start
                self.extension_end = locked_request.extension_end
                return False

            store = StoreSettings.objects.select_for_update().get(pk=locked_request.store_id)
            today = timezone.localdate()
            was_active = store.subscription_status == "Active"
            if was_active and store.subscription_end:
                extension_start = store.subscription_end + timedelta(days=1)
            else:
                extension_start = today
                store.subscription_start = extension_start
            extension_end = self.one_month_end(extension_start)

            store.active_plan = locked_request.requested_plan
            store.status = "Active"
            store.subscription_end = extension_end
            store.full_clean()
            store.save(
                update_fields=[
                    "active_plan",
                    "status",
                    "subscription_start",
                    "subscription_end",
                    "updated_at",
                ]
            )

            activated_at = timezone.now()
            locked_request.activated_at = activated_at
            locked_request.extension_start = extension_start
            locked_request.extension_end = extension_end
            locked_request.save(
                update_fields=["activated_at", "extension_start", "extension_end", "updated_at"]
            )
            self.store = store
            self.activated_at = activated_at
            self.extension_start = extension_start
            self.extension_end = extension_end
            return True

    def __str__(self):
        return f"{self.store.store_id} — {self.requested_plan} — {self.status}"


class ProductCategory(models.Model):
    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="product_categories")
    name = models.CharField(max_length=60)
    is_custom = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "product category"
        verbose_name_plural = "product categories"
        constraints = [
            models.UniqueConstraint(fields=["store", "name"], name="unique_store_product_category"),
        ]

    def clean(self):
        super().clean()
        self.name = " ".join((self.name or "").split())
        if not self.name:
            raise ValidationError({"name": "Enter a category name."})
        if self.name.casefold() == "all":
            raise ValidationError({"name": "‘All’ is reserved for the checkout category filter."})

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} — {self.store.business_name}"


class StoreMembership(models.Model):
    class Role(models.TextChoices):
        ADMINISTRATOR = "Administrator", "Store administrator"
        STAFF = "Staff", "Staff"

    class AccessLevel(models.TextChoices):
        CASHIER = "Cashier", "Cashier"
        INVENTORY = "Inventory", "Inventory controller"
        MANAGER = "Manager", "Manager"

    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="store_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    access_level = models.CharField(
        max_length=20,
        choices=AccessLevel.choices,
        default=AccessLevel.CASHIER,
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["store__business_name", "role", "user__username"]
        constraints = [
            models.UniqueConstraint(fields=["store", "user"], name="unique_store_user_membership"),
        ]

    def clean(self):
        """Apply plan limits before an active store assignment is persisted."""
        super().clean()
        if not self.store_id:
            return
        if self.user_id and self.active:
            # Multi-store access is an all-Pro invariant. Requiring both the
            # destination and every existing assignment to be Pro prevents a
            # lower-plan branch from being reached through a shared login.
            other_memberships = StoreMembership.objects.filter(
                user_id=self.user_id,
                active=True,
            ).exclude(pk=self.pk).exclude(store_id=self.store_id)
            if other_memberships.exists() and (
                not self.store.is_pro
                or other_memberships.exclude(store__active_plan="Pro").exists()
            ):
                raise ValidationError({
                    "user": "Multiple-store access is a Pro feature. Every store assigned to this user must use the Pro plan."
                })
        if self.role == self.Role.ADMINISTRATOR and self.active:
            existing = StoreMembership.objects.filter(
                store_id=self.store_id,
                role=self.Role.ADMINISTRATOR,
                active=True,
            ).exclude(pk=self.pk).count()
            if existing >= self.store.administrator_limit:
                raise ValidationError(
                    {"role": f"The {self.store.active_plan} plan allows {self.store.administrator_limit} store administrator account(s)."}
                )
            self.access_level = self.AccessLevel.MANAGER
        elif self.role == self.Role.STAFF and not self.store.is_pro:
            self.access_level = self.AccessLevel.CASHIER

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_administrator(self):
        return self.role == self.Role.ADMINISTRATOR

    @property
    def can_sell(self):
        return True

    @property
    def can_manage_inventory(self):
        return self.is_administrator or (
            self.store.is_pro
            and self.access_level in {self.AccessLevel.INVENTORY, self.AccessLevel.MANAGER}
        )

    @property
    def can_view_reports(self):
        return self.is_administrator or (
            self.store.is_pro and self.access_level == self.AccessLevel.MANAGER
        )

    @property
    def can_manage_customers(self):
        return self.is_administrator or (
            self.store.is_pro and self.access_level == self.AccessLevel.MANAGER
        )

    @property
    def can_approve(self):
        return self.is_administrator or (
            self.store.is_pro and self.access_level == self.AccessLevel.MANAGER
        )

    @property
    def can_manage_purchasing(self):
        return self.can_manage_inventory

    def __str__(self):
        return f"{self.user} — {self.store} ({self.role})"


class UserSecurityProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="security_profile",
    )
    must_change_password = models.BooleanField(default=False)
    password_change_required_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def require_password_change(cls, user):
        profile, _ = cls.objects.get_or_create(user=user)
        profile.must_change_password = True
        profile.password_change_required_at = timezone.now()
        profile.save(update_fields=["must_change_password", "password_change_required_at", "updated_at"])
        return profile

    @classmethod
    def clear_password_change(cls, user):
        profile, _ = cls.objects.get_or_create(user=user)
        profile.must_change_password = False
        profile.password_change_required_at = None
        profile.save(update_fields=["must_change_password", "password_change_required_at", "updated_at"])
        return profile

    def __str__(self):
        return f"Security settings for {self.user}"


class Customer(models.Model):
    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=140)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    loyalty_points = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.store.business_name})"


class InventoryMovement(models.Model):
    class MovementType(models.TextChoices):
        SALE = "Sale", "Sale"
        OFFLINE_SALE = "Offline sale", "Offline recovery sale"
        ADJUSTMENT = "Adjustment", "Adjustment"
        STOCKTAKE = "Stocktake", "Stocktake"
        TRANSFER_OUT = "Transfer out", "Transfer out"
        TRANSFER_IN = "Transfer in", "Transfer in"
        PURCHASE = "Purchase", "Purchase received"
        RETURN = "Return", "Sale reversal"
        IMPORT = "Import", "CSV import"

    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="inventory_movements")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="inventory_movements")
    movement_type = models.CharField(max_length=24, choices=MovementType.choices)
    quantity = models.IntegerField()
    previous_stock = models.PositiveIntegerField()
    new_stock = models.PositiveIntegerField()
    reason = models.CharField(max_length=240, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="inventory_movements",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product.name}: {self.quantity:+d} ({self.movement_type})"


class StockTransfer(models.Model):
    source_store = models.ForeignKey(StoreSettings, on_delete=models.PROTECT, related_name="outgoing_transfers")
    destination_store = models.ForeignKey(StoreSettings, on_delete=models.PROTECT, related_name="incoming_transfers")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_transfers")
    notes = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if self.source_store_id and self.destination_store_id:
            if self.source_store_id == self.destination_store_id:
                raise ValidationError("Source and destination stores must be different.")
            if not self.source_store.is_pro or not self.destination_store.is_pro:
                raise ValidationError("Stock transfers require Pro on both stores.")

    def __str__(self):
        return f"{self.source_store.store_id} to {self.destination_store.store_id}"


class StockTransferItem(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="items")
    source_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfer_items_out")
    destination_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfer_items_in")
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity} x {self.source_product.name}"


class CashRegisterSession(models.Model):
    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="register_sessions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="register_sessions")
    business_date = models.DateField(default=timezone.localdate, db_index=True)
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    closing_cash = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    notes = models.CharField(max_length=240, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store"],
                condition=Q(closed_at__isnull=True),
                name="one_open_register_per_store",
            ),
        ]

    @property
    def cash_sales(self):
        end = self.closed_at or timezone.now()
        return self.store.sales.filter(
            payment_method="Cash",
            status="Completed",
            created_at__gte=self.opened_at,
            created_at__lte=end,
        ).aggregate(value=Sum("total"))["value"] or Decimal("0.00")

    @property
    def expected_cash(self):
        return self.opening_cash + self.cash_sales

    @property
    def variance(self):
        if self.closing_cash is None:
            return None
        return self.closing_cash - self.expected_cash

    def __str__(self):
        return f"{self.store.store_id} register - {self.business_date}"


class CashRegisterActivity(models.Model):
    class StartReason(models.TextChoices):
        OPENED = "Opened", "Opened daily register"
        RESUMED = "Resumed", "Resumed shared register"
        HANDOVER = "Handover", "Took over from another staff member"

    class EndReason(models.TextChoices):
        LOGOUT = "Logout", "Staff logged out"
        HANDOVER = "Handover", "Handed over"
        FINAL_CLOSE = "Final close", "Register closed for the day"

    register = models.ForeignKey(CashRegisterSession, on_delete=models.CASCADE, related_name="activities")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="register_activities")
    start_reason = models.CharField(max_length=20, choices=StartReason.choices)
    end_reason = models.CharField(max_length=20, choices=EndReason.choices, blank=True)
    notes = models.CharField(max_length=240, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["register"],
                condition=Q(ended_at__isnull=True),
                name="one_active_staff_per_register",
            ),
        ]

    @property
    def duration(self):
        return (self.ended_at or timezone.now()) - self.started_at

    def __str__(self):
        return f"{self.register} — {self.user}"


class Supplier(models.Model):
    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=140)
    contact_person = models.CharField(max_length=140, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["store", "name"], name="unique_store_supplier_name"),
        ]

    def __str__(self):
        return f"{self.name} ({self.store.business_name})"


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        ORDERED = "Ordered", "Ordered"
        RECEIVED = "Received", "Received"
        CANCELLED = "Cancelled", "Cancelled"

    order_number = models.CharField(max_length=40, unique=True)
    store = models.ForeignKey(StoreSettings, on_delete=models.PROTECT, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_orders")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ORDERED)
    notes = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    received_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def total(self):
        return self.items.aggregate(value=Sum(models.F("quantity") * models.F("unit_cost")))["value"] or Decimal("0.00")

    def __str__(self):
        return self.order_number


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="purchase_order_items")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def line_total(self):
        return self.quantity * self.unit_cost

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"


class ReportSchedule(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "Daily", "Daily"
        WEEKLY = "Weekly", "Weekly"

    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="report_schedules")
    recipient_email = models.EmailField()
    frequency = models.CharField(max_length=12, choices=Frequency.choices, default=Frequency.DAILY)
    active = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["recipient_email"]
        constraints = [
            models.UniqueConstraint(fields=["store", "recipient_email"], name="unique_store_report_recipient"),
        ]

    def __str__(self):
        return f"{self.store.store_id} - {self.recipient_email}"


class StoreAuditEvent(models.Model):
    store = models.ForeignKey(StoreSettings, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="store_audit_events",
        blank=True,
        null=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approved_store_events",
        blank=True,
        null=True,
    )
    action = models.CharField(max_length=60)
    description = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.store.store_id}: {self.action}"


class DemoRequest(models.Model):
    class Status(models.TextChoices):
        NEW = "New", "New"
        CONTACTED = "Contacted", "Contacted"
        SCHEDULED = "Scheduled", "Scheduled"
        CLOSED = "Closed", "Closed"

    name = models.CharField(max_length=140)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    business_name = models.CharField(max_length=160)
    business_type = models.CharField(max_length=80, blank=True)
    preferred_schedule = models.DateTimeField(blank=True, null=True)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.business_name} — {self.name}"
