"""
Triage: recording observations, scoring them, and getting a sick patient seen.

Every name and number here is synthetic.

``tests/test_news2.py`` checks the arithmetic. This file checks the thing the
arithmetic is for: **that a bad score reaches a person.** A NEWS2 of 7 stored
in a table nobody is looking at has achieved nothing, so most of what follows
is about what happens to the queue.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.clinical import triage
from apps.clinical.models import NursingNote, Observation, TriageAssessment, TriageCategory
from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.journey import service as journey
from apps.journey.models import Encounter, Stage
from apps.patients.models import Patient, Sex
from apps.patients.registration import RegistrationInput, register_patient
from apps.scheduling import booking
from apps.scheduling.models import QueueTicket

pytestmark = [pytest.mark.django_db]


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------


@pytest.fixture
def department(hospital) -> Department:
    with hospital.scope():
        found = Department.objects.filter(kind=Department.Kind.CLINIC).first()
        assert found is not None
        return found


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Emeka",
        "last_name": "Nwosu",
        "sex": Sex.MALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1985, 6, 1),
    }
    defaults.update(overrides)
    with hospital.scope():
        return register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(**defaults),
        )


def arrive(hospital, department, **overrides) -> Encounter:
    """Check somebody in, so they have an encounter and a queue ticket."""
    patient = make_patient(hospital, **overrides)
    with hospital.scope():
        booking.check_in(patient=patient, department=department, branch=hospital.main_branch)
        return Encounter.objects.select_related("patient", "ticket").get(
            patient=patient, status=Encounter.Status.OPEN
        )


WELL = {
    "respiration_rate": 16,
    "spo2": 98,
    "on_oxygen": False,
    "systolic_bp": 120,
    "pulse": 72,
    "consciousness": "A",
    "temperature": 36.8,
}

SEPTIC = {
    "respiration_rate": 26,
    "spo2": 93,
    "on_oxygen": True,
    "systolic_bp": 88,
    "pulse": 125,
    "consciousness": "C",
    "temperature": 39.4,
}


@pytest.fixture
def api_as_nurse(api, hospital):
    user = hospital.staff("nurse")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


# --------------------------------------------------------------------------
# Recording observations
# --------------------------------------------------------------------------


def test_an_observation_is_stored_with_its_score(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        observation, computed = triage.record_observation(encounter=encounter, data=dict(WELL))

    assert observation.news2_score == 0
    assert observation.news2_band == "routine"
    assert observation.score_version == "NEWS2-2017"
    assert computed.is_complete


def test_a_form_with_nothing_in_it_is_refused(hospital, department) -> None:
    """A row of nothing but a timestamp is noise on a chart people scan."""
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(triage.TriageError, match="Nothing was entered"):
        triage.record_observation(encounter=encounter, data={"note": "saw the patient"})


def test_one_measurement_is_enough(hospital, department) -> None:
    """
    A nurse with forty people waiting takes a temperature and moves on. A
    form that refuses produces an invented respiratory rate, which is worse
    than a blank because it gets scored.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        observation, computed = triage.record_observation(
            encounter=encounter, data={"temperature": 38.5}
        )

    assert observation.pk is not None
    assert computed.is_complete is False
    assert "pulse" in computed.missing


def test_blood_pressure_entered_the_wrong_way_round_is_caught(hospital, department) -> None:
    from django.core.exceptions import ValidationError

    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(ValidationError) as caught:
        triage.record_observation(
            encounter=encounter, data={"systolic_bp": 80, "diastolic_bp": 120}
        )
    assert "wrong way round" in str(caught.value)


def test_observations_build_a_chart_rather_than_overwriting(hospital, department) -> None:
    """
    The whole point: seeing that a pulse of 96 was 72 two hours ago.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.record_observation(encounter=encounter, data={"pulse": 72})
        triage.record_observation(encounter=encounter, data={"pulse": 96})
        rows = triage.chart(encounter.patient)

    assert [row.pulse for row in rows] == [96, 72]


def test_a_wrong_reading_is_superseded_not_edited(hospital, department) -> None:
    """
    A nurse who typed 220 instead of 120 and quietly changed it leaves no
    trace that anybody reacted to a hypertensive crisis which never happened.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        wrong, _ = triage.record_observation(
            encounter=encounter, data={"systolic_bp": 220, "pulse": 72}
        )
        right, _ = triage.record_observation(
            encounter=encounter,
            data={"systolic_bp": 120, "pulse": 72},
            supersedes=wrong,
            correction_reason="Typed 220 in error.",
        )
        wrong.refresh_from_db()
        still_there = Observation.objects.filter(pk=wrong.pk).exists()

    assert wrong.superseded_by_id == right.pk
    assert wrong.is_current is False
    assert right.is_current is True
    assert still_there, "the wrong reading was deleted"
    assert right.correction_reason == "Typed 220 in error."


