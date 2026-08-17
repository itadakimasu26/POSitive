import csv
import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    CashRegisterActivity,
    CashRegisterSession,
    Customer,
    DemoRequest,
    InventoryMovement,
    Product,
    ProductCategory,
    PurchaseOrder,
    ReportSchedule,
    Sale,
    StockTransfer,
    StoreAuditEvent,
    StoreMembership,
    StoreSettings,
    SubscriptionExtensionRequest,
    Supplier,
    UserSecurityProfile,
)


class StoreTestCase(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="strong-test-password",
        )
        self.store = StoreSettings.objects.create(
            business_name="First Store",
            store_id="STORE-ONE",
            active_plan="Starter",
            status="Active",
            subscription_start=today,
            subscription_end=today + timedelta(days=30),
        )
        self.membership = StoreMembership.objects.create(
            store=self.store,
            user=self.user,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        self.product = Product.objects.create(
            store=self.store,
            name="Test Latte",
            category="Drinks",
            barcode="LATTE-001",
            cost=Decimal("50.00"),
            price=Decimal("100.00"),
            stock=10,
            low_stock_threshold=3,
        )
        self.client.force_login(self.user)

    def complete_sale(self, product=None, quantity=1, **extra):
        product = product or self.product
        payload = {
            "cart_json": json.dumps([{"id": product.pk, "qty": quantity}]),
            "payment_method": "Cash",
            **extra,
        }
        return self.client.post(reverse("complete_sale"), payload)


class PosFlowTests(StoreTestCase):
    def test_dashboard_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_complete_sale_is_scoped_and_reduces_stock(self):
        response = self.complete_sale(quantity=2, payment_method="GCash")
        sale = Sale.objects.get()
        self.assertRedirects(response, reverse("receipt", args=[sale.pk]))
        self.assertEqual(sale.store, self.store)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(sale.total, Decimal("224.00"))
        self.assertEqual(sale.items.get().unit_cost, Decimal("50.00"))

    def test_dine_in_sale_applies_store_service_charge_and_populates_receipt_and_reports(self):
        self.store.store_type = StoreSettings.StoreType.CAFE
        self.store.service_charge_rate = Decimal("10.00")
        self.store.save(update_fields=["store_type", "service_charge_rate"])

        response = self.complete_sale(order_type=Sale.OrderType.DINE_IN)
        sale = Sale.objects.get()

        self.assertRedirects(response, reverse("receipt", args=[sale.pk]))
        self.assertEqual(sale.order_type, Sale.OrderType.DINE_IN)
        self.assertEqual(sale.service_charge_rate, Decimal("10.00"))
        self.assertEqual(sale.service_charge, Decimal("10.00"))
        self.assertEqual(sale.tax, Decimal("13.20"))
        self.assertEqual(sale.total, Decimal("123.20"))

        receipt = self.client.get(reverse("receipt", args=[sale.pk]))
        self.assertContains(receipt, "Order type: Dine-in")
        self.assertContains(receipt, "Dine-in service charge")
        reports = self.client.get(reverse("reports"))
        self.assertContains(reports, sale.receipt_number)
        self.assertContains(reports, reverse("receipt", args=[sale.pk]))
        self.assertEqual(reports.context["service_charges"], Decimal("10.00"))
        self.assertEqual(reports.context["dine_in_orders"], 1)

    def test_take_out_sale_does_not_apply_configured_service_charge(self):
        self.store.store_type = StoreSettings.StoreType.CAFE
        self.store.service_charge_rate = Decimal("10.00")
        self.store.save(update_fields=["store_type", "service_charge_rate"])

        self.complete_sale(order_type=Sale.OrderType.TAKE_OUT)
        sale = Sale.objects.get()

        self.assertEqual(sale.order_type, Sale.OrderType.TAKE_OUT)
        self.assertEqual(sale.service_charge_rate, Decimal("0.00"))
        self.assertEqual(sale.service_charge, Decimal("0.00"))
        self.assertEqual(sale.total, Decimal("112.00"))

    def test_retail_store_hides_and_rejects_dining_options(self):
        """Non-food businesses must never acquire dining-only sale metadata."""
        self.store.store_type = StoreSettings.StoreType.ELECTRONICS
        self.store.service_charge_rate = Decimal("10.00")
        self.store.save(update_fields=["store_type", "service_charge_rate"])
        self.store.refresh_from_db()
        self.assertFalse(self.store.supports_dining)
        self.assertEqual(self.store.service_charge_rate, Decimal("0.00"))

        sell_page = self.client.get(reverse("sell"))
        self.assertNotContains(sell_page, "Dine-in")
        self.assertNotContains(sell_page, "Take-out")

        self.complete_sale(order_type=Sale.OrderType.DINE_IN)
        sale = Sale.objects.get()
        self.assertEqual(sale.order_type, Sale.OrderType.RETAIL)
        self.assertEqual(sale.service_charge_rate, Decimal("0.00"))
        self.assertEqual(sale.service_charge, Decimal("0.00"))
        self.assertEqual(sale.total, Decimal("112.00"))

        receipt = self.client.get(reverse("receipt", args=[sale.pk]))
        self.assertNotContains(receipt, "Order type")
        report = self.client.get(reverse("reports"))
        self.assertNotContains(report, "Dine-in orders")
        self.assertNotContains(report, "Service charge")
        export_header = self.client.get(reverse("export_sales")).content.decode().splitlines()[0]
        self.assertNotIn("Order type", export_header)
        self.assertNotIn("Service charge", export_header)

    def test_settings_keep_plan_prices_without_subscription_payment_controls(self):
        page = self.client.get(reverse("settings"))
        self.assertContains(page, "₱399")
        self.assertContains(page, "₱799")
        self.assertContains(page, "Prices are shown for plan comparison")
        for retired_copy in ["LANDBANK", "Maya", "Submit payment", "Proof of payment"]:
            self.assertNotContains(page, retired_copy)

        # Unknown settings actions cannot recreate the retired payment path.
        response = self.client.post(reverse("settings"), {"action": "subscription"})
        self.assertEqual(response.status_code, 403)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SUPPORT_CONTACT_EMAIL="oxpos-requests@example.com",
    )
    def test_expired_store_administrator_can_request_subscription_extension(self):
        self.store.subscription_end = timezone.localdate() - timedelta(days=1)
        self.store.save(update_fields=["subscription_end"])

        settings_page = self.client.get(reverse("settings"))
        self.assertContains(settings_page, "Request a subscription extension")
        self.assertContains(settings_page, "Requested plan")
        self.assertContains(settings_page, "Preferred payment type")
        self.assertContains(settings_page, "Comments (optional)")
        self.assertContains(settings_page, 'class="sync-state expired"')
        self.assertContains(settings_page, "online-dot expired")

        blocked_page = self.client.get(reverse("sell"))
        self.assertEqual(blocked_page.status_code, 403)
        self.assertContains(blocked_page, "Request an extension", status_code=403)

        response = self.client.post(
            reverse("settings"),
            {
                "action": "extension",
                "requested_plan": "Pro",
                "payment_type": "GCash",
                "comments": "Please contact me in the afternoon.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('settings')}#subscription-extension")

        extension_request = SubscriptionExtensionRequest.objects.get()
        self.assertEqual(extension_request.store, self.store)
        self.assertEqual(extension_request.requested_by, self.user)
        self.assertEqual(extension_request.requested_plan, "Pro")
        self.assertEqual(extension_request.payment_type, "GCash")
        self.assertEqual(extension_request.status, SubscriptionExtensionRequest.Status.NEW)
        self.assertIsNotNone(extension_request.email_sent_at)
        self.assertEqual(extension_request.email_error, "")
        self.assertTrue(
            StoreAuditEvent.objects.filter(
                store=self.store,
                actor=self.user,
                action="subscription.extension_requested",
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["oxpos-requests@example.com"])
        self.assertEqual(mail.outbox[0].reply_to, [self.user.email])
        self.assertIn("First Store (STORE-ONE)", mail.outbox[0].body)
        self.assertIn("Requested plan: Pro", mail.outbox[0].body)
        self.assertIn("Preferred payment type: GCash", mail.outbox[0].body)
        self.assertIn("Please contact me in the afternoon.", mail.outbox[0].body)
        self.assertIn(
            reverse("admin:pos_subscriptionextensionrequest_change", args=[extension_request.pk]),
            mail.outbox[0].body,
        )
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertEqual(mail.outbox[0].alternatives[0].mimetype, "text/html")
        request_html = mail.outbox[0].alternatives[0].content
        self.assertIn("New subscription extension request", request_html)
        self.assertIn("Action requested", request_html)
        self.assertIn("Review request in OXPOS", request_html)
        self.assertIn("First Store", request_html)
        self.assertIn("Please contact me in the afternoon.", request_html)
        self.assertIn(
            reverse("admin:pos_subscriptionextensionrequest_change", args=[extension_request.pk]),
            request_html,
        )

        pending_page = self.client.get(reverse("settings"))
        self.assertContains(pending_page, "Request under review")
        self.assertNotContains(pending_page, "Send extension request")
        duplicate_response = self.client.post(
            reverse("settings"),
            {
                "action": "extension",
                "requested_plan": "Starter",
                "payment_type": "Cash",
                "comments": "Duplicate request.",
            },
            follow=True,
        )
        self.assertContains(duplicate_response, "already has an extension request under review")
        self.assertEqual(SubscriptionExtensionRequest.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_database_prevents_two_open_extension_requests_for_one_store(self):
        first_request = SubscriptionExtensionRequest.objects.create(
            store=self.store,
            requested_by=self.user,
            requested_plan="Starter",
            payment_type="Cash",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SubscriptionExtensionRequest.objects.create(
                    store=self.store,
                    requested_by=self.user,
                    requested_plan="Pro",
                    payment_type="GCash",
                )

        first_request.status = SubscriptionExtensionRequest.Status.DECLINED
        first_request.save(update_fields=["status"])
        second_request = SubscriptionExtensionRequest.objects.create(
            store=self.store,
            requested_by=self.user,
            requested_plan="Pro",
            payment_type="GCash",
        )
        self.assertEqual(second_request.status, SubscriptionExtensionRequest.Status.NEW)

    def test_calendar_month_extension_end_is_inclusive(self):
        self.assertEqual(
            SubscriptionExtensionRequest.one_month_end(date(2026, 8, 17)),
            date(2026, 9, 16),
        )
        self.assertEqual(
            SubscriptionExtensionRequest.one_month_end(date(2026, 1, 31)),
            date(2026, 2, 28),
        )

    def test_active_store_cannot_submit_subscription_extension_request(self):
        response = self.client.post(
            reverse("settings"),
            {
                "action": "extension",
                "requested_plan": "Starter",
                "payment_type": "Cash",
                "comments": "Not expired.",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SubscriptionExtensionRequest.objects.exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch("pos.views.EmailMultiAlternatives.send", side_effect=OSError("SMTP unavailable"))
    def test_extension_request_survives_email_delivery_failure(self, _send_email):
        self.store.subscription_end = timezone.localdate() - timedelta(days=1)
        self.store.save(update_fields=["subscription_end"])

        response = self.client.post(
            reverse("settings"),
            {
                "action": "extension",
                "requested_plan": "Starter",
                "payment_type": "Bank transfer",
                "comments": "Please send bank instructions.",
            },
            follow=True,
        )
        self.assertContains(response, "request was recorded")
        extension_request = SubscriptionExtensionRequest.objects.get()
        self.assertIsNone(extension_request.email_sent_at)
        self.assertIn("OSError", extension_request.email_error)

    def test_pro_report_performance_cards_have_padded_headings(self):
        self.store.active_plan = "Pro"
        self.store.save(update_fields=["active_plan"])
        response = self.client.get(reverse("reports"))
        self.assertContains(response, 'class="card-head table-heading"', count=4)

    def test_pro_administrator_can_remove_scheduled_report_recipient(self):
        self.store.active_plan = "Pro"
        self.store.save(update_fields=["active_plan"])
        schedule = ReportSchedule.objects.create(
            store=self.store,
            recipient_email="remove-me@example.com",
            frequency=ReportSchedule.Frequency.DAILY,
        )
        response = self.client.post(
            reverse("remove_report_schedule", args=[schedule.pk]),
        )
        self.assertRedirects(response, reverse("settings"))
        self.assertFalse(ReportSchedule.objects.filter(pk=schedule.pk).exists())
        self.assertTrue(
            StoreAuditEvent.objects.filter(
                store=self.store,
                actor=self.user,
                action="report.schedule_removed",
            ).exists()
        )

    def test_duplicate_cart_lines_are_consolidated(self):
        response = self.client.post(
            reverse("complete_sale"),
            {
                "cart_json": json.dumps(
                    [{"id": self.product.pk, "qty": 1}, {"id": self.product.pk, "qty": 2}]
                )
            },
        )
        sale = Sale.objects.get()
        self.assertRedirects(response, reverse("receipt", args=[sale.pk]))
        self.assertEqual(sale.items.get().quantity, 3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)

    def test_duplicate_cart_lines_cannot_oversell(self):
        response = self.client.post(
            reverse("complete_sale"),
            {
                "cart_json": json.dumps(
                    [{"id": self.product.pk, "qty": 6}, {"id": self.product.pk, "qty": 5}]
                )
            },
            follow=True,
        )
        self.assertContains(response, "remain in stock")
        self.assertFalse(Sale.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_profit_uses_cost_captured_at_sale_time(self):
        self.complete_sale(quantity=2)
        self.product.cost = Decimal("90.00")
        self.product.save()
        response = self.client.get(reverse("reports"))
        self.assertEqual(response.context["net_profit"], Decimal("100.00"))

    def test_product_search_and_export_are_store_scoped(self):
        response = self.client.get(reverse("products"), {"q": self.product.barcode})
        self.assertContains(response, "Test Latte")
        export = self.client.get(reverse("export_products"))
        self.assertIn(b"Test Latte", export.content)

    def test_quick_stock_adjustment_endpoint_updates_inventory_and_audit(self):
        response = self.client.post(
            reverse("adjust_stock", args=[self.product.pk]),
            {"amount": "5", "reason": "Release health check"},
        )
        self.assertRedirects(response, reverse("products"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 15)
        movement = InventoryMovement.objects.get(
            product=self.product,
            movement_type=InventoryMovement.MovementType.ADJUSTMENT,
        )
        self.assertEqual(movement.quantity, 5)
        self.assertEqual(movement.reason, "Release health check")

    def test_product_uses_name_based_placeholder_and_picture_can_be_edited(self):
        placeholder_url = reverse("product_placeholder", args=[self.product.pk])
        placeholder = self.client.get(placeholder_url)
        self.assertEqual(placeholder.status_code, 200)
        self.assertEqual(placeholder["Content-Type"], "image/svg+xml; charset=utf-8")
        self.assertIn("☕".encode(), placeholder.content)
        self.assertContains(self.client.get(reverse("products")), placeholder_url)

        picture_url = "https://images.example.com/products/test-latte.jpg"
        response = self.client.post(
            reverse("edit_product_picture", args=[self.product.pk]),
            {"picture_url": picture_url},
        )
        self.assertRedirects(response, reverse("products"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.picture_url, picture_url)
        self.assertContains(self.client.get(reverse("products")), picture_url)

        self.client.post(
            reverse("edit_product_picture", args=[self.product.pk]),
            {"picture_url": ""},
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.picture_url, "")

    def test_receipt_preference_can_return_to_checkout(self):
        self.store.receipt_after_sale = False
        self.store.save()
        self.assertRedirects(self.complete_sale(), reverse("sell"))

    def test_negative_product_price_is_rejected_server_side(self):
        response = self.client.post(
            reverse("products"),
            {
                "name": "Invalid Product",
                "category": "Other",
                "barcode": "INVALID-PRICE",
                "cost": "10.00",
                "price": "-1.00",
                "stock": "1",
                "low_stock_threshold": "1",
            },
        )
        self.assertFormError(
            response.context["form"],
            "price",
            "Ensure this value is greater than or equal to 0.00.",
        )
        self.assertFalse(Product.objects.filter(store=self.store, barcode="INVALID-PRICE").exists())

    def test_store_uses_focused_categories_and_users_can_add_another(self):
        cafe = StoreSettings.objects.create(
            business_name="Focused Cafe",
            store_id="FOCUSED-CAFE",
            store_type=StoreSettings.StoreType.CAFE,
        )
        self.assertEqual(
            set(cafe.product_categories.values_list("name", flat=True)),
            set(StoreSettings.CATEGORY_PRESETS[StoreSettings.StoreType.CAFE]),
        )
        self.assertFalse(cafe.product_categories.filter(name="Hardware").exists())

        response = self.client.post(reverse("add_product_category"), {"name": "Frozen Desserts"})
        self.assertRedirects(response, reverse("products"))
        category = ProductCategory.objects.get(store=self.store, name="Frozen Desserts")
        self.assertTrue(category.is_custom)
        products_page = self.client.get(reverse("products"))
        self.assertContains(products_page, '<option value="Frozen Desserts">Frozen Desserts</option>', html=True)

    def test_product_rejects_category_that_is_not_available_to_store(self):
        response = self.client.post(
            reverse("products"),
            {
                "name": "Unfocused Product",
                "category": "Gadgets",
                "barcode": "WRONG-CATEGORY",
                "cost": "10.00",
                "price": "20.00",
                "stock": "1",
                "low_stock_threshold": "1",
            },
        )
        self.assertFormError(response.context["form"], "category", "Select a valid choice. Gadgets is not one of the available choices.")
        self.assertFalse(Product.objects.filter(store=self.store, barcode="WRONG-CATEGORY").exists())

    def test_store_type_change_replaces_only_unused_preset_categories(self):
        store = StoreSettings.objects.create(
            business_name="Changing Shop",
            store_id="CHANGING-SHOP",
            store_type=StoreSettings.StoreType.FASHION,
        )
        Product.objects.create(store=store, name="Running Shoes", category="Footwear", price=100, stock=1)
        store.store_type = StoreSettings.StoreType.BEAUTY
        store.save()
        names = set(store.product_categories.values_list("name", flat=True))
        self.assertTrue({"Cosmetics", "Personal Care", "Other", "Footwear"}.issubset(names))
        self.assertNotIn("Clothing", names)
        self.assertNotIn("Fashion Accessories", names)

    def test_expired_subscription_blocks_operations_but_keeps_dashboard(self):
        self.store.subscription_end = timezone.localdate() - timedelta(days=1)
        self.store.save()
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertContains(self.client.get(reverse("dashboard")), "Expired subscription")
        self.assertEqual(self.client.get(reverse("sell")).status_code, 403)
        self.assertEqual(self.client.get(reverse("products")).status_code, 403)


class OfflineSalesRecoveryTests(StoreTestCase):
    fields = [
        "offline_order_id",
        "sold_at",
        "payment_method",
        "product_name",
        "barcode",
        "unit_price",
        "quantity",
        "collected_order_total",
        "notes",
    ]

    def upload(self, rows, name="offline-sales.csv"):
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=self.fields)
        writer.writeheader()
        writer.writerows(rows)
        return SimpleUploadedFile(name, output.getvalue().encode("utf-8"), content_type="text/csv")

    def order_rows(self, *, order_id="OFF-20260808-001", total="336.00"):
        sold_at = timezone.localtime(timezone.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M")
        common = {
            "offline_order_id": order_id,
            "sold_at": sold_at,
            "payment_method": "Cash",
            "product_name": self.product.name,
            "barcode": self.product.barcode,
            "unit_price": "100.00",
            "collected_order_total": total,
            "notes": "Recorded during connectivity outage",
        }
        return [{**common, "quantity": "1"}, {**common, "quantity": "2"}]

    def test_template_contains_uploadable_headers_and_current_catalog(self):
        page = self.client.get(reverse("sell"))
        self.assertContains(page, "Outage recovery")
        self.assertContains(page, reverse("offline_sales_template"))
        self.assertContains(page, reverse("import_offline_sales"))

        response = self.client.get(reverse("offline_sales_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        rows = list(csv.DictReader(StringIO(response.content.decode("utf-8"))))
        self.assertEqual(list(rows[0]), self.fields)
        self.assertEqual(rows[0]["barcode"], self.product.barcode)
        self.assertEqual(rows[0]["unit_price"], "100.00")
        self.assertEqual(rows[0]["offline_order_id"], "")

    def test_import_is_atomic_audited_and_duplicate_safe(self):
        response = self.client.post(
            reverse("import_offline_sales"),
            {"csv_file": self.upload(self.order_rows())},
            follow=True,
        )
        self.assertContains(response, "Imported 1 offline order")
        sale = Sale.objects.get(external_order_id="OFF-20260808-001")
        self.assertEqual(sale.source, Sale.Source.OFFLINE_CSV)
        self.assertEqual(sale.subtotal, Decimal("300.00"))
        self.assertEqual(sale.tax, Decimal("36.00"))
        self.assertEqual(sale.total, Decimal("336.00"))
        self.assertEqual(sale.offline_notes, "Recorded during connectivity outage")
        item = sale.items.get()
        self.assertEqual(item.quantity, 3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)
        movement = InventoryMovement.objects.get(
            movement_type=InventoryMovement.MovementType.OFFLINE_SALE
        )
        self.assertEqual(movement.quantity, -3)
        self.assertEqual(movement.reference, "OFF-20260808-001")
        self.assertTrue(
            StoreAuditEvent.objects.filter(store=self.store, action="sale.offline_import").exists()
        )

        duplicate = self.client.post(
            reverse("import_offline_sales"),
            {"csv_file": self.upload(self.order_rows())},
            follow=True,
        )
        self.assertContains(duplicate, "already exist")
        self.assertEqual(Sale.objects.filter(source=Sale.Source.OFFLINE_CSV).count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)

    def test_invalid_total_rolls_back_the_entire_import(self):
        response = self.client.post(
            reverse("import_offline_sales"),
            {"csv_file": self.upload(self.order_rows(total="300.00"))},
            follow=True,
        )
        self.assertContains(response, "OXPOS calculates ₱336.00")
        self.assertFalse(Sale.objects.filter(source=Sale.Source.OFFLINE_CSV).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.MovementType.OFFLINE_SALE
            ).exists()
        )


class MultiStoreIsolationTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.store.active_plan = "Pro"
        self.store.save(update_fields=["active_plan"])
        self.other_store = StoreSettings.objects.create(
            business_name="Second Store",
            store_id="STORE-TWO",
            active_plan="Pro",
            status="Active",
            subscription_start=today,
            subscription_end=today + timedelta(days=60),
        )
        StoreMembership.objects.create(
            store=self.other_store,
            user=self.user,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        self.other_product = Product.objects.create(
            store=self.other_store,
            name="Other Store Tea",
            category="Drinks",
            barcode="LATTE-001",
            cost=Decimal("20.00"),
            price=Decimal("40.00"),
            stock=8,
        )

    def test_store_administrator_can_switch_between_all_assigned_stores(self):
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(len(dashboard.wsgi_request.available_memberships), 2)
        self.assertTrue(dashboard.wsgi_request.multi_store_enabled)
        self.assertContains(dashboard, "PRO MULTI-STORE")
        response = self.client.post(
            reverse("switch_store"),
            {"store_id": self.other_store.pk, "next": reverse("sell")},
        )
        self.assertRedirects(response, reverse("sell"))
        sell_page = self.client.get(reverse("sell"))
        self.assertContains(sell_page, "Other Store Tea")
        self.assertNotContains(sell_page, "Test Latte")

    def test_shared_pro_store_cannot_be_downgraded(self):
        self.store.active_plan = "Starter"
        with self.assertRaises(ValidationError):
            self.store.save(update_fields=["active_plan"])
        self.store.refresh_from_db()
        self.assertEqual(self.store.active_plan, "Pro")

    def test_owner_super_dashboard_summarizes_only_administered_pro_stores(self):
        today = timezone.localdate()
        Sale.objects.create(
            store=self.store,
            receipt_number="PORTFOLIO-FIRST",
            user=self.user,
            subtotal=Decimal("100.00"),
            tax=Decimal("0.00"),
            total=Decimal("100.00"),
        )
        Sale.objects.create(
            store=self.other_store,
            receipt_number="PORTFOLIO-SECOND",
            user=self.user,
            subtotal=Decimal("40.00"),
            tax=Decimal("0.00"),
            total=Decimal("40.00"),
        )
        unassigned_store = StoreSettings.objects.create(
            business_name="Unassigned Pro Store",
            store_id="UNASSIGNED-PRO",
            active_plan="Pro",
            subscription_end=today + timedelta(days=30),
        )
        Sale.objects.create(
            store=unassigned_store,
            receipt_number="PORTFOLIO-HIDDEN",
            user=self.user,
            subtotal=Decimal("999.00"),
            tax=Decimal("0.00"),
            total=Decimal("999.00"),
        )

        response = self.client.get(reverse("multi_store_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store_count"], 2)
        self.assertEqual(response.context["today_revenue"], Decimal("140.00"))
        self.assertEqual(response.context["total_revenue"], Decimal("140.00"))
        self.assertEqual(response.context["total_inventory_units"], 18)
        self.assertEqual(response.context["total_low_stock"], 1)
        self.assertEqual(response.context["attention_store_count"], 1)
        self.assertContains(response, "Owner super dashboard")
        self.assertContains(response, "First Store")
        self.assertContains(response, "Second Store")
        self.assertContains(response, "Open dashboard")
        self.assertContains(response, "Purchase orders")
        self.assertNotContains(response, "Unassigned Pro Store")
        self.assertNotContains(response, "999.00")

    def test_tampered_cart_cannot_sell_another_stores_product(self):
        response = self.complete_sale(product=self.other_product)
        self.assertRedirects(response, reverse("sell"))
        self.assertFalse(Sale.objects.exists())
        self.other_product.refresh_from_db()
        self.assertEqual(self.other_product.stock, 8)

    def test_receipts_and_exports_do_not_leak_between_stores(self):
        other_sale = Sale.objects.create(
            store=self.other_store,
            receipt_number="OTHER-RECEIPT",
            user=self.user,
            payment_method="Cash",
            subtotal=Decimal("40.00"),
            tax=Decimal("0.00"),
            total=Decimal("40.00"),
        )
        self.assertEqual(self.client.get(reverse("receipt", args=[other_sale.pk])).status_code, 404)
        self.assertNotIn("OTHER-RECEIPT", self.client.get(reverse("export_sales")).content.decode())

    def test_same_barcode_is_allowed_in_different_stores_only(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(
                store=self.store,
                name="Duplicate",
                barcode="LATTE-001",
                price=Decimal("10.00"),
            )


class StoreTeamAccessTests(StoreTestCase):
    def test_store_administrator_cannot_modify_platform_controlled_store(self):
        response = self.client.post(
            reverse("settings"),
            {
                "action": "store",
                "business_name": "Unauthorized Rename",
                "active_plan": "Pro",
                "status": "Suspended",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.store.refresh_from_db()
        self.assertEqual(self.store.business_name, "First Store")
        self.assertEqual(self.store.active_plan, "Starter")
        self.assertEqual(self.store.status, "Active")

    def test_store_administrator_can_create_staff_up_to_starter_limit(self):
        for index in range(2):
            response = self.client.post(
                reverse("settings"),
                {
                    "action": "team",
                    "username": f"cashier{index}",
                    "email": f"cashier{index}@example.com",
                    "first_name": "Cashier",
                    "last_name": str(index),
                    "password": "strong-cashier-password-42",
                    "password_confirm": "strong-cashier-password-42",
                },
            )
            self.assertRedirects(response, reverse("settings"))
        self.assertEqual(self.store.active_staff_count, 2)

        response = self.client.post(
            reverse("settings"),
            {
                "action": "team",
                "username": "cashier-over-limit",
                "email": "over@example.com",
                "password": "strong-cashier-password-42",
                "password_confirm": "strong-cashier-password-42",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Starter plan allows 2 staff account")
        self.assertFalse(User.objects.filter(username="cashier-over-limit").exists())

    def test_existing_user_cannot_be_assigned_to_multiple_starter_stores(self):
        today = timezone.localdate()
        second_store = StoreSettings.objects.create(
            business_name="Branch Two",
            store_id="BRANCH-TWO",
            active_plan="Starter",
            subscription_end=today + timedelta(days=30),
        )
        existing = User.objects.create_user(
            username="shared-staff",
            email="shared-staff@example.com",
            password="existing-password-42",
        )
        StoreMembership.objects.create(store=second_store, user=existing, role=StoreMembership.Role.STAFF)
        response = self.client.post(
            reverse("settings"),
            {"action": "team", "username": "shared-staff", "email": "", "password": "", "password_confirm": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Multiple-store access is a Pro feature")
        self.assertEqual(existing.store_memberships.count(), 1)
        existing.refresh_from_db()
        self.assertTrue(existing.check_password("existing-password-42"))

    def test_staff_only_see_assigned_stores_and_cannot_manage(self):
        cashier = User.objects.create_user(username="cashier", password="cashier-password-42")
        StoreMembership.objects.create(store=self.store, user=cashier, role=StoreMembership.Role.STAFF)
        unassigned = StoreSettings.objects.create(
            business_name="Hidden Store",
            store_id="HIDDEN",
            active_plan="Pro",
        )
        self.client.force_login(cashier)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual([item.store for item in dashboard.wsgi_request.available_memberships], [self.store])
        self.assertNotContains(dashboard, unassigned.business_name)
        for name in ["products", "reports", "settings", "export_products", "export_sales"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.client.get(reverse("sell")).status_code, 200)

    def test_removing_staff_revokes_only_that_store_membership(self):
        cashier = User.objects.create_user(username="cashier", password="cashier-password-42")
        membership = StoreMembership.objects.create(store=self.store, user=cashier, role=StoreMembership.Role.STAFF)
        self.client.post(reverse("remove_staff", args=[membership.pk]))
        self.assertFalse(StoreMembership.objects.filter(pk=membership.pk).exists())
        self.assertTrue(User.objects.filter(pk=cashier.pk).exists())

    def test_starter_allows_one_administrator_and_pro_allows_three(self):
        second_admin = User.objects.create_user(username="second-admin", password="second-admin-password-42")
        with self.assertRaises(ValidationError):
            StoreMembership.objects.create(
                store=self.store,
                user=second_admin,
                role=StoreMembership.Role.ADMINISTRATOR,
            )

        self.store.active_plan = "Pro"
        self.store.save()
        StoreMembership.objects.create(
            store=self.store,
            user=second_admin,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        third_admin = User.objects.create_user(username="third-admin", password="third-admin-password-42")
        StoreMembership.objects.create(
            store=self.store,
            user=third_admin,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        fourth_admin = User.objects.create_user(username="fourth-admin", password="fourth-admin-password-42")
        with self.assertRaises(ValidationError):
            StoreMembership.objects.create(
                store=self.store,
                user=fourth_admin,
                role=StoreMembership.Role.ADMINISTRATOR,
            )
        self.assertEqual(self.store.active_administrator_count, 3)


class ProOperationsTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.active_plan = "Pro"
        self.store.save()

    def add_staff(self, username, access_level):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="Role-password-4827!",
        )
        membership = StoreMembership.objects.create(
            store=self.store,
            user=user,
            role=StoreMembership.Role.STAFF,
            access_level=access_level,
        )
        return user, membership

    def test_role_permissions_are_enforced(self):
        inventory_user, _ = self.add_staff("inventory-user", StoreMembership.AccessLevel.INVENTORY)
        self.client.force_login(inventory_user)
        self.assertEqual(self.client.get(reverse("products")).status_code, 200)
        self.assertEqual(self.client.get(reverse("pro_inventory")).status_code, 200)
        self.assertEqual(self.client.get(reverse("purchasing")).status_code, 200)
        self.assertEqual(self.client.get(reverse("reports")).status_code, 403)
        self.assertEqual(self.client.get(reverse("customers")).status_code, 403)
        self.assertEqual(self.client.get(reverse("settings")).status_code, 403)

        manager, _ = self.add_staff("manager-user", StoreMembership.AccessLevel.MANAGER)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("reports")).status_code, 200)
        self.assertEqual(self.client.get(reverse("customers")).status_code, 200)

    def test_customer_discount_loyalty_and_refund_restore_balances(self):
        customer = Customer.objects.create(
            store=self.store,
            name="Loyal Customer",
            loyalty_points=20,
        )
        response = self.complete_sale(
            quantity=2,
            payment_method="GCash",
            customer_id=customer.pk,
            discount_rate="10",
            loyalty_points="10",
        )
        sale = Sale.objects.get()
        self.assertRedirects(response, reverse("receipt", args=[sale.pk]))
        self.assertEqual(sale.discount, Decimal("20.00"))
        self.assertEqual(sale.loyalty_discount, Decimal("10.00"))
        self.assertEqual(sale.total, Decimal("190.40"))
        customer.refresh_from_db()
        self.assertEqual(customer.loyalty_points, 11)
        self.assertEqual(customer.total_spent, Decimal("190.40"))

        response = self.client.post(
            reverse("reverse_sale", args=[sale.pk]),
            {"action": "refund", "reason": "Customer returned the order"},
        )
        self.assertRedirects(response, reverse("receipt", args=[sale.pk]))
        sale.refresh_from_db()
        customer.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(sale.status, "Refunded")
        self.assertEqual(customer.loyalty_points, 20)
        self.assertEqual(customer.total_spent, Decimal("0.00"))
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(
            InventoryMovement.objects.filter(store=self.store, product=self.product).count(),
            2,
        )

    def test_cash_register_is_required_and_reconciles_cash_sales(self):
        response = self.complete_sale(payment_method="Cash")
        self.assertRedirects(response, reverse("sell"))
        self.assertContains(self.client.get(reverse("sell")), "Cash register closed")
        self.assertFalse(Sale.objects.exists())

        self.assertRedirects(
            self.client.post(
                reverse("cash_register"),
                {"action": "open", "opening_cash": "100.00", "notes": "Morning shift"},
            ),
            reverse("cash_register"),
        )
        self.complete_sale(payment_method="Cash")
        session = CashRegisterSession.objects.get(store=self.store)
        self.assertEqual(session.expected_cash, Decimal("212.00"))
        self.assertRedirects(
            self.client.post(
                reverse("cash_register"),
                {"action": "close", "closing_cash": "212.00", "notes": "Balanced"},
            ),
            reverse("cash_register"),
        )
        session.refresh_from_db()
        self.assertEqual(session.variance, Decimal("0.00"))

    def test_shared_register_survives_logout_handover_and_exports_audit(self):
        cashier, _ = self.add_staff("handover-cashier", StoreMembership.AccessLevel.CASHIER)
        self.client.post(
            reverse("cash_register"),
            {"action": "open", "opening_cash": "100.00", "notes": "Morning opening"},
        )
        register = CashRegisterSession.objects.get(store=self.store, closed_at__isnull=True)
        opening_activity = register.activities.get(user=self.user)

        self.client.post(reverse("logout"))
        register.refresh_from_db()
        opening_activity.refresh_from_db()
        self.assertIsNone(register.closed_at)
        self.assertEqual(opening_activity.end_reason, CashRegisterActivity.EndReason.LOGOUT)
        self.assertIsNotNone(opening_activity.ended_at)

        self.client.force_login(cashier)
        handover_page = self.client.get(reverse("cash_register"))
        self.assertContains(handover_page, "Resume shared register")
        self.assertNotContains(handover_page, 'name="opening_cash"')
        self.client.post(
            reverse("cash_register"),
            {"action": "resume", "notes": "Afternoon handover"},
        )
        active = register.activities.get(user=cashier, ended_at__isnull=True)
        self.assertEqual(active.start_reason, CashRegisterActivity.StartReason.RESUMED)

        self.complete_sale(payment_method="Cash")
        register.refresh_from_db()
        self.assertEqual(register.cash_sales, Decimal("112.00"))
        self.assertEqual(register.expected_cash, Decimal("212.00"))

        self.client.force_login(self.user)
        export = self.client.get(reverse("export_cash_register"))
        csv_text = export.content.decode()
        self.assertEqual(export.status_code, 200)
        self.assertIn("owner", csv_text)
        self.assertIn("handover-cashier", csv_text)
        self.assertIn("Afternoon handover", csv_text)

    def test_stocktake_and_inter_store_transfer_write_movements(self):
        destination = StoreSettings.objects.create(
            business_name="Pro Branch",
            store_id="PRO-BRANCH",
            active_plan="Pro",
            subscription_end=timezone.localdate() + timedelta(days=30),
        )
        StoreMembership.objects.create(
            store=destination,
            user=self.user,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        self.client.post(
            reverse("pro_inventory"),
            {
                "action": "stocktake",
                "product": self.product.pk,
                "counted_stock": 12,
                "reason": "Physical count",
            },
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 12)

        response = self.client.post(
            reverse("pro_inventory"),
            {
                "action": "transfer",
                "destination_store": destination.pk,
                "product": self.product.pk,
                "quantity": 3,
                "notes": "Branch replenishment",
            },
        )
        self.assertRedirects(response, reverse("pro_inventory"))
        self.product.refresh_from_db()
        destination_product = destination.products.get(barcode=self.product.barcode)
        self.assertEqual(self.product.stock, 9)
        self.assertEqual(destination_product.stock, 3)
        self.assertEqual(StockTransfer.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.filter(reference__startswith="TRANSFER-").count(), 2)

    def test_supplier_purchase_order_and_receiving_update_inventory(self):
        self.client.post(
            reverse("purchasing"),
            {
                "action": "supplier",
                "name": "Coffee Supply Co.",
                "contact_person": "Kai",
                "email": "supply@example.com",
                "phone": "",
                "address": "",
            },
        )
        supplier = Supplier.objects.get(store=self.store)
        response = self.client.post(
            reverse("purchasing"),
            {
                "action": "purchase_order",
                "supplier": supplier.pk,
                "notes": "Weekly order",
                "items-TOTAL_FORMS": "3",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "0",
                "items-MAX_NUM_FORMS": "10",
                "items-0-product": self.product.pk,
                "items-0-quantity": "5",
                "items-0-unit_cost": "45.00",
                "items-1-product": "",
                "items-1-quantity": "",
                "items-1-unit_cost": "",
                "items-2-product": "",
                "items-2-quantity": "",
                "items-2-unit_cost": "",
            },
        )
        self.assertRedirects(response, reverse("purchasing"))
        order = PurchaseOrder.objects.get(store=self.store)
        self.assertEqual(order.total, Decimal("225"))
        self.assertRedirects(
            self.client.post(reverse("receive_purchase_order", args=[order.pk])),
            reverse("purchasing"),
        )
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(self.product.stock, 15)
        self.assertEqual(self.product.cost, Decimal("45.00"))

    def test_bulk_import_custom_reports_and_multi_store_overview(self):
        csv_file = SimpleUploadedFile(
            "products.csv",
            b"name,category,barcode,cost,price,stock,low_stock_threshold\nCold Brew,Drinks,CB-001,40,90,7,2\n",
            content_type="text/csv",
        )
        self.assertRedirects(
            self.client.post(reverse("import_products"), {"csv_file": csv_file}),
            reverse("products"),
        )
        self.assertTrue(Product.objects.filter(store=self.store, barcode="CB-001", stock=7).exists())
        today = timezone.localdate()
        report = self.client.get(
            reverse("reports"),
            {"start": (today - timedelta(days=10)).isoformat(), "end": today.isoformat()},
        )
        self.assertEqual(report.context["range_key"], "custom")
        overview = self.client.get(reverse("multi_store_dashboard"))
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, "Every Pro store in one view")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_scheduled_summary_command_sends_due_pro_report(self):
        schedule = ReportSchedule.objects.create(
            store=self.store,
            recipient_email="reports@example.com",
            frequency=ReportSchedule.Frequency.DAILY,
        )
        output = StringIO()
        call_command("send_scheduled_reports", stdout=output)
        schedule.refresh_from_db()
        self.assertIsNotNone(schedule.last_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["reports@example.com"])
        self.assertIn("Sent 1 scheduled report", output.getvalue())


class PlatformAdministrationTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="platform-admin",
            email="platform@example.com",
            password="platform-admin-password-42",
        )

    def test_only_superuser_can_open_platform_administration(self):
        client_admin = User.objects.create_user(username="store-admin", password="store-admin-password-42")
        self.client.force_login(client_admin)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin-brand")
        self.assertContains(response, "admin-account")
        self.assertContains(response, "Log out")
        self.assertNotContains(response, "Welcome,")
        self.assertContains(response, 'id="storeSearch"')
        self.assertContains(response, "Account access")
        self.assertContains(response, "pos/css/admin.css")

    def test_platform_admin_creates_store_and_initial_administrator_together(self):
        today = timezone.localdate()
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse("admin:pos_storesettings_add"),
            {
                "business_name": "Created Business",
                "store_id": "CREATED-01",
                "store_type": StoreSettings.StoreType.ELECTRONICS,
                "contact_number": "",
                "address": "",
                "tax_rate": "12.00",
                "service_charge_rate": "0.00",
                "active_plan": "Pro",
                "status": "Active",
                "subscription_start": today.isoformat(),
                "subscription_end": (today + timedelta(days=30)).isoformat(),
                "receipt_after_sale": "on",
                "low_stock_alerts": "on",
                "barcode_scanning": "",
                "admin_username": "business-owner",
                "admin_email": "business@example.com",
                "admin_password": "Violet-lantern-9274!",
                "admin_password_confirm": "Violet-lantern-9274!",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        store = StoreSettings.objects.get(store_id="CREATED-01")
        membership = store.memberships.get(role=StoreMembership.Role.ADMINISTRATOR)
        self.assertEqual(membership.user.username, "business-owner")
        self.assertFalse(membership.user.is_staff)
        self.assertFalse(membership.user.is_superuser)
        self.assertEqual(store.staff_limit, 10)
        self.assertEqual(store.store_type, StoreSettings.StoreType.ELECTRONICS)
        self.assertEqual(store.service_charge_rate, Decimal("0.00"))
        self.assertEqual(
            set(store.product_categories.values_list("name", flat=True)),
            set(StoreSettings.CATEGORY_PRESETS[StoreSettings.StoreType.ELECTRONICS]),
        )
        self.assertIsNotNone(
            authenticate(username="business-owner", password="Violet-lantern-9274!")
        )
        self.assertTrue(membership.user.security_profile.must_change_password)

        self.client.logout()
        login_response = self.client.post(
            reverse("login"),
            {"username": "business-owner", "password": "Violet-lantern-9274!"},
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.url, reverse("dashboard"))
        self.assertRedirects(
            self.client.get(reverse("dashboard")),
            reverse("password_change_required"),
        )

        password_response = self.client.post(
            reverse("password_change_required"),
            {
                "old_password": "Violet-lantern-9274!",
                "new_password1": "Permanent-elm-6519!",
                "new_password2": "Permanent-elm-6519!",
            },
        )
        self.assertRedirects(password_response, reverse("dashboard"))
        membership.user.refresh_from_db()
        membership.user.security_profile.refresh_from_db()
        self.assertFalse(membership.user.security_profile.must_change_password)
        self.assertIsNone(authenticate(username="business-owner", password="Violet-lantern-9274!"))
        self.assertIsNotNone(authenticate(username="business-owner", password="Permanent-elm-6519!"))

    def test_platform_dashboard_lists_all_stores_and_five_day_expirations(self):
        today = timezone.localdate()
        StoreSettings.objects.create(
            business_name="Due Soon",
            store_id="DUE-SOON",
            active_plan="Starter",
            subscription_end=today + timedelta(days=5),
        )
        StoreSettings.objects.create(
            business_name="Later Store",
            store_id="LATER",
            active_plan="Pro",
            subscription_end=today + timedelta(days=6),
        )
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Due Soon")
        self.assertContains(response, "Later Store")
        self.assertEqual(
            [row["store"].business_name for row in response.context["expiring_store_rows"]],
            ["Due Soon"],
        )

    def test_platform_dashboard_surfaces_subscription_extension_requests(self):
        requester = User.objects.create_user(
            username="expired-owner",
            email="expired@example.com",
            password="expired-owner-password-42",
        )
        store = StoreSettings.objects.create(
            business_name="Expired Request Store",
            store_id="EXPIRED-REQ",
            active_plan="Starter",
            subscription_end=timezone.localdate() - timedelta(days=1),
        )
        extension_request = SubscriptionExtensionRequest.objects.create(
            store=store,
            requested_by=requester,
            requested_plan="Pro",
            payment_type="Maya / card",
            comments="We would like to move to Pro.",
        )

        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Requests")
        self.assertContains(response, "Expired Request Store")
        self.assertContains(response, "Pro · Maya / card")
        self.assertContains(
            response,
            reverse(
                "admin:pos_subscriptionextensionrequest_change",
                args=[extension_request.pk],
            ),
        )
        self.assertEqual(response.context["open_extension_request_count"], 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_completing_extension_request_activates_one_month_and_emails_requester(self):
        today = timezone.localdate()
        requester = User.objects.create_user(
            username="activation-owner",
            email="activation@example.com",
            password="activation-owner-password-42",
        )
        store = StoreSettings.objects.create(
            business_name="Activation Store",
            store_id="ACTIVATE-REQ",
            active_plan="Starter",
            status="Active",
            subscription_start=today - timedelta(days=31),
            subscription_end=today - timedelta(days=1),
        )
        extension_request = SubscriptionExtensionRequest.objects.create(
            store=store,
            requested_by=requester,
            requested_plan="Pro",
            payment_type="GCash",
            comments="Please reactivate us.",
        )

        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse(
                "admin:pos_subscriptionextensionrequest_change",
                args=[extension_request.pk],
            ),
            {
                "status": SubscriptionExtensionRequest.Status.COMPLETED,
                "admin_notes": "Internal payment reference that must stay private.",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)

        store.refresh_from_db()
        extension_request.refresh_from_db()
        expected_end = SubscriptionExtensionRequest.one_month_end(today)
        self.assertEqual(store.active_plan, "Pro")
        self.assertEqual(store.subscription_status, "Active")
        self.assertEqual(store.subscription_start, today)
        self.assertEqual(store.subscription_end, expected_end)
        self.assertEqual(extension_request.status, SubscriptionExtensionRequest.Status.COMPLETED)
        self.assertEqual(extension_request.extension_start, today)
        self.assertEqual(extension_request.extension_end, expected_end)
        self.assertIsNotNone(extension_request.activated_at)
        self.assertIsNotNone(extension_request.status_email_sent_at)
        self.assertEqual(extension_request.status_email_error, "")
        self.assertTrue(
            StoreAuditEvent.objects.filter(
                store=store,
                actor=self.superuser,
                action="subscription.extension_completed",
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [requester.email])
        self.assertIn("is now Completed", mail.outbox[0].body)
        self.assertIn(f"through {expected_end:%b %d, %Y}", mail.outbox[0].body)
        self.assertNotIn("Internal payment reference", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertEqual(mail.outbox[0].alternatives[0].mimetype, "text/html")
        status_html = mail.outbox[0].alternatives[0].content
        self.assertIn("Your store access is active", status_html)
        self.assertIn("Active subscription term", status_html)
        self.assertIn("Pro plan · Access restored", status_html)
        self.assertNotIn("Internal payment reference", status_html)

        original_end = store.subscription_end
        self.assertFalse(extension_request.activate_subscription())
        store.refresh_from_db()
        self.assertEqual(store.subscription_end, original_end)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_review_and_decline_status_emails_use_branded_variants(self):
        requester = User.objects.create_user(
            username="review-owner",
            email="review-owner@example.com",
            password="review-owner-password-42",
        )
        store = StoreSettings.objects.create(
            business_name="Review Status Store",
            store_id="REVIEW-STATUS",
            active_plan="Starter",
            subscription_end=timezone.localdate() - timedelta(days=1),
        )
        extension_request = SubscriptionExtensionRequest.objects.create(
            store=store,
            requested_by=requester,
            requested_plan="Starter",
            payment_type="Bank transfer",
        )
        change_url = reverse(
            "admin:pos_subscriptionextensionrequest_change",
            args=[extension_request.pk],
        )
        self.client.force_login(self.superuser)

        review_response = self.client.post(
            change_url,
            {
                "status": SubscriptionExtensionRequest.Status.IN_REVIEW,
                "admin_notes": "Checking payment details.",
                "_save": "Save",
            },
        )
        self.assertEqual(review_response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        review_html = mail.outbox[0].alternatives[0].content
        self.assertIn("We’re reviewing your request", review_html)
        self.assertIn("In review", review_html)

        decline_response = self.client.post(
            change_url,
            {
                "status": SubscriptionExtensionRequest.Status.DECLINED,
                "admin_notes": "Checking payment details.",
                "_save": "Save",
            },
        )
        self.assertEqual(decline_response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        decline_html = mail.outbox[1].alternatives[0].content
        self.assertIn("Your request needs another option", decline_html)
        self.assertIn("Declined", decline_html)
        self.assertNotIn("Checking payment details", decline_html)

    def test_platform_can_assign_one_administrator_login_to_multiple_stores(self):
        owner = User.objects.create_user(
            username="multi-owner",
            email="multi@example.com",
            password="Copper-river-6812!",
        )
        self.client.force_login(self.superuser)
        today = timezone.localdate()
        for index in range(2):
            existing_administrator = owner.pk if index else ""
            response = self.client.post(
                reverse("admin:pos_storesettings_add"),
                {
                    "business_name": f"Owner Store {index}",
                    "store_id": f"OWNER-{index}",
                    "store_type": StoreSettings.StoreType.GENERAL,
                    "contact_number": "",
                    "address": "",
                    "tax_rate": "12.00",
                    "active_plan": "Pro",
                    "status": "Active",
                    "subscription_start": today.isoformat(),
                    "subscription_end": (today + timedelta(days=30)).isoformat(),
                    "receipt_after_sale": "on",
                    "low_stock_alerts": "on",
                    "barcode_scanning": "",
                    "existing_administrator": existing_administrator,
                    "admin_username": "" if index else owner.username,
                    "admin_email": "" if index else owner.email,
                    "admin_password": "",
                    "admin_password_confirm": "",
                    "_save": "Save",
                },
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(
            owner.store_memberships.filter(role=StoreMembership.Role.ADMINISTRATOR).count(),
            2,
        )
        self.client.force_login(owner)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(len(dashboard.wsgi_request.available_memberships), 2)
        self.assertTrue(dashboard.wsgi_request.multi_store_enabled)
        overview = self.client.get(reverse("multi_store_dashboard"))
        self.assertContains(overview, "Owner super dashboard")
        self.assertContains(overview, "Open dashboard")

    def test_platform_rejects_shared_administrator_on_starter_store(self):
        today = timezone.localdate()
        owner = User.objects.create_user(
            username="pro-branch-owner",
            email="pro-branch@example.com",
            password="Copper-river-6812!",
        )
        pro_store = StoreSettings.objects.create(
            business_name="Existing Pro Branch",
            store_id="EXISTING-PRO",
            active_plan="Pro",
            subscription_end=today + timedelta(days=30),
        )
        StoreMembership.objects.create(
            store=pro_store,
            user=owner,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        self.client.force_login(self.superuser)
        add_page = self.client.get(reverse("admin:pos_storesettings_add"))
        self.assertContains(add_page, "Assign an existing Pro administrator")
        self.assertContains(add_page, "pro-branch-owner")
        response = self.client.post(
            reverse("admin:pos_storesettings_add"),
            {
                "business_name": "Rejected Starter Branch",
                "store_id": "REJECTED-STARTER",
                "store_type": StoreSettings.StoreType.GENERAL,
                "contact_number": "",
                "address": "",
                "tax_rate": "12.00",
                "active_plan": "Starter",
                "status": "Active",
                "subscription_start": today.isoformat(),
                "subscription_end": (today + timedelta(days=30)).isoformat(),
                "receipt_after_sale": "on",
                "low_stock_alerts": "on",
                "existing_administrator": owner.pk,
                "admin_username": "",
                "admin_email": "",
                "admin_password": "",
                "admin_password_confirm": "",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing multi-store administrators can only be assigned to Pro stores")
        self.assertFalse(StoreSettings.objects.filter(store_id="REJECTED-STARTER").exists())

    def test_platform_can_add_additional_administrator_to_pro_store(self):
        today = timezone.localdate()
        first_owner = User.objects.create_user(
            username="first-pro-owner",
            email="first-pro@example.com",
            password="First-owner-6812!",
        )
        store = StoreSettings.objects.create(
            business_name="Pro Owners Store",
            store_id="PRO-OWNERS",
            active_plan="Pro",
            status="Active",
            subscription_start=today,
            subscription_end=today + timedelta(days=30),
        )
        StoreMembership.objects.create(
            store=store,
            user=first_owner,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse("admin:pos_storesettings_change", args=[store.pk]),
            {
                "business_name": store.business_name,
                "store_id": store.store_id,
                "store_type": store.store_type,
                "contact_number": "",
                "address": "",
                "tax_rate": "12.00",
                "active_plan": "Pro",
                "status": "Active",
                "subscription_start": today.isoformat(),
                "subscription_end": (today + timedelta(days=30)).isoformat(),
                "receipt_after_sale": "on",
                "low_stock_alerts": "on",
                "admin_username": "second-pro-owner",
                "admin_email": "second-pro@example.com",
                "admin_password": "Violet-lantern-9246!",
                "admin_password_confirm": "Violet-lantern-9246!",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(store.active_administrator_count, 2)
        second_owner = User.objects.get(username="second-pro-owner")
        self.assertTrue(second_owner.security_profile.must_change_password)


class PublicTrialAndGuideTests(TestCase):
    def trial_data(self, **overrides):
        return {
            "business_name": "Walk-in Trial Business",
            "store_type": StoreSettings.StoreType.CAFE,
            "service_charge_rate": "7.50",
            "first_name": "Trial",
            "last_name": "Owner",
            "username": "trial-owner",
            "email": "trial@example.com",
            "password1": "Orchid-cabin-4815!",
            "password2": "Orchid-cabin-4815!",
            "website": "",
            **overrides,
        }

    def test_login_links_to_trial_plan_comparison_and_pdf(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Start a 30-day free trial")
        self.assertContains(response, reverse("password_reset"))
        self.assertContains(response, reverse("feature_guide"))
        self.assertContains(response, reverse("feature_guide_pdf"))

    def test_home_is_a_public_conversion_page_for_supported_retailers(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start free trial")
        self.assertContains(response, "Book a demo")
        self.assertContains(response, "Cafes")
        self.assertContains(response, "Minimarts &amp; specialty grocery", html=True)
        self.assertContains(response, "Clothing &amp; footwear", html=True)
        self.assertContains(response, "Gadgets &amp; accessories", html=True)
        self.assertContains(response, "Pet-supply stores")
        self.assertContains(response, "Hardware &amp; general merchandise", html=True)
        self.assertContains(response, "Cosmetics &amp; personal care", html=True)
        self.assertContains(response, "Hardware requirements")
        self.assertContains(response, "Frequently asked questions")
        self.assertContains(response, "Multi-store command center")
        self.assertContains(response, "Owner super dashboard")
        self.assertContains(response, "every store sharing an administrator login must remain on Pro")
        self.assertContains(response, "oxpos-60-second-story-v3.webm")
        self.assertContains(response, "oxpos-demo-story-poster-v3.png")
        self.assertContains(response, "oxpos-demo-captions-v3.vtt")
        self.assertContains(response, "Sell it once. Stock updates instantly.")
        self.assertContains(response, "60 seconds to a better close")
        self.assertContains(response, reverse("public_document", args=["privacy"]))
        self.assertContains(response, reverse("public_document", args=["security"]))
        self.assertContains(response, "Local support Monday–Friday, 9:00 AM–6:00 PM")
        self.assertContains(response, "Secure browser-based access")
        self.assertContains(response, 'rel="canonical"')
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'type="application/ld+json"')
        self.assertNotContains(response, "Lifetime software updates")
        self.assertNotContains(response, "ingredient or item stock tracking")
        self.assertNotContains(response, "Cafe / food service")

    def test_demo_request_is_stored_and_honeypot_is_rejected(self):
        response = self.client.post(
            reverse("home"),
            {
                "name": "Cafe Owner",
                "email": "owner@cafe.test",
                "phone": "09170000000",
                "business_name": "Morning Cup",
                "business_type": "Cafe",
                "preferred_schedule": "2026-08-12T10:30",
                "message": "Show me checkout and inventory.",
                "website": "",
            },
        )
        self.assertRedirects(response, f"{reverse('home')}#book-demo", fetch_redirect_response=False)
        demo_request = DemoRequest.objects.get()
        self.assertEqual(demo_request.business_name, "Morning Cup")
        self.assertEqual(demo_request.business_type, "Cafe")
        self.assertEqual(demo_request.status, DemoRequest.Status.NEW)

        blocked = self.client.post(
            reverse("home"),
            {
                "name": "Bot",
                "email": "bot@example.com",
                "business_name": "Spam",
                "website": "https://spam.example",
            },
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(DemoRequest.objects.count(), 1)

    def test_public_legal_and_trust_documents_are_published(self):
        expected = {
            "privacy": "Information we collect",
            "terms": "Acceptable use",
            "policies": "Backup and recovery policy",
            "security": "Application protections",
        }
        for document, copy in expected.items():
            with self.subTest(document=document):
                response = self.client.get(reverse("public_document", args=[document]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, copy)
                self.assertContains(response, "oxpos2026@gmail.com")
        self.assertEqual(self.client.get(reverse("public_document", args=["missing"])).status_code, 404)

    def test_each_supported_store_type_has_its_own_category_preset(self):
        expected = {
            StoreSettings.StoreType.CAFE: "Coffee and Tea",
            StoreSettings.StoreType.GROCERY: "Canned Goods",
            StoreSettings.StoreType.FASHION: "Footwear",
            StoreSettings.StoreType.ELECTRONICS: "Mobile Accessories",
            StoreSettings.StoreType.PET_SUPPLIES: "Pet Food",
            StoreSettings.StoreType.HARDWARE: "Tools",
            StoreSettings.StoreType.BEAUTY: "Personal Care",
        }
        for store_type, category in expected.items():
            with self.subTest(store_type=store_type):
                self.assertIn(category, StoreSettings.CATEGORY_PRESETS[store_type])

    def test_self_service_signup_creates_exactly_one_thirty_day_trial(self):
        today = timezone.localdate()
        signup_page = self.client.get(reverse("start_trial"))
        self.assertContains(signup_page, "Select your store type")
        self.assertNotContains(signup_page, "superuser", status_code=200)
        self.assertNotContains(signup_page, "platform administrator")
        self.assertNotContains(signup_page, "OXPOS provider")
        response = self.client.post(reverse("start_trial"), self.trial_data())
        self.assertRedirects(response, reverse("dashboard"))
        store = StoreSettings.objects.get(business_name="Walk-in Trial Business")
        membership = store.memberships.select_related("user").get()
        self.assertEqual(store.active_plan, "Trial")
        self.assertEqual(store.store_type, StoreSettings.StoreType.CAFE)
        self.assertEqual(store.service_charge_rate, Decimal("7.50"))
        self.assertTrue(store.product_categories.filter(name="Coffee and Tea").exists())
        self.assertFalse(store.product_categories.filter(name="Hardware").exists())
        self.assertEqual(store.status, "Active")
        self.assertEqual(store.subscription_start, today)
        self.assertEqual((store.subscription_end - store.subscription_start).days + 1, 30)
        self.assertEqual(store.staff_limit, 0)
        self.assertEqual(membership.role, StoreMembership.Role.ADMINISTRATOR)
        self.assertEqual(membership.user.username, "trial-owner")
        self.assertEqual(int(self.client.session["_auth_user_id"]), membership.user_id)

        second_attempt = self.client.get(reverse("start_trial"))
        self.assertRedirects(second_attempt, reverse("dashboard"))
        self.assertEqual(StoreSettings.objects.count(), 1)

    def test_trial_store_type_has_no_default_and_is_required(self):
        response = self.client.post(reverse("start_trial"), self.trial_data(store_type=""))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select your store type")
        self.assertContains(response, "This field is required")
        self.assertFalse(StoreSettings.objects.exists())

    def test_public_search_files_list_only_public_routes(self):
        robots = self.client.get(reverse("robots"))
        self.assertEqual(robots.status_code, 200)
        self.assertContains(robots, "Disallow: /admin/")
        self.assertContains(robots, self.client.get(reverse("sitemap")).wsgi_request.build_absolute_uri(reverse("sitemap")))

        sitemap = self.client.get(reverse("sitemap"))
        self.assertEqual(sitemap.status_code, 200)
        self.assertEqual(sitemap["Content-Type"], "application/xml; charset=utf-8")
        self.assertContains(sitemap, reverse("feature_guide"))
        self.assertContains(sitemap, reverse("public_document", args=["privacy"]))
        self.assertNotContains(sitemap, reverse("dashboard"))

    def test_non_cafe_trial_rejects_a_crafted_dining_service_charge(self):
        response = self.client.post(
            reverse("start_trial"),
            self.trial_data(
                store_type=StoreSettings.StoreType.ELECTRONICS,
                service_charge_rate="7.50",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "available only for Cafe stores")
        self.assertFalse(StoreSettings.objects.exists())

    def test_non_cafe_trial_is_created_as_standard_retail(self):
        response = self.client.post(
            reverse("start_trial"),
            self.trial_data(
                store_type=StoreSettings.StoreType.GROCERY,
                service_charge_rate="0.00",
            ),
        )
        self.assertRedirects(response, reverse("dashboard"))
        store = StoreSettings.objects.get()
        self.assertFalse(store.supports_dining)
        self.assertEqual(store.service_charge_rate, Decimal("0.00"))
        sell_page = self.client.get(reverse("sell"))
        self.assertNotContains(sell_page, "Dine-in")
        self.assertNotContains(sell_page, "Take-out")

    def test_trial_rejects_existing_email_without_partial_store(self):
        User.objects.create_user(
            username="existing",
            email="trial@example.com",
            password="Existing-violet-8241!",
        )
        response = self.client.post(reverse("start_trial"), self.trial_data())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "account with this email already exists")
        self.assertFalse(StoreSettings.objects.exists())
        self.assertFalse(User.objects.filter(username="trial-owner").exists())

    def test_trial_plan_has_no_additional_staff_seats(self):
        self.client.post(reverse("start_trial"), self.trial_data())
        response = self.client.post(
            reverse("settings"),
            {
                "action": "team",
                "username": "trial-staff",
                "email": "staff@example.com",
                "password": "Pine-station-7316!",
                "password_confirm": "Pine-station-7316!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trial plan allows 0 staff account")
        self.assertFalse(User.objects.filter(username="trial-staff").exists())

    def test_public_feature_page_and_downloadable_pdf(self):
        page = self.client.get(reverse("feature_guide"))
        self.assertContains(page, "Core system")
        self.assertContains(page, "STARTER")
        self.assertContains(page, "PRO")
        self.assertContains(page, "30 days free")
        self.assertContains(page, "₱399")
        self.assertContains(page, "₱799")
        self.assertContains(page, "Pro owner dashboard")
        self.assertContains(page, "Shared administrator login across assigned Pro stores")
        self.assertContains(page, "Up to 3 administrators and 10 staff per store")
        self.assertContains(page, "Multi-store access requires Pro on every store")
        self.assertContains(page, "scheduled summaries")
        self.assertContains(page, "Included with every OXPOS plan")
        self.assertContains(page, "Local support Monday–Friday, 9:00 AM–6:00 PM")
        self.assertContains(page, "Book a demo")
        self.assertContains(page, "oxpos-logo-transparent-v2.png")
        self.assertNotContains(page, "superuser")
        self.assertNotContains(page, "platform administrator")
        self.assertNotContains(page, "OXPOS provider")
        self.assertNotContains(page, "Django Administration")

        response = self.client.get(reverse("feature_guide_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("OXPOS-feature-and-plan-guide.pdf", response["Content-Disposition"])
        content = b"".join(response.streaming_content)
        self.assertTrue(content.startswith(b"%PDF-1.4"))
        self.assertTrue(content.rstrip().endswith(b"%%EOF"))
        self.assertGreater(len(content), 200_000)
        self.assertIn(b"/Subtype /Image", content)
        normalized_content = content.lower()
        self.assertNotIn(b"superuser", normalized_content)
        self.assertNotIn(b"platform administrator", normalized_content)
        self.assertNotIn(b"oxpos provider", normalized_content)
        self.assertNotIn(b"django administration", normalized_content)
        self.assertEqual(content.count(b"/Type /Page\n"), 3)

    def test_database_backup_can_be_verified_in_isolation(self):
        User.objects.create_user(username="backup-user", password="Back-up-test-9137!")
        StoreSettings.objects.create(business_name="Backup Store", store_id="BACKUP-01")
        with tempfile.TemporaryDirectory() as temp_directory:
            backup_path = Path(temp_directory) / "oxpos-test.json.gz"
            call_command("backup_database", output=str(backup_path), verbosity=0)
            self.assertTrue(backup_path.exists())
            self.assertTrue(Path(f"{backup_path}.sha256").exists())
            call_command("verify_database_backup", input=str(backup_path), verbosity=0)

    def test_store_workspace_exposes_persistent_theme_toggle(self):
        today = timezone.localdate()
        user = User.objects.create_user(username="theme-user", password="Theme-signal-5318!")
        store = StoreSettings.objects.create(
            business_name="Theme Store",
            store_id="THEME",
            active_plan="Starter",
            subscription_end=today + timedelta(days=30),
        )
        StoreMembership.objects.create(
            store=store,
            user=user,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'id="themeToggle"')
        self.assertContains(response, "oxpos-theme")
        self.assertContains(response, "sidebar-logout")
        self.assertContains(response, "Log out")


class PasswordRecoveryTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.user = User.objects.create_user(
            username="recover-owner",
            email="recover@example.com",
            password="Temporary-maple-5729!",
        )
        self.store = StoreSettings.objects.create(
            business_name="Recovery Store",
            store_id="RECOVERY",
            active_plan="Starter",
            status="Active",
            subscription_end=today + timedelta(days=30),
        )
        StoreMembership.objects.create(
            store=self.store,
            user=self.user,
            role=StoreMembership.Role.ADMINISTRATOR,
        )
        UserSecurityProfile.require_password_change(self.user)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_registered_email_can_reset_password_and_clear_temporary_state(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "recover@example.com"},
        )
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["recover@example.com"])

        reset_url = next(
            line.strip()
            for line in mail.outbox[0].body.splitlines()
            if line.strip().startswith("http://testserver/")
        )
        token_response = self.client.get(reset_url)
        self.assertEqual(token_response.status_code, 302)
        password_response = self.client.post(
            token_response.url,
            {
                "new_password1": "Recovered-oak-8462!",
                "new_password2": "Recovered-oak-8462!",
            },
        )
        self.assertRedirects(password_response, reverse("password_reset_complete"))

        self.user.refresh_from_db()
        self.user.security_profile.refresh_from_db()
        self.assertFalse(self.user.security_profile.must_change_password)
        self.assertIsNone(authenticate(username="recover-owner", password="Temporary-maple-5729!"))
        self.assertIsNotNone(authenticate(username="recover-owner", password="Recovered-oak-8462!"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_unknown_email_gets_same_confirmation_without_sending_mail(self):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "unknown@example.com"},
        )
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    @patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP unavailable"))
    def test_delivery_failure_is_shown_instead_of_false_success(self, _send_mail):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "recover@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "We could not send the reset email right now")
