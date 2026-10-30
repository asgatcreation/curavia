"""Scheduling routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("clinics/", views.ClinicListView.as_view(), name="clinic-list"),
    path("clinics/availability/", views.AvailabilityView.as_view(), name="availability"),
    path("schedules/", views.ScheduleListView.as_view(), name="schedule-list"),
    path("schedules/exceptions/", views.ExceptionView.as_view(), name="schedule-exception"),
    path("appointments/", views.AppointmentListView.as_view(), name="appointment-list"),
    path("appointments/book/", views.BookView.as_view(), name="appointment-book"),
    # One route, two verbs: see AppointmentActionView for why moving and
    # cancelling belong together.
    path(
        "appointments/<uuid:pk>/<str:action>/",
        views.AppointmentActionView.as_view(),
        name="appointment-action",
    ),
    path("queue/", views.QueueView.as_view(), name="queue"),
    path("queue/check-in/", views.CheckInView.as_view(), name="queue-check-in"),
    path("queue/call-next/", views.CallNextView.as_view(), name="queue-call-next"),
    path(
        "queue/<uuid:pk>/<str:action>/",
        views.TicketActionView.as_view(),
        name="queue-ticket-action",
    ),
]
