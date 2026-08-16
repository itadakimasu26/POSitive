from datetime import timedelta

from django.contrib.admin import AdminSite
from django.utils import timezone


class PlatformAdminSite(AdminSite):
    site_header = "OXPOS Platform Administration"
    site_title = "OXPOS Platform Admin"
    index_title = "Business overview"
    index_template = "admin/platform_index.html"

    def has_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def index(self, request, extra_context=None):
        from pos.models import StoreMembership, StoreSettings, SubscriptionExtensionRequest

        today = timezone.localdate()
        stores = list(
            StoreSettings.objects.prefetch_related("memberships__user").order_by(
                "business_name",
                "store_id",
            )
        )
        rows = []
        for store in stores:
            administrator = next(
                (
                    membership.user
                    for membership in store.memberships.all()
                    if membership.active and membership.role == StoreMembership.Role.ADMINISTRATOR
                ),
                None,
            )
            rows.append({"store": store, "administrator": administrator})

        expiring_rows = [
            row
            for row in rows
            if row["store"].status == "Active"
            and row["store"].subscription_end
            and today <= row["store"].subscription_end <= today + timedelta(days=5)
        ]
        extension_requests = SubscriptionExtensionRequest.objects.select_related(
            "store",
            "requested_by",
        )
        extra_context = {
            **(extra_context or {}),
            "store_rows": rows,
            "expiring_store_rows": expiring_rows,
            "store_count": len(rows),
            "active_store_count": sum(row["store"].subscription_status == "Active" for row in rows),
            "expired_store_count": sum(row["store"].subscription_status == "Expired" for row in rows),
            "suspended_store_count": sum(row["store"].subscription_status == "Suspended" for row in rows),
            "extension_request_rows": list(extension_requests[:5]),
            "open_extension_request_count": extension_requests.filter(
                status__in=[
                    SubscriptionExtensionRequest.Status.NEW,
                    SubscriptionExtensionRequest.Status.IN_REVIEW,
                ]
            ).count(),
        }
        return super().index(request, extra_context=extra_context)


platform_admin_site = PlatformAdminSite(name="admin")