def test_a_superseded_reading_still_shows_on_the_chart(hospital, department) -> None:
    """Hiding it would hide that somebody reacted to a wrong number."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        wrong, _ = triage.record_observation(encounter=encounter, data={"systolic_bp": 220})
        triage.record_observation(encounter=encounter, data={"systolic_bp": 120}, supersedes=wrong)
        chart = triage.chart(encounter.patient)

    assert len(chart) == 2


def test_a_child_gets_no_score_rather_than_a_wrong_one(hospital, department) -> None:
    """
    NEWS2 is not validated under 16, and a score for a three-year-old is a
    number somebody will act on.
    """
    encounter = arrive(
        hospital,
        department,
        last_name="Junior",
        date_of_birth=timezone.localdate() - dt.timedelta(days=365 * 3),
    )
    with hospital.scope():
        observation, computed = triage.record_observation(
            encounter=encounter, data={"pulse": 130, "respiration_rate": 30}
        )

    assert observation.news2_score is None, "a child with no score is not a child scoring nought"
    assert observation.news2_band == "not_applicable"
    assert computed.usable is False


def test_observations_cannot_be_added_to_a_closed_visit(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        journey.close_encounter(encounter)
        encounter.refresh_from_db()
        with pytest.raises(triage.TriageError, match="closed"):
            triage.record_observation(encounter=encounter, data={"pulse": 72})


# --------------------------------------------------------------------------
# Escalation — the point of the whole thing
# --------------------------------------------------------------------------


def test_a_bad_score_raises_the_patient_up_the_queue(hospital, department) -> None:
    """
    The reason any of this exists. A score nobody acts on has achieved
    nothing, so recording it is what moves the patient.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.GREEN, complaint="Feels unwell")
        before = QueueTicket.objects.get(pk=encounter.ticket_id).priority

        triage.record_observation(encounter=encounter, data=dict(SEPTIC))

        ticket = QueueTicket.objects.get(pk=encounter.ticket_id)
        assessment = TriageAssessment.objects.get(encounter=encounter)
        encounter.refresh_from_db()

    assert before == 0
    assert ticket.priority == triage.ESCALATED_PRIORITY
    assert assessment.category == triage.ESCALATED_CATEGORY
    assert encounter.triage_category == triage.ESCALATED_CATEGORY
    assert "NEWS2 17" in assessment.revision_reason


def test_a_single_reading_scoring_three_escalates_even_on_a_low_total(hospital, department) -> None:
    """
    Total 3, all from one parameter. The RCP says that needs an urgent
    review, and a system reporting only the total loses this patient.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.GREEN, complaint="Dizzy")
        observation, computed = triage.record_observation(
            encounter=encounter, data={**WELL, "pulse": 38}
        )
        ticket = QueueTicket.objects.get(pk=encounter.ticket_id)

    assert computed.total == 3
    assert computed.red_flag is True
    assert observation.news2_red_flag is True
    assert ticket.priority == triage.ESCALATED_PRIORITY, "a single 3 did not escalate"


def test_a_good_score_leaves_the_queue_alone(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.GREEN, complaint="Sore throat")
        triage.record_observation(encounter=encounter, data=dict(WELL))
        ticket = QueueTicket.objects.get(pk=encounter.ticket_id)
        assessment = TriageAssessment.objects.get(encounter=encounter)

    assert ticket.priority == 0
    assert assessment.category == TriageCategory.GREEN


def test_a_score_never_lowers_a_nurses_category(hospital, department) -> None:
    """
    A patient a nurse put on red with normal observations stays on red. She
    saw something the machine did not — how they look, what they said, that
    they came in alone.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(
            encounter=encounter, category=TriageCategory.RED, complaint="Looks very unwell"
        )
        triage.record_observation(encounter=encounter, data=dict(SEPTIC))
        assessment = TriageAssessment.objects.get(encounter=encounter)
        ticket = QueueTicket.objects.get(pk=encounter.ticket_id)

    assert assessment.category == TriageCategory.RED
    assert ticket.priority == -5, "a red patient was demoted by the algorithm"


