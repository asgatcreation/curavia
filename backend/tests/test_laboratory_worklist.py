"""
The laboratory worklist: work arriving, blood being drawn, tubes booked in.

Every name here is synthetic.

This file is about four things that go wrong in real laboratories, and what
the system does about each.

**A patient stuck more times than necessary.** Three tests that share a tube
must produce one tube. Most of the collection tests are about that.

**A tube with no number, or two tubes with the same one.** The accession
number is what the label, the bench sheet and the result all hang off, so it
is allocated under a lock and never reused.

**A rejection that explains nothing.** "Sample rejected" with no reason
starts an argument between a ward and a laboratory and hides the pattern that
would have fixed it. The reason is required, it comes from a list, and
rejecting a tube puts the tests back on the collection list with a count so
the next nurse knows it is a second attempt.

**A ward closing its own loop.** A nurse may draw and label a sample. Only
the laboratory may say it arrived, or that it is unusable. That split is a
permission, and there is a test for the 403.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalogue.models import Service, ServiceCategory
from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.core.tenancy import unscoped
from apps.journey.models import Encounter
from apps.laboratory import labels as label_render
from apps.laboratory import worklist
from apps.laboratory.models import (
    AccessionCounter,
    LabPanel,
    LabRequest,
    RejectionReason,
    RequestStatus,
    SampleStatus,
    Specimen,
    next_accession_number,
)
from apps.orders import service as ordering
from apps.orders.models import Order, OrderKind, OrderStatus
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
    return hospital.staff("nurse", first_name="Amaka", last_name="Nwosu")


@pytest.fixture
def scientist(hospital):
    return hospital.staff("lab_scientist", first_name="Sunday", last_name="Eze")


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Tunde",
        "last_name": "Bakare",
        "sex": Sex.MALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1988, 1, 20),
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


def make_panel(
    hospital,
    *,
    code: str,
    name: str,
    specimen: str = Specimen.SERUM,
    container: str = "Plain tube, 5 mL",
    turnaround: int = 240,
    send_out: bool = False,
) -> LabPanel:
    """A billable service and the laboratory's description of it."""
    with hospital.scope():
        service = Service.objects.create(
            organisation=hospital.organisation,
            code=code,
            name=name,
            category=ServiceCategory.LABORATORY,
            module="lab",
        )
        return LabPanel.objects.create(
            organisation=hospital.organisation,
            service=service,
            specimen=specimen,
            container=container,
            turnaround_minutes=turnaround,
            is_send_out=send_out,
        )


@pytest.fixture
def fbc(hospital) -> LabPanel:
    return make_panel(
        hospital,
        code="FBC",
        name="Full blood count",
        specimen=Specimen.WHOLE_BLOOD_EDTA,
        container="Purple top (EDTA), 4 mL",
        turnaround=120,
    )


@pytest.fixture
def pcv(hospital) -> LabPanel:
    return make_panel(
        hospital,
        code="PCV",
        name="Packed cell volume",
        specimen=Specimen.WHOLE_BLOOD_EDTA,
        container="Purple top (EDTA), 4 mL",
        turnaround=60,
    )


@pytest.fixture
def eucr(hospital) -> LabPanel:
    return make_panel(
        hospital,
        code="EUCR",
        name="Electrolytes, urea and creatinine",
        specimen=Specimen.SERUM,
        container="Plain tube, 5 mL",
    )


def order_tests(hospital, encounter, panels, actor, *, priority: str = "routine") -> list[Order]:
    """Draft and sign laboratory orders, the way a consultation does."""
    with hospital.scope():
        for panel in panels:
            ordering.draft(
                encounter=encounter,
                kind=OrderKind.LABORATORY,
                service=panel.service,
                name=panel.name,
                priority=priority,
                actor=actor,
            )
        return ordering.sign(encounter=encounter, actor=actor)


def requests_for(hospital, encounter) -> list[LabRequest]:
    with hospital.scope():
        return list(LabRequest.objects.filter(encounter=encounter).select_related("panel__service"))


# --------------------------------------------------------------------------
# Work arriving
# --------------------------------------------------------------------------


