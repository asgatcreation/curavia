"""
Onboarding routes, mounted under /api/v1/onboarding/.

Public by necessity: nobody has an account or a hospital yet when they call
these.
"""

from __future__ import annotations

from django.urls import path

from . import import_views, setup_views, views

urlpatterns = [
    path("sign-up/", views.SignUpView.as_view(), name="sign-up"),
    path("verify-email/", views.VerifyEmailView.as_view(), name="verify-email"),
    path(
        "verify-email/resend/",
        views.ResendVerificationView.as_view(),
        name="verify-email-resend",
    ),
    # The setup wizard. Signed in, inside a hospital, and gated on
    # org.settings.change.
    path("setup/", setup_views.SetupStateView.as_view(), name="setup"),
    path("setup/<slug:step>/", setup_views.ApplyStepView.as_view(), name="setup-step"),
    path(
        "setup/<slug:step>/skip/",
        setup_views.SkipStepView.as_view(),
        name="setup-step-skip",
    ),
    # Spreadsheet import. The file is uploaded with each step; see
    # import_views for why.
    path("import/", import_views.ImportCatalogueView.as_view(), name="import-catalogue"),
    path(
        "import/<slug:kind>/analyse/",
        import_views.AnalyseImportView.as_view(),
        name="import-analyse",
    ),
    path(
        "import/<slug:kind>/preview/",
        import_views.PreviewImportView.as_view(),
        name="import-preview",
    ),
    path(
        "import/<slug:kind>/commit/",
        import_views.CommitImportView.as_view(),
        name="import-commit",
    ),
]
