"""Consent and document routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("consent-forms/", views.ConsentFormListView.as_view(), name="consent-forms"),
    path(
        "document-templates/",
        views.DocumentTemplateListView.as_view(),
        name="document-templates",
    ),
    path(
        "patients/<uuid:pk>/consents/",
        views.PatientConsentsView.as_view(),
        name="patient-consents",
    ),
    path("consents/<uuid:pk>/", views.ConsentDetailView.as_view(), name="consent-detail"),
    path(
        "patients/<uuid:pk>/documents/",
        views.PatientDocumentsView.as_view(),
        name="patient-documents",
    ),
    path("documents/<uuid:pk>/", views.DocumentDetailView.as_view(), name="document-detail"),
    # Its own route: drafting is writing, issuing puts a registration number
    # on paper that leaves the building.
    path(
        "documents/<uuid:pk>/issue/",
        views.IssueDocumentView.as_view(),
        name="document-issue",
    ),
    path(
        "documents/<uuid:pk>/revoke/",
        views.RevokeDocumentView.as_view(),
        name="document-revoke",
    ),
    path(
        "encounters/<uuid:pk>/paperwork/",
        views.EncounterPaperworkView.as_view(),
        name="encounter-paperwork",
    ),
    # No authentication. The token is the whole of the authorisation.
    path(
        "verify/<str:token>/",
        views.VerifyDocumentView.as_view(),
        name="document-verify-api",
    ),
]
