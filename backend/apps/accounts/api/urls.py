"""
Authentication routes, mounted under /api/v1/auth/.

These sit outside the tenant middleware (see ``UNSCOPED_PREFIXES``) because
nobody has a hospital yet when they are logging in.
"""

from __future__ import annotations

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/mfa/", views.MfaChallengeView.as_view(), name="login-mfa"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("sessions/", views.SessionListView.as_view(), name="sessions"),
    path("sessions/revoke-all/", views.RevokeAllSessionsView.as_view(), name="sessions-revoke"),
]
