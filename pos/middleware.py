from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .models import UserSecurityProfile


PASSWORD_RECOVERY_URL_NAMES = {
    "logout",
    "password_change_required",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
}


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                must_change_password = request.user.security_profile.must_change_password
            except UserSecurityProfile.DoesNotExist:
                must_change_password = False
            if must_change_password:
                try:
                    match = resolve(request.path_info)
                except Resolver404:
                    match = None
                if match is None or match.url_name not in PASSWORD_RECOVERY_URL_NAMES:
                    return redirect("password_change_required")
        return self.get_response(request)