def test_a_child_with_terrible_observations_is_not_auto_escalated(hospital, department) -> None:
    """
    Not because they are not sick — because NEWS2 cannot say, and the system
    must not pretend it can. A nurse's own category is how a child is
    escalated.
    """
    encounter = arrive(
        hospital,
        department,
        last_name="Child",
        date_of_birth=timezone.localdate() - dt.timedelta(days=365 * 4),
    )
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.GREEN, complaint="Fever")
        triage.record_observation(encounter=encounter, data=dict(SEPTIC))
        assessment = TriageAssessment.objects.get(encounter=encounter)

    assert assessment.category == TriageCategory.GREEN


def test_a_broken_channel_layer_never_loses_the_observation(
    hospital, department, monkeypatch
) -> None:
    """Redis being down must not stop a nurse recording vitals."""

    def explode(*args, **kwargs):
        raise RuntimeError("redis is on fire")

    monkeypatch.setattr(journey, "broadcast", explode)
    encounter = arrive(hospital, department)
    with hospital.scope():
        observation, _ = triage.record_observation(encounter=encounter, data=dict(SEPTIC))

    assert observation.pk is not None
    assert observation.news2_score == 17


# --------------------------------------------------------------------------
# The triage decision
# --------------------------------------------------------------------------


def test_assessing_sets_the_colour_and_the_queue_position(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        assessment = triage.assess(
            encounter=encounter,
            category=TriageCategory.ORANGE,
            complaint="Chest pain for two hours",
        )
        ticket = QueueTicket.objects.get(pk=encounter.ticket_id)
        encounter.refresh_from_db()

    assert assessment.category == TriageCategory.ORANGE
    assert assessment.target_minutes == 10
    assert ticket.priority == -3
    assert encounter.triage_category == TriageCategory.ORANGE


def test_revising_a_category_keeps_one_row_and_records_why(hospital, department) -> None:
    """
    A second row would make "when were they triaged" ambiguous, and
    downgrading somebody should have a name attached to it.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.ORANGE, complaint="Chest pain")
        triage.assess(
            encounter=encounter, category=TriageCategory.GREEN, complaint="Chest pain, settled"
        )
        assessment = TriageAssessment.objects.get(encounter=encounter)
        assert TriageAssessment.objects.filter(encounter=encounter).count() == 1

    assert assessment.category == TriageCategory.GREEN
    assert assessment.revised_at is not None
    assert "Orange" in assessment.revision_reason


def test_a_complaint_is_required(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(triage.TriageError, match="what the patient says"):
        triage.assess(encounter=encounter, category=TriageCategory.GREEN, complaint="   ")


def test_an_invented_category_is_refused(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(triage.TriageError, match="no triage category"):
        triage.assess(encounter=encounter, category="purple", complaint="Sore throat")


def test_each_colour_is_overdue_against_its_own_promise(hospital, department) -> None:
    """
    The only wait that means anything: a green patient at 90 minutes is fine
    and a red one at five is a crisis.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        assessment = triage.assess(
            encounter=encounter, category=TriageCategory.GREEN, complaint="Rash"
        )
        ninety_minutes = assessment.assessed_at + dt.timedelta(minutes=90)
        assert assessment.is_overdue(now=ninety_minutes) is False

        TriageAssessment.objects.filter(pk=assessment.pk).update(category=TriageCategory.ORANGE)
        assessment.refresh_from_db()
        assert assessment.is_overdue(now=ninety_minutes) is True


def test_a_blue_patient_is_never_overdue(hospital, department) -> None:
    """Non-urgent makes no promise, so it cannot break one."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        assessment = triage.assess(
            encounter=encounter, category=TriageCategory.BLUE, complaint="Repeat prescription"
        )
        assert assessment.target_minutes is None
        assert assessment.is_overdue(now=timezone.now() + dt.timedelta(days=2)) is False


def test_finishing_triage_sends_them_to_the_doctor(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.YELLOW, complaint="Headache")
        triage.finish_triage(encounter)
        encounter.refresh_from_db()

    assert encounter.stage == Stage.WAITING_DOCTOR


def test_the_waiting_room_is_ordered_by_urgency_then_by_wait(hospital, department) -> None:
    """
    Found by this test: ordering by the category column sorts the colour
    names, which puts blue and green ahead of red. A triage queue sorted
    alphabetically is a triage queue that kills somebody.
    """
    green = arrive(hospital, department, last_name="Green")
    red = arrive(hospital, department, last_name="Red")
    with hospital.scope():
        triage.assess(encounter=green, category=TriageCategory.GREEN, complaint="Rash")
        triage.assess(encounter=red, category=TriageCategory.RED, complaint="Collapsed")
        rows = triage.waiting_room(hospital.main_branch.pk)

    assert [row["category"] for row in rows] == ["red", "green"]
    assert rows[0]["patient_name"].endswith("Red")


# --------------------------------------------------------------------------
# Nursing notes
# --------------------------------------------------------------------------


def test_a_note_is_never_edited_only_added_to(hospital, department) -> None:
    """
    A nursing record that can be rewritten after an incident is one nobody
    can rely on, and the person it protects first is the nurse.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        first = triage.write_note(encounter=encounter, body="Patient seen, appears comfortable.")
        added = triage.write_note(
            encounter=encounter, body="Also complained of nausea.", amends=first
        )
        first.refresh_from_db()
        addenda = list(first.addenda.all())

    assert first.body == "Patient seen, appears comfortable."
    assert added.kind == NursingNote.Kind.ADDENDUM
    assert added.amends_id == first.pk
    assert addenda == [added]


def test_an_empty_note_is_refused(hospital, department) -> None:
    from django.core.exceptions import ValidationError

    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(ValidationError):
        triage.write_note(encounter=encounter, body="   ")


def test_a_note_cannot_amend_one_from_another_visit(hospital, department) -> None:
    one = arrive(hospital, department, last_name="One")
    two = arrive(hospital, department, last_name="Two")
    with hospital.scope():
        note = triage.write_note(encounter=one, body="Seen.")
        with pytest.raises(triage.TriageError, match="different visit"):
            triage.write_note(encounter=two, body="Adding to the wrong one.", amends=note)


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------


def test_a_nurse_records_vitals_and_sees_the_score(api_as_nurse, hospital, department) -> None:
    encounter = arrive(hospital, department)
    response = api_as_nurse.post(
        reverse("api:v1:observation-create", args=[encounter.pk]), SEPTIC, format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["score"]["total"] == 17
    assert response.data["score"]["band"] == "high"
    assert "critical care" in response.data["score"]["response"]
    # The working is shown, because a nurse looking at a 17 needs to know
    # which reading produced it.
    names = {row["name"] for row in response.data["score"]["parameters"]}
    assert "respiration_rate" in names


def test_the_score_can_be_previewed_without_storing_anything(
    api_as_nurse, hospital, department
) -> None:
    """The triage form calls this as the nurse types."""
    arrive(hospital, department)
    response = api_as_nurse.post(reverse("api:v1:triage-score"), SEPTIC, format="json")

    assert response.status_code == 200, response.data
    assert response.data["total"] == 17
    with hospital.scope():
        assert Observation.objects.count() == 0, "a preview stored something"


def test_the_preview_refuses_for_a_child(api_as_nurse, hospital, department) -> None:
    patient = make_patient(
        hospital,
        last_name="Small",
        date_of_birth=timezone.localdate() - dt.timedelta(days=365 * 5),
    )
    response = api_as_nurse.post(
        reverse("api:v1:triage-score"),
        {**SEPTIC, "patient_id": str(patient.pk)},
        format="json",
    )
    assert response.data["usable"] is False
    assert "paediatric" in response.data["reason"]


def test_recording_vitals_is_audited_and_a_red_flag_is_marked(
    api_as_nurse, hospital, department
) -> None:
    encounter = arrive(hospital, department)
    api_as_nurse.post(
        reverse("api:v1:observation-create", args=[encounter.pk]),
        {**WELL, "pulse": 38},
        format="json",
    )
    event = AuditEvent.objects.filter(action="triage.vitals.record").first()

    assert event is not None
    assert event.patient_id == encounter.patient_id
    assert "sensitive" in event.flags, "a single reading scoring 3 should be flagged"


def test_the_triage_queue_is_audited_as_a_bulk_read(api_as_nurse, hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.YELLOW, complaint="Headache")

    response = api_as_nurse.get(reverse("api:v1:triage-queue"))
    assert response.status_code == 200, response.data
    assert len(response.data["rows"]) == 1
    assert AuditEvent.objects.filter(action="triage.queue.view").exists()


def test_a_cashier_cannot_record_vitals(api, hospital, department) -> None:
    encounter = arrive(hospital, department)
    api.force_authenticate(user=hospital.staff("cashier"))
    api.credentials(HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk))

    response = api.post(
        reverse("api:v1:observation-create", args=[encounter.pk]), WELL, format="json"
    )
    assert response.status_code == 403


def test_recording_vitals_and_reading_a_chart_are_different_permissions(
    api, hospital, department
) -> None:
    """
    Taking a pulse and reading somebody's history are different acts. A
    healthcare assistant may be trusted with one and not the other.
    """
    encounter = arrive(hospital, department)
    from apps.accounts.models import Role

    with hospital.scope():
        role = Role.objects.create(
            organisation=hospital.organisation,
            name="Healthcare assistant",
            slug="hca-test",
            permissions=["patients.view", "triage.vitals.record"],
        )
    user = hospital.staff("nurse", email="hca@lifeline.test")
    with hospital.scope():
        membership = user.memberships.get(organisation=hospital.organisation)
        membership.roles.set([role])

    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )

    assert (
        api.post(
            reverse("api:v1:observation-create", args=[encounter.pk]), WELL, format="json"
        ).status_code
        == 201
    )
    assert (
        api.get(reverse("api:v1:observation-chart", args=[encounter.patient_id])).status_code == 403
    )


