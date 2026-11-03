"""
Taking observations, scoring them, and deciding how urgent somebody is.

The one rule everything here serves: **a high score must reach a person.**

A NEWS2 of 7 computed, stored, and displayed on a screen nobody is looking at
has achieved nothing. So recording an observation does three things in one
transaction — stores it, scores it, and raises the patient's position in the
queue — and then tells every open board about it. The nurse who typed the
numbers does not have to remember to do anything else, because at the moment
she is typing them she has forty other people waiting.

Triage colour and NEWS2 are deliberately **not** collapsed into each other.
They answer different questions: the colour is "how soon must somebody see
this person", which is a judgement including things no machine measures — how
they look, what they said, that they came in alone — and the score is "how
abnormal are these seven numbers". A nurse can put a red on a patient whose
observations are all normal, and must be able to. What the score does is stop
a patient with a quietly terrible set of numbers being left on green.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.clinical import news2
from apps.clinical.models import (
    NursingNote,
    Observation,
    TriageAssessment,
    TriageCategory,
)
from apps.journey.models import Encounter, Stage

logger = logging.getLogger("curavia.clinical")

#: A score at or above this makes the system raise the patient up the queue
#: by itself. Five is the RCP's "urgent response" threshold.
AUTO_ESCALATE_FROM = 5

#: Where an escalated patient is put in the queue. Negative sorts earlier;
#: see apps/scheduling/models.QueueTicket.priority.
ESCALATED_PRIORITY = -3

#: The lowest category the system will let a patient sit on once their score
#: says otherwise. It never *lowers* a nurse's category — only raises it.
ESCALATED_CATEGORY = TriageCategory.ORANGE


class TriageError(Exception):
    """The request was refused. The message is shown to the nurse."""


#: Fields stored to one or two decimal places, with how many.
#:
#: A caller passing 36.8 as a Python float is passing 36.79999999999999716,
#: which a DecimalField with one decimal place rightly refuses. The service
#: takes a number and stores a number; whether it arrived as a float, a
#: string or a Decimal is not the caller's problem to solve.
DECIMAL_PLACES: dict[str, int] = {
    "temperature": 1,
    "oxygen_litres": 1,
    "height_cm": 1,
    "blood_glucose": 1,
    "weight_kg": 2,
}


def _as_decimal(value, places: int) -> Decimal:
    """Round half up, like every other number in Curavia."""
    try:
        quantum = Decimal(1).scaleb(-places)
        return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise TriageError(f"{value!r} is not a number.") from exc


def normalise(data: dict) -> dict:
    """Make the decimal measurements safe to store, whatever they arrived as."""
    cleaned = dict(data)
    for field, places in DECIMAL_PLACES.items():
        if cleaned.get(field) is not None:
            cleaned[field] = _as_decimal(cleaned[field], places)
    return cleaned


def patient_age(patient, *, on: dt.date | None = None) -> int | None:
    """Age in whole years, or None when the date of birth is unknown."""
    if not patient.date_of_birth:
        return None
    today = on or timezone.localdate()
    years = today.year - patient.date_of_birth.year
    if (today.month, today.day) < (patient.date_of_birth.month, patient.date_of_birth.day):
        years -= 1
    return max(years, 0)


def score_for(observation: Observation, *, patient=None) -> news2.Score:
    """
    Score one observation, with the patient's age taken into account.

    Age matters because NEWS2 is not valid under 16 — see
    apps/clinical/news2.py. Passing the patient explicitly rather than
    reading ``observation.patient`` saves a query on the hot path where the
    caller already has it.
    """
    patient = patient or observation.patient
    return news2.score(
        respiration_rate=observation.respiration_rate,
        spo2=observation.spo2,
        on_oxygen=observation.on_oxygen,
        systolic_bp=observation.systolic_bp,
        pulse=observation.pulse,
        consciousness=observation.consciousness or None,
        temperature=float(observation.temperature) if observation.temperature is not None else None,
        scale=news2.OxygenScale(observation.oxygen_scale or 1),
        age_years=patient_age(patient),
    )


@transaction.atomic
def record_observation(
    *,
    encounter: Encounter,
    data: dict,
    actor=None,
    supersedes: Observation | None = None,
    correction_reason: str = "",
) -> tuple[Observation, news2.Score]:
    """
    Store a set of vitals, score them, and escalate if they are bad.

    ``supersedes`` corrects an earlier reading. The earlier one is kept and
    marked rather than edited: a nurse who typed 220 instead of 120 and then
    quietly changed it leaves no trace that anybody reacted to a
    hypertensive crisis which never happened.
    """
    if encounter.status != Encounter.Status.OPEN:
        raise TriageError("That visit is closed.")

    patient = encounter.patient
    data = normalise(data)
    observation = Observation(
        organisation_id=encounter.organisation_id,
        branch_id=encounter.branch_id,
        encounter=encounter,
        patient=patient,
        taken_by=actor,
        correction_reason=correction_reason.strip()[:200],
        **{key: value for key, value in data.items() if value is not None or key == "on_oxygen"},
    )
    if not observation.recorded_anything():
        raise TriageError("Nothing was entered. Record at least one measurement.")

    observation.full_clean(exclude=["organisation", "branch", "encounter", "patient"])

    computed = score_for(observation, patient=patient)
    if computed.usable:
        observation.news2_score = computed.total
        observation.news2_red_flag = computed.red_flag
        observation.news2_band = computed.band
    else:
        # Deliberately left null rather than zero. A child with no score is
        # not a child with a score of nought.
        observation.news2_score = None
        observation.news2_band = "not_applicable"
    observation.save()

    if supersedes is not None:
        if supersedes.encounter_id != encounter.pk:
            raise TriageError("That reading belongs to a different visit.")
        Observation.objects.filter(pk=supersedes.pk).update(
            superseded_by=observation, updated_at=timezone.now()
        )

    _escalate_if_needed(encounter, computed, actor=actor)
    return observation, computed


def _escalate_if_needed(encounter: Encounter, computed: news2.Score, *, actor=None) -> None:
    """
    Raise the patient up the queue when the numbers say to.

    Only ever upwards. A nurse's judgement is never overridden downwards by
    an algorithm — a patient she put on red with normal observations stays on
    red, because she saw something the machine did not.
    """
    if not computed.usable:
        return
    escalating = computed.total >= AUTO_ESCALATE_FROM or computed.red_flag
    if not escalating:
        return

    assessment = TriageAssessment.objects.filter(encounter=encounter).first()
    if assessment is not None and _rank(assessment.category) > _rank(ESCALATED_CATEGORY):
        TriageAssessment.objects.filter(pk=assessment.pk).update(
            category=ESCALATED_CATEGORY,
            revision_reason=(
                f"Raised automatically: NEWS2 {computed.total}"
                f"{', one reading scored 3' if computed.red_flag else ''}."
            ),
            revised_at=timezone.now(),
            revised_by=actor,
            updated_at=timezone.now(),
        )

    # Re-read, never the cached relation. `encounter.ticket` was loaded with
    # the encounter and may predate a triage decision made since — which is
    # how a red patient on priority -5 got quietly demoted to -3 by a score
    # that should only ever raise somebody.
    from apps.scheduling.models import QueueTicket

    ticket = QueueTicket.objects.filter(encounter=encounter).first()
    if ticket is not None and ticket.priority > ESCALATED_PRIORITY:
        QueueTicket.objects.filter(pk=ticket.pk).update(
            priority=ESCALATED_PRIORITY, updated_at=timezone.now()
        )

    Encounter.objects.filter(pk=encounter.pk).update(
        triage_category=ESCALATED_CATEGORY, updated_at=timezone.now()
    )

    logger.warning(
        "NEWS2 %s for encounter %s — patient raised up the queue",
        computed.total,
        encounter.pk,
    )
    _announce(encounter)


#: Queue position per colour. Lower is seen sooner.
QUEUE_PRIORITY: dict[str, int] = {
    TriageCategory.RED: -5,
    TriageCategory.ORANGE: -3,
    TriageCategory.YELLOW: -1,
    TriageCategory.GREEN: 0,
    TriageCategory.BLUE: 1,
}


def _rank(category: str) -> int:
    """Lower is more urgent, matching the colours' own order."""
    order = [choice.value for choice in TriageCategory]
    return order.index(category) if category in order else len(order)


