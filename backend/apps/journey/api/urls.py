"""Journey routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("ws/ticket/", views.WebSocketTicketView.as_view(), name="ws-ticket"),
    path("journey/", views.BoardView.as_view(), name="journey-board"),
    path("journey/start/", views.StartEncounterView.as_view(), name="journey-start"),
    path("journey/<uuid:pk>/move/", views.MoveView.as_view(), name="journey-move"),
    path("journey/<uuid:pk>/events/", views.EncounterEventsView.as_view(), name="journey-events"),
    path("displays/", views.DisplayScreenListView.as_view(), name="display-list"),
    path(
        "displays/<uuid:pk>/<str:action>/",
        views.DisplayScreenTokenView.as_view(),
        name="display-token",
    ),
    # No authentication: the token in the path is the whole of it, and what
    # it buys is numbers with no names. See PublicDisplayView.
    path("displays/public/<str:token>/", views.PublicDisplayView.as_view(), name="display-public"),
]
