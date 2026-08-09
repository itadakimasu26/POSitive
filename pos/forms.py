import uuid
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Customer,
    DemoRequest,
    Product,
    ProductCategory,
    PurchaseOrder,
    ReportSchedule,
    StoreMembership,
    StoreSettings,
    Supplier,
    UserSecurityProfile,
)


User = get_user_model()


class DemoRequestForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="Leave blank")

    class Meta:
        model = DemoRequest
        fields = ["name", "email", "phone", "business_name", "business_type", "preferred_schedule", "message"]
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Your name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@business.com"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "Mobile or WhatsApp number"}),
            "business_name": forms.TextInput(attrs={"autocomplete": "organization", "placeholder": "Business name"}),
            "business_type": forms.Select(choices=[
                ("", "Select your retail type"),
                ("Cafe", "Cafe"),
                ("Minimart / specialty grocery", "Minimart / specialty grocery"),
                ("Clothing / footwear", "Clothing / footwear boutique"),
                ("Gadgets / accessories", "Gadget / accessory shop"),
                ("Pet supplies", "Pet-supply store"),
                ("Hardware / general merchandise", "Hardware / general merchandise"),
                ("Cosmetics / personal care", "Cosmetics / personal care"),
                ("Other", "Other"),
            ]),
            "preferred_schedule": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "message": forms.Textarea(attrs={"rows": 4, "placeholder": "Tell us what you want to see in the demo"}),
        }

    def clean_website(self):
        value = self.cleaned_data.get("website", "")
        if value:
            raise ValidationError("Unable to submit this request.")
        return value


