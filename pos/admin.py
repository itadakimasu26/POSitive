from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from positive_pos.admin_site import platform_admin_site

from .models import (
    CashRegisterActivity,
    CashRegisterSession,
    Customer,
    DemoRequest,
    InventoryMovement,
    Product,
    ProductCategory,
    PurchaseOrder,
    PurchaseOrderItem,
    ReportSchedule,
    Sale,
    SaleItem,
    StockTransfer,
    StockTransferItem,
    StoreAuditEvent,
    StoreMembership,
    StoreSettings,
    SubscriptionRequest,
    Supplier,
    UserSecurityProfile,
)
from .subscriptions import activate_subscription, reject_subscription


User = get_user_model()


@admin.register(DemoRequest, site=platform_admin_site)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = ("business_name", "name", "business_type", "email", "phone", "preferred_schedule", "status", "created_at")
    list_filter = ("status", "business_type", "created_at")
    search_fields = ("business_name", "name", "email", "phone", "message")
    list_editable = ("status",)
    readonly_fields = ("name", "email", "phone", "business_name", "business_type", "preferred_schedule", "message", "created_at")

    def has_add_permission(self, request):
        return False


class PlatformStoreForm(forms.ModelForm):
    admin_username = forms.CharField(
        label="Store administrator username",
        max_length=150,
        validators=User._meta.get_field("username").validators,
        help_text="Enter an existing username or issue a new administrator login. Pro permits up to three administrators per store.",
    )
    admin_email = forms.EmailField(
        label="Store administrator email",
        required=False,
        help_text="Required when creating a new username.",
    )
    admin_password = forms.CharField(
        label="Temporary password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Required for a new username. The user must replace it at first sign-in.",
    )
    admin_password_confirm = forms.CharField(
        label="Confirm temporary password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Type the same temporary password again to prevent account setup mistakes.",
    )

    class Meta:
        model = StoreSettings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.administrator_user = None
        if self.instance.pk:
            membership = self.instance.memberships.filter(
                role=StoreMembership.Role.ADMINISTRATOR,
                active=True,
            ).select_related("user").first()
            if membership:
                self.fields["admin_username"].initial = membership.user.username
                self.fields["admin_email"].initial = membership.user.email

    def clean_store_id(self):
        store_id = self.cleaned_data["store_id"].strip().upper()
        if StoreSettings.objects.filter(store_id__iexact=store_id).exclude(pk=self.instance.pk).exists():
            raise ValidationError("A store with this ID already exists.")
        return store_id

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("admin_username", "").strip()
        password = cleaned_data.get("admin_password")
        password_confirm = cleaned_data.get("admin_password_confirm")
        email = cleaned_data.get("admin_email", "").strip()
        if not username:
            return cleaned_data

        self.administrator_user = User.objects.filter(username__iexact=username).first()
        email_users = User.objects.filter(email__iexact=email) if email else User.objects.none()
        if self.administrator_user:
            email_users = email_users.exclude(pk=self.administrator_user.pk)
        if email and email_users.exists():
            self.add_error("admin_email", "Another account already uses this email address.")
        if self.administrator_user:
            cleaned_data["admin_username"] = self.administrator_user.username
            if not self.administrator_user.is_active:
                self.add_error("admin_username", "This user account is inactive.")
            if self.administrator_user.is_superuser:
                self.add_error("admin_username", "Use a client account, not a platform superuser.")
            if not email and not self.administrator_user.email:
                self.add_error("admin_email", "This account needs a registered email for password recovery.")
            if password:
                try:
                    validate_password(password, user=self.administrator_user)
                except ValidationError as exc:
                    self.add_error("admin_password", exc)
        else:
            if not email:
                self.add_error("admin_email", "An email address is required for a new administrator.")
            if not password:
                self.add_error("admin_password", "A temporary password is required for a new administrator.")
            else:
                provisional_user = User(username=username, email=email)
                try:
                    validate_password(password, user=provisional_user)
                except ValidationError as exc:
                    self.add_error("admin_password", exc)
        if password != password_confirm:
            self.add_error("admin_password_confirm", "The temporary passwords do not match.")
        plan = cleaned_data.get("active_plan", self.instance.active_plan)
        admin_limit = StoreSettings.ADMIN_LIMITS.get(plan, 1)
        if self.instance.pk and self.administrator_user:
            already_assigned = self.instance.memberships.filter(
                user=self.administrator_user,
                role=StoreMembership.Role.ADMINISTRATOR,
                active=True,
            ).exists()
            current_count = self.instance.memberships.filter(
                role=StoreMembership.Role.ADMINISTRATOR,
                active=True,
            ).count()
            if not already_assigned and current_count >= admin_limit:
                self.add_error("admin_username", f"The {plan} plan allows {admin_limit} administrator account(s).")
        return cleaned_data

    def save_administrator(self, store):
        user = self.administrator_user
        password = self.cleaned_data.get("admin_password")
        email = self.cleaned_data.get("admin_email", "").strip()
        if user is None:
            user = User.objects.create_user(
                username=self.cleaned_data["admin_username"].strip(),
                email=email,
                password=password,
            )
            UserSecurityProfile.require_password_change(user)
        else:
            changed_fields = []
            if email and user.email != email:
                user.email = email
                changed_fields.append("email")
            if password:
                user.set_password(password)
                changed_fields.append("password")
            if changed_fields:
                user.save(update_fields=changed_fields)
            if password:
                UserSecurityProfile.require_password_change(user)

        if not store.is_pro:
            store.memberships.filter(
                role=StoreMembership.Role.ADMINISTRATOR,
            ).exclude(user=user).delete()
        StoreMembership.objects.update_or_create(
            store=store,
            user=user,
            defaults={
                "role": StoreMembership.Role.ADMINISTRATOR,
                "access_level": StoreMembership.AccessLevel.MANAGER,
                "active": True,
            },
        )


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ("product", "product_name", "quantity", "unit_cost", "unit_price", "line_total")


