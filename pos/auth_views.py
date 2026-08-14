import logging

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect
from django.template import loader
from django.urls import reverse_lazy

from .models import UserSecurityProfile
from .registers import release_user_registers


logger = logging.getLogger(__name__)


class DeliverablePasswordResetForm(PasswordResetForm):
    """Let SMTP failures reach the view instead of reporting false success."""

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        subject = "".join(loader.render_to_string(subject_template_name, context).splitlines())
        body = loader.render_to_string(email_template_name, context)
        email_message = EmailMultiAlternatives(subject, body, from_email, [to_email])
        if html_email_template_name is not None:
            html_email = loader.render_to_string(html_email_template_name, context)
            email_message.attach_alternative(html_email, "text/html")
        email_message.send(fail_silently=False)


class OXPOSLogoutView(auth_views.LogoutView):
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            release_user_registers(request.user)
        return super().post(request, *args, **kwargs)


class OXPOSPasswordResetView(auth_views.PasswordResetView):
    form_class = DeliverablePasswordResetForm
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except Exception:
            logger.exception("Password-reset email delivery failed.")
            form.add_error(
                None,
                "We could not send the reset email right now. Please try again or contact OXPOS support.",
            )
            return self.form_invalid(form)


class RequiredPasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    template_name = "registration/password_change_required.html"
    success_url = reverse_lazy("dashboard")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        try:
            required = request.user.security_profile.must_change_password
        except UserSecurityProfile.DoesNotExist:
            required = False
        if not required:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        UserSecurityProfile.clear_password_change(self.request.user)
        messages.success(self.request, "Your permanent password has been saved.")
        return response


class OXPOSPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        UserSecurityProfile.clear_password_change(self.user)
        return response
