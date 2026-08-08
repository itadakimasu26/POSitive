from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .models import StoreMembership


CURRENT_STORE_SESSION_KEY = "positive_current_store_id"


def load_store_access(request):
    """Attach the user's active, session-selected store context to a request.

    Views continue to query through ``request.store`` so switching branches
    changes the complete tenant scope instead of merely changing the display.
    ``multi_store_enabled`` is true only when every assigned branch is Pro.
    """
    if hasattr(request, "store_membership"):
        return request.store_membership

    memberships = list(
        StoreMembership.objects.filter(user=request.user, active=True)
        .select_related("store")
        .order_by("store__business_name", "store__store_id")
    )
    selected_store_id = request.session.get(CURRENT_STORE_SESSION_KEY)
    membership = next(
        (item for item in memberships if item.store_id == selected_store_id),
        memberships[0] if memberships else None,
    )
    if membership:
        request.session[CURRENT_STORE_SESSION_KEY] = membership.store_id

    request.available_memberships = memberships
    # This mirrors StoreMembership.clean() and safely hides switching if legacy
    # or bulk-updated data ever contains a mixed-plan assignment.
    request.multi_store_enabled = (
        len(memberships) > 1
        and all(item.store.is_pro for item in memberships)
    )
    request.store_membership = membership
    request.store = membership.store if membership else None
    return membership


def store_required(view_func=None, *, administrator=False, operational=False, permission=None, pro=False):
    def decorator(func):
        @login_required
        @wraps(func)
        def wrapped(request, *args, **kwargs):
            membership = load_store_access(request)
            if membership is None:
                if request.user.is_superuser:
                    return redirect("admin:index")
                return render(request, "pos/no_store_access.html", status=403)
            if administrator and not membership.is_administrator:
                raise PermissionDenied("Store administrator access is required.")
            if permission and not getattr(membership, permission, False):
                raise PermissionDenied("Your store role does not allow this action.")
            if pro and not membership.store.is_pro:
                return render(
                    request,
                    "pos/pro_required.html",
                    {"store": membership.store},
                    status=403,
                )
            if operational and not membership.store.is_subscription_active:
                return render(
                    request,
                    "pos/store_unavailable.html",
                    {"store": membership.store},
                    status=403,
                )
            return func(request, *args, **kwargs)

        return wrapped

    if view_func is None:
        return decorator
    return decorator(view_func)