@admin.register(StoreSettings, site=platform_admin_site)
class StoreAdmin(admin.ModelAdmin):
    form = PlatformStoreForm
    list_display = (
        "business_name",
        "store_id",
        "store_type",
        "active_plan",
        "subscription_state",
        "subscription_end",
        "administrator_account",
        "owner_usage",
        "staff_usage",
    )
    list_filter = ("store_type", "active_plan", "status", "subscription_end")
    search_fields = ("business_name", "store_id", "memberships__user__username", "memberships__user__email")
    readonly_fields = ("subscription_state", "staff_usage", "created_at", "updated_at")
    fieldsets = (
        ("Business", {"fields": ("business_name", "store_id", "store_type", "contact_number", "address", "tax_rate"), "description": "Choose the store type to automatically prepare a focused product-category list."}),
        ("Subscription", {"fields": ("active_plan", "status", "subscription_start", "subscription_end", "subscription_state", "staff_usage")}),
        ("Store administrator", {"fields": ("admin_username", "admin_email", "admin_password", "admin_password_confirm"), "description": "On Pro, save another username here to add a second or third administrator without removing current administrators."}),
        ("Preferences", {"fields": ("receipt_after_sale", "low_stock_alerts", "barcode_scanning")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def subscription_state(self, obj):
        return obj.subscription_status

    @admin.display(description="Administrator")
    def administrator_account(self, obj):
        memberships = obj.memberships.filter(
            role=StoreMembership.Role.ADMINISTRATOR,
            active=True,
        ).select_related("user")
        return ", ".join(membership.user.username for membership in memberships) or "Not assigned"

    @admin.display(description="Administrators")
    def owner_usage(self, obj):
        return f"{obj.active_administrator_count} / {obj.administrator_limit}"

    @admin.display(description="Staff seats")
    def staff_usage(self, obj):
        return f"{obj.active_staff_count} / {obj.staff_limit}"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.save_administrator(obj)


@admin.register(StoreMembership, site=platform_admin_site)
class StoreMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "store", "role", "access_level", "active", "created_at")
    list_filter = ("role", "access_level", "active", "store")
    search_fields = ("user__username", "user__email", "store__business_name", "store__store_id")
    autocomplete_fields = ("user", "store")


@admin.register(Product, site=platform_admin_site)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "category", "barcode", "stock", "price", "active")
    list_filter = ("store", "category", "active")
    search_fields = ("name", "barcode", "store__business_name", "store__store_id")
    autocomplete_fields = ("store",)


@admin.register(ProductCategory, site=platform_admin_site)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "is_custom", "created_at")
    list_filter = ("store", "is_custom")
    search_fields = ("name", "store__business_name", "store__store_id")
    autocomplete_fields = ("store",)


@admin.register(Sale, site=platform_admin_site)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "store", "created_at", "user", "source", "payment_method", "total")
    list_filter = ("store", "source", "payment_method", "created_at")
    search_fields = ("receipt_number", "external_order_id", "user__username", "store__business_name", "store__store_id")
    autocomplete_fields = ("store", "user")
    inlines = [SaleItemInline]


