"""
The two public pages, mounted at the site root.

Short paths on purpose. ``/d/K7PQ-3MXD`` style addresses are what get typed
by hand when a QR code will not scan — a torn photocopy, a cracked camera,
a phone with no camera at all — and a path with four segments in it is a
path nobody types correctly.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("verify/<str:token>/", views.verify, name="verify"),
    path("verify/<str:token>/print/", views.printable, name="printable"),
]
