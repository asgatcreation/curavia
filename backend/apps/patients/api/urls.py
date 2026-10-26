"""Patient routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("patients/", views.PatientSearchView.as_view(), name="patient-search"),
    path("patients/register/", views.PatientRegisterView.as_view(), name="patient-register"),
    path(
        "patients/register/quick/",
        views.QuickRegisterView.as_view(),
        name="patient-register-quick",
    ),
    path(
        "patients/check-duplicates/",
        views.DuplicateCheckView.as_view(),
        name="patient-check-duplicates",
    ),
    # Before the <uuid:pk> route, or "by-mrn" would be read as an id.
    path("patients/by-mrn/<path:mrn>/", views.PatientByMrnView.as_view(), name="patient-by-mrn"),
    path("patients/<uuid:pk>/", views.PatientDetailView.as_view(), name="patient-detail"),
]
