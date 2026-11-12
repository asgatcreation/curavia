"""Nursing task and handover routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("wards/", views.WardsView.as_view(), name="wards"),
    path("nursing-tasks/", views.TaskListView.as_view(), name="nursing-tasks"),
    path("nursing-tasks/<uuid:pk>/", views.TaskDetailView.as_view(), name="nursing-task-detail"),
    # The ward screen's one call: the list, the handover to read, and the
    # draft being written, together.
    path("wards/<uuid:pk>/tasks/", views.WardTasksView.as_view(), name="ward-tasks"),
    path("encounters/<uuid:pk>/tasks/", views.EncounterTasksView.as_view(), name="encounter-tasks"),
    path("patients/<uuid:pk>/tasks/", views.PatientTasksView.as_view(), name="patient-tasks"),
    path("wards/<uuid:pk>/handovers/", views.HandoverListView.as_view(), name="ward-handovers"),
    path("handovers/<uuid:pk>/", views.HandoverDetailView.as_view(), name="handover-detail"),
    # Its own route: writing is thinking, signing makes the ward somebody
    # else's responsibility.
    path("handovers/<uuid:pk>/sign/", views.SignHandoverView.as_view(), name="handover-sign"),
    path(
        "handovers/<uuid:pk>/accept/",
        views.AcceptHandoverView.as_view(),
        name="handover-accept",
    ),
]
