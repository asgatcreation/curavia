"""
Staff, roles and invitations, mounted under /api/v1/.

Separate from ``urls.py``, which is the public authentication surface under
/api/v1/auth/. These need a signed-in caller and a hospital in scope; those
deliberately do not.
"""

from __future__ import annotations

from django.urls import path

from . import staff_views

urlpatterns = [
    path("roles/", staff_views.RoleListView.as_view(), name="role-list"),
    path("staff/", staff_views.StaffListView.as_view(), name="staff-list"),
    path(
        "staff/<uuid:pk>/deactivate/",
        staff_views.DeactivateStaffView.as_view(),
        name="staff-deactivate",
    ),
    path("invitations/", staff_views.InvitationListView.as_view(), name="invitation-list"),
    path(
        "invitations/<uuid:pk>/<slug:action>/",
        staff_views.InvitationActionView.as_view(),
        name="invitation-action",
    ),
]
