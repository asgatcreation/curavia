"""Clinical routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("triage/", views.WaitingRoomView.as_view(), name="triage-queue"),
    # Writes nothing: the triage form calls it as the nurse types, so the
    # score appears beside the numbers rather than after a save.
    path("triage/score/", views.ScorePreviewView.as_view(), name="triage-score"),
    path(
        "encounters/<uuid:pk>/observations/",
        views.ObservationView.as_view(),
        name="observation-create",
    ),
    path("encounters/<uuid:pk>/triage/", views.AssessView.as_view(), name="triage-assess"),
    path("encounters/<uuid:pk>/notes/", views.NotesView.as_view(), name="nursing-notes"),
    path("patients/<uuid:pk>/chart/", views.ChartView.as_view(), name="observation-chart"),
]