def test_signing_an_order_puts_it_on_the_laboratory_worklist(
    hospital, department, doctor, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)

    rows = requests_for(hospital, encounter)
    assert len(rows) == 1
    assert rows[0].status == RequestStatus.AWAITING_COLLECTION
    assert rows[0].panel_id == fbc.pk


def test_a_draft_order_is_not_on_the_worklist(hospital, department, doctor, fbc) -> None:
    """A draft is a thought, not an instruction. Nobody should be drawing blood."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.LABORATORY,
            service=fbc.service,
            name=fbc.name,
            actor=doctor,
        )
    assert requests_for(hospital, encounter) == []


def test_the_specimen_is_copied_rather_than_read_through_the_panel(
    hospital, department, doctor, fbc
) -> None:
    """
    A supervisor editing the catalogue must not change what an outstanding
    request is waiting for.
    """
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)

    with hospital.scope():
        fbc.specimen = Specimen.URINE
        fbc.container = "Universal bottle"
        fbc.save(update_fields=["specimen", "container"])

    row = requests_for(hospital, encounter)[0]
    assert row.specimen == Specimen.WHOLE_BLOOD_EDTA
    assert row.container == "Purple top (EDTA), 4 mL"


def test_the_promise_to_the_ward_is_the_turnaround_at_the_time(
    hospital, department, doctor, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    row = requests_for(hospital, encounter)[0]
    assert (row.due_at - row.requested_at) == dt.timedelta(minutes=120)


def test_a_laboratory_service_with_no_panel_is_skipped_not_refused(
    hospital, department, doctor
) -> None:
    """
    The wizard creates the billable service; the laboratory may not have
    described the test yet. The order still exists and is still charged.
    """
    with hospital.scope():
        service = Service.objects.create(
            organisation=hospital.organisation,
            code="XRAYLAB",
            name="Something nobody has set up",
            category=ServiceCategory.LABORATORY,
            module="lab",
        )
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.LABORATORY,
            service=service,
            name=service.name,
            actor=doctor,
        )
        signed = ordering.sign(encounter=encounter, actor=doctor)

    assert signed[0].status == OrderStatus.ORDERED
    assert requests_for(hospital, encounter) == []


def test_a_medicine_order_does_not_reach_the_laboratory(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.MEDICATION,
            name="Paracetamol 500 mg",
            dose="500 mg",
            route="Oral",
            frequency="Three times a day",
            actor=doctor,
        )
        ordering.sign(encounter=encounter, actor=doctor)
    assert requests_for(hospital, encounter) == []


def test_signing_twice_does_not_make_two_requests(hospital, department, doctor, fbc) -> None:
    encounter = arrive(hospital, department)
    orders = order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        worklist.open_requests_for(orders, actor=doctor)
    assert len(requests_for(hospital, encounter)) == 1


# --------------------------------------------------------------------------
# Planning the draw
# --------------------------------------------------------------------------


def test_two_tests_on_one_tube_produce_one_tube(hospital, department, doctor, fbc, pcv) -> None:
    """The whole point. A full blood count and a PCV are both EDTA."""
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, pcv], doctor)

    with hospital.scope():
        tubes = worklist.plan_collection(requests_for(hospital, encounter))

    assert len(tubes) == 1
    assert tubes[0].specimen == Specimen.WHOLE_BLOOD_EDTA
    assert sorted(tubes[0].panel_names.split(", ")) == ["FBC", "PCV"]


def test_three_tests_across_two_specimens_produce_two_tubes(
    hospital, department, doctor, fbc, pcv, eucr
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, pcv, eucr], doctor)

    with hospital.scope():
        tubes = worklist.plan_collection(requests_for(hospital, encounter))

    assert len(tubes) == 2
    assert {tube.specimen for tube in tubes} == {Specimen.WHOLE_BLOOD_EDTA, Specimen.SERUM}


def test_the_tube_list_is_in_a_stable_order(hospital, department, doctor, fbc, eucr) -> None:
    """A list that reshuffles between refreshes is a list that gets mis-read."""
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, eucr], doctor)
    with hospital.scope():
        rows = requests_for(hospital, encounter)
        first = [tube.specimen for tube in worklist.plan_collection(rows)]
        second = [tube.specimen for tube in worklist.plan_collection(list(reversed(rows)))]
    assert first == second


def test_the_container_comes_from_the_first_request_in_the_group(
    hospital, department, doctor, fbc, pcv
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, pcv], doctor)
    with hospital.scope():
        tubes = worklist.plan_collection(requests_for(hospital, encounter))
    assert tubes[0].container == "Purple top (EDTA), 4 mL"


# --------------------------------------------------------------------------
# Accession numbers
# --------------------------------------------------------------------------


def test_the_accession_number_reads_like_a_laboratory_number(hospital) -> None:
    with hospital.scope():
        issued = next_accession_number(hospital.organisation.pk, year=2026)
    assert issued == "L26-000001"


def test_accession_numbers_do_not_repeat(hospital) -> None:
    with hospital.scope():
        issued = [next_accession_number(hospital.organisation.pk, year=2026) for _ in range(5)]
    assert issued == [
        "L26-000001",
        "L26-000002",
        "L26-000003",
        "L26-000004",
        "L26-000005",
    ]


def test_the_sequence_restarts_each_year(hospital) -> None:
    with hospital.scope():
        next_accession_number(hospital.organisation.pk, year=2026)
        next_accession_number(hospital.organisation.pk, year=2026)
        new_year = next_accession_number(hospital.organisation.pk, year=2027)
    assert new_year == "L27-000001"


def test_two_hospitals_number_their_samples_separately(hospital, other_hospital) -> None:
    with hospital.scope():
        mine = next_accession_number(hospital.organisation.pk, year=2026)
    with other_hospital.scope():
        theirs = next_accession_number(other_hospital.organisation.pk, year=2026)
    assert mine == theirs == "L26-000001"
    with unscoped(reason="test: counters are per organisation, read across both"):
        assert AccessionCounter.objects.count() == 2


# --------------------------------------------------------------------------
# Collecting
# --------------------------------------------------------------------------


def test_collecting_numbers_the_tube_and_moves_the_tests_on(
    hospital, department, doctor, nurse, fbc, pcv
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, pcv], doctor)

    with hospital.scope():
        rows = requests_for(hospital, encounter)
        samples = worklist.collect(requests=rows, actor=nurse)

    assert len(samples) == 1
    assert samples[0].accession.startswith("L")
    assert samples[0].status == SampleStatus.COLLECTED
    assert samples[0].panel_names in ("FBC, PCV", "PCV, FBC")

    for row in requests_for(hospital, encounter):
        assert row.status == RequestStatus.COLLECTED
        assert row.sample_id == samples[0].pk


def test_collecting_records_who_drew_it_and_when(hospital, department, doctor, nurse, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
    assert sample.collected_by_id == nurse.pk
    assert sample.collected_at is not None


def test_a_test_that_already_has_a_tube_is_not_drawn_again(
    hospital, department, doctor, nurse, fbc
) -> None:
    """A patient stuck twice for one result is the mistake this prevents."""
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        rows = requests_for(hospital, encounter)
        worklist.collect(requests=rows, actor=nurse)
        with pytest.raises(worklist.WorklistError) as caught:
            worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)
    assert "already been taken" in str(caught.value)


def test_one_collection_cannot_cover_two_patients(hospital, department, doctor, nurse, fbc) -> None:
    first = arrive(hospital, department, make_patient(hospital))
    second = arrive(
        hospital,
        department,
        make_patient(hospital, first_name="Ngozi", last_name="Adeyemi", phone="08039999999"),
    )
    order_tests(hospital, encounter=first, panels=[fbc], actor=doctor)
    order_tests(hospital, encounter=second, panels=[fbc], actor=doctor)

    with hospital.scope():
        rows = requests_for(hospital, first) + requests_for(hospital, second)
        with pytest.raises(worklist.WorklistError) as caught:
            worklist.collect(requests=rows, actor=nurse)
    assert "one patient" in str(caught.value)


def test_collecting_nothing_is_refused(hospital, nurse) -> None:
    with hospital.scope(), pytest.raises(worklist.WorklistError):
        worklist.collect(requests=[], actor=nurse)


def test_collecting_is_written_to_the_audit_log(hospital, department, doctor, nurse, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]

    with unscoped(reason="test: the audit log is written outside one hospital's scope"):
        event = AuditEvent.objects.filter(action="lab.sample.collected").first()
    assert event is not None
    assert event.changes["accession"] == sample.accession


# --------------------------------------------------------------------------
# Receiving
# --------------------------------------------------------------------------


def test_receiving_a_tube_starts_the_work_and_the_order(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    orders = order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.receive(sample=sample, actor=scientist)

        assert sample.status == SampleStatus.RECEIVED
        assert sample.received_by_id == scientist.pk
        assert requests_for(hospital, encounter)[0].status == RequestStatus.IN_PROGRESS
        assert Order.objects.get(pk=orders[0].pk).status == OrderStatus.IN_PROGRESS


def test_a_tube_cannot_be_received_twice(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.receive(sample=sample, actor=scientist)
        with pytest.raises(worklist.WorklistError) as caught:
            worklist.receive(sample=sample, actor=scientist)
    assert "already been received" in str(caught.value)


def test_a_cancelled_order_does_not_stop_the_tube_being_booked_in(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    """
    The tube is in the building either way. The laboratory's record of it is
    what matters here, and the order refusing to advance must not be a 500.
    """
    encounter = arrive(hospital, department)
    orders = order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        ordering.cancel(order=orders[0], reason="Patient went home", actor=doctor)
        worklist.receive(sample=sample, actor=scientist)

    assert sample.status == SampleStatus.RECEIVED


def test_the_time_in_transit_is_recorded(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    drawn = timezone.now() - dt.timedelta(minutes=35)
    with hospital.scope():
        sample = worklist.collect(
            requests=requests_for(hospital, encounter), actor=nurse, now=drawn
        )[0]
        worklist.receive(sample=sample, actor=scientist)
    assert sample.minutes_in_transit() == 35


# --------------------------------------------------------------------------
# Rejecting, and redrawing
# --------------------------------------------------------------------------


def test_rejecting_puts_the_tests_back_on_the_collection_list(
    hospital, department, doctor, nurse, scientist, fbc, pcv
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, pcv], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(sample=sample, reason=RejectionReason.CLOTTED, actor=scientist)

        rows = requests_for(hospital, encounter)

    assert sample.status == SampleStatus.REJECTED
    assert all(row.status == RequestStatus.AWAITING_COLLECTION for row in rows)
    assert all(row.sample_id is None for row in rows)
    assert all(row.rejection_count == 1 for row in rows)


def test_a_rejected_tube_still_says_what_it_was_for(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    """
    The requests move on to a fresh tube, so without the snapshot the
    rejection log would show a sample that appears to have been for nothing.
    """
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(sample=sample, reason=RejectionReason.HAEMOLYSED, actor=scientist)
    assert sample.panel_names == "FBC"


def test_a_rejection_needs_a_reason_from_the_list(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        with pytest.raises(worklist.WorklistError):
            worklist.reject(sample=sample, reason="it looked wrong", actor=scientist)


def test_something_else_needs_a_sentence(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        with pytest.raises(worklist.WorklistError) as caught:
            worklist.reject(sample=sample, reason=RejectionReason.OTHER, actor=scientist)
    assert "sentence" in str(caught.value)


def test_a_received_tube_can_still_be_rejected(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    """Haemolysis is usually seen when the sample is spun, after booking in."""
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.receive(sample=sample, actor=scientist)
        worklist.reject(sample=sample, reason=RejectionReason.HAEMOLYSED, actor=scientist)
    assert sample.status == SampleStatus.REJECTED


def test_a_rejected_tube_cannot_be_received(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(sample=sample, reason=RejectionReason.LEAKED, actor=scientist)
        with pytest.raises(worklist.WorklistError):
            worklist.receive(sample=sample, actor=scientist)


def test_the_redraw_links_back_to_the_tube_it_replaces(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        first = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(sample=first, reason=RejectionReason.CLOTTED, actor=scientist)
        second = worklist.collect(
            requests=requests_for(hospital, encounter), actor=nurse, replaces=first
        )[0]

    assert second.recollection_of_id == first.pk
    assert second.accession != first.accession


def test_a_third_attempt_is_visible_as_a_third_attempt(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        for _ in range(2):
            tube = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
            worklist.reject(sample=tube, reason=RejectionReason.CLOTTED, actor=scientist)
        assert requests_for(hospital, encounter)[0].rejection_count == 2


def test_a_wrong_patient_rejection_is_flagged_as_a_safety_event(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    """
    Not a redraw. Somebody was mis-identified at the bedside, and the audit
    log has to be searchable for that.
    """
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(
            sample=sample,
            reason=RejectionReason.WRONG_PATIENT,
            note="Drawn from the patient in the next bed",
            actor=scientist,
        )

    assert sample.is_serious_rejection is True
    with unscoped(reason="test: the audit log is written outside one hospital's scope"):
        event = AuditEvent.objects.filter(action="lab.sample.rejected").first()
    assert event is not None
    assert "patient_safety" in event.flags


def test_an_ordinary_rejection_is_not_flagged_as_a_safety_event(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(sample=sample, reason=RejectionReason.CLOTTED, actor=scientist)
    assert sample.is_serious_rejection is False


def test_the_rejection_reads_as_a_sentence(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(
            sample=sample,
            reason=RejectionReason.CLOTTED,
            note="drawn through a 25G needle",
            actor=scientist,
        )
    assert sample.rejection_label() == "Clotted - drawn through a 25G needle"


# --------------------------------------------------------------------------
# Cancelling
# --------------------------------------------------------------------------


def test_a_test_can_be_cancelled_before_the_bench_has_it(hospital, department, doctor, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        row = requests_for(hospital, encounter)[0]
        worklist.cancel_request(request=row, reason="Doctor changed the plan", actor=doctor)
    assert row.status == RequestStatus.CANCELLED


def test_a_test_in_progress_cannot_be_cancelled(
    hospital, department, doctor, nurse, scientist, fbc
) -> None:
    """The patient has already been stuck. Let it finish."""
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.receive(sample=sample, actor=scientist)
        row = requests_for(hospital, encounter)[0]
        with pytest.raises(worklist.WorklistError):
            worklist.cancel_request(request=row, reason="Changed my mind", actor=doctor)


def test_cancelling_needs_a_reason(hospital, department, doctor, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        row = requests_for(hospital, encounter)[0]
        with pytest.raises(worklist.WorklistError):
            worklist.cancel_request(request=row, reason="   ", actor=doctor)


# --------------------------------------------------------------------------
# Reading the worklist
# --------------------------------------------------------------------------


def test_the_counts_add_up(hospital, department, doctor, nurse, fbc, eucr) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, eucr], doctor)
    with hospital.scope():
        counts = worklist.counts(hospital.main_branch.pk)
        assert counts["awaiting_collection"] == 2
        assert counts["in_transit"] == 0

        sample = worklist.collect(requests=requests_for(hospital, encounter)[:1], actor=nurse)[0]
        counts = worklist.counts(hospital.main_branch.pk)

        assert counts["awaiting_collection"] == 1
        assert counts["in_transit"] == 1
        # Drawn, but still in a corridor: on the way and not yet at the bench.
        assert counts["in_laboratory"] == 0

        worklist.receive(sample=sample, actor=nurse)
        counts = worklist.counts(hospital.main_branch.pk)

    assert counts["in_transit"] == 0
    assert counts["in_laboratory"] == 1


def test_a_redraw_is_counted_as_one(hospital, department, doctor, nurse, scientist, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        worklist.reject(sample=sample, reason=RejectionReason.CLOTTED, actor=scientist)
        counts = worklist.counts(hospital.main_branch.pk)
    assert counts["redraws"] == 1


def test_overdue_is_counted_against_the_servers_clock(hospital, department, doctor, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        row = requests_for(hospital, encounter)[0]
        row.due_at = timezone.now() - dt.timedelta(minutes=30)
        row.save(update_fields=["due_at"])
        counts = worklist.counts(hospital.main_branch.pk)
        assert counts["overdue"] == 1
        assert row.minutes_late() == 30


def test_a_tube_late_in_a_corridor_is_still_counted_as_overdue(
    hospital, department, doctor, nurse, fbc
) -> None:
    """
    The worst case the tile exists for: drawn, past its turnaround, and
    nowhere near the bench. It is in neither the collection list nor the
    laboratory's own list, so counting only those two would lose it.
    """
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        row = requests_for(hospital, encounter)[0]
        worklist.collect(requests=[row], actor=nurse)
        row.refresh_from_db()
        row.due_at = timezone.now() - dt.timedelta(minutes=45)
        row.save(update_fields=["due_at"])

        counts = worklist.counts(hospital.main_branch.pk)

    assert counts["in_transit"] == 1
    assert counts["in_laboratory"] == 0
    assert counts["overdue"] == 1


def test_a_cancelled_test_is_never_overdue(hospital, department, doctor, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        row = requests_for(hospital, encounter)[0]
        row.due_at = timezone.now() - dt.timedelta(hours=5)
        row.save(update_fields=["due_at"])
        worklist.cancel_request(request=row, reason="No longer needed", actor=doctor)
    assert row.is_overdue() is False
    assert row.minutes_late() == 0


def test_stat_sorts_above_an_earlier_routine(hospital, department, doctor, fbc, eucr) -> None:
    """Stat first, then urgent, then whatever was promised soonest."""
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [eucr], doctor, priority="routine")
    order_tests(hospital, encounter, [fbc], doctor, priority="stat")

    with hospital.scope():
        rows = sorted(worklist.awaiting_collection(hospital.main_branch.pk), key=worklist.sort_key)
    assert [row.panel.code for row in rows] == ["FBC", "EUCR"]


def test_the_other_branch_does_not_see_these_samples(
    hospital, department, doctor, nurse, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)
        assert worklist.samples_in_transit(hospital.second_branch.pk) == []
        assert worklist.awaiting_collection(hospital.second_branch.pk) == []


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


def test_the_barcode_is_drawn_as_bars(hospital) -> None:
    drawn = label_render.code128_svg("L26-000147")
    assert drawn.svg.startswith("<svg")
    assert "<rect" in drawn.svg
    # Narrow enough to sit beside the QR code on a 50 mm label, wide enough
    # for a cheap scanner. See apps.laboratory.labels.
    assert 25 < drawn.width_mm < 36


def test_the_barcode_has_no_hairlines_inside_a_bar(hospital) -> None:
    """
    Consecutive dark modules are one rectangle. Two that share an edge can
    leave a white hairline once a renderer rounds the coordinates, and a
    hairline inside a bar is a misread.
    """
    from barcode import Code128

    bits = "".join(Code128("L26-000147").build())
    runs = sum(
        1 for index, bit in enumerate(bits) if bit == "1" and (index == 0 or bits[index - 1] == "0")
    )
    assert label_render.code128_svg("L26-000147").svg.count("<rect") == runs + 1


def test_the_label_carries_what_a_nurse_checks_by_eye(
    hospital, department, doctor, nurse, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]
        context = label_render.label_context(sample)

    assert context["accession"] == sample.accession
    assert context["patient_name"] == "Tunde Bakare"
    assert context["mrn"] == sample.patient.mrn
    assert context["panels"] == "FBC"
    assert context["specimen"] == "Whole blood (EDTA)"


def test_the_age_is_printed_short(hospital) -> None:
    patient = make_patient(hospital, date_of_birth=timezone.localdate() - dt.timedelta(days=10))
    assert label_render.short_age(patient) == "10 d"
    patient.date_of_birth = timezone.localdate() - dt.timedelta(days=200)
    assert label_render.short_age(patient) == "7 m"
    patient.date_of_birth = timezone.localdate() - dt.timedelta(days=round(40 * 365.25))
    assert label_render.short_age(patient) == "40 y"


def test_an_age_rounds_down_to_the_last_birthday(hospital) -> None:
    """
    Somebody a fortnight short of forty is thirty-nine. An age that rounded
    up would put a year on a child's label, which is where it matters.
    """
    patient = make_patient(
        hospital, date_of_birth=timezone.localdate() - dt.timedelta(days=40 * 365)
    )
    assert label_render.short_age(patient) == "39 y"


def test_an_unknown_date_of_birth_prints_no_age(hospital) -> None:
    """Better blank than a guess on a tube."""
    patient = make_patient(hospital, date_of_birth=None)
    assert label_render.short_age(patient) == ""


def test_the_labels_render_as_a_pdf(hospital, department, doctor, nurse, fbc, eucr) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, eucr], doctor)
    with hospital.scope():
        samples = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)
        pdf = label_render.render_pdf(samples, hospital="Lifeline Test Hospital")

    assert pdf.startswith(b"%PDF")
    assert len(samples) == 2


def test_one_label_is_exactly_one_sticker(hospital, department, doctor, nurse, fbc) -> None:
    """
    A thermal printer treats a page as a sticker, and the sticker is clipped
    at its edge. So two different things can go wrong with a label that is a
    hair too tall: it spills onto a second, nearly blank sticker and the roll
    runs out at half the labels, or it is clipped and the collection time -
    which is what decides whether the sample is still testable - silently
    goes missing.

    This checks both: one page per tube, and the last row of the label
    finishing above the bottom of the printable area, with the longest name
    and tube description the sticker is likely to carry.
    """
    from weasyprint import HTML

    patient = make_patient(
        hospital,
        first_name="Oluwafunmilayo Adebimpe",
        last_name="Oyelaran-Ogundipe",
    )
    encounter = arrive(hospital, department, patient)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        samples = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)
        html = label_render.render_html(samples, hospital="Lifeline Specialist Hospital, Ikeja")

    assert len(samples) == 1
    pages = HTML(string=html).render().pages
    assert len(pages) == 1

    # WeasyPrint lays out in CSS pixels at 96 to the inch.
    def mm(value: float) -> float:
        return value * 25.4 / 96

    def rows(box, found=None):
        found = {} if found is None else found
        css_class = box.element.get("class") if box.element is not None else None
        if css_class in {"top", "patient", "tube", "foot"}:
            found[css_class] = mm(box.position_y) + mm(box.margin_height())
        for child in getattr(box, "children", ()):
            rows(child, found)
        return found

    printable = mm(pages[0]._page_box.children[0].height)
    bottoms = rows(pages[0]._page_box)
    assert set(bottoms) == {"top", "patient", "tube", "foot"}, bottoms
    assert bottoms["foot"] <= printable, (
        f"the label is {bottoms['foot']:.2f}mm tall inside {printable:.2f}mm of sticker"
    )


def test_printing_is_counted(hospital, department, doctor, nurse, fbc) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        samples = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)
        label_render.mark_printed(samples, actor=nurse)
        label_render.mark_printed(samples, actor=nurse)
    assert samples[0].label_print_count == 2
    assert samples[0].label_printed_at is not None


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


def test_the_worklist_endpoint_answers_the_whole_screen(
    hospital, department, doctor, as_staff, fbc, eucr
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, eucr], doctor)

    client = as_staff(doctor, branch=hospital.main_branch, organisation=hospital.organisation)
    response = client.get(reverse("api:v1:lab-worklist"))

    assert response.status_code == 200
    assert len(response.data["awaiting_collection"]) == 2
    assert response.data["counts"]["awaiting_collection"] == 2
    # A doctor reads the worklist and does not draw blood.
    assert response.data["may_collect"] is False
    assert response.data["may_receive"] is False


def test_a_nurse_may_collect_but_not_receive(
    hospital, department, doctor, nurse, as_staff, fbc
) -> None:
    """The split that stops a ward closing its own loop."""
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)

    client = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    worklist_response = client.get(reverse("api:v1:lab-worklist"))
    assert worklist_response.data["may_collect"] is True
    assert worklist_response.data["may_receive"] is False

    rows = requests_for(hospital, encounter)
    collected = client.post(
        reverse("api:v1:lab-collect"), {"requests": [str(rows[0].pk)]}, format="json"
    )
    assert collected.status_code == 201
    sample_id = collected.data["samples"][0]["id"]

    refused = client.post(reverse("api:v1:lab-sample-receive", args=[sample_id]), {}, format="json")
    assert refused.status_code == 403


def test_the_laboratory_may_receive(
    hospital, department, doctor, nurse, scientist, as_staff, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]

    client = as_staff(scientist, branch=hospital.main_branch, organisation=hospital.organisation)
    response = client.post(
        reverse("api:v1:lab-sample-receive", args=[sample.pk]), {}, format="json"
    )

    assert response.status_code == 200
    assert response.data["status"] == SampleStatus.RECEIVED


def test_the_collection_plan_endpoint_says_how_many_tubes(
    hospital, department, doctor, nurse, as_staff, fbc, pcv, eucr
) -> None:
    patient = make_patient(hospital)
    encounter = arrive(hospital, department, patient)
    order_tests(hospital, encounter, [fbc, pcv, eucr], doctor)

    client = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    response = client.get(reverse("api:v1:lab-collection-plan", args=[patient.pk]))

    assert response.status_code == 200
    assert len(response.data) == 2


def test_rejecting_over_http_needs_a_reason_from_the_list(
    hospital, department, doctor, nurse, scientist, as_staff, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]

    client = as_staff(scientist, branch=hospital.main_branch, organisation=hospital.organisation)
    bad = client.post(
        reverse("api:v1:lab-sample-reject", args=[sample.pk]),
        {"reason": "looked wrong"},
        format="json",
    )
    assert bad.status_code == 400

    good = client.post(
        reverse("api:v1:lab-sample-reject", args=[sample.pk]),
        {"reason": RejectionReason.CLOTTED},
        format="json",
    )
    assert good.status_code == 200
    assert good.data["rejection"] == "Clotted"


def test_the_label_endpoint_returns_a_pdf(
    hospital, department, doctor, nurse, as_staff, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]

    client = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    response = client.get(reverse("api:v1:lab-sample-label", args=[sample.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    # Patient-identifying paper must never sit in a shared browser's cache.
    assert "no-store" in response["Cache-Control"]
    assert response.content.startswith(b"%PDF")


def test_a_label_is_served_to_a_browser_asking_for_a_pdf(
    hospital, department, doctor, nurse, as_staff, fbc
) -> None:
    """
    The browser sends ``Accept: application/pdf``, and DRF negotiates the
    content type before the handler runs. A route advertising only JSON
    answers 406 before any of our code is reached, which is what happened
    the first time this screen tried to print.
    """
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]

    client = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    response = client.get(
        reverse("api:v1:lab-sample-label", args=[sample.pk]), HTTP_ACCEPT="application/pdf"
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_a_label_sheet_is_fetched_by_accession_number(
    hospital, department, doctor, nurse, as_staff, fbc, eucr
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc, eucr], doctor)
    with hospital.scope():
        samples = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)

    client = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    numbers = ",".join(sample.accession for sample in samples)
    response = client.get(reverse("api:v1:lab-label-sheet"), {"accessions": numbers})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_an_unknown_accession_number_prints_nothing(hospital, nurse, as_staff) -> None:
    client = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    response = client.get(reverse("api:v1:lab-label-sheet"), {"accessions": "L26-999999"})
    assert response.status_code == 404


def test_printing_a_label_is_audited_as_patient_data_leaving_the_system(
    hospital, department, doctor, nurse, as_staff, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]

    client = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    client.get(reverse("api:v1:lab-sample-label", args=[sample.pk]))

    with unscoped(reason="test: the audit log is written outside one hospital's scope"):
        event = AuditEvent.objects.filter(action="lab.label.printed").first()
    assert event is not None
    assert event.patient_id == sample.patient_id


def test_another_hospital_cannot_touch_these_samples(
    hospital, other_hospital, department, doctor, nurse, as_staff, fbc
) -> None:
    encounter = arrive(hospital, department)
    order_tests(hospital, encounter, [fbc], doctor)
    with hospital.scope():
        sample = worklist.collect(requests=requests_for(hospital, encounter), actor=nurse)[0]

    intruder = other_hospital.staff("lab_supervisor")
    client = as_staff(
        intruder, branch=other_hospital.main_branch, organisation=other_hospital.organisation
    )
    response = client.post(
        reverse("api:v1:lab-sample-receive", args=[sample.pk]), {}, format="json"
    )
    assert response.status_code == 404


def test_the_worklist_is_not_shown_to_a_role_without_the_permission(hospital, as_staff) -> None:
    clerk = hospital.staff("front_desk")
    client = as_staff(clerk, branch=hospital.main_branch, organisation=hospital.organisation)
    assert client.get(reverse("api:v1:lab-worklist")).status_code == 403
