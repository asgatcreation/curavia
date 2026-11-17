"""Laboratory catalogue routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("lab/panels/", views.PanelListView.as_view(), name="lab-panels"),
    path("lab/panels/<uuid:pk>/", views.PanelDetailView.as_view(), name="lab-panel-detail"),
    path(
        "lab/panels/<uuid:pk>/analytes/",
        views.AnalyteListView.as_view(),
        name="lab-panel-analytes",
    ),
    path("lab/analytes/<uuid:pk>/", views.AnalyteDetailView.as_view(), name="lab-analyte-detail"),
    # Which range a patient of this age and sex would be judged against, and
    # what a value would be flagged as. See the views module docstring.
    path(
        "lab/analytes/<uuid:pk>/preview/",
        views.AnalytePreviewView.as_view(),
        name="lab-analyte-preview",
    ),
    path(
        "lab/analytes/<uuid:pk>/ranges/",
        views.RangeListView.as_view(),
        name="lab-analyte-ranges",
    ),
    path("lab/ranges/<uuid:pk>/", views.RangeDetailView.as_view(), name="lab-range-detail"),
]
