import csv
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape
from io import TextIOWrapper

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    CashRegisterCloseForm,
    CashRegisterOpenForm,
    CustomerForm,
    DemoRequestForm,
    OfflineSalesCSVImportForm,
    ProductCSVImportForm,
    ProductCategoryForm,
    ProductForm,
    ProductPictureForm,
    PurchaseOrderForm,
    PurchaseOrderItemFormSet,
    ReportScheduleForm,
    StocktakeForm,
    StockTransferForm,
    StoreTeamMemberForm,
    SupplierForm,
    TrialSignupForm,
)
from .models import (
    CashRegisterSession,
    CashRegisterActivity,
    Customer,
    DemoRequest,
    InventoryMovement,
    Product,
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
    Supplier,
)
from .registers import assign_open_register
from .tenancy import CURRENT_STORE_SESSION_KEY, load_store_access, store_required


REPORT_RANGES = {"1": 1, "7": 7, "30": 30, "365": 365}
OFFLINE_SALES_FIELDS = (
    "offline_order_id",
    "sold_at",
    "payment_method",
    "product_name",
    "barcode",
    "unit_price",
    "quantity",
    "collected_order_total",
    "notes",
)
OFFLINE_ORDER_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,39}$")


class SaleInputError(Exception):
    pass


def _audit(store, actor, action, description, approved_by=None):
    return StoreAuditEvent.objects.create(
        store=store,
        actor=actor,
        approved_by=approved_by,
        action=action,
        description=description,
    )


def _resolve_approver(request):
    membership = request.store_membership
    if membership.can_approve:
        return request.user
    username = request.POST.get("approver_username", "").strip()
    password = request.POST.get("approver_password", "")
    approver = authenticate(request, username=username, password=password)
    if not approver:
        raise SaleInputError("Manager approval credentials are invalid.")
    approval_membership = StoreMembership.objects.filter(
        store=request.store,
        user=approver,
        active=True,
    ).select_related("store").first()
    if not approval_membership or not approval_membership.can_approve:
        raise SaleInputError("This account cannot approve the action for the current store.")
    return approver


def _money_sum(queryset, field="total"):
    return queryset.aggregate(value=Sum(field))["value"] or Decimal("0.00")


def _report_period(range_key):
    today = timezone.localdate()
    range_key = range_key if range_key in REPORT_RANGES else "7"
    if range_key == "365":
        start = date(today.year, 1, 1)
    else:
        start = today - timedelta(days=REPORT_RANGES[range_key] - 1)
    return range_key, start, (today - start).days + 1


def _parse_cart(raw_cart):
    try:
        cart = json.loads(raw_cart)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SaleInputError("The cart data is invalid. Please rebuild the order.") from exc
    if not isinstance(cart, list):
        raise SaleInputError("The cart data is invalid. Please rebuild the order.")

    quantities = {}
    for raw in cart:
        if not isinstance(raw, dict):
            raise SaleInputError("The cart contains an invalid item.")
        try:
            raw_product_id = raw.get("id")
            raw_quantity = raw.get("qty")
            if isinstance(raw_product_id, bool) or isinstance(raw_quantity, bool):
                raise ValueError
            if isinstance(raw_quantity, float) and not raw_quantity.is_integer():
                raise ValueError
            product_id = int(raw_product_id)
            quantity = int(raw_quantity)
        except (TypeError, ValueError) as exc:
            raise SaleInputError("The cart contains an invalid item.") from exc
        if product_id < 1 or quantity < 1:
            raise SaleInputError("Every cart item must have a valid quantity.")
        quantities[product_id] = quantities.get(product_id, 0) + quantity

    if not quantities:
        raise SaleInputError("Your cart is empty.")
    return quantities


def _csv_safe(value):
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


def _public_metadata(request, route_name):
    return {
        "canonical_url": request.build_absolute_uri(reverse(route_name)),
        "social_image_url": request.build_absolute_uri(static("pos/images/positive-logo-v2.png")),
    }


@require_http_methods(["GET", "POST"])
def marketing_home(request):
    form = DemoRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        demo_request = form.save()
        messages.success(
            request,
            f"Thanks, {demo_request.name}. Your demo request is booked and we will contact you using the details provided.",
        )
        return redirect(f"{reverse('home')}#book-demo")
    metadata = _public_metadata(request, "home")
    metadata["structured_data"] = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "POSitive!",
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Any modern web browser",
            "url": metadata["canonical_url"],
            "image": metadata["social_image_url"],
            "description": "Browser-based point of sale, inventory, reporting, and staff controls for growing local retailers.",
            "offers": [
                {"@type": "Offer", "name": "Starter", "price": "399", "priceCurrency": "PHP"},
                {"@type": "Offer", "name": "Pro", "price": "799", "priceCurrency": "PHP"},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return render(
        request,
        "pos/marketing_home.html",
        {
            "demo_form": form,
            "support_contact_email": django_settings.SUPPORT_CONTACT_EMAIL,
            "support_contact_messenger": django_settings.SUPPORT_CONTACT_MESSENGER,
            "support_contact_phone": django_settings.SUPPORT_CONTACT_PHONE,
            **metadata,
        },
    )


def public_document(request, document):
    documents = {
        "privacy": ("Privacy notice", "privacy"),
        "terms": ("Terms of service", "terms"),
        "policies": ("Service policies", "policies"),
        "security": ("Security information", "security"),
    }
    if document not in documents:
        raise Http404("Document not found.")
    title, template_key = documents[document]
    return render(
        request,
        "pos/public_document.html",
        {
            "document_title": title,
            "document": template_key,
            "support_contact_email": django_settings.SUPPORT_CONTACT_EMAIL,
            "support_contact_messenger": django_settings.SUPPORT_CONTACT_MESSENGER,
            "support_contact_phone": django_settings.SUPPORT_CONTACT_PHONE,
        },
    )


@require_http_methods(["GET", "POST"])
def start_trial(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = TrialSignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user, store = form.save()
        except IntegrityError:
            form.add_error(None, "That username or business account was just registered. Please try another.")
        else:
            login(request, user)
            request.session[CURRENT_STORE_SESSION_KEY] = store.pk
            messages.success(
                request,
                f"Your 30-day trial for {store.business_name} is ready.",
            )
            return redirect("dashboard")
    return render(request, "registration/trial_signup.html", {"form": form})


def feature_guide(request):
    return render(request, "pos/feature_guide.html", _public_metadata(request, "feature_guide"))


@require_http_methods(["GET"])
def robots_txt(request):
    sitemap_url = request.build_absolute_uri(reverse("sitemap"))
    content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin/",
            "Disallow: /dashboard/",
            "Disallow: /products/",
            "Disallow: /pro/",
            "Disallow: /receipt/",
            "Disallow: /reports/",
            "Disallow: /sell/",
            "Disallow: /settings/",
            "Disallow: /stores/",
            f"Sitemap: {sitemap_url}",
            "",
        ]
    )
    return HttpResponse(content, content_type="text/plain; charset=utf-8")


@require_http_methods(["GET"])
def sitemap(request):
    public_routes = [
        reverse("home"),
        reverse("feature_guide"),
        reverse("start_trial"),
        *[reverse("public_document", args=[document]) for document in ("privacy", "terms", "policies", "security")],
    ]
    urls = "".join(
        f"<url><loc>{escape(request.build_absolute_uri(route), quote=True)}</loc></url>"
        for route in public_routes
    )
    content = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return HttpResponse(content, content_type="application/xml; charset=utf-8")


def feature_guide_pdf(request):
    path = django_settings.BASE_DIR / "static" / "pos" / "docs" / "positive-feature-guide.pdf"
    if not path.exists():
        raise Http404("The feature guide is unavailable.")
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename="POSitive-feature-and-plan-guide.pdf",
        content_type="application/pdf",
    )