def _urgency_ordering():
    """
    Order by urgency, not by the colour's name.

    Found by a test: ``order_by("category")`` sorts the strings, which puts
    blue and green ahead of red. A triage queue sorted alphabetically is a
    triage queue that kills somebody.
    """
    from django.db.models import Case, IntegerField, Value, When

    return Case(
        *[When(category=category, then=Value(rank)) for category, rank in QUEUE_PRIORITY.items()],
        default=Value(99),
        output_field=IntegerField(),
    )


def _announce(encounter: Encounter) -> None:
    """
    Tell the boards. A score nobody sees has achieved nothing.

    Guarded, because a broken channel layer must not stop a nurse recording
    observations — see apps/journey/service.py for the same decision.
    """
    try:
        from apps.journey import service as journey

        journey.broadcast(
            journey.board_group(encounter.organisation_id, encounter.branch_id),
            {"type": "broadcast", "event": "journey", "reason": "triage", "row": None},
        )
    except Exception:  # the board is not worth losing the observation over
        logger.exception("could not announce a triage escalation")


# ---------------------------------------------------------------------------
# The triage decision
# ---------------------------------------------------------------------------


@transaction.atomic
def assess(
    *,
    encounter: Encounter,
    category: str,
    complaint: str,
    actor=None,
    observation: Observation | None = None,
) -> TriageAssessment:
    """
    Record, or revise, how urgent somebody is.

    One assessment per encounter. Revising it keeps the same row and records
    why, because a patient's urgency changing is part of their story and a
    second row would make "when were they triaged" ambiguous.
    """
    if category not in TriageCategory.values:
        raise TriageError(f"There is no triage category called {category!r}.")
    if not complaint.strip():
        raise TriageError("Write what the patient says is wrong.")

    existing = TriageAssessment.objects.filter(encounter=encounter).first()
    if existing is not None:
        if existing.category != category:
            existing.revision_reason = f"Changed from {existing.get_category_display()} by a nurse."
            existing.revised_at = timezone.now()
            existing.revised_by = actor
        existing.category = category
        existing.complaint = complaint.strip()[:200]
        if observation is not None:
            existing.observation = observation
        existing.save(
            update_fields=[
                "category",
                "complaint",
                "observation",
                "revision_reason",
                "revised_at",
                "revised_by",
                "updated_at",
            ]
        )
        assessment = existing
    else:
        assessment = TriageAssessment.objects.create(
            organisation_id=encounter.organisation_id,
            branch_id=encounter.branch_id,
            encounter=encounter,
            patient=encounter.patient,
            category=category,
            complaint=complaint.strip()[:200],
            observation=observation,
            assessed_by=actor,
        )

    # Mirrored onto the encounter so the board can colour a row without
    # joining to a module the hospital may not have switched on.
    Encounter.objects.filter(pk=encounter.pk).update(
        triage_category=category, updated_at=timezone.now()
    )

    from apps.scheduling.models import QueueTicket

    ticket = QueueTicket.objects.filter(encounter=encounter).first()
    if ticket is not None:
        QueueTicket.objects.filter(pk=ticket.pk).update(
            priority=QUEUE_PRIORITY[category], updated_at=timezone.now()
        )

    _announce(encounter)
    return assessment


