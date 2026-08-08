from django.urls import path

from . import views


urlpatterns = [
    path("", views.marketing_home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("trial/", views.start_trial, name="start_trial"),
    path("features/", views.feature_guide, name="feature_guide"),
    path("features/guide.pdf", views.feature_guide_pdf, name="feature_guide_pdf"),
    path("legal/<str:document>/", views.public_document, name="public_document"),
    path("stores/switch/", views.switch_store, name="switch_store"),
    path("sell/", views.sell, name="sell"),
    path("sell/complete/", views.complete_sale, name="complete_sale"),
    path("sell/offline-template.csv", views.download_offline_sales_template, name="offline_sales_template"),
    path("sell/offline-import/", views.import_offline_sales, name="import_offline_sales"),
    path("receipt/<int:pk>/", views.receipt, name="receipt"),
    path("receipt/<int:pk>/reverse/", views.reverse_sale, name="reverse_sale"),
    path("products/", views.products, name="products"),
    path("products/categories/add/", views.add_product_category, name="add_product_category"),
    path("products/<int:pk>/stock/", views.adjust_stock, name="adjust_stock"),
    path("products/import/", views.import_products, name="import_products"),
    path("products/export/", views.export_products, name="export_products"),
    path("reports/", views.reports, name="reports"),
    path("reports/export/", views.export_sales, name="export_sales"),
    path("pro/overview/", views.multi_store_dashboard, name="multi_store_dashboard"),
    path("pro/customers/", views.customers, name="customers"),
    path("pro/inventory/", views.pro_inventory, name="pro_inventory"),
    path("pro/register/", views.cash_register, name="cash_register"),
    path("pro/register/export/", views.export_cash_register, name="export_cash_register"),
    path("pro/purchasing/", views.purchasing, name="purchasing"),
    path("pro/purchasing/<int:pk>/receive/", views.receive_purchase_order, name="receive_purchase_order"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/subscription/maya/result/<int:pk>/<str:outcome>/", views.maya_payment_result, name="maya_payment_result"),
    path("payments/maya/webhook/", views.maya_payment_webhook, name="maya_payment_webhook"),
    path("settings/staff/<int:pk>/remove/", views.remove_staff, name="remove_staff"),
    path("settings/reports/<int:pk>/remove/", views.remove_report_schedule, name="remove_report_schedule"),
]