@admin.register(SubscriptionRequest, site=platform_admin_site)
class SubscriptionRequestAdmin(admin.ModelAdmin):
    list_display = ("store", "plan", "amount", "requested_by", "payment_method", "payment_reference", "status", "created_at")
    list_filter = ("store", "plan", "status", "payment_method")
    search_fields = ("store__business_name", "store__store_id", "requested_by__username", "payer_name", "payment_reference", "contact_details")
    readonly_fields = (
        "store", "plan", "amount", "requested_by", "payment_method", "payer_name",
        "payment_reference", "contact_details", "message", "status", "provider_reference",
        "provider_payment_id", "provider_checkout_url", "reviewed_by", "reviewed_at",
        "activated_at", "created_at",
    )
    actions = ("approve_and_activate", "reject_payment")

    @admin.action(description="Approve payment and activate subscription")
    def approve_and_activate(self, request, queryset):
        activated = 0
        skipped = 0
        for subscription_request in queryset:
            _, changed = activate_subscription(subscription_request.pk, reviewed_by=request.user)
            activated += int(changed)
            skipped += int(not changed)
        self.message_user(request, f"Activated {activated} subscription(s); skipped {skipped} already activated request(s).")

    @admin.action(description="Reject selected unactivated payments")
    def reject_payment(self, request, queryset):
        rejected = 0
        skipped = 0
        for subscription_request in queryset:
            _, changed = reject_subscription(subscription_request.pk, reviewed_by=request.user)
            rejected += int(changed)
            skipped += int(not changed)
        self.message_user(request, f"Rejected {rejected} request(s); skipped {skipped} activated request(s).")

    def has_add_permission(self, request):
        return False


@admin.register(Customer, site=platform_admin_site)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "email", "phone", "loyalty_points", "total_spent", "active")
    list_filter = ("store", "active")
    search_fields = ("name", "email", "phone", "store__business_name")


@admin.register(Supplier, site=platform_admin_site)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "contact_person", "email", "phone", "active")
    list_filter = ("store", "active")
    search_fields = ("name", "contact_person", "email", "store__business_name")


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0


@admin.register(PurchaseOrder, site=platform_admin_site)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "store", "supplier", "status", "created_by", "created_at", "received_at")
    list_filter = ("store", "status", "supplier")
    search_fields = ("order_number", "supplier__name", "store__business_name")
    inlines = (PurchaseOrderItemInline,)


class StockTransferItemInline(admin.TabularInline):
    model = StockTransferItem
    extra = 0
    readonly_fields = ("source_product", "destination_product", "quantity")


@admin.register(StockTransfer, site=platform_admin_site)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ("id", "source_store", "destination_store", "created_by", "created_at")
    list_filter = ("source_store", "destination_store")
    readonly_fields = ("source_store", "destination_store", "created_by", "notes", "created_at")
    inlines = (StockTransferItemInline,)

    def has_add_permission(self, request):
        return False


@admin.register(InventoryMovement, site=platform_admin_site)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "store", "movement_type", "quantity", "previous_stock", "new_stock", "user", "created_at")
    list_filter = ("store", "movement_type", "created_at")
    search_fields = ("product__name", "product__barcode", "reference", "reason")
    readonly_fields = ("store", "product", "movement_type", "quantity", "previous_stock", "new_stock", "reason", "reference", "user", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(CashRegisterSession, site=platform_admin_site)
class CashRegisterSessionAdmin(admin.ModelAdmin):
    list_display = ("store", "business_date", "user", "opening_cash", "opened_at", "closing_cash", "closed_at")
    list_filter = ("store", "business_date", "opened_at", "closed_at")
    readonly_fields = ("store", "business_date", "user", "opening_cash", "closing_cash", "notes", "opened_at", "closed_at")

    def has_add_permission(self, request):
        return False


@admin.register(CashRegisterActivity, site=platform_admin_site)
class CashRegisterActivityAdmin(admin.ModelAdmin):
    list_display = ("register", "user", "start_reason", "started_at", "end_reason", "ended_at")
    list_filter = ("register__store", "start_reason", "end_reason", "started_at")
    search_fields = ("register__store__business_name", "register__store__store_id", "user__username", "notes")
    readonly_fields = ("register", "user", "start_reason", "end_reason", "notes", "started_at", "ended_at")

    def has_add_permission(self, request):
        return False


@admin.register(ReportSchedule, site=platform_admin_site)
class ReportScheduleAdmin(admin.ModelAdmin):
    list_display = ("store", "recipient_email", "frequency", "active", "last_sent_at")
    list_filter = ("store", "frequency", "active")


@admin.register(StoreAuditEvent, site=platform_admin_site)
class StoreAuditEventAdmin(admin.ModelAdmin):
    list_display = ("store", "action", "actor", "approved_by", "created_at")
    list_filter = ("store", "action", "created_at")
    search_fields = ("description", "actor__username", "approved_by__username")
    readonly_fields = ("store", "actor", "approved_by", "action", "description", "created_at")

    def has_add_permission(self, request):
        return False


class UserSecurityProfileInline(admin.StackedInline):
    model = UserSecurityProfile
    extra = 0
    max_num = 1
    can_delete = False
    fields = ("must_change_password", "password_change_required_at", "updated_at")
    readonly_fields = ("password_change_required_at", "updated_at")


class PlatformUserAdmin(UserAdmin):
    inlines = (UserSecurityProfileInline,)


platform_admin_site.register(User, PlatformUserAdmin)
