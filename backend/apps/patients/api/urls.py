"""Patient routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import merge_views, views

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
    path("patients/scan/", merge_views.ScanView.as_view(), name="patient-scan"),
    path("patients/merge/", merge_views.MergeView.as_view(), name="patient-merge"),
    path(
        "patients/merge/preview/",
        merge_views.MergePreviewView.as_view(),
        name="patient-merge-preview",
    ),
    path(
        "patients/merge/<uuid:pk>/reverse/",
        merge_views.MergeReverseView.as_view(),
        name="patient-merge-reverse",
    ),
    # Before the <uuid:pk> route, or "by-mrn" would be read as an id.
    path("patients/by-mrn/<path:mrn>/", views.PatientByMrnView.as_view(), name="patient-by-mrn"),
    path("patients/<uuid:pk>/", views.PatientDetailView.as_view(), name="patient-detail"),
    path(
        "patients/<uuid:pk>/timeline/",
        merge_views.PatientTimelineView.as_view(),
        name="patient-timeline",
    ),
    path("patients/<uuid:pk>/card/", merge_views.PatientCardView.as_view(), name="patient-card"),
    path(
        "patients/<uuid:pk>/merges/",
        merge_views.PatientMergeHistoryView.as_view(),
        name="patient-merges",
    ),
]