def test_the_chart_endpoint_is_audited_as_a_record_view(api_as_nurse, hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.record_observation(encounter=encounter, data=dict(WELL))

    response = api_as_nurse.get(reverse("api:v1:observation-chart", args=[encounter.patient_id]))
    assert response.status_code == 200, response.data
    assert len(response.data) == 1
    event = AuditEvent.objects.filter(
        patient_id=encounter.patient_id, action="patient.view"
    ).first()
    assert event is not None
    assert event.changes["read"] == ["observations"]


def test_the_api_assesses_and_writes_notes(api_as_nurse, hospital, department) -> None:
    encounter = arrive(hospital, department)

    assessed = api_as_nurse.post(
        reverse("api:v1:triage-assess", args=[encounter.pk]),
        {"category": "orange", "complaint": "Chest pain for two hours"},
        format="json",
    )
    assert assessed.status_code == 200, assessed.data
    assert assessed.data["target_minutes"] == 10

    written = api_as_nurse.post(
        reverse("api:v1:nursing-notes", args=[encounter.pk]),
        {"body": "Seen at triage, ECG requested."},
        format="json",
    )
    assert written.status_code == 201, written.data

    read = api_as_nurse.get(reverse("api:v1:nursing-notes", args=[encounter.pk]))
    assert [row["body"] for row in read.data] == ["Seen at triage, ECG requested."]


def test_bmi_comes_back_with_the_observation(api_as_nurse, hospital, department) -> None:
    encounter = arrive(hospital, department)
    response = api_as_nurse.post(
        reverse("api:v1:observation-create", args=[encounter.pk]),
        {"weight_kg": "70.0", "height_cm": "175.0"},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["observation"]["bmi"]["value"] == 22.9
    assert response.data["observation"]["bmi"]["category"] == "Healthy weight"


def test_triage_is_scoped_to_one_hospital(hospital, other_hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.record_observation(encounter=encounter, data=dict(WELL))
    with other_hospital.scope():
        assert Observation.objects.count() == 0
        assert triage.waiting_room(other_hospital.main_branch.pk) == []


def test_the_queue_shows_the_latest_score_not_the_one_attached_at_assessment(
    hospital, department
) -> None:
    """
    Found by walking the app: the nurse's category is saved before the
    observations, so the assessment has nothing attached — and the row lost
    its score entirely. A patient whose vitals were taken again ten minutes
    ago should show that score, not whichever one happened to be linked.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.YELLOW, complaint="Headache")
        triage.record_observation(encounter=encounter, data={**WELL, "pulse": 38})
        rows = triage.waiting_room(hospital.main_branch.pk)

    assert rows[0]["news2"] == 3
    assert rows[0]["news2_red_flag"] is True


def test_a_newer_reading_replaces_the_one_on_the_queue_row(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        triage.assess(encounter=encounter, category=TriageCategory.GREEN, complaint="Cough")
        triage.record_observation(encounter=encounter, data=dict(WELL))
        triage.record_observation(encounter=encounter, data={**WELL, "respiration_rate": 26})
        rows = triage.waiting_room(hospital.main_branch.pk)

    assert rows[0]["news2"] == 3, "the queue showed an older score"
