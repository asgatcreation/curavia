"""
Coding a visit, and keeping the problem list honest.

The problem list is the part that earns its place. Diagnoses are easy —
somebody picks a code and it is stored. What makes a record usable years
later is that the **patient's conditions** are in one short list that is
true today, rather than scattered across forty visits for the next doctor to
reconstruct in the ninety seconds they have.

So the rule here is: **confirming a chronic condition puts it on the list,
once.** Not twice, not a row per visit. The doctor does not have to remember
to maintain anything, because at the moment they are coding the visit they
are already thinking about the next patient.

Three smaller rules, each learned from how paper records fail:

* One primary diagnosis per visit. Two "main reasons for attending" means
  every count the hospital reports is wrong by an unknown amount.
* Ruling something out is recorded, never deleted. "Not typhoid, checked"
  is often the most useful line in a record.
* A resolved problem keeps its row and gains a date. A problem that comes
  back is the same problem returning, not a new one.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from apps.clinical import icd10
from apps.clinical.models import Certainty, Diagnosis, Problem
from apps.journey.models import Encounter

logger = logging.getLogger("curavia.clinical")


class DiagnosisError(Exception):
    """Refused. The message is shown to the clinician as written."""


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def search(query: str, *, limit: int = 20) -> list[icd10.Code]:
    return icd10.search(query, limit=limit)


# ---------------------------------------------------------------------------
# Diagnoses
# ---------------------------------------------------------------------------


@transaction.atomic
def add(
    *,
    encounter: Encounter,
    code: str,
    certainty: str = Certainty.SUSPECTED,
    is_primary: bool = False,
    note: str = "",
    actor=None,
) -> Diagnosis:
    """
    Code this visit.

    Adding a code that is already on the visit **updates** it rather than
    refusing or duplicating: a doctor who suspected malaria in the morning
    and confirmed it after the blood film is doing exactly this, and making
    them delete and re-add would lose the time at which it was first
    considered.
    """
    entry = icd10.get(code)
    if entry is None:
        raise DiagnosisError(f"{code} is not a code in the list.")
    if certainty not in Certainty.values:
        raise DiagnosisError(f"There is no certainty called {certainty!r}.")
    if encounter.status != Encounter.Status.OPEN:
        raise DiagnosisError("That visit is closed.")

    existing = Diagnosis.objects.filter(
        encounter=encounter, code=entry.code, archived_at__isnull=True
    ).first()

    if existing is not None:
        existing.certainty = certainty
        existing.note = note.strip()[:300] or existing.note
        existing.updated_by = actor
        existing.save(update_fields=["certainty", "note", "updated_by", "updated_at"])
        diagnosis = existing
    else:
        diagnosis = Diagnosis.objects.create(
            organisation_id=encounter.organisation_id,
            branch_id=encounter.branch_id,
            encounter=encounter,
            patient_id=encounter.patient_id,
            code=entry.code,
            title=entry.title,
            chapter=entry.chapter,
            certainty=certainty,
            is_chronic=entry.chronic,
            note=note.strip()[:300],
            diagnosed_by=actor,
        )

    if is_primary:
        set_primary(diagnosis=diagnosis, actor=actor)

    _reconcile_problem(diagnosis, actor=actor)
    return diagnosis


@transaction.atomic
def set_primary(*, diagnosis: Diagnosis, actor=None) -> Diagnosis:
    """
    Make this the reason they came, and demote whatever held it.

    Enforced here rather than by a database constraint because the swap is
    two writes, and a partial unique index would make the first of them fail
    depending on which order they happened to run in.
    """
    if diagnosis.certainty == Certainty.RULED_OUT:
        raise DiagnosisError("Something that has been ruled out cannot be the main diagnosis.")

    Diagnosis.objects.filter(
        encounter_id=diagnosis.encounter_id, is_primary=True, archived_at__isnull=True
    ).exclude(pk=diagnosis.pk).update(is_primary=False, updated_at=timezone.now())

    if not diagnosis.is_primary:
        diagnosis.is_primary = True
        diagnosis.updated_by = actor
        diagnosis.save(update_fields=["is_primary", "updated_by", "updated_at"])
    return diagnosis


@transaction.atomic
def rule_out(*, diagnosis: Diagnosis, actor=None, note: str = "") -> Diagnosis:
    """
    Mark it excluded. Never deletes it.

    What has already been considered and excluded is exactly what the next
    doctor needs, and a record that quietly drops it invites the same
    investigation to be ordered twice.
    """
    diagnosis.certainty = Certainty.RULED_OUT
    diagnosis.is_primary = False
    if note.strip():
        diagnosis.note = note.strip()[:300]
    diagnosis.updated_by = actor
    diagnosis.save(update_fields=["certainty", "is_primary", "note", "updated_by", "updated_at"])

    # A problem opened by this diagnosis is stood down with it. Not resolved
    # — it was never there — so it leaves the active list without claiming
    # the patient got better.
    Problem.objects.filter(
        patient_id=diagnosis.patient_id,
        code=diagnosis.code,
        opened_by_diagnosis=diagnosis,
        status=Problem.Status.ACTIVE,
    ).update(status=Problem.Status.INACTIVE, updated_at=timezone.now())
    return diagnosis


@transaction.atomic
def remove(*, diagnosis: Diagnosis, actor=None) -> None:
    """
    Take a code off the visit, for a code entered in error.

    Archived, not deleted — "somebody coded this and took it off again" is a
    fact, and the audit log refers to the row. For a diagnosis that was
    genuinely considered and excluded, ``rule_out`` is the right call and
    this one is not.
    """
    diagnosis.archive(by=actor)
    Problem.objects.filter(opened_by_diagnosis=diagnosis, status=Problem.Status.ACTIVE).update(
        status=Problem.Status.INACTIVE, updated_at=timezone.now()
    )


def for_encounter(encounter: Encounter) -> list[Diagnosis]:
    return list(
        Diagnosis.objects.filter(encounter=encounter, archived_at__isnull=True)
        .select_related("diagnosed_by")
        .order_by("-is_primary", "diagnosed_at")
    )


def history(patient, *, limit: int = 100) -> list[Diagnosis]:
    """Every code this patient has ever had, newest first."""
    return list(
        Diagnosis.objects.filter(patient=patient, archived_at__isnull=True)
        .select_related("diagnosed_by", "encounter")
        .order_by("-diagnosed_at")[:limit]
    )


# ---------------------------------------------------------------------------
# The problem list
# ---------------------------------------------------------------------------


def _reconcile_problem(diagnosis: Diagnosis, *, actor=None) -> Problem | None:
    """
    Put a confirmed chronic condition on the list, exactly once.

    Only confirmed, and only chronic. A suspicion does not belong on a
    problem list — the next doctor reads that list as what the patient has —
    and neither does a chest infection they will be over in a week.
    """
    if diagnosis.certainty != Certainty.CONFIRMED or not diagnosis.is_chronic:
        return None

    now = timezone.now()
    problem = Problem.objects.filter(patient_id=diagnosis.patient_id, code=diagnosis.code).first()

    if problem is None:
        return Problem.objects.create(
            organisation_id=diagnosis.organisation_id,
            patient_id=diagnosis.patient_id,
            code=diagnosis.code,
            title=diagnosis.title,
            chapter=diagnosis.chapter,
            onset_on=timezone.localdate(),
            opened_by_diagnosis=diagnosis,
            opened_by=actor,
            opened_at=now,
            last_noted_at=now,
        )

    fields = ["last_noted_at", "updated_at"]
    problem.last_noted_at = now
    if problem.status != Problem.Status.ACTIVE:
        # Confirmed again after being resolved or stood down: the same
        # condition has come back, so the row comes back with it rather than
        # a second row appearing beside the first.
        problem.status = Problem.Status.ACTIVE
        problem.resolved_on = None
        fields += ["status", "resolved_on"]
    problem.save(update_fields=fields)
    return problem


@transaction.atomic
def add_problem(
    *,
    organisation_id,
    patient,
    code: str,
    note: str = "",
    onset_on: dt.date | None = None,
    actor=None,
) -> Problem:
    """
    Put a condition on the list directly.

    For what the patient arrives already having — "hypertensive for eight
    years, on amlodipine" — which was never diagnosed here and must still be
    on the list before anybody prescribes anything.
    """
    entry = icd10.get(code)
    if entry is None:
        raise DiagnosisError(f"{code} is not a code in the list.")

    now = timezone.now()
    problem, created = Problem.objects.get_or_create(
        patient=patient,
        code=entry.code,
        defaults={
            "organisation_id": organisation_id,
            "title": entry.title,
            "chapter": entry.chapter,
            "onset_on": onset_on,
            "note": note.strip()[:300],
            "opened_by": actor,
            "opened_at": now,
            "last_noted_at": now,
        },
    )
    if not created:
        problem.status = Problem.Status.ACTIVE
        problem.resolved_on = None
        problem.last_noted_at = now
        if note.strip():
            problem.note = note.strip()[:300]
        if onset_on:
            problem.onset_on = onset_on
        problem.save(
            update_fields=[
                "status",
                "resolved_on",
                "last_noted_at",
                "note",
                "onset_on",
                "updated_at",
            ]
        )
    return problem


@transaction.atomic
def resolve_problem(
    *, problem: Problem, actor=None, on: dt.date | None = None, note: str = ""
) -> Problem:
    """Mark it over. Keeps the row and the date."""
    if problem.status == Problem.Status.RESOLVED:
        raise DiagnosisError("That problem is already marked resolved.")
    problem.status = Problem.Status.RESOLVED
    problem.resolved_on = on or timezone.localdate()
    if note.strip():
        problem.note = note.strip()[:300]
    problem.updated_by = actor
    problem.save(update_fields=["status", "resolved_on", "note", "updated_by", "updated_at"])
    return problem


@transaction.atomic
def reopen_problem(*, problem: Problem, actor=None) -> Problem:
    """It has come back. The same row, active again."""
    problem.status = Problem.Status.ACTIVE
    problem.resolved_on = None
    problem.last_noted_at = timezone.now()
    problem.updated_by = actor
    problem.save(
        update_fields=["status", "resolved_on", "last_noted_at", "updated_by", "updated_at"]
    )
    return problem


def problem_list(patient, *, include_closed: bool = True) -> list[Problem]:
    """
    What this patient has, active first.

    Resolved and stood-down problems come after, because the history is part
    of the picture — but they are never mixed in among the active ones,
    which is the list somebody reads in the seconds before the consultation.
    """
    rows = Problem.objects.filter(patient=patient).select_related("opened_by")
    if not include_closed:
        rows = rows.filter(status=Problem.Status.ACTIVE)

    order = {Problem.Status.ACTIVE: 0, Problem.Status.INACTIVE: 1, Problem.Status.RESOLVED: 2}
    return sorted(
        rows,
        key=lambda problem: (order.get(problem.status, 9), -problem.last_noted_at.timestamp()),
    )


__all__ = [
    "DiagnosisError",
    "add",
    "add_problem",
    "for_encounter",
    "history",
    "problem_list",
    "remove",
    "reopen_problem",
    "resolve_problem",
    "rule_out",
    "search",
    "set_primary",
]
