"""Clinical routes, mounted under /api/v1/."""

from __future__ import annotations

from django.urls import path

from . import note_views, views

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
    # ------------------------------------------------------ consultation notes
    path("note-templates/", note_views.NoteTemplateListView.as_view(), name="note-templates"),
    path(
        "encounters/<uuid:pk>/consultation/",
        note_views.ConsultationNotesView.as_view(),
        name="consultation-notes",
    ),
    # One route for reading, autosaving and discarding, because they are the
    # same resource in three verbs.
    path("notes/<uuid:pk>/", note_views.NoteDetailView.as_view(), name="clinical-note"),
    path("notes/<uuid:pk>/sign/", note_views.SignNoteView.as_view(), name="clinical-note-sign"),
    path("notes/<uuid:pk>/amend/", note_views.AmendNoteView.as_view(), name="clinical-note-amend"),
    path(
        "patients/<uuid:pk>/notes/",
        note_views.PatientNotesView.as_view(),
        name="patient-clinical-notes",
    ),
]
