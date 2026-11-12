"""
The ward's task list, and the handover between shifts.

Every name here is synthetic.

This file is mostly about one thing: **nothing falls between two shifts.**
Not a wrong drug or a missed diagnosis — those happen and are rarer. What
happens every week is that a nurse meant to do something, got pulled away,
and the person who came on at two o'clock never knew it was outstanding.

So the tests that matter here are the ones that check a task cannot quietly
disappear, that the handover's outstanding list is built by the system
rather than typed from memory, and that a handover has two halves — given
and received — which the ward can tell apart.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.clinical.models import Observation
from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.core.tenancy import unscoped
from apps.journey.models import Encounter
from apps.nursing import service as nursing
from apps.nursing.models import Handover, HandoverStatus, NursingTask, TaskKind, TaskStatus
from apps.orders import service as ordering
from apps.orders.models import OrderKind
from apps.patients.models import Patient, Sex
from apps.patients.registration import RegistrationInput, register_patient
from apps.scheduling import booking

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


@pytest.fixture
def doctor(hospital):
    return hospital.staff("doctor", first_name="Folake", last_name="Ilesanmi")


@pytest.fixture
def nurse(hospital):
    return hospital.staff("nurse", first_name="Amarachi", last_name="Eze")


@pytest.fixture
def night_nurse(hospital):
    return hospital.staff("nurse", first_name="Hauwa", last_name="Bello")


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Adebayo",
        "last_name": "Okonkwo",
        "sex": Sex.MALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1986, 4, 2),
    }
    defaults.update(overrides)
    with hospital.scope():
        return register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(**defaults),
        )


def arrive(hospital, department, patient=None) -> Encounter:
    patient = patient or make_patient(hospital)
    with hospital.scope():
        booking.check_in(patient=patient, department=department, branch=hospital.main_branch)
        return Encounter.objects.select_related("patient").get(
            patient=patient, status=Encounter.Status.OPEN
        )


def a_task(hospital, department, nurse, **overrides) -> NursingTask:
    encounter = overrides.pop("encounter", None) or arrive(hospital, department)
    defaults = {"name": "Change the wound dressing", "kind": TaskKind.CARE}
    defaults.update(overrides)
    with hospital.scope():
        return nursing.add_task(
            patient=encounter.patient,
            encounter=encounter,
            department=department,
            actor=nurse,
            **defaults,
        )


# --------------------------------------------------------------------------
# Making the list
# --------------------------------------------------------------------------


def test_a_task_needs_something_to_do(hospital, department, nurse) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(nursing.NursingError, match="what has to be done"):
        nursing.add_task(patient=encounter.patient, encounter=encounter, name="   ", actor=nurse)


def test_a_signed_order_becomes_a_task(hospital, department, doctor) -> None:
    """
    The doctor's instruction becomes the nurse's task without anybody
    retyping it. Retyping is where the dose changes.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.NURSING,
            name="Wound dressing, left leg",
            instructions="Daily, with saline.",
        )
        ordering.sign(encounter=encounter, actor=doctor)
        made = nursing.tasks_from_orders(encounter)

    assert len(made) == 1
    assert made[0].name == "Wound dressing, left leg"
    assert made[0].detail == "Daily, with saline."
    # Once, not twice: `Order.summary` already folds the instructions in for
    # everything that is not a medicine.
    assert "saline" not in made[0].name


def test_a_draft_order_does_not_become_a_task(hospital, department, doctor, nurse) -> None:
    """
    A draft is the doctor still thinking. A ward acting on drafts is a ward
    acting on things nobody agreed to.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.NURSING, name="Wound dressing")
        assert nursing.tasks_from_orders(encounter) == []


def test_rebuilding_the_list_does_not_duplicate_a_task(hospital, department, doctor) -> None:
    """
    The screen rebuilds this whenever it loads. A second task for one
    injection is how a patient gets two.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.MEDICATION,
            name="Paracetamol",
            dose="1 g",
        )
        ordering.sign(encounter=encounter, actor=doctor)
        nursing.tasks_from_orders(encounter)
        again = nursing.tasks_from_orders(encounter)
        total = NursingTask.objects.filter(encounter=encounter).count()

    assert again == []
    assert total == 1


