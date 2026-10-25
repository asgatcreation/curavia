"""
Onboarding routes, mounted under /api/v1/onboarding/.

Public by necessity: nobody has an account or a hospital yet when they call
these.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("sign-up/", views.SignUpView.as_view(), name="sign-up"),
    path("verify-email/", views.VerifyEmailView.as_view(), name="verify-email"),
    path(
        "verify-email/resend/",
        views.ResendVerificationView.as_view(),
        name="verify-email-resend",
    ),
]
