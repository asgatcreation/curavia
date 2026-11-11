"""Order and referral routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("services/", views.ServiceSearchView.as_view(), name="service-search"),
    path(
        "encounters/<uuid:pk>/orders/",
        views.EncounterOrdersView.as_view(),
        name="encounter-orders",
    ),
    # Its own route: drafting is thinking, signing instructs a department to
    # do something to a patient.
    path(
        "encounters/<uuid:pk>/orders/sign/",
        views.SignOrdersView.as_view(),
        name="encounter-orders-sign",
    ),
    path("orders/<uuid:pk>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("patients/<uuid:pk>/orders/", views.PatientOrdersView.as_view(), name="patient-orders"),
    path(
        "encounters/<uuid:pk>/referrals/",
        views.EncounterReferralsView.as_view(),
        name="encounter-referrals",
    ),
    path("referrals/<uuid:pk>/", views.ReferralDetailView.as_view(), name="referral-detail"),
]