@store_required
def dashboard(request):
    store = request.store
    today = timezone.localdate()
    today_sales = Sale.objects.filter(store=store, status="Completed", created_at__date=today)
    products = Product.objects.filter(store=store, active=True)
    start = today - timedelta(days=6)
    period_sales = Sale.objects.filter(store=store, status="Completed", created_at__date__gte=start)
    daily = {
        row["created_at__date"]: row["value"] or Decimal("0")
        for row in period_sales.values("created_at__date").annotate(value=Sum("total"))
    }
    values = [daily.get(start + timedelta(days=index), Decimal("0")) for index in range(7)]
    maximum = max(values) if values else Decimal("1")
    if maximum == 0:
        maximum = Decimal("1")
    chart_points = [
        {
            "label": (start + timedelta(days=index)).strftime("%a"),
            "value": value,
            "height": max(8, int((value / maximum) * 100)),
        }
        for index, value in enumerate(values)
    ]
    context = {
        "store": store,
        "today_total": _money_sum(today_sales),
        "today_count": today_sales.count(),
        "average_order": _money_sum(today_sales) / today_sales.count() if today_sales.exists() else Decimal("0"),
        "low_stock": products.filter(stock__lte=F("low_stock_threshold")).count(),
        "low_products": products.filter(stock__lte=F("low_stock_threshold"))[:4],
        "recent_sales": Sale.objects.filter(store=store).select_related("user")[:5],
        "week_total": _money_sum(period_sales),
        "chart_points": chart_points,
    }
    return render(request, "pos/dashboard.html", context)


@store_required(operational=True)
def sell(request):
    products = Product.objects.filter(store=request.store, active=True).order_by("category", "name")
    customers = Customer.objects.filter(store=request.store, active=True) if request.store.is_pro else Customer.objects.none()
    open_register = None
    register_activity = None
    if request.store.is_pro:
        open_register = CashRegisterSession.objects.filter(
            store=request.store,
            closed_at__isnull=True,
        ).first()
        if open_register:
            register_activity = open_register.activities.filter(ended_at__isnull=True).select_related("user").first()
    last_completed_sale = None
    last_completed_sale_id = request.session.pop("last_completed_sale_id", None)
    if last_completed_sale_id:
        last_completed_sale = Sale.objects.filter(
            pk=last_completed_sale_id,
            store=request.store,
        ).first()
    return render(
        request,
        "pos/sell.html",
        {
            "products": products,
            "store": request.store,
            "customers": customers,
            "open_register": open_register,
            "register_activity": register_activity,
            "register_ready": bool(register_activity and register_activity.user_id == request.user.pk),
            "last_completed_sale": last_completed_sale,
            "offline_import_form": OfflineSalesCSVImportForm(),
        },
    )


@store_required(operational=True)
def download_offline_sales_template(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="POSitive-{request.store.store_id}-offline-sales.csv"'
    )
    response["Cache-Control"] = "no-store"
    writer = csv.DictWriter(response, fieldnames=OFFLINE_SALES_FIELDS)
    writer.writeheader()
    products = Product.objects.filter(store=request.store, active=True).order_by("category", "name")
    for product in products:
        writer.writerow(
            {
                "offline_order_id": "",
                "sold_at": "",
                "payment_method": "",
                "product_name": _csv_safe(product.name),
                "barcode": _csv_safe(product.barcode),
                "unit_price": f"{product.price:.2f}",
                "quantity": "",
                "collected_order_total": "",
                "notes": "",
            }
        )
    if not products.exists():
        writer.writerow({field: "" for field in OFFLINE_SALES_FIELDS})
    return response


def _offline_decimal(value, label, line_number):
    try:
        result = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise SaleInputError(f"Row {line_number}: {label} must be a valid amount.") from exc
    if result < 0:
        raise SaleInputError(f"Row {line_number}: {label} cannot be negative.")
    return result


