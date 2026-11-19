"""Laboratory routes, mounted under /api/v1/: the catalogue, and the worklist."""

from __future__ import annotations

from django.urls import path

from . import views, worklist_views

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
    # ---------------------------------------------------------- the worklist
    path("lab/worklist/", worklist_views.WorklistView.as_view(), name="lab-worklist"),
    path(
        "lab/worklist/patient/<uuid:patient_id>/tubes/",
        worklist_views.CollectionPlanView.as_view(),
        name="lab-collection-plan",
    ),
    path("lab/collect/", worklist_views.CollectView.as_view(), name="lab-collect"),
    path(
        "lab/requests/<uuid:pk>/cancel/",
        worklist_views.RequestCancelView.as_view(),
        name="lab-request-cancel",
    ),
    path(
        "lab/samples/<uuid:pk>/receive/",
        worklist_views.SampleReceiveView.as_view(),
        name="lab-sample-receive",
    ),
    path(
        "lab/samples/<uuid:pk>/reject/",
        worklist_views.SampleRejectView.as_view(),
        name="lab-sample-reject",
    ),
    # Labels are PDFs. See apps.laboratory.labels for why.
    path(
        "lab/samples/<uuid:pk>/label.pdf",
        worklist_views.SampleLabelView.as_view(),
        name="lab-sample-label",
    ),
    path(
        "lab/labels.pdf",
        worklist_views.SampleLabelSheetView.as_view(),
        name="lab-label-sheet",
    ),
]
