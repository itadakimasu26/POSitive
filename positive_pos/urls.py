from django.contrib.auth import views as auth_views
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from pos.auth_views import (
    OXPOSLogoutView,
    OXPOSPasswordResetConfirmView,
    OXPOSPasswordResetView,
    RequiredPasswordChangeView,
)
from .admin_site import platform_admin_site


urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url=static("pos/images/oxpos-mark.png"), permanent=True)),
    path("admin/", platform_admin_site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", OXPOSLogoutView.as_view(), name="logout"),
    path(
        "account/password/change-required/",
        RequiredPasswordChangeView.as_view(),
        name="password_change_required",
    ),
    path(
        "password-reset/",
        OXPOSPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/confirm/<uidb64>/<token>/",
        OXPOSPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path("", include("pos.urls")),
]