def test_a_stat_order_is_due_now_and_a_routine_one_is_not(hospital, department, doctor) -> None:
    """
    Inventing a deadline for a routine order turns the whole list red by the
    afternoon, which teaches the ward to ignore the colour.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.MEDICATION,
            name="Adrenaline",
            dose="1 mg",
            priority="stat",
        )
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.NURSING,
            name="Daily weight",
        )
        ordering.sign(encounter=encounter, actor=doctor)
        made = {task.kind: task for task in nursing.tasks_from_orders(encounter)}

    stat = made[TaskKind.MEDICATION]
    routine = made[TaskKind.CARE]
    # The medicine's dose rides on the line a nurse reads.
    assert stat.name == "Adrenaline · 1 mg"
    assert stat.due_at is not None
    assert stat.urgent is True
    assert routine.name == "Daily weight"
    assert routine.due_at is None
    assert routine.urgent is False


def test_an_order_a_nurse_does_not_carry_out_raises_no_task(hospital, department, doctor) -> None:
    """A chest X-ray is not something the ward does at the bedside."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.RADIOLOGY, name="Chest X-ray")
        ordering.sign(encounter=encounter, actor=doctor)
        assert nursing.tasks_from_orders(encounter) == []


def _observe(hospital, encounter, *, band: str, minutes_ago: int) -> Observation:
    with hospital.scope():
        return Observation.objects.create(
            organisation=hospital.organisation,
            branch=encounter.branch,
            patient=encounter.patient,
            encounter=encounter,
            taken_at=timezone.now() - dt.timedelta(minutes=minutes_ago),
            news2_band=band,
        )


def test_observations_fall_due_on_the_news2_interval(hospital, department) -> None:
    """
    "She is due her obs" belongs on a list, not in somebody's head. The
    interval comes from the band the nurse was shown at the time.
    """
    encounter = arrive(hospital, department)
    _observe(hospital, encounter, band="low", minutes_ago=5 * 60)

    with hospital.scope():
        task = nursing.observation_tasks(encounter)

    assert task is not None
    assert task.kind == TaskKind.OBSERVATION
    assert task.is_overdue


def test_observations_are_not_due_before_the_interval(hospital, department) -> None:
    encounter = arrive(hospital, department)
    _observe(hospital, encounter, band="low", minutes_ago=30)

    with hospital.scope():
        assert nursing.observation_tasks(encounter) is None


def test_a_sicker_patient_is_due_sooner(hospital, department) -> None:
    """A patient scoring medium is due hourly, not every four hours."""
    encounter = arrive(hospital, department)
    _observe(hospital, encounter, band="medium", minutes_ago=90)

    with hospital.scope():
        task = nursing.observation_tasks(encounter)

    assert task is not None
    assert task.urgent is True


def test_only_one_observation_task_is_ever_open(hospital, department) -> None:
    """A patient with two "observations due" lines is one nobody trusts."""
    encounter = arrive(hospital, department)
    _observe(hospital, encounter, band="low", minutes_ago=10 * 60)

    with hospital.scope():
        first = nursing.observation_tasks(encounter)
        second = nursing.observation_tasks(encounter)

    assert first is not None
    assert second is None


def test_a_patient_with_no_observations_raises_nothing(hospital, department) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        assert nursing.observation_tasks(encounter) is None


# --------------------------------------------------------------------------
# Working through it
# --------------------------------------------------------------------------