def finish_triage(encounter: Encounter, *, actor=None) -> Encounter:
    """Send the patient on from triage to wait for the doctor."""
    from apps.journey import service as journey

    return journey.move_to(encounter, Stage.WAITING_DOCTOR, actor=actor, note="Triaged.")


# ---------------------------------------------------------------------------
# Nursing notes
# ---------------------------------------------------------------------------


def write_note(
    *,
    encounter: Encounter,
    body: str,
    kind: str = NursingNote.Kind.OBSERVATION,
    actor=None,
    amends: NursingNote | None = None,
) -> NursingNote:
    """
    Add a note. Never edits one.

    An addendum points at the note it follows and the original is untouched.
    A nursing record that can be rewritten after an incident is one nobody
    can rely on, and the person it protects first is the nurse.
    """
    if amends is not None:
        kind = NursingNote.Kind.ADDENDUM
        if amends.encounter_id != encounter.pk:
            raise TriageError("That note belongs to a different visit.")

    note = NursingNote(
        organisation_id=encounter.organisation_id,
        branch_id=encounter.branch_id,
        encounter=encounter,
        patient=encounter.patient,
        kind=kind,
        body=body.strip(),
        written_by=actor,
        amends=amends,
    )
    note.full_clean(exclude=["organisation", "branch", "encounter", "patient", "amends"])
    note.save()
    return note


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


