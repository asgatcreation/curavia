"""Scheduling routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import reminder_views, views

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
    path("reminders/", reminder_views.ReminderListView.as_view(), name="reminder-list"),
    path("reminders/cost/", reminder_views.ReminderCostView.as_view(), name="reminder-cost"),
    path("reminders/send/", reminder_views.SendNowView.as_view(), name="reminder-send"),
    path("no-shows/", reminder_views.NoShowListView.as_view(), name="no-show-list"),
    path(
        "patients/<uuid:pk>/reminders/",
        reminder_views.OptOutView.as_view(),
        name="patient-reminder-preference",
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
