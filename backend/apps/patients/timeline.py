"""
The lifetime timeline.

One list, oldest at the bottom, of everything that has ever happened to this
person at this hospital. It is the answer to the question a doctor seeing
somebody for the first time actually asks — "what has been going on with
you?" — which no single screen in a departmental system can answer, because
each department only holds its own part.

**Built as a registry rather than a query.** Every module that will exist
later owns events that belong here: encounters, triage, consultations, lab
results, prescriptions, admissions, invoices, appointments missed. If this
were one big query it would have to be rewritten, and re-tested, at every
one of those milestones. Instead each source is a function that takes a
patient and yields entries, and a module registers its own by adding to
``SOURCES``.

**What is not here yet is named rather than hidden.** ``PENDING_SOURCES``
lists the events this timeline will carry and which milestone brings them,
and the API returns it. A timeline that silently showed only a registration
would read as "nothing has ever happened to this patient", which on a
clinical screen is not an empty state — it is a wrong answer.

Entries carry a tone from the shared status palette, so the timeline colours
the same way every other Curavia screen does, and never colour alone: each
entry has a title in words.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

from apps.patients.models import Patient, PatientMerge

#: Source modules that will add to this timeline, and when. Returned by the
#: API so the screen can say what it is not showing — see the module
#: docstring, and the same approach in apps/core/api/group_views.py.
PENDING_SOURCES: tuple[dict[str, str], ...] = (
    {
        "key": "appointments",
        "label": "Appointments booked, attended and missed",
        "milestone": "milestone 5",
    },
    {
        "key": "encounters",
        "label": "Visits, with the department and who was seen",
        "milestone": "milestone 5",
    },
    {"key": "triage", "label": "Vitals and triage category", "milestone": "milestone 6"},
    {"key": "consultations", "label": "Diagnoses and clinical notes", "milestone": "milestone 6"},
    {"key": "lab", "label": "Tests ordered and results", "milestone": "milestone 7"},
    {"key": "radiology", "label": "Imaging and reports", "milestone": "milestone 7"},
    {
        "key": "prescriptions",
        "label": "Medicines prescribed and dispensed",
        "milestone": "milestone 8",
    },
    {"key": "admissions", "label": "Admissions, wards and discharge", "milestone": "milestone 10"},
    {"key": "billing", "label": "Bills and payments", "milestone": "milestone 9"},
)


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """
    One thing that happened.

    ``kind`` is a stable key the client switches an icon on; ``title`` is the
    sentence a human reads. Both, because an icon alone is not an answer and
    a sentence alone cannot be grouped.
    """

    at: datetime
    kind: str
    title: str
    detail: str = ""
    #: Who did it, as a name. Blank where the system did it or the actor has
    #: since been deleted.
    actor: str = ""
    #: From the shared status palette: neutral, info, stable, warning,
    #: urgent, critical.
    tone: str = "neutral"
    #: True for entries a clinician must not scroll past — an allergy, a
    #: death, a merge that changed the record they are reading.
    is_pinned: bool = False


# ---------------------------------------------------------------------------
# The sources that exist today
# ---------------------------------------------------------------------------


def registration_entries(patient: Patient) -> Iterator[TimelineEntry]:
    """Where every timeline starts."""
    yield TimelineEntry(
        at=patient.registered_at,
        kind="registered",
        title=f"Registered as {patient.mrn}",
        detail=(
            f"At {patient.home_branch.name}"
            if patient.home_branch_id and patient.home_branch
            else ""
        ),
        actor=name_of(patient.registered_by),
        tone="info",
    )
    if patient.is_provisional:
        yield TimelineEntry(
            at=patient.registered_at,
            kind="provisional",
            title="Registered without confirmed identity",
            detail="An emergency registration. The record still needs to be identified.",
            tone="warning",
            is_pinned=True,
        )
    if patient.is_deceased and patient.died_on:
        yield TimelineEntry(
            # A date, not a time: nobody records a time of death at the front
            # desk, and inventing midnight would be a fact the record does
            # not have.
            at=_as_datetime(patient.died_on, patient.registered_at),
            kind="died",
            title="Recorded as deceased",
            tone="critical",
            is_pinned=True,
        )


def allergy_entries(patient: Patient) -> Iterator[TimelineEntry]:
    """
    Each allergy, on the day it was noted.

    Pinned when severe, because a timeline is read by scrolling and an
    anaphylaxis recorded four years ago is still the most important thing on
    it.
    """
    if patient.allergies_checked_at is not None and not patient.allergies.exists():
        yield TimelineEntry(
            at=patient.allergies_checked_at,
            kind="allergies_none",
            title="Allergy history taken — none known",
            tone="stable",
        )
        return

    for allergy in patient.allergies.all():
        yield TimelineEntry(
            at=_as_datetime(allergy.noted_on, patient.registered_at),
            kind="allergy",
            title=f"Allergy recorded: {allergy.substance}",
            detail=" · ".join(
                part for part in (allergy.reaction, allergy.get_severity_display()) if part
            ),
            actor=name_of(allergy.recorded_by),
            tone="critical" if allergy.is_critical else "warning",
            is_pinned=allergy.is_critical and allergy.is_active,
        )


def merge_entries(patient: Patient) -> Iterator[TimelineEntry]:
    """
    Merges, from both sides.

    On the surviving record this says where the extra history came from; on
    a merged-away record it says where the history went. A clinician reading
    a history that suddenly doubles in size needs to know why, or the
    timeline reads as though the patient attended twice as often.
    """
    for merge in PatientMerge.objects.filter(winner=patient).select_related("loser", "by"):
        yield TimelineEntry(
            at=merge.at,
            kind="merge_in",
            title=f"Record {merge.loser.mrn} was merged into this one",
            detail=merge.reason
            or "A duplicate registration for the same person was folded in here.",
            actor=name_of(merge.by),
            tone="info",
            is_pinned=not merge.is_reversed,
        )
        if merge.is_reversed and merge.reversed_at:
            yield TimelineEntry(
                at=merge.reversed_at,
                kind="merge_reversed",
                title=f"That merge with {merge.loser.mrn} was reversed",
                detail=merge.reversal_reason,
                actor=name_of(merge.reversed_by),
                tone="warning",
            )

    merged_away = PatientMerge.objects.filter(loser=patient, reversed_at__isnull=True)
    for merge in merged_away.select_related("winner", "by"):
        yield TimelineEntry(
            at=merge.at,
            kind="merge_out",
            title=f"This record was merged into {merge.winner.mrn}",
            detail="Everything since is recorded on that record. This number still opens it.",
            actor=name_of(merge.by),
            tone="warning",
            is_pinned=True,
        )


#: Registered sources, in no particular order — the result is sorted by time.
#: A later milestone adds its own by appending here from its app config, so
#: the timeline grows without this module being edited.
SOURCES: list[Callable[[Patient], Iterable[TimelineEntry]]] = [
    registration_entries,
    allergy_entries,
    merge_entries,
]


def build_timeline(patient: Patient, *, limit: int | None = None) -> list[TimelineEntry]:
    """
    Everything known about this patient, newest first.

    Pinned entries are NOT hoisted to the top: the timeline is chronological
    and a clinician reading it needs the order to mean something. Pinning is
    a flag the screen uses to keep an entry visible while scrolling, and the
    patient banner carries the allergies regardless.

    A source that raises is a bug in that source, and it must not take the
    whole timeline with it — a doctor needs the rest of the history more
    than they need to know the lab module is broken. So each is guarded, and
    the failure is logged and surfaced as an entry rather than swallowed.
    """
    entries: list[TimelineEntry] = []
    for source in SOURCES:
        try:
            entries.extend(source(patient))
        except Exception:  # a broken source must not take the history with it
            import logging

            logging.getLogger("curavia.patients").exception(
                "Timeline source %s failed for patient %s",
                getattr(source, "__name__", source),
                patient.pk,
            )
            entries.append(
                TimelineEntry(
                    at=patient.registered_at,
                    kind="source_failed",
                    title="Part of this history could not be loaded",
                    detail="Somebody has been told. What is shown below is incomplete.",
                    tone="warning",
                    is_pinned=True,
                )
            )

    entries.sort(key=lambda entry: entry.at, reverse=True)
    return entries[:limit] if limit else entries


def name_of(user) -> str:
    if user is None:
        return ""
    name = getattr(user, "get_full_name", lambda: "")() or ""
    return name.strip() or getattr(user, "email", "") or ""


def _as_datetime(value, fallback: datetime) -> datetime:
    """
    A date where the timeline needs a time.

    Uses midnight in the active timezone and keeps it aware, because mixing
    aware and naive datetimes in one sort raises — which would turn a date
    of death into a 500 on the patient record.
    """
    from django.utils import timezone

    if value is None:  # pragma: no cover - callers check first
        return fallback
    return timezone.make_aware(datetime.combine(value, datetime.min.time()))


__all__ = ["PENDING_SOURCES", "SOURCES", "TimelineEntry", "build_timeline", "name_of"]