def _offline_sold_at(value, line_number):
    raw_value = str(value).strip()
    parsed = parse_datetime(raw_value)
    if parsed is None:
        try:
            parsed = datetime.strptime(raw_value, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise SaleInputError(
                f"Row {line_number}: sold_at must use YYYY-MM-DD HH:MM."
            ) from exc
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    now = timezone.now()
    if parsed > now + timedelta(minutes=5):
        raise SaleInputError(f"Row {line_number}: sold_at cannot be in the future.")
    if parsed < now - timedelta(days=90):
        raise SaleInputError(f"Row {line_number}: offline orders must be imported within 90 days.")
    return parsed


@store_required(permission="can_sell", operational=True)
@require_POST
def import_offline_sales(request):
    form = OfflineSalesCSVImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
        return redirect("sell")

    upload = form.cleaned_data["csv_file"]
    try:
        reader = csv.DictReader(TextIOWrapper(upload.file, encoding="utf-8-sig", newline=""))
        missing = [field for field in OFFLINE_SALES_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise SaleInputError(f"The CSV is missing column(s): {', '.join(missing)}.")
        groups = {}
        used_rows = 0
        for line_number, row in enumerate(reader, start=2):
            if line_number > 1001:
                raise SaleInputError("The offline-sales CSV may contain at most 1,000 rows.")
            external_id = str(row.get("offline_order_id", "")).strip().upper()
            if not external_id:
                continue
            used_rows += 1
            if not OFFLINE_ORDER_ID_PATTERN.fullmatch(external_id):
                raise SaleInputError(
                    f"Row {line_number}: offline_order_id must be 3–40 letters, numbers, hyphens, or underscores."
                )
            sold_at = _offline_sold_at(row.get("sold_at", ""), line_number)
            payment_lookup = {value.lower(): value for value, _ in Sale.PAYMENT_CHOICES}
            payment = payment_lookup.get(str(row.get("payment_method", "")).strip().lower())
            if not payment:
                raise SaleInputError(f"Row {line_number}: payment_method must be Cash, GCash, or Card.")
            barcode = str(row.get("barcode", "")).strip().upper()
            if not barcode:
                raise SaleInputError(f"Row {line_number}: barcode is required.")
            unit_price = _offline_decimal(row.get("unit_price", ""), "unit_price", line_number)
            collected_total = _offline_decimal(
                row.get("collected_order_total", ""), "collected_order_total", line_number
            )
            try:
                quantity = int(str(row.get("quantity", "")).strip())
            except (TypeError, ValueError) as exc:
                raise SaleInputError(f"Row {line_number}: quantity must be a whole number.") from exc
            if quantity < 1 or quantity > 10_000:
                raise SaleInputError(f"Row {line_number}: quantity must be between 1 and 10,000.")
            notes = str(row.get("notes", "")).strip()[:240]
            group = groups.setdefault(
                external_id,
                {
                    "sold_at": sold_at,
                    "payment": payment,
                    "collected_total": collected_total,
                    "notes": notes,
                    "lines": [],
                },
            )
            if (
                group["sold_at"] != sold_at
                or group["payment"] != payment
                or group["collected_total"] != collected_total
                or group["notes"] != notes
            ):
                raise SaleInputError(
                    f"Row {line_number}: repeat the same sold_at, payment, collected total, and notes for every line in {external_id}."
                )
            group["lines"].append(
                {"line_number": line_number, "barcode": barcode, "quantity": quantity, "unit_price": unit_price}
            )
        if not used_rows:
            raise SaleInputError("No completed offline orders were found. Fill offline_order_id on each used row.")
        if len(groups) > 250:
            raise SaleInputError("Import at most 250 offline orders per file.")

        existing_ids = set(
            Sale.objects.filter(store=request.store, external_order_id__in=groups).values_list(
                "external_order_id", flat=True
            )
        )
        new_groups = {
            external_id: group
            for external_id, group in groups.items()
            if external_id not in existing_ids
        }
        if not new_groups:
            messages.info(
                request,
                f"No new orders were imported; all {len(existing_ids)} offline order(s) already exist.",
            )
            return redirect("sell")

        barcodes = {
            line["barcode"]
            for group in new_groups.values()
            for line in group["lines"]
        }
        imported_line_count = 0
        with transaction.atomic():
            products = {
                product.barcode.upper(): product
                for product in Product.objects.select_for_update().filter(
                    store=request.store,
                    active=True,
                    barcode__in=barcodes,
                )
            }
            required_stock = {}
            for external_id, group in new_groups.items():
                subtotal = Decimal("0.00")
                consolidated = {}
                for line in group["lines"]:
                    product = products.get(line["barcode"])
                    if product is None:
                        raise SaleInputError(
                            f"Row {line['line_number']}: barcode {line['barcode']} is not an active product in this store."
                        )
                    if line["unit_price"] != product.price:
                        raise SaleInputError(
                            f"Row {line['line_number']}: {product.name} is now ₱{product.price:.2f}; download a fresh template or reconcile the price before import."
                        )
                    consolidated[product.pk] = consolidated.get(product.pk, 0) + line["quantity"]
                    subtotal += product.price * line["quantity"]
                tax = (subtotal * request.store.tax_rate / Decimal("100")).quantize(Decimal("0.01"))
                total = subtotal + tax
                if group["collected_total"] != total:
                    raise SaleInputError(
                        f"Order {external_id}: collected total is ₱{group['collected_total']:.2f}, but POSitive! calculates ₱{total:.2f}."
                    )
                group["subtotal"] = subtotal
                group["tax"] = tax
                group["total"] = total
                group["quantities"] = consolidated
                for product_id, quantity in consolidated.items():
                    required_stock[product_id] = required_stock.get(product_id, 0) + quantity

            products_by_id = {product.pk: product for product in products.values()}
            for product_id, quantity in required_stock.items():
                product = products_by_id[product_id]
                if product.stock < quantity:
                    raise SaleInputError(
                        f"Insufficient stock for {product.name}: the file needs {quantity}, but {product.stock} remain."
                    )

            for external_id, group in new_groups.items():
                digest = hashlib.sha256(
                    f"{request.store.pk}:{external_id}".encode("utf-8")
                ).hexdigest()[:14].upper()
                sale = Sale.objects.create(
                    receipt_number=f"OFF-{request.store.pk}-{digest}",
                    store=request.store,
                    user=request.user,
                    payment_method=group["payment"],
                    order_type=(
                        Sale.OrderType.TAKE_OUT
                        if request.store.supports_dining
                        else Sale.OrderType.RETAIL
                    ),
                    subtotal=group["subtotal"],
                    tax=group["tax"],
                    total=group["total"],
                    source=Sale.Source.OFFLINE_CSV,
                    external_order_id=external_id,
                    offline_notes=group["notes"],
                )
                Sale.objects.filter(pk=sale.pk).update(created_at=group["sold_at"])
                sale.created_at = group["sold_at"]
                for product_id, quantity in group["quantities"].items():
                    product = products_by_id[product_id]
                    previous_stock = product.stock
                    product.stock -= quantity
                    product.save(update_fields=["stock", "updated_at"])
                    SaleItem.objects.create(
                        sale=sale,
                        product=product,
                        product_name=product.name,
                        quantity=quantity,
                        unit_cost=product.cost,
                        unit_price=product.price,
                        line_total=product.price * quantity,
                    )
                    InventoryMovement.objects.create(
                        store=request.store,
                        product=product,
                        movement_type=InventoryMovement.MovementType.OFFLINE_SALE,
                        quantity=-quantity,
                        previous_stock=previous_stock,
                        new_stock=product.stock,
                        reason="Recovered from offline-sales CSV",
                        reference=external_id,
                        user=request.user,
                    )
                    imported_line_count += 1
            _audit(
                request.store,
                request.user,
                "sale.offline_import",
                f"Imported {len(new_groups)} offline order(s) and {imported_line_count} consolidated line(s); skipped {len(existing_ids)} duplicate(s).",
            )
    except (UnicodeError, csv.Error, SaleInputError, IntegrityError) as exc:
        messages.error(request, f"Offline sales were not imported: {exc}")
        return redirect("sell")

    messages.success(
        request,
        f"Imported {len(new_groups)} offline order(s). {len(existing_ids)} duplicate(s) were safely skipped.",
    )
    return redirect("sell")


@store_required(operational=True)
@require_POST
def complete_sale(request):
    try:
        quantities = _parse_cart(request.POST.get("cart_json", "[]"))
    except SaleInputError as exc:
        messages.error(request, str(exc))
        return redirect("sell")

    payment = request.POST.get("payment_method", "Cash")
    if payment not in dict(Sale.PAYMENT_CHOICES):
        payment = "Cash"
    store = request.store
    if store.supports_dining:
        order_type = request.POST.get("order_type", Sale.OrderType.TAKE_OUT)
        if order_type not in {Sale.OrderType.DINE_IN, Sale.OrderType.TAKE_OUT}:
            order_type = Sale.OrderType.TAKE_OUT
    else:
        # Never trust a hidden/crafted dining value for a retail category.
        order_type = Sale.OrderType.RETAIL

    try:
        try:
            discount_rate = Decimal(request.POST.get("discount_rate", "0") or "0").quantize(Decimal("0.01"))
            loyalty_points = int(request.POST.get("loyalty_points", "0") or 0)
        except (ArithmeticError, TypeError, ValueError):
            raise SaleInputError("Discount or loyalty details are invalid.")
        if discount_rate < 0 or discount_rate > 50:
            raise SaleInputError("Discounts must be between 0% and 50%.")
        if loyalty_points < 0:
            raise SaleInputError("Loyalty points cannot be negative.")
        if not store.is_pro and (discount_rate or loyalty_points or request.POST.get("customer_id")):
            raise SaleInputError("Customers, loyalty, and discounts require the Pro plan.")
        if store.is_pro and payment == "Cash" and not CashRegisterSession.objects.filter(
            store=store,
            closed_at__isnull=True,
            activities__user=request.user,
            activities__ended_at__isnull=True,
        ).exists():
            raise SaleInputError("Resume the shared cash register before completing a cash sale.")
        approver = _resolve_approver(request) if discount_rate else None

        with transaction.atomic():
            products = {
                product.pk: product
                for product in Product.objects.select_for_update().filter(
                    store=store,
                    pk__in=quantities,
                    active=True,
                )
            }
            if len(products) != len(quantities):
                raise SaleInputError("A product in the cart is no longer available.")

            lines = []
            subtotal = Decimal("0.00")
            for product_id, quantity in quantities.items():
                product = products[product_id]
                if product.stock < quantity:
                    raise SaleInputError(
                        f"Only {product.stock} {product.name} item(s) remain in stock."
                    )
                line_total = product.price * quantity
                subtotal += line_total
                lines.append((product, quantity, line_total))

            customer = None
            customer_id = request.POST.get("customer_id")
            if customer_id and store.is_pro:
                customer = Customer.objects.select_for_update().filter(
                    pk=customer_id,
                    store=store,
                    active=True,
                ).first()
                if customer is None:
                    raise SaleInputError("The selected customer is unavailable.")
            if loyalty_points and customer is None:
                raise SaleInputError("Select a customer before redeeming loyalty points.")
            if customer and loyalty_points > customer.loyalty_points:
                raise SaleInputError(f"This customer has only {customer.loyalty_points} loyalty point(s).")

            discount = (subtotal * discount_rate / Decimal("100")).quantize(Decimal("0.01"))
            after_discount = max(Decimal("0.00"), subtotal - discount)
            loyalty_discount = min(Decimal(loyalty_points), after_discount)
            taxable_total = after_discount - loyalty_discount
            service_charge_rate = (
                store.service_charge_rate
                if store.supports_dining and order_type == Sale.OrderType.DINE_IN
                else Decimal("0.00")
            )
            service_charge = (
                taxable_total * service_charge_rate / Decimal("100")
            ).quantize(Decimal("0.01"))
            tax = (
                (taxable_total + service_charge) * store.tax_rate / Decimal("100")
            ).quantize(Decimal("0.01"))
            total = taxable_total + service_charge + tax
            points_earned = int(total // Decimal("100")) if customer else 0
            sale = Sale.objects.create(
                receipt_number=f"POS-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
                store=store,
                user=request.user,
                customer=customer,
                payment_method=payment,
                order_type=order_type,
                subtotal=subtotal,
                discount_rate=discount_rate,
                discount=discount,
                loyalty_discount=loyalty_discount,
                service_charge_rate=service_charge_rate,
                service_charge=service_charge,
                tax=tax,
                total=total,
                loyalty_points_earned=points_earned,
                loyalty_points_redeemed=loyalty_points,
                approved_by=approver,
            )
            for product, quantity, line_total in lines:
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    product_name=product.name,
                    quantity=quantity,
                    unit_cost=product.cost,
                    unit_price=product.price,
                    line_total=line_total,
                )
                updated = Product.objects.filter(
                    store=store,
                    pk=product.pk,
                    active=True,
                    stock__gte=quantity,
                ).update(stock=F("stock") - quantity, updated_at=timezone.now())
                if updated != 1:
                    raise SaleInputError(
                        f"Stock for {product.name} changed during checkout. Please review the order."
                    )
                InventoryMovement.objects.create(
                    store=store,
                    product=product,
                    movement_type=InventoryMovement.MovementType.SALE,
                    quantity=-quantity,
                    previous_stock=product.stock,
                    new_stock=product.stock - quantity,
                    reason="Checkout sale",
                    reference=sale.receipt_number,
                    user=request.user,
                )
            if customer:
                customer.loyalty_points = customer.loyalty_points - loyalty_points + points_earned
                customer.total_spent += total
                customer.save(update_fields=["loyalty_points", "total_spent"])
            _audit(
                store,
                request.user,
                "sale.completed",
                (
                    f"Completed {sale.receipt_number} as {order_type} for {total:.2f}."
                    if store.supports_dining
                    else f"Completed retail sale {sale.receipt_number} for {total:.2f}."
                ),
                approved_by=approver if discount_rate else None,
            )
    except SaleInputError as exc:
        messages.error(request, str(exc))
        return redirect("sell")

    messages.success(request, f"Sale {sale.receipt_number} completed successfully.")
    if store.receipt_after_sale:
        return redirect("receipt", pk=sale.pk)
    request.session["last_completed_sale_id"] = sale.pk
    return redirect("sell")


@store_required
def receipt(request, pk):
    sale = get_object_or_404(
        Sale.objects.filter(store=request.store).select_related("user", "customer", "approved_by").prefetch_related("items"),
        pk=pk,
    )
    return render(request, "pos/receipt.html", {"sale": sale, "store": request.store})


@store_required(pro=True)
@require_POST
def reverse_sale(request, pk):
    action = request.POST.get("action")
    if action not in {"refund", "void"}:
        messages.error(request, "Choose refund or void.")
        return redirect("receipt", pk=pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A reason is required for every reversal.")
        return redirect("receipt", pk=pk)
    try:
        approver = _resolve_approver(request)
        with transaction.atomic():
            sale = get_object_or_404(
                Sale.objects.select_for_update().select_related("customer"),
                pk=pk,
                store=request.store,
            )
            if sale.status != "Completed":
                raise SaleInputError(f"This sale is already {sale.status.lower()}.")
            for item in sale.items.select_related("product"):
                if not item.product_id or item.product.store_id != request.store.pk:
                    continue
                product = Product.objects.select_for_update().get(pk=item.product_id)
                previous_stock = product.stock
                product.stock += item.quantity
                product.save(update_fields=["stock", "updated_at"])
                InventoryMovement.objects.create(
                    store=request.store,
                    product=product,
                    movement_type=InventoryMovement.MovementType.RETURN,
                    quantity=item.quantity,
                    previous_stock=previous_stock,
                    new_stock=product.stock,
                    reason=reason[:240],
                    reference=sale.receipt_number,
                    user=request.user,
                )
            if sale.customer_id:
                customer = Customer.objects.select_for_update().get(pk=sale.customer_id)
                customer.loyalty_points = max(
                    0,
                    customer.loyalty_points - sale.loyalty_points_earned + sale.loyalty_points_redeemed,
                )
                customer.total_spent = max(Decimal("0.00"), customer.total_spent - sale.total)
                customer.save(update_fields=["loyalty_points", "total_spent"])
            sale.status = "Refunded" if action == "refund" else "Voided"
            sale.reversal_reason = reason[:240]
            sale.reversed_by = request.user
            sale.approved_by = approver
            sale.reversed_at = timezone.now()
            sale.save(
                update_fields=["status", "reversal_reason", "reversed_by", "approved_by", "reversed_at"]
            )
            _audit(
                request.store,
                request.user,
                f"sale.{action}",
                f"{sale.receipt_number} was {sale.status.lower()}: {reason[:180]}",
                approved_by=approver,
            )
    except SaleInputError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{sale.receipt_number} was {sale.status.lower()} and stock was restored.")
    return redirect("receipt", pk=pk)


@store_required(permission="can_manage_inventory", operational=True)
def products(request):
    store = request.store
    if request.method == "POST":
        form = ProductForm(request.POST, store=store)
        if form.is_valid():
            product = form.save()
            messages.success(request, f"{product.name} was added to inventory.")
            return redirect("products")
        messages.error(request, "Please correct the product details below.")
    else:
        form = ProductForm(store=store)
    query = request.GET.get("q", "").strip()
    product_list = Product.objects.filter(store=store)
    if query:
        product_list = product_list.filter(
            Q(name__icontains=query)
            | Q(barcode__icontains=query)
            | Q(category__icontains=query)
        )
    inventory_value = product_list.aggregate(
        value=Sum(ExpressionWrapper(F("price") * F("stock"), output_field=DecimalField(max_digits=14, decimal_places=2)))
    )["value"] or Decimal("0")
    return render(
        request,
        "pos/products.html",
        {
            "products": product_list,
            "form": form,
            "category_form": ProductCategoryForm(store=store),
            "product_categories": store.product_categories.all(),
            "import_form": ProductCSVImportForm(),
            "query": query,
            "inventory_value": inventory_value,
            "low_count": Product.objects.filter(
                store=store,
                stock__lte=F("low_stock_threshold"),
                active=True,
            ).count(),
            "store": store,
        },
    )


@store_required(permission="can_manage_inventory", operational=True)
@require_POST
def edit_product_picture(request, pk):
    product = get_object_or_404(Product, pk=pk, store=request.store)
    form = ProductPictureForm(request.POST, instance=product)
    if not form.is_valid():
        error = form.errors.get("picture_url", ["Enter a valid product picture URL."])[0]
        messages.error(request, f"Picture was not updated: {error}")
        return redirect("products")
    product = form.save()
    _audit(
        request.store,
        request.user,
        "inventory.picture_updated",
        f"Updated the product picture for {product.name}.",
    )
    if product.picture_url:
        messages.success(request, f"{product.name}'s picture was updated.")
    else:
        messages.success(request, f"{product.name} now uses its automatic placeholder picture.")
    return redirect("products")


@store_required
def product_placeholder(request, pk):
    product = get_object_or_404(Product, pk=pk, store=request.store)
    palettes = {
        "mint": ("#dff7ed", "#118564"),
        "amber": ("#fff0c9", "#a96408"),
        "coral": ("#ffe3dc", "#b84b35"),
        "blue": ("#e1edff", "#315eaa"),
    }
    background, foreground = palettes.get(product.color_class, palettes["blue"])
    product_name = escape(product.name[:34])
    category = escape(product.category[:28])
    icon = escape(product.picture_emoji)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="420" viewBox="0 0 640 420" role="img" aria-label="{product_name}">
<rect width="640" height="420" rx="34" fill="{background}"/>
<circle cx="320" cy="154" r="94" fill="#fff" fill-opacity=".72"/>
<text x="320" y="186" text-anchor="middle" font-family="Segoe UI Emoji,Apple Color Emoji,sans-serif" font-size="104">{icon}</text>
<text x="320" y="300" text-anchor="middle" font-family="Arial,sans-serif" font-size="34" font-weight="700" fill="{foreground}">{product_name}</text>
<text x="320" y="342" text-anchor="middle" font-family="Arial,sans-serif" font-size="20" fill="{foreground}" opacity=".72">{category}</text>
</svg>"""
    response = HttpResponse(svg, content_type="image/svg+xml; charset=utf-8")
    response["Cache-Control"] = "private, max-age=3600"
    return response


@store_required(permission="can_manage_inventory", operational=True)
@require_POST
def add_product_category(request):
    form = ProductCategoryForm(request.POST, store=request.store)
    if not form.is_valid():
        error = form.errors.get("name", ["Enter a valid category name."])[0]
        messages.error(request, f"Category was not added: {error}")
        return redirect("products")
    try:
        with transaction.atomic():
            category = form.save(commit=False)
            category.store = request.store
            category.is_custom = True
            category.save()
            _audit(
                request.store,
                request.user,
                "inventory.category_added",
                f"Added the custom product category {category.name}.",
            )
    except IntegrityError:
        messages.error(request, "That category already exists in this store.")
    else:
        messages.success(request, f"{category.name} is now available when adding products.")
    return redirect("products")


@store_required(permission="can_manage_inventory", operational=True)
@require_POST
def adjust_stock(request, pk):
    try:
        amount = int(request.POST.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0
    if amount == 0:
        messages.error(request, "Enter a non-zero stock adjustment.")
        return redirect("products")

    with transaction.atomic():
        product = get_object_or_404(
            Product.objects.select_for_update(),
            pk=pk,
            store=request.store,
        )
        new_stock = product.stock + amount
        if new_stock < 0:
            messages.error(request, f"{product.name} only has {product.stock} item(s) in stock.")
            return redirect("products")
        previous_stock = product.stock
        product.stock = new_stock
        product.save(update_fields=["stock", "updated_at"])
        InventoryMovement.objects.create(
            store=request.store,
            product=product,
            movement_type=InventoryMovement.MovementType.ADJUSTMENT,
            quantity=amount,
            previous_stock=previous_stock,
            new_stock=new_stock,
            reason=request.POST.get("reason", "Quick stock adjustment")[:240],
            user=request.user,
        )
    messages.success(request, f"{product.name} stock updated to {product.stock}.")
    return redirect("products")


@store_required(permission="can_view_reports")
def reports(request):
    end = timezone.localdate()
    range_key, start, period_days = _report_period(request.GET.get("range", "7"))
    if request.store.is_pro and request.GET.get("start") and request.GET.get("end"):
        custom_start = parse_date(request.GET.get("start"))
        custom_end = parse_date(request.GET.get("end"))
        if custom_start and custom_end and custom_start <= custom_end and (custom_end - custom_start).days <= 730:
            start, end = custom_start, custom_end
            period_days = (end - start).days + 1
            range_key = "custom"
        else:
            messages.error(request, "Choose a valid report period of 731 days or less.")
    transactions = Sale.objects.filter(
        store=request.store,
        created_at__date__gte=start,
        created_at__date__lte=end,
    )
    sales = transactions.filter(status="Completed")
    sale_items = SaleItem.objects.filter(sale__in=sales)
    top_products = sale_items.values("product_name").annotate(
        quantity=Sum("quantity"), revenue=Sum("line_total")
    ).order_by("-revenue")[:8]
    payment_mix = sales.values("payment_method").annotate(value=Sum("total")).order_by("-value")
    gross_margin = sale_items.aggregate(
        value=Sum(ExpressionWrapper(
            (F("unit_price") - F("unit_cost")) * F("quantity"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ))
    )["value"] or Decimal("0")
    profit = gross_margin - _money_sum(sales, "discount") - _money_sum(sales, "loyalty_discount")
    daily = {
        row["created_at__date"]: row["value"] or Decimal("0")
        for row in sales.values("created_at__date").annotate(value=Sum("total"))
    }
    chart_days = min(period_days, 14)
    chart_start = end - timedelta(days=chart_days - 1)
    values = [daily.get(chart_start + timedelta(days=i), Decimal("0")) for i in range(chart_days)]
    maximum = max(values) if values else Decimal("1")
    if maximum == 0:
        maximum = Decimal("1")
    chart_points = [
        {"label": (chart_start + timedelta(days=i)).strftime("%b %d"), "height": max(7, int(value / maximum * 100)), "value": value}
        for i, value in enumerate(values)
    ]
    total = _money_sum(sales)
    previous_total = Decimal("0.00")
    comparison_percent = None
    cashier_performance = []
    category_performance = []
    if request.store.is_pro:
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=period_days - 1)
        previous_sales = Sale.objects.filter(
            store=request.store,
            status="Completed",
            created_at__date__gte=previous_start,
            created_at__date__lte=previous_end,
        )
        previous_total = _money_sum(previous_sales)
        if previous_total:
            comparison_percent = ((total - previous_total) / previous_total * Decimal("100")).quantize(Decimal("0.1"))
        cashier_performance = sales.values("user__username").annotate(
            orders=Count("id"),
            revenue=Sum("total"),
        ).order_by("-revenue")
        category_performance = sale_items.values("product__category").annotate(
            quantity=Sum("quantity"),
            revenue=Sum("line_total"),
        ).order_by("-revenue")
    context = {
        "store": request.store,
        "range_key": range_key,
        "gross_sales": total,
        "net_profit": profit,
        "order_count": sales.count(),
        "average_order": total / sales.count() if sales.exists() else Decimal("0"),
        "top_products": top_products,
        "payment_mix": payment_mix,
        "chart_points": chart_points,
        "start": start,
        "end": end,
        "previous_total": previous_total,
        "comparison_percent": comparison_percent,
        "cashier_performance": cashier_performance,
        "category_performance": category_performance,
        "service_charges": _money_sum(sales, "service_charge"),
        "dine_in_orders": sales.filter(order_type=Sale.OrderType.DINE_IN).count(),
        "take_out_orders": sales.filter(order_type=Sale.OrderType.TAKE_OUT).count(),
        "transactions": transactions.select_related("user", "customer").order_by("-created_at")[:100],
    }
    return render(request, "pos/reports.html", context)


@store_required(administrator=True)
def settings_page(request):
    store = request.store
    team_form = StoreTeamMemberForm(store=store)
    schedule_form = ReportScheduleForm(store=store)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "team":
            team_form = StoreTeamMemberForm(request.POST, store=store)
            team_is_valid = team_form.is_valid()
            if not store.is_subscription_active:
                team_form.add_error(None, "Staff accounts cannot be added while this store is suspended or expired.")
                team_is_valid = False
            if team_is_valid:
                try:
                    membership = team_form.save()
                except ValidationError as exc:
                    for message in exc.messages:
                        team_form.add_error(None, message)
                else:
                    _audit(
                        store,
                        request.user,
                        "team.staff_added",
                        f"Added {membership.user.username} as {membership.access_level}.",
                    )
                    messages.success(request, f"{membership.user.username} can now access {store.business_name}.")
                    return redirect("settings")
            messages.error(request, "Please correct the staff account details below.")
        elif action == "schedule" and store.is_pro:
            schedule_form = ReportScheduleForm(request.POST, store=store)
            if schedule_form.is_valid():
                schedule = schedule_form.save()
                _audit(store, request.user, "report.schedule_added", f"Added {schedule.frequency} reports for {schedule.recipient_email}.")
                messages.success(request, "The scheduled report recipient was added.")
                return redirect("settings")
            messages.error(request, "Please correct the scheduled report details below.")
        else:
            raise PermissionDenied("This store setting cannot be changed here.")
    return render(
        request,
        "pos/settings.html",
        {
            "store": store,
            "team_form": team_form,
            "schedule_form": schedule_form,
            "starter_price": StoreSettings.PLAN_PRICES["Starter"],
            "pro_price": StoreSettings.PLAN_PRICES["Pro"],
            "report_schedules": store.report_schedules.all(),
            "administrator_memberships": store.memberships.filter(
                role=StoreMembership.Role.ADMINISTRATOR,
                active=True,
            ).select_related("user"),
            "team_memberships": store.memberships.filter(
                role=StoreMembership.Role.STAFF,
                active=True,
            ).select_related("user"),
            "staff_count": store.active_staff_count,
            "staff_slots_remaining": max(0, store.staff_limit - store.active_staff_count),
        },
    )


@store_required(administrator=True)
@require_POST
def remove_staff(request, pk):
    membership = get_object_or_404(
        StoreMembership.objects.select_related("user"),
        pk=pk,
        store=request.store,
        role=StoreMembership.Role.STAFF,
    )
    username = membership.user.username
    membership.delete()
    _audit(request.store, request.user, "team.staff_removed", f"Removed {username} from the store.")
    messages.success(request, f"{username} no longer has access to {request.store.business_name}.")
    return redirect("settings")


@store_required(administrator=True, pro=True)
def multi_store_dashboard(request):
    """Render portfolio-wide operating data for the signed-in Pro owner.

    The store list comes only from active administrator memberships. Every
    metric is then scoped to those stores, while opening an action goes through
    ``switch_store`` so the destination page receives the correct tenant.
    """
    pro_memberships = StoreMembership.objects.filter(
        user=request.user,
        active=True,
        role=StoreMembership.Role.ADMINISTRATOR,
        store__active_plan="Pro",
    ).select_related("store")
    stores = [membership.store for membership in pro_memberships]
    today = timezone.localdate()
    start = today - timedelta(days=29)
    rows = []
    alerts = []
    for store in stores:
        sales = Sale.objects.filter(
            store=store,
            status="Completed",
            created_at__date__gte=start,
        )
        today_sales = sales.filter(created_at__date=today)
        products = Product.objects.filter(store=store, active=True)
        low_stock = products.filter(stock__lte=F("low_stock_threshold")).count()
        out_of_stock = products.filter(stock=0).count()
        team_counts = store.memberships.filter(active=True).aggregate(
            administrators=Count("id", filter=Q(role=StoreMembership.Role.ADMINISTRATOR)),
            staff=Count("id", filter=Q(role=StoreMembership.Role.STAFF)),
        )
        open_register = CashRegisterSession.objects.filter(
            store=store,
            closed_at__isnull=True,
        ).select_related("user").first()
        row_alerts = []
        if not store.is_subscription_active:
            row_alerts.append(f"Subscription is {store.subscription_status.lower()}.")
        elif store.days_until_expiry is not None and store.days_until_expiry <= 7:
            if store.days_until_expiry == 0:
                row_alerts.append("Subscription expires today.")
            else:
                row_alerts.append(f"Subscription expires in {store.days_until_expiry} day(s).")
        if low_stock:
            stock_message = f"{low_stock} low-stock product(s)"
            if out_of_stock:
                stock_message += f", including {out_of_stock} out of stock"
            row_alerts.append(f"{stock_message}.")
        if not products.exists():
            row_alerts.append("No active products have been added.")
        row = {
            "store": store,
            "revenue": _money_sum(sales),
            "orders": sales.count(),
            "today_revenue": _money_sum(today_sales),
            "today_orders": today_sales.count(),
            "last_sale": Sale.objects.filter(store=store, status="Completed").first(),
            "product_count": products.count(),
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
            "inventory_units": products.aggregate(value=Sum("stock"))["value"] or 0,
            "customers": Customer.objects.filter(store=store, active=True).count(),
            "administrators": team_counts["administrators"],
            "staff": team_counts["staff"],
            "open_register": open_register,
            "open_purchase_orders": PurchaseOrder.objects.filter(
                store=store,
                status=PurchaseOrder.Status.ORDERED,
            ).count(),
            "alerts": row_alerts,
        }
        rows.append(row)
        if row_alerts:
            alerts.append(row)
    all_sales = Sale.objects.filter(
        store__in=stores,
        status="Completed",
        created_at__date__gte=start,
    )
    today_sales = all_sales.filter(created_at__date=today)
    return render(
        request,
        "pos/multi_store.html",
        {
            "store": request.store,
            "rows": rows,
            "store_count": len(stores),
            "total_revenue": _money_sum(all_sales),
            "total_orders": all_sales.count(),
            "today_revenue": _money_sum(today_sales),
            "today_orders": today_sales.count(),
            "total_inventory_units": sum(row["inventory_units"] for row in rows),
            "total_low_stock": sum(row["low_stock"] for row in rows),
            "attention_store_count": len(alerts),
            "alerts": alerts,
            "recent_activity": StoreAuditEvent.objects.filter(store__in=stores).select_related("store", "actor")[:12],
        },
    )


@store_required(permission="can_manage_customers", pro=True)
def customers(request):
    store = request.store
    form = CustomerForm(request.POST or None, store=store)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        _audit(store, request.user, "customer.created", f"Created customer {customer.name}.")
        messages.success(request, f"{customer.name} was added to customers.")
        return redirect("customers")
    query = request.GET.get("q", "").strip()
    customer_list = Customer.objects.filter(store=store, active=True)
    if query:
        customer_list = customer_list.filter(
            Q(name__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query)
        )
    return render(
        request,
        "pos/customers.html",
        {"store": store, "customers": customer_list, "form": form, "query": query},
    )


@store_required(permission="can_manage_inventory", pro=True, operational=True)
def pro_inventory(request):
    store = request.store
    stocktake_form = StocktakeForm(store=store)
    transfer_form = StockTransferForm(store=store, user=request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "stocktake":
            stocktake_form = StocktakeForm(request.POST, store=store)
            if stocktake_form.is_valid():
                with transaction.atomic():
                    product = Product.objects.select_for_update().get(
                        pk=stocktake_form.cleaned_data["product"].pk,
                        store=store,
                    )
                    previous_stock = product.stock
                    product.stock = stocktake_form.cleaned_data["counted_stock"]
                    product.save(update_fields=["stock", "updated_at"])
                    InventoryMovement.objects.create(
                        store=store,
                        product=product,
                        movement_type=InventoryMovement.MovementType.STOCKTAKE,
                        quantity=product.stock - previous_stock,
                        previous_stock=previous_stock,
                        new_stock=product.stock,
                        reason=stocktake_form.cleaned_data["reason"] or "Physical stocktake",
                        user=request.user,
                    )
                    _audit(store, request.user, "inventory.stocktake", f"Counted {product.name}: {previous_stock} to {product.stock}.")
                messages.success(request, f"{product.name} was updated to the counted quantity.")
                return redirect("pro_inventory")
        elif action == "transfer":
            transfer_form = StockTransferForm(request.POST, store=store, user=request.user)
            if transfer_form.is_valid():
                try:
                    with transaction.atomic():
                        source_product = Product.objects.select_for_update().get(
                            pk=transfer_form.cleaned_data["product"].pk,
                            store=store,
                        )
                        quantity = transfer_form.cleaned_data["quantity"]
                        if source_product.stock < quantity:
                            raise SaleInputError(f"Only {source_product.stock} item(s) remain in stock.")
                        destination = transfer_form.cleaned_data["destination_store"]
                        destination_product = Product.objects.select_for_update().filter(
                            store=destination,
                            barcode=source_product.barcode,
                        ).first()
                        if destination_product is None:
                            destination_product = Product.objects.create(
                                store=destination,
                                name=source_product.name,
                                category=source_product.category,
                                barcode=source_product.barcode,
                                cost=source_product.cost,
                                price=source_product.price,
                                stock=0,
                                low_stock_threshold=source_product.low_stock_threshold,
                            )
                        transfer = StockTransfer(
                            source_store=store,
                            destination_store=destination,
                            created_by=request.user,
                            notes=transfer_form.cleaned_data["notes"],
                        )
                        transfer.full_clean()
                        transfer.save()
                        source_previous = source_product.stock
                        destination_previous = destination_product.stock
                        source_product.stock -= quantity
                        destination_product.stock += quantity
                        source_product.save(update_fields=["stock", "updated_at"])
                        destination_product.save(update_fields=["stock", "updated_at"])
                        StockTransferItem.objects.create(
                            transfer=transfer,
                            source_product=source_product,
                            destination_product=destination_product,
                            quantity=quantity,
                        )
                        reference = f"TRANSFER-{transfer.pk}"
                        InventoryMovement.objects.bulk_create([
                            InventoryMovement(
                                store=store,
                                product=source_product,
                                movement_type=InventoryMovement.MovementType.TRANSFER_OUT,
                                quantity=-quantity,
                                previous_stock=source_previous,
                                new_stock=source_product.stock,
                                reason=transfer.notes,
                                reference=reference,
                                user=request.user,
                            ),
                            InventoryMovement(
                                store=destination,
                                product=destination_product,
                                movement_type=InventoryMovement.MovementType.TRANSFER_IN,
                                quantity=quantity,
                                previous_stock=destination_previous,
                                new_stock=destination_product.stock,
                                reason=transfer.notes,
                                reference=reference,
                                user=request.user,
                            ),
                        ])
                        _audit(store, request.user, "inventory.transfer_out", f"Transferred {quantity} {source_product.name} to {destination.business_name}.")
                        _audit(destination, request.user, "inventory.transfer_in", f"Received {quantity} {source_product.name} from {store.business_name}.")
                except SaleInputError as exc:
                    transfer_form.add_error("quantity", exc)
                else:
                    messages.success(request, f"Stock was transferred to {destination.business_name}.")
                    return redirect("pro_inventory")
        else:
            raise PermissionDenied("Unknown inventory action.")
    return render(
        request,
        "pos/pro_inventory.html",
        {
            "store": store,
            "stocktake_form": stocktake_form,
            "transfer_form": transfer_form,
            "movements": store.inventory_movements.select_related("product", "user")[:50],
            "low_products": Product.objects.filter(store=store, active=True, stock__lte=F("low_stock_threshold")),
            "transfers": StockTransfer.objects.filter(Q(source_store=store) | Q(destination_store=store)).prefetch_related("items")[:20],
            "can_transfer": transfer_form.fields["destination_store"].queryset.exists(),
        },
    )


@store_required(pro=True, operational=True)
def cash_register(request):
    store = request.store
    current = CashRegisterSession.objects.filter(
        store=store,
        closed_at__isnull=True,
    ).first()
    active_activity = (
        current.activities.filter(ended_at__isnull=True).select_related("user").first()
        if current
        else None
    )
    user_has_control = bool(active_activity and active_activity.user_id == request.user.pk)
    open_form = CashRegisterOpenForm()
    close_form = CashRegisterCloseForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "open":
            open_form = CashRegisterOpenForm(request.POST)
            if current:
                open_form.add_error(None, "This store already has an open daily register. Resume it instead.")
            elif open_form.is_valid():
                with transaction.atomic():
                    current = CashRegisterSession.objects.create(
                        store=store,
                        user=request.user,
                        opening_cash=open_form.cleaned_data["opening_cash"],
                        notes=open_form.cleaned_data["notes"],
                    )
                    CashRegisterActivity.objects.create(
                        register=current,
                        user=request.user,
                        start_reason=CashRegisterActivity.StartReason.OPENED,
                        notes=open_form.cleaned_data["notes"],
                    )
                _audit(store, request.user, "register.opened", f"Opened register with {current.opening_cash:.2f}.")
                messages.success(request, "The shared daily cash register is open.")
                return redirect("cash_register")
        elif action == "resume":
            if not current:
                messages.error(request, "There is no shared register to resume.")
            else:
                previous_user = active_activity.user.username if active_activity else "the previous staff member"
                current, active_activity = assign_open_register(
                    store,
                    request.user,
                    request.POST.get("notes", "")[:240],
                )
                _audit(store, request.user, "register.resumed", f"Resumed the shared register after {previous_user}.")
                messages.success(request, "Shared register resumed. The opening cash carries forward automatically.")
                return redirect("cash_register")
        elif action == "close":
            close_form = CashRegisterCloseForm(request.POST)
            if not current:
                close_form.add_error(None, "There is no open register session.")
            elif not user_has_control:
                close_form.add_error(None, "Resume the shared register before completing the final close.")
            elif close_form.is_valid():
                with transaction.atomic():
                    now = timezone.now()
                    active_activity.ended_at = now
                    active_activity.end_reason = CashRegisterActivity.EndReason.FINAL_CLOSE
                    active_activity.save(update_fields=["ended_at", "end_reason"])
                    current.closing_cash = close_form.cleaned_data["closing_cash"]
                    current.notes = close_form.cleaned_data["notes"] or current.notes
                    current.closed_at = now
                    current.save(update_fields=["closing_cash", "notes", "closed_at"])
                _audit(store, request.user, "register.closed", f"Closed register with variance {current.variance:.2f}.")
                messages.success(request, f"Daily register closed. Variance: ₱{current.variance:.2f}.")
                return redirect("cash_register")
        else:
            raise PermissionDenied("Unknown register action.")
    end_date = parse_date(request.GET.get("end", "")) or timezone.localdate()
    start_date = parse_date(request.GET.get("start", "")) or end_date - timedelta(days=6)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    sessions = CashRegisterSession.objects.filter(
        store=store,
        business_date__range=(start_date, end_date),
    ).select_related("user")
    return render(
        request,
        "pos/cash_register.html",
        {
            "store": store,
            "current_session": current,
            "active_activity": active_activity,
            "user_has_control": user_has_control,
            "open_form": open_form,
            "close_form": close_form,
            "sessions": sessions[:60],
            "activities": CashRegisterActivity.objects.filter(register__in=sessions).select_related("user", "register")[:150],
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@store_required(administrator=True, pro=True)
def export_cash_register(request):
    end_date = parse_date(request.GET.get("end", "")) or timezone.localdate()
    start_date = parse_date(request.GET.get("start", "")) or end_date - timedelta(days=6)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    sessions = CashRegisterSession.objects.filter(
        store=request.store,
        business_date__range=(start_date, end_date),
    ).select_related("user").prefetch_related("activities__user")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="{request.store.store_id}-cash-register-{start_date}-{end_date}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "Business date", "Register opened", "Register closed", "Opening staff", "Opening cash",
        "Cash sales", "Expected cash", "Closing cash", "Variance", "Staff", "Activity start",
        "Activity end", "Start reason", "End reason", "Activity notes", "Register notes",
    ])
    for register in sessions:
        activities = list(register.activities.all()) or [None]
        for activity in activities:
            writer.writerow([
                register.business_date,
                timezone.localtime(register.opened_at).isoformat(),
                timezone.localtime(register.closed_at).isoformat() if register.closed_at else "",
                register.user.username,
                register.opening_cash,
                register.cash_sales,
                register.expected_cash,
                register.closing_cash if register.closing_cash is not None else "",
                register.variance if register.variance is not None else "",
                activity.user.username if activity else "",
                timezone.localtime(activity.started_at).isoformat() if activity else "",
                timezone.localtime(activity.ended_at).isoformat() if activity and activity.ended_at else "",
                activity.start_reason if activity else "",
                activity.end_reason if activity else "",
                activity.notes if activity else "",
                register.notes,
            ])
    return response


@store_required(permission="can_manage_purchasing", pro=True, operational=True)
def purchasing(request):
    store = request.store
    supplier_form = SupplierForm(store=store)
    order_form = PurchaseOrderForm(store=store)
    item_formset = PurchaseOrderItemFormSet(prefix="items", form_kwargs={"store": store})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "supplier":
            supplier_form = SupplierForm(request.POST, store=store)
            if supplier_form.is_valid():
                supplier = supplier_form.save()
                _audit(store, request.user, "supplier.created", f"Added supplier {supplier.name}.")
                messages.success(request, f"{supplier.name} was added.")
                return redirect("purchasing")
        elif action == "purchase_order":
            order_form = PurchaseOrderForm(request.POST, store=store)
            item_formset = PurchaseOrderItemFormSet(
                request.POST,
                prefix="items",
                form_kwargs={"store": store},
            )
            if order_form.is_valid() and item_formset.is_valid():
                lines = [form.cleaned_data for form in item_formset if form.cleaned_data.get("product")]
                if not lines:
                    order_form.add_error(None, "Add at least one product line.")
                else:
                    with transaction.atomic():
                        order = order_form.save(commit=False)
                        order.order_number = f"PO-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
                        order.store = store
                        order.created_by = request.user
                        order.save()
                        for line in lines:
                            PurchaseOrderItem.objects.create(purchase_order=order, **line)
                        _audit(store, request.user, "purchase_order.created", f"Created {order.order_number} for {order.supplier.name}.")
                    messages.success(request, f"{order.order_number} was created.")
                    return redirect("purchasing")
        else:
            raise PermissionDenied("Unknown purchasing action.")
    purchase_orders = PurchaseOrder.objects.filter(store=store).select_related(
        "supplier", "created_by"
    ).prefetch_related("items__product")
    return render(
        request,
        "pos/purchasing.html",
        {
            "store": store,
            "supplier_form": supplier_form,
            "order_form": order_form,
            "item_formset": item_formset,
            "suppliers": Supplier.objects.filter(store=store, active=True),
            "purchase_orders": purchase_orders[:30],
            "open_order_count": purchase_orders.filter(status=PurchaseOrder.Status.ORDERED).count(),
        },
    )


@store_required(permission="can_manage_purchasing", pro=True, operational=True)
@require_POST
def receive_purchase_order(request, pk):
    with transaction.atomic():
        order = get_object_or_404(
            PurchaseOrder.objects.select_for_update().prefetch_related("items__product"),
            pk=pk,
            store=request.store,
        )
        if order.status != PurchaseOrder.Status.ORDERED:
            messages.error(request, "Only an ordered purchase order can be received.")
            return redirect("purchasing")
        for item in order.items.all():
            product = Product.objects.select_for_update().get(pk=item.product_id, store=request.store)
            previous_stock = product.stock
            product.stock += item.quantity
            product.cost = item.unit_cost
            product.save(update_fields=["stock", "cost", "updated_at"])
            InventoryMovement.objects.create(
                store=request.store,
                product=product,
                movement_type=InventoryMovement.MovementType.PURCHASE,
                quantity=item.quantity,
                previous_stock=previous_stock,
                new_stock=product.stock,
                reason=f"Received from {order.supplier.name}",
                reference=order.order_number,
                user=request.user,
            )
        order.status = PurchaseOrder.Status.RECEIVED
        order.received_at = timezone.now()
        order.save(update_fields=["status", "received_at"])
        _audit(request.store, request.user, "purchase_order.received", f"Received {order.order_number}.")
    messages.success(request, f"{order.order_number} was received into inventory.")
    return redirect("purchasing")


@store_required(permission="can_manage_inventory", pro=True, operational=True)
@require_POST
def import_products(request):
    form = ProductCSVImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Upload a valid product CSV file.")
        return redirect("products")
    upload = form.cleaned_data["csv_file"]
    created_count = 0
    updated_count = 0
    allowed_categories = {
        name.casefold(): name for name in request.store.product_categories.values_list("name", flat=True)
    }
    try:
        reader = csv.DictReader(TextIOWrapper(upload.file, encoding="utf-8-sig"))
        required = {"name", "category", "barcode", "cost", "price", "stock", "low_stock_threshold"}
        if not reader.fieldnames or not required.issubset({name.strip() for name in reader.fieldnames}):
            raise ValidationError("The CSV columns do not match the product import template.")
        with transaction.atomic():
            for row_number, row in enumerate(reader, start=2):
                if row_number > 1001:
                    raise ValidationError("A single import can contain at most 1,000 products.")
                name = row["name"].strip()
                submitted_category = row["category"].strip()
                category = allowed_categories.get(submitted_category.casefold())
                barcode = row["barcode"].strip().upper()
                if not name:
                    raise ValidationError(f"Row {row_number}: name is required.")
                if not category:
                    raise ValidationError(
                        f"Row {row_number}: category is not available for this store. Add it on the Products page first."
                    )
                cost = Decimal(row["cost"])
                price = Decimal(row["price"])
                stock = int(row["stock"])
                threshold = int(row["low_stock_threshold"])
                if min(cost, price) < 0 or min(stock, threshold) < 0:
                    raise ValidationError(f"Row {row_number}: numeric values cannot be negative.")
                existing = Product.objects.filter(store=request.store, barcode=barcode).first() if barcode else None
                previous_stock = existing.stock if existing else 0
                if existing:
                    existing.name = name
                    existing.category = category
                    existing.cost = cost
                    existing.price = price
                    existing.stock = stock
                    existing.low_stock_threshold = threshold
                    existing.full_clean()
                    existing.save()
                    product = existing
                    updated_count += 1
                else:
                    product = Product(
                        store=request.store,
                        name=name,
                        category=category,
                        barcode=barcode,
                        cost=cost,
                        price=price,
                        stock=stock,
                        low_stock_threshold=threshold,
                    )
                    product.full_clean()
                    product.save()
                    created_count += 1
                if product.stock != previous_stock:
                    InventoryMovement.objects.create(
                        store=request.store,
                        product=product,
                        movement_type=InventoryMovement.MovementType.IMPORT,
                        quantity=product.stock - previous_stock,
                        previous_stock=previous_stock,
                        new_stock=product.stock,
                        reason="Product CSV import",
                        user=request.user,
                    )
            _audit(request.store, request.user, "inventory.import", f"Imported {created_count} new and {updated_count} updated products.")
    except (ArithmeticError, KeyError, TypeError, ValueError, UnicodeDecodeError, ValidationError) as exc:
        messages.error(request, f"The CSV import was cancelled: {exc}")
    else:
        messages.success(request, f"Import complete: {created_count} created, {updated_count} updated.")
    return redirect("products")


@store_required(administrator=True, pro=True)
@require_POST
def remove_report_schedule(request, pk):
    schedule = get_object_or_404(ReportSchedule, pk=pk, store=request.store)
    email = schedule.recipient_email
    schedule.delete()
    _audit(request.store, request.user, "report.schedule_removed", f"Removed scheduled reports for {email}.")
    messages.success(request, "The scheduled report recipient was removed.")
    return redirect("settings")


@login_required
@require_POST
def switch_store(request):
    """Select an assigned Pro store and redirect into its tenant-scoped view."""
    membership = get_object_or_404(
        StoreMembership,
        store_id=request.POST.get("store_id"),
        user=request.user,
        active=True,
    )
    memberships = StoreMembership.objects.filter(user=request.user, active=True).select_related("store")
    if memberships.count() > 1 and (
        not membership.store.is_pro
        or memberships.exclude(store__active_plan="Pro").exists()
    ):
        raise PermissionDenied("Multiple-store switching requires the Pro plan on every assigned store.")
    request.session[CURRENT_STORE_SESSION_KEY] = membership.store_id
    messages.success(request, f"Now viewing {membership.store.business_name}.")
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("dashboard")


@store_required(permission="can_manage_inventory")
def export_products(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="positive-products.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Name", "Category", "Barcode", "Cost", "Price", "Stock", "Low stock threshold", "Picture URL"])
    for product in Product.objects.filter(store=request.store):
        writer.writerow([_csv_safe(product.name), product.category, _csv_safe(product.barcode), product.cost, product.price, product.stock, product.low_stock_threshold, _csv_safe(product.picture_url)])
    return response


@store_required(permission="can_view_reports")
def export_sales(request):
    requested_range = request.GET.get("range")
    sales = Sale.objects.filter(store=request.store, status="Completed").select_related("user", "customer")
    filename = "positive-sales.csv"
    if requested_range in REPORT_RANGES:
        range_key, start, _ = _report_period(requested_range)
        sales = sales.filter(created_at__date__gte=start)
        filename = f"positive-sales-{range_key}.csv"
    elif request.store.is_pro and request.GET.get("start") and request.GET.get("end"):
        try:
            start = date.fromisoformat(request.GET["start"])
            end = date.fromisoformat(request.GET["end"])
        except ValueError:
            raise PermissionDenied("Invalid report dates.")
        if end < start or (end - start).days > 731:
            raise PermissionDenied("The report range must be valid and no longer than two years.")
        sales = sales.filter(created_at__date__range=(start, end))
        filename = f"positive-sales-{start.isoformat()}-{end.isoformat()}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    writer = csv.writer(response)
    headers = ["Receipt", "Date", "Cashier", "Customer"]
    if request.store.supports_dining:
        headers.extend(["Order type", "Service charge rate", "Service charge"])
    headers.extend(["Payment", "Subtotal", "Discount", "Loyalty discount", "Tax", "Total"])
    writer.writerow(headers)
    for sale in sales:
        row = [
            sale.receipt_number,
            sale.created_at.isoformat(),
            _csv_safe(sale.user.username),
            _csv_safe(sale.customer.name if sale.customer else ""),
        ]
        if request.store.supports_dining:
            row.extend([sale.order_type, sale.service_charge_rate, sale.service_charge])
        row.extend([
            sale.payment_method,
            sale.subtotal,
            sale.discount,
            sale.loyalty_discount,
            sale.tax,
            sale.total,
        ])
        writer.writerow(row)
    return response