class TrialSignupForm(forms.Form):
    business_name = forms.CharField(
        max_length=140,
        widget=forms.TextInput(attrs={"placeholder": "Your business name", "autocomplete": "organization"}),
    )
    store_type = forms.ChoiceField(
        label="Type of store",
        choices=[("", "Select your store type"), *StoreSettings.StoreType.choices],
        help_text="We will prepare the most useful product categories for your business.",
    )
    service_charge_rate = forms.DecimalField(
        label="Dine-in service charge (%)",
        required=False,
        initial=Decimal("0.00"),
        min_value=Decimal("0.00"),
        max_value=Decimal("100.00"),
        decimal_places=2,
        max_digits=5,
        help_text="Cafe stores only. Applied to dine-in orders; take-out orders are not charged.",
        widget=forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01"}),
    )
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "given-name"}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "family-name"}))
    username = forms.CharField(
        max_length=150,
        validators=User._meta.get_field("username").validators,
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_business_name(self):
        return self.cleaned_data["business_name"].strip()

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("This username is already in use.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("website"):
            raise ValidationError("Unable to create this trial account.")
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The passwords do not match.")
        if password1:
            provisional_user = User(
                username=cleaned_data.get("username", ""),
                email=cleaned_data.get("email", ""),
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
            )
            try:
                validate_password(password1, user=provisional_user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        if (
            cleaned_data.get("store_type") != StoreSettings.StoreType.CAFE
            and cleaned_data.get("service_charge_rate")
        ):
            self.add_error(
                "service_charge_rate",
                "Dine-in service charges are available only for Cafe stores.",
            )
        return cleaned_data

    @transaction.atomic
    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data["username"],
            email=self.cleaned_data["email"],
            first_name=self.cleaned_data["first_name"].strip(),
            last_name=self.cleaned_data["last_name"].strip(),
            password=self.cleaned_data["password1"],
        )
        today = timezone.localdate()
        while True:
            store_id = f"TRIAL-{uuid.uuid4().hex[:10].upper()}"
            if not StoreSettings.objects.filter(store_id=store_id).exists():
                break
        store = StoreSettings.objects.create(
            business_name=self.cleaned_data["business_name"],
            store_id=store_id,
            store_type=self.cleaned_data["store_type"],
            service_charge_rate=self.cleaned_data.get("service_charge_rate") or Decimal("0.00"),
            active_plan="Trial",
            status="Active",
            subscription_start=today,
            subscription_end=today + timedelta(days=StoreSettings.TRIAL_LENGTH_DAYS - 1),
        )
        StoreMembership.objects.create(
            store=store,
            user=user,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        return user, store


class ProductForm(forms.ModelForm):
    category = forms.ChoiceField(choices=(), help_text="Categories are tailored to this store type.")

    class Meta:
        model = Product
        fields = ["name", "category", "barcode", "cost", "price", "stock", "low_stock_threshold", "picture_url"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Matcha latte"}),
            "barcode": forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
            "cost": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "price": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
            "stock": forms.NumberInput(attrs={"min": 0}),
            "low_stock_threshold": forms.NumberInput(attrs={"min": 0}),
            "picture_url": forms.URLInput(attrs={"placeholder": "https://example.com/product-photo.jpg"}),
        }

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.instance.store = store
        available_names = list(store.product_categories.values_list("name", flat=True))
        if not available_names:
            store.sync_product_categories()
            available_names = list(store.product_categories.values_list("name", flat=True))
        current_category = self.instance.category if self.instance.pk else ""
        if current_category and current_category not in available_names:
            available_names.append(current_category)
        preset_order = {name: index for index, name in enumerate(store.default_product_category_names)}
        available_names.sort(key=lambda name: (preset_order.get(name, len(preset_order)), name.casefold()))
        self.fields["category"].choices = [(name, name) for name in available_names]

    def clean_category(self):
        category = self.cleaned_data["category"]
        stored_category = self.store.product_categories.filter(name__iexact=category).first()
        if not stored_category:
            raise ValidationError("Choose a category available for this store.")
        return stored_category.name

    def clean_barcode(self):
        barcode = self.cleaned_data["barcode"].strip().upper()
        if barcode and Product.objects.filter(store=self.store, barcode__iexact=barcode).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This barcode is already used by another product in this store.")
        return barcode

    def clean_picture_url(self):
        picture_url = self.cleaned_data.get("picture_url", "").strip()
        if picture_url and not picture_url.lower().startswith(("https://", "http://")):
            raise ValidationError("Use an http:// or https:// image URL.")
        return picture_url


class ProductPictureForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["picture_url"]
        widgets = {
            "picture_url": forms.URLInput(
                attrs={"placeholder": "https://example.com/product-photo.jpg", "autocomplete": "url"}
            ),
        }

    def clean_picture_url(self):
        picture_url = self.cleaned_data.get("picture_url", "").strip()
        if picture_url and not picture_url.lower().startswith(("https://", "http://")):
            raise ValidationError("Use an http:// or https:// image URL.")
        return picture_url


class ProductCategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Frozen desserts", "maxlength": 60}),
        }

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.instance.store = store

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if name.casefold() == "all":
            raise ValidationError("‘All’ is reserved for the checkout category filter.")
        if ProductCategory.objects.filter(store=self.store, name__iexact=name).exists():
            raise ValidationError("This category already exists in the store.")
        return name


class StoreTeamMemberForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        validators=User._meta.get_field("username").validators,
        widget=forms.TextInput(attrs={"placeholder": "New or existing username", "autocomplete": "off"}),
    )
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"placeholder": "name@example.com"}))
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    password = forms.CharField(
        label="Temporary password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Required for a new username", "autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        label="Confirm temporary password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Type the temporary password again", "autocomplete": "new-password"}),
    )
    access_level = forms.ChoiceField(
        choices=StoreMembership.AccessLevel.choices,
        initial=StoreMembership.AccessLevel.CASHIER,
        help_text="Pro stores can assign Cashier, Inventory controller, or Manager access.",
    )

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.user = None
        if not store.is_pro:
            self.fields["access_level"].disabled = True
            self.fields["access_level"].initial = StoreMembership.AccessLevel.CASHIER
            self.fields["access_level"].help_text = "Starter staff use the standard Cashier access level."

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username", "").strip()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")
        if not username:
            return cleaned_data

        self.user = User.objects.filter(username__iexact=username).first()
        if self.user and StoreMembership.objects.filter(store=self.store, user=self.user).exists():
            self.add_error("username", "This user already belongs to the store.")
        if self.user:
            cleaned_data["username"] = self.user.username
            if not self.user.is_active:
                self.add_error("username", "This user account is inactive.")
            if self.user.is_superuser:
                self.add_error("username", "Platform superusers cannot be assigned as store staff.")
            if not self.user.email:
                self.add_error(
                    "username",
                    "This existing account has no registered email. Ask the platform administrator to add one first.",
                )
            if password or password_confirm:
                self.add_error("password", "Leave both password fields blank when assigning an existing user.")
        else:
            email = cleaned_data.get("email", "").strip()
            if not email:
                self.add_error("email", "An email address is required for a new user.")
            elif User.objects.filter(email__iexact=email).exists():
                self.add_error("email", "An account with this email address already exists.")
            if not password:
                self.add_error("password", "A temporary password is required for a new user.")
            else:
                provisional_user = User(username=username, email=cleaned_data.get("email", ""))
                try:
                    validate_password(password, user=provisional_user)
                except ValidationError as exc:
                    self.add_error("password", exc)
            if password != password_confirm:
                self.add_error("password_confirm", "The temporary passwords do not match.")

        if self.store.active_staff_count >= self.store.staff_limit:
            raise ValidationError(
                f"The {self.store.active_plan} plan allows {self.store.staff_limit} staff account(s) per store."
            )
        return cleaned_data

    @transaction.atomic
    def save(self):
        store = StoreSettings.objects.select_for_update().get(pk=self.store.pk)
        if store.active_staff_count >= store.staff_limit:
            raise ValidationError(
                f"The {store.active_plan} plan allows {store.staff_limit} staff account(s) per store."
            )

        if self.user is None:
            self.user = User.objects.create_user(
                username=self.cleaned_data["username"].strip(),
                email=self.cleaned_data["email"],
                first_name=self.cleaned_data.get("first_name", "").strip(),
                last_name=self.cleaned_data.get("last_name", "").strip(),
                password=self.cleaned_data["password"],
            )
            UserSecurityProfile.require_password_change(self.user)
        return StoreMembership.objects.create(
            store=store,
            user=self.user,
            role=StoreMembership.Role.STAFF,
            access_level=self.cleaned_data["access_level"],
        )


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "email", "phone"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Customer name"}),
            "email": forms.EmailInput(attrs={"placeholder": "customer@example.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "Contact number"}),
        }

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.instance.store = store

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and Customer.objects.filter(store=self.store, email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This email is already used by another customer in this store.")
        return email


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "email", "phone", "address"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Supplier name"}),
            "contact_person": forms.TextInput(attrs={"placeholder": "Contact person"}),
        }

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.instance.store = store


class StocktakeForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    counted_stock = forms.IntegerField(min_value=0, label="Counted quantity")
    reason = forms.CharField(
        max_length=240,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Monthly physical count"}),
    )

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(store=store, active=True)


class StockTransferForm(forms.Form):
    destination_store = forms.ModelChoiceField(queryset=StoreSettings.objects.none())
    product = forms.ModelChoiceField(queryset=Product.objects.none())
    quantity = forms.IntegerField(min_value=1)
    notes = forms.CharField(max_length=240, required=False)

    def __init__(self, *args, store, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.fields["product"].queryset = Product.objects.filter(store=store, active=True)
        destination_ids = StoreMembership.objects.filter(
            user=user,
            active=True,
            role=StoreMembership.Role.ADMINISTRATOR,
            store__active_plan="Pro",
            store__status="Active",
        ).filter(
            Q(store__subscription_end__isnull=True)
            | Q(store__subscription_end__gte=timezone.localdate())
        ).exclude(store=store).values_list("store_id", flat=True)
        self.fields["destination_store"].queryset = StoreSettings.objects.filter(pk__in=destination_ids)

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        quantity = cleaned_data.get("quantity")
        if product and quantity and quantity > product.stock:
            self.add_error("quantity", f"Only {product.stock} item(s) are available to transfer.")
        return cleaned_data


class CashRegisterOpenForm(forms.Form):
    opening_cash = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)
    notes = forms.CharField(max_length=240, required=False)


class CashRegisterCloseForm(forms.Form):
    closing_cash = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)
    notes = forms.CharField(max_length=240, required=False)


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["supplier", "notes"]

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(store=store, active=True)


class PurchaseOrderItemForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none(), required=False)
    quantity = forms.IntegerField(min_value=1, required=False)
    unit_cost = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, required=False)

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(store=store, active=True)

    def clean(self):
        cleaned_data = super().clean()
        values = [cleaned_data.get("product"), cleaned_data.get("quantity"), cleaned_data.get("unit_cost")]
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValidationError("Complete the product, quantity, and unit cost for this line.")
        return cleaned_data


PurchaseOrderItemFormSet = forms.formset_factory(PurchaseOrderItemForm, extra=3, max_num=10)


class ReportScheduleForm(forms.ModelForm):
    class Meta:
        model = ReportSchedule
        fields = ["recipient_email", "frequency"]

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.instance.store = store

    def clean_recipient_email(self):
        email = self.cleaned_data["recipient_email"].strip().lower()
        if ReportSchedule.objects.filter(store=self.store, recipient_email__iexact=email).exists():
            raise ValidationError("This email already receives a scheduled report for the store.")
        return email


class ProductCSVImportForm(forms.Form):
    csv_file = forms.FileField(
        label="Product CSV",
        help_text="Columns: name, category, barcode, cost, price, stock, low_stock_threshold.",
    )

    def clean_csv_file(self):
        upload = self.cleaned_data["csv_file"]
        if not upload.name.lower().endswith(".csv"):
            raise ValidationError("Upload a CSV file.")
        if upload.size > 2 * 1024 * 1024:
            raise ValidationError("The CSV file must be 2 MB or smaller.")
        return upload


class OfflineSalesCSVImportForm(forms.Form):
    csv_file = forms.FileField(
        label="Completed offline-sales CSV",
        help_text="Upload the POSitive! outage-recovery template after service returns.",
    )

    def clean_csv_file(self):
        upload = self.cleaned_data["csv_file"]
        if not upload.name.lower().endswith(".csv"):
            raise ValidationError("Upload a CSV file.")
        if upload.size > 2 * 1024 * 1024:
            raise ValidationError("The offline-sales CSV must be 2 MB or smaller.")
        return upload