def test_doing_a_task_puts_a_name_and_a_time_against_it(hospital, department, nurse) -> None:
    task = a_task(hospital, department, nurse)
    with hospital.scope():
        task = nursing.complete(task=task, actor=nurse, note="Wound clean, no discharge.")

    assert task.status == TaskStatus.DONE
    assert task.done_by_name == "Amarachi Eze"
    assert task.done_at is not None
    assert "clean" in task.done_note


def test_not_done_needs_a_reason(hospital, department, nurse) -> None:
    """
    "Off the ward for a scan" and "refused" lead to different next steps,
    and a blank is how a ward loses the difference.
    """
    task = a_task(hospital, department, nurse)
    with hospital.scope(), pytest.raises(nursing.NursingError, match="why"):
        nursing.mark_not_done(task=task, reason="   ", actor=nurse)


def test_not_done_is_recorded_as_not_done(hospital, department, nurse) -> None:
    task = a_task(hospital, department, nurse)
    with hospital.scope():
        task = nursing.mark_not_done(task=task, reason="Patient was in theatre.", actor=nurse)

    assert task.status == TaskStatus.NOT_DONE
    assert task.is_open is False
    assert "theatre" in task.not_done_reason


def test_cancelled_is_not_the_same_as_not_done(hospital, department, nurse) -> None:
    """
    Not done means the patient still needs it. Cancelled means they never
    did. A list that collapsed the two would hide real outstanding work.
    """
    task = a_task(hospital, department, nurse)
    with hospital.scope():
        task = nursing.cancel(task=task, reason="Ordered in error.", actor=nurse)

    assert task.status == TaskStatus.CANCELLED
    assert task.status != TaskStatus.NOT_DONE


def test_a_closed_task_cannot_be_closed_again(hospital, department, nurse) -> None:
    task = a_task(hospital, department, nurse)
    with hospital.scope():
        nursing.complete(task=task, actor=nurse)
        with pytest.raises(nursing.NursingError, match="already closed"):
            nursing.complete(task=task, actor=nurse)


def test_a_task_is_never_deleted(hospital, department, nurse) -> None:
    """
    A list that can be tidied up gets tidied up at the end of a bad shift.
    Every way of closing a task keeps the row.
    """
    for close, kwargs in (
        (nursing.complete, {}),
        (nursing.mark_not_done, {"reason": "Patient refused."}),
        (nursing.cancel, {"reason": "Ordered in error."}),
    ):
        task = a_task(hospital, department, nurse)
        with hospital.scope():
            close(task=task, actor=nurse, **kwargs)
            assert NursingTask.objects.filter(pk=task.pk).exists()


def test_the_model_refuses_a_not_done_task_with_no_reason(hospital, department, nurse) -> None:
    """The service checks it; so does the model, for writes from anywhere."""
    task = a_task(hospital, department, nurse)
    task.status = TaskStatus.NOT_DONE
    with hospital.scope(), pytest.raises(ValidationError):
        task.full_clean(exclude=["organisation", "branch", "patient", "encounter", "department"])


def test_urgent_tasks_come_first_then_the_soonest_due(hospital, department, nurse) -> None:
    """
    And a task with no due time sorts last: a null at the top would push
    real deadlines off the screen.
    """
    now = timezone.now()
    encounter = arrive(hospital, department)
    a_task(hospital, department, nurse, encounter=encounter, name="No deadline")
    a_task(
        hospital,
        department,
        nurse,
        encounter=encounter,
        name="Later",
        due_at=now + dt.timedelta(hours=3),
    )
    a_task(
        hospital,
        department,
        nurse,
        encounter=encounter,
        name="Soon",
        due_at=now + dt.timedelta(minutes=10),
    )
    a_task(
        hospital,
        department,
        nurse,
        encounter=encounter,
        name="Urgent",
        due_at=now + dt.timedelta(hours=8),
        urgent=True,
    )

    with hospital.scope():
        names = [task.name for task in nursing.open_tasks(department=department)]

    assert names == ["Urgent", "Soon", "Later", "No deadline"]


