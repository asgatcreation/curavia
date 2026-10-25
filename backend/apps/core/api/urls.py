"""Core API routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import group_views, views

router = DefaultRouter()
router.register("branches", views.BranchViewSet, basename="branch")
router.register("departments", views.DepartmentViewSet, basename="department")
router.register("audit", views.AuditEventViewSet, basename="audit")

urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("me/branch/", views.SwitchBranchView.as_view(), name="switch-branch"),
    path("modules/", views.ModuleListView.as_view(), name="modules"),
    path("reporting/group/", group_views.GroupSummaryView.as_view(), name="group-summary"),
    path("permissions/", views.PermissionCatalogueView.as_view(), name="permissions"),
    path("", include(router.urls)),
]
