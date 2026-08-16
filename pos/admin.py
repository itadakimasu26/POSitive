from decimal import Decimal

from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

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
    SubscriptionExtensionRequest,
    Supplier,
    UserSecurityProfile,
)


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


@admin.register(SubscriptionExtensionRequest, site=platform_admin_site)
class SubscriptionExtensionRequestAdmin(admin.ModelAdmin):
    list_display = (
        "store",
        "requested_plan",
        "payment_type",
        "requested_by",
        "status",
        "email_delivered",
        "created_at",
    )
    list_filter = ("status", "requested_plan", "payment_type", "created_at")
    search_fields = (
        "store__business_name",
        "store__store_id",
        "requested_by__username",
        "requested_by__email",
        "comments",
        "admin_notes",
    )
    list_editable = ("status",)
    readonly_fields = (
        "store",
        "requested_by",
        "requested_plan",
        "payment_type",
        "comments",
        "email_sent_at",
        "email_error",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Request",
            {
                "fields": (
                    "store",
                    "requested_by",
                    "requested_plan",
                    "payment_type",
                    "comments",
                    "status",
                )
            },
        ),
        ("Administration notes", {"fields": ("admin_notes",)}),
        (
            "Delivery and review",
            {
                "fields": (
                    "email_sent_at",
                    "email_error",
                    "reviewed_by",
                    "reviewed_at",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(boolean=True, description="Email sent")
    def email_delivered(self, obj):
        return bool(obj.email_sent_at)

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)


class ExistingProAdministratorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        store_names = list(
            user.store_memberships.filter(
                active=True,
                role=StoreMembership.Role.ADMINISTRATOR,
                store__active_plan="Pro",
            ).values_list("store__business_name", flat=True)
        )
        stores = ", ".join(store_names) or "Pro administrator"
        email = user.email or "no email"
        return f"{user.username} — {email} — {stores}"


class PlatformStoreForm(forms.ModelForm):
    """Create or edit a store together with its administrator assignment.

    A new/single-store administrator can be entered as credentials. For Pro
    branches, the explicit selector reuses an administrator already assigned
    to another Pro store without changing that account's credentials.
    """
    service_charge_rate = forms.DecimalField(
        label="Dine-in service charge (%)",
        required=False,
        initial=Decimal("0.00"),
        min_value=Decimal("0.00"),
        max_value=Decimal("100.00"),
        decimal_places=2,
        max_digits=5,
        help_text="Cafe stores only. Applied to dine-in orders; take-out orders are not charged.",
    )
    existing_administrator = ExistingProAdministratorChoiceField(
        label="Assign an existing Pro administrator",
        queryset=User.objects.none(),
        required=False,
        help_text="Pro only. Select an administrator who already manages a Pro store to give the same login access to this store.",
    )
    admin_username = forms.CharField(
        label="New or single-store administrator username",
        max_length=150,
        required=False,
        validators=User._meta.get_field("username").validators,
        help_text="Use this when creating a new login or assigning an account to its first store.",
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
        self.selected_existing_administrator = None
        self.fields["existing_administrator"].queryset = User.objects.filter(
            is_active=True,
            is_superuser=False,
            store_memberships__active=True,
            store_memberships__role=StoreMembership.Role.ADMINISTRATOR,
            store_memberships__store__active_plan="Pro",
        ).distinct().order_by("username")
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

    def clean_service_charge_rate(self):
        return self.cleaned_data.get("service_charge_rate") or Decimal("0.00")

    def clean(self):
        """Validate credentials, seat limits, and the all-Pro sharing rule."""
        cleaned_data = super().clean()
        store_type = cleaned_data.get("store_type", self.instance.store_type)
        if store_type != StoreSettings.StoreType.CAFE and cleaned_data.get("service_charge_rate"):
            self.add_error(
                "service_charge_rate",
                "Dine-in service charges are available only for Cafe stores.",
            )
        plan = cleaned_data.get("active_plan", self.instance.active_plan)
        selected_administrator = cleaned_data.get("existing_administrator")
        username = cleaned_data.get("admin_username", "").strip()
        password = cleaned_data.get("admin_password")
        password_confirm = cleaned_data.get("admin_password_confirm")
        email = cleaned_data.get("admin_email", "").strip()

        if selected_administrator:
            self.selected_existing_administrator = selected_administrator
            self.administrator_user = selected_administrator
            cleaned_data["admin_username"] = selected_administrator.username
            cleaned_data["admin_email"] = selected_administrator.email
            if plan != "Pro":
                self.add_error(
                    "existing_administrator",
                    "Existing multi-store administrators can only be assigned to Pro stores.",
                )
            other_assignments = selected_administrator.store_memberships.filter(active=True)
            if self.instance.pk:
                other_assignments = other_assignments.exclude(store_id=self.instance.pk)
            if other_assignments.exclude(store__active_plan="Pro").exists():
                self.add_error(
                    "existing_administrator",
                    "This account is assigned to a non-Pro store and cannot receive multi-store access.",
                )
            if password or password_confirm:
                self.add_error(
                    "admin_password",
                    "Do not change credentials when assigning an existing Pro administrator.",
                )
        elif not username:
            self.add_error(
                "admin_username",
                "Enter an administrator username or select an existing Pro administrator.",
            )
            return cleaned_data

        if selected_administrator:
            password = ""
            password_confirm = ""
            email = selected_administrator.email
        if not username:
            username = selected_administrator.username

        if not selected_administrator:
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
            other_assignments = self.administrator_user.store_memberships.filter(active=True)
            if self.instance.pk:
                other_assignments = other_assignments.exclude(store_id=self.instance.pk)
            if other_assignments.exists() and (
                plan != "Pro"
                or other_assignments.exclude(store__active_plan="Pro").exists()
            ):
                self.add_error(
                    "admin_username",
                    "Multiple-store access requires the Pro plan on every assigned store.",
                )
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
        password = "" if self.selected_existing_administrator else self.cleaned_data.get("admin_password")
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
        ("Business", {"fields": ("business_name", "store_id", "store_type", "contact_number", "address", "tax_rate", "service_charge_rate"), "description": "Choose the store type to prepare product categories. Dine-in, take-out, and service charges are available only for Cafe stores."}),
        ("Subscription", {"fields": ("active_plan", "status", "subscription_start", "subscription_end", "subscription_state", "staff_usage"), "description": "Trial and Starter are single-store plans. Pro enables shared administrator accounts, the owner super dashboard, and advanced store operations."}),
        ("Store administrator", {"fields": ("existing_administrator", "admin_username", "admin_email", "admin_password", "admin_password_confirm"), "description": "For a Pro branch, select an existing Pro administrator to share one login across stores. Otherwise create or assign the store's first administrator below."}),
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
    list_display = ("name", "store", "category", "barcode", "stock", "price", "picture_url", "active")
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
    list_display = ("receipt_number", "store", "created_at", "user", "order_type", "source", "payment_method", "service_charge", "total")
    list_filter = ("store", "order_type", "source", "payment_method", "created_at")
    search_fields = ("receipt_number", "external_order_id", "user__username", "store__business_name", "store__store_id")
    autocomplete_fields = ("store", "user")
    inlines = [SaleItemInline]


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