def test_overdue_is_measured_from_the_due_time(hospital, department, nurse) -> None:
    task = a_task(hospital, department, nurse, due_at=timezone.now() - dt.timedelta(minutes=45))
    assert task.is_overdue is True
    assert 44 <= task.minutes_late <= 46


def test_a_task_with_no_due_time_is_never_overdue(hospital, department, nurse) -> None:
    task = a_task(hospital, department, nurse)
    assert task.is_overdue is False
    assert task.minutes_late == 0


# --------------------------------------------------------------------------
# Handover
# --------------------------------------------------------------------------


def test_the_outstanding_list_is_built_not_typed(hospital, department, nurse) -> None:
    """
    The whole point of the feature. A handover sheet typed from memory omits
    precisely the thing nobody remembered — that is what "forgot" means.
    """
    a_task(hospital, department, nurse, name="Give the antibiotic")
    a_task(hospital, department, nurse, name="Change the dressing")

    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        # The nurse writes nothing about either task.
        nursing.save_draft(handover=handover, content={"ward_note": "Quiet shift."}, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)

    names = {row["name"] for row in handover.outstanding}
    assert names == {"Give the antibiotic", "Change the dressing"}


def test_a_task_done_before_the_handover_is_not_outstanding(hospital, department, nurse) -> None:
    done = a_task(hospital, department, nurse, name="Give the antibiotic")
    a_task(hospital, department, nurse, name="Change the dressing")

    with hospital.scope():
        nursing.complete(task=done, actor=nurse)
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)

    names = {row["name"] for row in handover.outstanding}
    assert names == {"Change the dressing"}


def test_handing_over_counts_how_many_shifts_a_task_has_survived(
    hospital, department, nurse, night_nurse
) -> None:
    """
    A task on its third handover is one the ward should be asked about, and
    counting is cheaper than anybody noticing.
    """
    task = a_task(hospital, department, nurse, name="Chase the scan report")

    with hospital.scope():
        first = nursing.open_handover(department=department, actor=nurse)
        nursing.sign(handover=first, actor=nurse)
        second = nursing.open_handover(department=department, actor=night_nurse)
        nursing.sign(handover=second, actor=night_nurse)
        task.refresh_from_db()

    assert task.handovers == 2


def test_the_snapshot_keeps_what_the_nurse_was_told(hospital, department, nurse) -> None:
    """
    A link to the live list answers a different question. What matters a
    week later is what this nurse was told was outstanding when she came on.
    """
    task = a_task(hospital, department, nurse, name="Give the antibiotic")

    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)
        # The task is settled afterwards; the snapshot does not change.
        nursing.complete(task=task, actor=nurse)
        handover.refresh_from_db()

    assert len(handover.outstanding) == 1
    assert handover.outstanding[0]["name"] == "Give the antibiotic"


def test_a_signed_handover_cannot_be_rewritten(hospital, department, nurse) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        nursing.save_draft(handover=handover, content={"ward_note": "Quiet."}, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)
        handover.content = {"ward_note": "Actually it was chaos."}
        with pytest.raises(ValidationError):
            handover.save()


def test_a_signed_handover_cannot_be_saved_through_the_service(hospital, department, nurse) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)
        with pytest.raises(nursing.NursingError, match="given to the next shift"):
            nursing.save_draft(handover=handover, content={"ward_note": "no"}, actor=nurse)


def test_one_draft_per_nurse_per_ward(hospital, department, nurse) -> None:
    """Two half-written sheets is how a ward hands over twice and means neither."""
    with hospital.scope():
        first = nursing.open_handover(department=department, actor=nurse)
        again = nursing.open_handover(department=department, actor=nurse)

    assert first.pk == again.pk


def test_a_handover_is_signed_by_the_nurse_who_wrote_it(
    hospital, department, nurse, night_nurse
) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        with pytest.raises(nursing.NursingError, match="who wrote it"):
            nursing.sign(handover=handover, actor=night_nurse)