def chart(patient, *, limit: int = 50) -> list[Observation]:
    """
    The observation chart, newest first.

    Superseded readings are included. A chart that hid them would hide that
    somebody reacted to a wrong number, which is the thing a review is
    looking for.
    """
    return list(
        Observation.objects.filter(patient=patient)
        .select_related("taken_by")
        .order_by("-taken_at")[:limit]
    )


def latest(encounter: Encounter) -> Observation | None:
    return (
        Observation.objects.filter(encounter=encounter, superseded_by__isnull=True)
        .order_by("-taken_at")
        .first()
    )


def waiting_room(branch_id, *, now: dt.datetime | None = None) -> list[dict]:
    """
    Everybody triaged and still waiting, most urgent first.

    Ordered by category and then by how long they have waited — and each row
    carries whether they are past their own colour's target, which is the
    only wait that means anything.
    """
    now = now or timezone.now()
    rows = list(
        TriageAssessment.objects.filter(
            branch_id=branch_id,
            encounter__status=Encounter.Status.OPEN,
        )
        .select_related("patient", "encounter")
        .annotate(urgency=_urgency_ordering())
        .order_by("urgency", "assessed_at")
    )

    # The LATEST score, not whichever observation happened to be attached
    # when the nurse pressed save. A patient whose vitals were taken again
    # ten minutes ago should show that score, and an assessment made before
    # any observation existed has nothing attached at all.
    #
    # One query for the whole queue rather than one per row: forty waiting
    # patients would otherwise be forty round trips to draw one list.
    latest_scores: dict[str, Observation] = {}
    for observation in (
        Observation.objects.filter(
            encounter_id__in=[row.encounter_id for row in rows],
            superseded_by__isnull=True,
        )
        .only("encounter_id", "news2_score", "news2_red_flag", "taken_at")
        .order_by("taken_at")
    ):
        latest_scores[str(observation.encounter_id)] = observation

    return [
        {
            "id": str(row.pk),
            "encounter_id": str(row.encounter_id),
            "patient_name": row.patient.full_name,
            "mrn": row.patient.mrn,
            "category": row.category,
            "complaint": row.complaint,
            "assessed_at": row.assessed_at.isoformat(),
            "minutes_waiting": max(int((now - row.assessed_at).total_seconds() // 60), 0),
            "target_minutes": row.target_minutes,
            "is_overdue": row.is_overdue(now=now),
            "news2": _score_of(latest_scores.get(str(row.encounter_id)), "news2_score"),
            "news2_red_flag": bool(
                _score_of(latest_scores.get(str(row.encounter_id)), "news2_red_flag")
            ),
            "stage": row.encounter.stage,
        }
        for row in rows
    ]


def _score_of(observation, field: str):
    return getattr(observation, field, None) if observation is not None else None


__all__ = [
    "AUTO_ESCALATE_FROM",
    "ESCALATED_CATEGORY",
    "TriageError",
    "assess",
    "chart",
    "finish_triage",
    "latest",
    "patient_age",
    "record_observation",
    "score_for",
    "waiting_room",
    "write_note",
]