def test_signing_twice_is_refused(hospital, department, nurse) -> None:
    """Two taps, two requests, one handover."""
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        nursing.sign(handover=handover, actor=nurse)
        with pytest.raises(nursing.NursingError, match="already"):
            nursing.sign(handover=handover, actor=nurse)


def test_accepting_is_the_other_half(hospital, department, nurse, night_nurse) -> None:
    """
    A handover nobody received is a handover that did not happen, and the
    ward has to be able to tell that apart from one never written.
    """
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)
        assert handover.status == HandoverStatus.SIGNED

        handover = nursing.accept(handover=handover, actor=night_nurse, note="Read, thank you.")

    assert handover.status == HandoverStatus.ACCEPTED
    assert handover.to_nurse_name == "Hauwa Bello"
    assert handover.accepted_at is not None


def test_a_nurse_cannot_accept_her_own_handover(hospital, department, nurse) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)
        with pytest.raises(nursing.NursingError, match="coming on"):
            nursing.accept(handover=handover, actor=nurse)


def test_a_draft_cannot_be_accepted(hospital, department, nurse, night_nurse) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        with pytest.raises(nursing.NursingError, match="not been given"):
            nursing.accept(handover=handover, actor=night_nurse)


def test_accepting_twice_is_refused(hospital, department, nurse, night_nurse) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)
        nursing.accept(handover=handover, actor=night_nurse)
        with pytest.raises(nursing.NursingError, match="already been received"):
            nursing.accept(handover=handover, actor=night_nurse)


def test_a_draft_has_no_signature_block(hospital, department, nurse) -> None:
    """A draft must not be screenshotted as a handover."""
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
    assert handover.signature_block() == []


def test_the_stamp_is_in_the_wards_own_clock(hospital, department, nurse) -> None:
    """
    A handover timed in UTC appears to have happened an hour before the
    shift did — the same bug caught on the clinical note's stamp.
    """
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)

    local = timezone.localtime(handover.signed_at)
    assert f"{local:%H:%M}" in " ".join(handover.signature_block())


def test_the_nurse_coming_on_reads_the_last_one(hospital, department, nurse, night_nurse) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        signed = nursing.sign(handover=handover, actor=nurse)
        found = nursing.latest_received(department)

    assert found is not None
    assert found.pk == signed.pk


def test_an_accepted_handover_can_still_be_re_read(
    hospital, department, nurse, night_nurse
) -> None:
    """The nurse on at two o'clock re-reads what she was told at the start."""
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        handover = nursing.sign(handover=handover, actor=nurse)
        nursing.accept(handover=handover, actor=night_nurse)
        found = nursing.latest_received(department)

    assert found is not None
    assert found.status == HandoverStatus.ACCEPTED


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


def test_the_whole_shift_over_http(
    hospital, api, as_staff, nurse, night_nurse, doctor, department
) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.NURSING, name="Wound dressing")
        ordering.sign(encounter=encounter, actor=doctor)

    as_staff(nurse, branch=hospital.main_branch)

    # The visit's list is rebuilt on read.
    built = api.get(reverse("api:v1:encounter-tasks", args=[encounter.pk]))
    assert built.status_code == 200, built.data
    assert len(built.data["tasks"]) == 1

    ward = api.get(reverse("api:v1:ward-tasks", args=[department.pk]))
    assert ward.status_code == 200, ward.data
    assert len(ward.data["tasks"]) == 1
    assert ward.data["handover"] is None

    added = api.post(
        reverse("api:v1:nursing-tasks"),
        {
            "patient_id": str(encounter.patient_id),
            "encounter_id": str(encounter.pk),
            "department_id": str(department.pk),
            "name": "Chase the scan report",
        },
        format="json",
    )
    assert added.status_code == 201, added.data

    opened = api.post(
        reverse("api:v1:ward-handovers", args=[department.pk]),
        {"shift": "Morning to afternoon"},
        format="json",
    )
    assert opened.status_code == 201, opened.data

    signed = api.post(reverse("api:v1:handover-sign", args=[opened.data["id"]]))
    assert signed.status_code == 200, signed.data
    assert signed.data["outstanding_count"] == 2

    as_staff(night_nurse, branch=hospital.main_branch)
    accepted = api.post(
        reverse("api:v1:handover-accept", args=[opened.data["id"]]),
        {"note": "Read."},
        format="json",
    )
    assert accepted.status_code == 200, accepted.data
    assert accepted.data["status"] == "accepted"


def test_the_sheet_says_whose_it_is(
    hospital, api, as_staff, nurse, night_nurse, department
) -> None:
    """
    So the screen stops offering "I have taken the ward" to the nurse going
    off. The server refuses it anyway, and an offer that is always refused
    is worse than no offer.
    """
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)
        nursing.sign(handover=handover, actor=nurse)

    as_staff(nurse, branch=hospital.main_branch)
    mine = api.get(reverse("api:v1:handover-detail", args=[handover.pk]))
    assert mine.data["from_me"] is True

    as_staff(night_nurse, branch=hospital.main_branch)
    theirs = api.get(reverse("api:v1:handover-detail", args=[handover.pk]))
    assert theirs.data["from_me"] is False


def test_closing_a_task_over_http_needs_the_right_action(
    hospital, api, as_staff, nurse, department
) -> None:
    task = a_task(hospital, department, nurse)
    as_staff(nurse, branch=hospital.main_branch)

    bad = api.post(
        reverse("api:v1:nursing-task-detail", args=[task.pk]),
        {"action": "sort_of_done"},
        format="json",
    )
    assert bad.status_code == 400

    refused = api.post(
        reverse("api:v1:nursing-task-detail", args=[task.pk]),
        {"action": "not_done"},
        format="json",
    )
    assert refused.status_code == 400
    assert "why" in refused.data["detail"]

    done = api.post(
        reverse("api:v1:nursing-task-detail", args=[task.pk]),
        {"action": "done", "note": "Done at the bedside."},
        format="json",
    )
    assert done.status_code == 200, done.data
    assert done.data["status"] == "done"


def test_a_doctor_reads_the_list_but_does_not_work_it(
    hospital, api, as_staff, doctor, nurse, department
) -> None:
    """
    "Has she had her antibiotic yet" is a ward-round question. Completing it
    puts a name against something done to a patient, which is the nurse's.
    """
    task = a_task(hospital, department, nurse)
    as_staff(doctor, branch=hospital.main_branch)

    assert api.get(reverse("api:v1:ward-tasks", args=[department.pk])).status_code == 200
    closed = api.post(
        reverse("api:v1:nursing-task-detail", args=[task.pk]),
        {"action": "done"},
        format="json",
    )
    assert closed.status_code == 403


def test_a_nurse_can_list_her_own_wards(hospital, api, as_staff, nurse, department) -> None:
    """
    Its own route rather than /departments/, which needs org.settings.view —
    a permission a nurse has no business holding. A nurse who cannot list
    her wards cannot open her own list.
    """
    a_task(hospital, department, nurse, due_at=timezone.now() - dt.timedelta(hours=1))
    a_task(hospital, department, nurse, name="Not due yet")
    as_staff(nurse, branch=hospital.main_branch)

    response = api.get(reverse("api:v1:wards"))
    assert response.status_code == 200, response.data
    found = {row["name"]: row for row in response.data["wards"]}
    assert found[department.name]["open_tasks"] == 2
    assert found[department.name]["overdue_tasks"] == 1


def test_the_ward_picker_leaves_out_places_nobody_nurses(hospital, api, as_staff, nurse) -> None:
    """
    A picker listing the pharmacy, the store and the records room is one she
    reads past ten times a shift to reach the two places she works.
    """
    as_staff(nurse, branch=hospital.main_branch)
    response = api.get(reverse("api:v1:wards"))
    assert response.status_code == 200
    kinds = {row["kind"] for row in response.data["wards"]}

    assert kinds <= {"ward", "clinic", "theatre"}
    assert "pharmacy" not in kinds
    assert "admin" not in kinds


def test_a_nurse_is_not_offered_another_branchs_wards(hospital, api, as_staff, nurse) -> None:
    """
    The default manager scopes to the hospital, not the branch. Every other
    department listing says in_current_branch(); this one has to as well, or
    a nurse at Ikeja is offered Lekki's wards and can open their list.
    """
    from apps.core.models import Department

    with hospital.scope(hospital.second_branch):
        elsewhere = Department.objects.create(
            organisation=hospital.organisation,
            branch=hospital.second_branch,
            name="Lekki Ward",
            code="LEKW",
            kind=Department.Kind.WARD,
        )

    as_staff(nurse, branch=hospital.main_branch)
    response = api.get(reverse("api:v1:wards"))
    assert response.status_code == 200
    names = {row["name"] for row in response.data["wards"]}
    assert elsewhere.name not in names


def test_a_cashier_cannot_see_the_ward_list(hospital, api, as_staff, department) -> None:
    cashier = hospital.staff("cashier", first_name="Bola", last_name="Adeyemi")
    as_staff(cashier, branch=hospital.main_branch)
    assert api.get(reverse("api:v1:ward-tasks", args=[department.pk])).status_code == 403


def test_another_hospital_cannot_reach_the_tasks(
    hospital, other_hospital, api, as_staff, nurse, department
) -> None:
    task = a_task(hospital, department, nurse)
    intruder = other_hospital.staff("nurse", first_name="Ngozi", last_name="Ibe")
    as_staff(intruder, branch=other_hospital.main_branch, organisation=other_hospital.organisation)

    response = api.post(
        reverse("api:v1:nursing-task-detail", args=[task.pk]),
        {"action": "done"},
        format="json",
    )
    assert response.status_code == 404


def test_a_nurse_cannot_edit_somebody_elses_draft(
    hospital, api, as_staff, nurse, night_nurse, department
) -> None:
    with hospital.scope():
        handover = nursing.open_handover(department=department, actor=nurse)

    as_staff(night_nurse, branch=hospital.main_branch)
    response = api.patch(
        reverse("api:v1:handover-detail", args=[handover.pk]),
        {"content": {"ward_note": "Not mine to write."}},
        format="json",
    )
    assert response.status_code == 403


def test_the_shift_is_audited(hospital, api, as_staff, nurse, night_nurse, department) -> None:
    task = a_task(hospital, department, nurse)
    as_staff(nurse, branch=hospital.main_branch)

    api.post(
        reverse("api:v1:nursing-task-detail", args=[task.pk]),
        {"action": "done"},
        format="json",
    )
    opened = api.post(reverse("api:v1:ward-handovers", args=[department.pk]), {}, format="json")
    api.post(reverse("api:v1:handover-sign", args=[opened.data["id"]]))

    as_staff(night_nurse, branch=hospital.main_branch)
    api.post(reverse("api:v1:handover-accept", args=[opened.data["id"]]), {}, format="json")

    with unscoped(reason="test: read the audit log"):
        actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"nursing.task.done", "nursing.handover.sign", "nursing.handover.accept"} <= actions


def test_a_task_belongs_to_one_hospital(hospital, other_hospital, nurse, department) -> None:
    a_task(hospital, department, nurse)
    with other_hospital.scope():
        assert NursingTask.objects.count() == 0


def test_a_handover_belongs_to_one_hospital(hospital, other_hospital, nurse, department) -> None:
    with hospital.scope():
        nursing.open_handover(department=department, actor=nurse)
    with other_hospital.scope():
        assert Handover.objects.count() == 0
