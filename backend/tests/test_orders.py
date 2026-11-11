"""
Orders, the safety check, and referrals.

Every name here is synthetic.

The file is mostly about one thing: **a prescription that would hurt the
patient must not be signable by reflex.** "Allergic to penicillin" and a
prescription for Augmentin is the classic preventable death in a Nigerian
ward, and the system's job is to put the reason in front of the doctor at
the moment the drug is added — not in a dialog after they have signed,
which is a dialog they will dismiss without reading.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.catalogue.models import Service, ServiceCategory
from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.core.tenancy import unscoped
from apps.journey.models import Encounter
from apps.orders import safety
from apps.orders import service as ordering
from apps.orders.models import Order, OrderKind, OrderStatus, Referral
from apps.patients.models import Allergy, Patient, Sex
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


def allergic_to(hospital, patient, substance: str, severity: str = "severe") -> Allergy:
    with hospital.scope():
        return Allergy.objects.create(
            organisation=hospital.organisation,
            patient=patient,
            substance=substance,
            severity=severity,
            reaction="Swelling of the face",
        )


# --------------------------------------------------------------------------
# The safety check, on its own
# --------------------------------------------------------------------------


class FakeAllergy:
    def __init__(self, substance: str, severity: str = "severe", reaction: str = "") -> None:
        self.substance = substance
        self.severity = severity
        self.reaction = reaction
        self.is_active = True


def test_the_drug_the_patient_is_allergic_to_is_caught() -> None:
    found = safety.allergy_warnings(
        drug_name="Penicillin V 250 mg", allergies=[FakeAllergy("Penicillin")]
    )
    assert len(found) == 1
    assert found[0].tone == "critical"


def test_the_whole_family_is_caught_not_just_the_exact_name() -> None:
    """
    The classic preventable death: "allergic to penicillin" on the chart and
    a prescription for Augmentin, which is one.
    """
    for drug in ("Augmentin 625 mg", "Amoxicillin 500 mg", "Ampiclox", "Flucloxacillin"):
        found = safety.allergy_warnings(drug_name=drug, allergies=[FakeAllergy("Penicillin")])
        assert found, f"{drug} was not caught"
        assert "same family" in found[0].message


def test_a_mild_allergy_warns_without_blocking() -> None:
    """
    A rash to co-trimoxazole is worth saying and is the doctor's call. Only
    the severe ones stop the basket, because a check that fires on
    everything teaches people to click through it.
    """
    found = safety.allergy_warnings(
        drug_name="Septrin", allergies=[FakeAllergy("Sulphonamide", severity="mild")]
    )
    assert found[0].tone == "warning"


def test_an_unrelated_drug_is_not_flagged() -> None:
    assert (
        safety.allergy_warnings(drug_name="Paracetamol 1 g", allergies=[FakeAllergy("Penicillin")])
        == []
    )


def test_a_short_word_inside_another_does_not_fire() -> None:
    """
    "Cefalexin" contains "alex". A substring check on short strings produces
    warnings nobody believes, and a warning nobody believes is worse than
    none.
    """
    assert safety.allergy_warnings(drug_name="Cefalexin", allergies=[FakeAllergy("Alex")]) == []


def test_a_cleared_allergy_is_not_checked() -> None:
    cleared = FakeAllergy("Penicillin")
    cleared.is_active = False
    assert safety.allergy_warnings(drug_name="Augmentin", allergies=[cleared]) == []


def test_the_message_names_the_substance_and_the_reaction() -> None:
    """
    "ALLERGY ALERT" tells a tired doctor nothing they can act on.
    """
    found = safety.allergy_warnings(
        drug_name="Augmentin",
        allergies=[FakeAllergy("Penicillin", reaction="Swelling of the face")],
    )
    assert "Penicillin" in found[0].message
    assert "Swelling of the face" in found[0].message


# --------------------------------------------------------------------------
# The basket
# --------------------------------------------------------------------------


def test_orders_are_drafted_and_signed_together(hospital, department, doctor) -> None:
    """
    A doctor thinks in a plan — bloods, an antibiotic, something for the
    fever — and signing each separately is three times the clicks for one
    intention.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="Full blood count")
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.MEDICATION,
            name="Paracetamol",
            dose="1 g",
            frequency="Three times a day",
            actor=doctor,
        )
        assert len(ordering.basket(encounter)) == 2

        signed = ordering.sign(encounter=encounter, actor=doctor)

    assert len(signed) == 2
    assert all(order.status == OrderStatus.ORDERED for order in signed)
    assert all(order.ordered_at is not None for order in signed)
    with hospital.scope():
        assert ordering.basket(encounter) == []


def test_nothing_downstream_sees_a_draft(hospital, department, doctor) -> None:
    """A draft is a thought, not an instruction."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="Malaria parasite")
        live = Order.objects.filter(
            encounter=encounter, status__in=(OrderStatus.ORDERED, OrderStatus.IN_PROGRESS)
        )
        assert not live.exists()


def test_signing_an_empty_basket_is_refused(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(ordering.OrderError, match="nothing to sign"):
        ordering.sign(encounter=encounter, actor=doctor)


def test_a_medicine_needs_a_dose(hospital, department, doctor) -> None:
    from django.core.exceptions import ValidationError

    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(ValidationError):
        ordering.draft(encounter=encounter, kind=OrderKind.MEDICATION, name="Amoxicillin")


def test_an_order_needs_to_say_what_it_is(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(ordering.OrderError, match="what is being ordered"):
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="   ")


def test_a_closed_visit_takes_no_orders(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        Encounter.objects.filter(pk=encounter.pk).update(status=Encounter.Status.CLOSED)
        encounter.refresh_from_db()
        with pytest.raises(ordering.OrderError, match="closed"):
            ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")


# --------------------------------------------------------------------------
# The check, through the basket
# --------------------------------------------------------------------------


def test_a_dangerous_prescription_stops_the_whole_basket(hospital, department, doctor) -> None:
    """
    Not just itself. The orders in a consultation are one plan, and half a
    plan going through is how a patient gets bloods taken for a review that
    does not happen.
    """
    patient = make_patient(hospital)
    allergic_to(hospital, patient, "Penicillin")
    encounter = arrive(hospital, department, patient=patient)

    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="Full blood count")
        drug = ordering.draft(
            encounter=encounter,
            kind=OrderKind.MEDICATION,
            name="Augmentin 625 mg",
            dose="625 mg",
            actor=doctor,
        )
        assert drug.blocking_warnings, "the warning is on the line when it is added"

        with pytest.raises(ordering.OrderError, match="Augmentin"):
            ordering.sign(encounter=encounter, actor=doctor)

        assert len(ordering.basket(encounter)) == 2, "nothing was sent"


def test_going_ahead_anyway_needs_a_reason_and_is_recorded(hospital, department, doctor) -> None:
    """
    A doctor may have a reason — the allergy was a childhood rash, or there
    is nothing else. What is not allowed is doing it silently.
    """
    patient = make_patient(hospital)
    allergic_to(hospital, patient, "Penicillin")
    encounter = arrive(hospital, department, patient=patient)

    with hospital.scope():
        ordering.draft(
            encounter=encounter,
            kind=OrderKind.MEDICATION,
            name="Augmentin 625 mg",
            dose="625 mg",
            actor=doctor,
        )
        signed = ordering.sign(
            encounter=encounter,
            actor=doctor,
            override_reason="Childhood rash only; discussed with the patient.",
        )

    assert signed[0].status == OrderStatus.ORDERED
    assert "Childhood rash" in signed[0].override_reason
    assert signed[0].warnings, "what the doctor was told is kept with the order"


def test_the_check_runs_again_at_signing(hospital, department, doctor) -> None:
    """
    The basket may have been open for ten minutes while a nurse recorded an
    allergy. What is enforced is what is true now, not what the browser
    remembers.
    """
    patient = make_patient(hospital)
    encounter = arrive(hospital, department, patient=patient)

    with hospital.scope():
        order = ordering.draft(
            encounter=encounter,
            kind=OrderKind.MEDICATION,
            name="Amoxicillin 500 mg",
            dose="500 mg",
            actor=doctor,
        )
        assert order.warnings == [], "nothing known about the patient yet"

    allergic_to(hospital, patient, "Penicillin")

    with hospital.scope(), pytest.raises(ordering.OrderError, match="Amoxicillin"):
        ordering.sign(encounter=encounter, actor=doctor)


def test_ordering_the_same_thing_twice_warns_without_blocking(hospital, department, doctor) -> None:
    """
    A repeat potassium at four hours is deliberate. The commoner cause is
    two people ordering the same thing without knowing, which is worth
    saying and not worth refusing.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="Full blood count")
        second = ordering.draft(
            encounter=encounter, kind=OrderKind.LABORATORY, name="Full blood count"
        )
        assert second.warnings[0]["kind"] == "duplicate"
        assert second.blocking_warnings == []
        assert len(ordering.sign(encounter=encounter, actor=doctor)) == 2


def test_an_order_is_not_a_duplicate_of_itself(hospital, department, doctor) -> None:
    """
    Signing re-checks every line, and the line is already in the table by
    then. Finding itself would put "already ordered on this visit" on the
    only order there is — which teaches the ward that the warning means
    nothing, the week before it means something.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="Fasting blood sugar")
        signed = ordering.sign(encounter=encounter, actor=doctor)

    assert signed[0].warnings == []


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


def test_an_order_carries_what_it_is_expected_to_cost(hospital, department, doctor) -> None:
    from apps.catalogue.models import Payer, Price

    encounter = arrive(hospital, department)
    with hospital.scope():
        service = Service.objects.create(
            organisation=hospital.organisation,
            code="FBC",
            name="Full blood count",
            category=ServiceCategory.LABORATORY,
        )
        payer = Payer.objects.filter(kind=Payer.Kind.SELF).first() or Payer.objects.create(
            organisation=hospital.organisation, name="Self-pay", kind=Payer.Kind.SELF
        )
        Price.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            service=service,
            payer=payer,
            amount_kobo=350_000,
        )

        order = ordering.draft(
            encounter=encounter, kind=OrderKind.LABORATORY, service=service, actor=doctor
        )

    assert order.estimate_kobo == 350_000
    assert order.name == "Full blood count", "the name comes from the service"


def test_a_service_with_no_price_says_so_rather_than_costing_nothing(
    hospital, department, doctor
) -> None:
    """
    Never zero. A service that silently costs nothing is how a hospital
    gives away a CT scan for a month before anybody notices.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        service = Service.objects.create(
            organisation=hospital.organisation,
            code="CT-HEAD",
            name="CT scan of the head",
            category=ServiceCategory.RADIOLOGY,
        )
        order = ordering.draft(
            encounter=encounter, kind=OrderKind.RADIOLOGY, service=service, actor=doctor
        )

    assert order.estimate_kobo is None
    assert "No price" in order.estimate_note


# --------------------------------------------------------------------------
# Changing and stopping
# --------------------------------------------------------------------------


def test_a_sent_order_cannot_be_quietly_reworded(hospital, department, doctor) -> None:
    """
    The paper in the laboratory and the record would stop saying the same
    thing. That is a cancellation and a new order.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")
        order = ordering.sign(encounter=encounter, actor=doctor)[0]
        with pytest.raises(ordering.OrderError, match="already been sent"):
            ordering.update_draft(order=order, name="Malaria parasite", actor=doctor)


def test_stopping_an_order_keeps_the_row_and_the_reason(hospital, department, doctor) -> None:
    """
    An order that vanishes is an order the patient still gets charged for.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")
        order = ordering.sign(encounter=encounter, actor=doctor)[0]
        ordering.cancel(order=order, reason="Patient could not afford it today.", actor=doctor)
        order.refresh_from_db()

    assert order.status == OrderStatus.CANCELLED
    assert "afford" in order.cancel_reason


def test_stopping_a_sent_order_needs_a_reason(hospital, department, doctor) -> None:
    """A department may already be acting on it, so the record has to say why."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")
        order = ordering.sign(encounter=encounter, actor=doctor)[0]
        with pytest.raises(ordering.OrderError, match="why"):
            ordering.cancel(order=order, reason="  ", actor=doctor)


def test_taking_a_draft_off_the_basket_needs_no_reason(hospital, department, doctor) -> None:
    """
    Nobody has been told to do anything yet.

    A question asked at this moment is answered with a full stop, and that
    answer then devalues the reason on the orders that really went out.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        order = ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")
        ordering.cancel(order=order, reason="", actor=doctor)
        order.refresh_from_db()
        still_shown = ordering.for_encounter(encounter)

    assert order.status == OrderStatus.CANCELLED
    assert order.cancel_reason == ""
    # The row is kept, but the visit stops carrying a thing that never happened.
    assert still_shown == []


def test_a_stopped_order_that_was_sent_stays_on_the_visit(hospital, department, doctor) -> None:
    """An order that vanishes is an order the patient is still charged for."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")
        order = ordering.sign(encounter=encounter, actor=doctor)[0]
        ordering.cancel(order=order, reason="Patient went home.", actor=doctor)
        still_shown = ordering.for_encounter(encounter)

    assert [row.pk for row in still_shown] == [order.pk]


def test_a_finished_order_cannot_be_stopped(hospital, department, doctor) -> None:
    """The blood is drawn; the scan is done. Stopping it would be a fiction."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")
        order = ordering.sign(encounter=encounter, actor=doctor)[0]
        ordering.advance(order=order, status=OrderStatus.COMPLETED)
        with pytest.raises(ordering.OrderError, match="finished"):
            ordering.cancel(order=order, reason="Changed my mind.", actor=doctor)


def test_an_order_cannot_skip_backwards(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        order = ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC")
        with pytest.raises(ordering.OrderError):
            ordering.advance(order=order, status=OrderStatus.COMPLETED)


# --------------------------------------------------------------------------
# Referrals
# --------------------------------------------------------------------------


def test_an_internal_referral_goes_to_a_department(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        referral = ordering.refer(
            encounter=encounter,
            kind=Referral.Kind.INTERNAL,
            to_department=department,
            reason="Please review this knee.",
            actor=doctor,
        )
        waiting = ordering.inbox(department)

    assert referral.status == Referral.Status.SENT
    assert referral.referred_at is not None
    assert referral.destination == department.name
    assert referral in waiting


def test_an_internal_referral_needs_a_department(hospital, department, doctor) -> None:
    from django.core.exceptions import ValidationError

    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(ValidationError):
        ordering.refer(
            encounter=encounter,
            kind=Referral.Kind.INTERNAL,
            reason="Please review.",
            actor=doctor,
        )


def test_an_external_referral_takes_a_name_rather_than_a_record(
    hospital, department, doctor
) -> None:
    """
    The receiving hospital is not in anybody's database, and waiting for it
    to be would stop the referral.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        referral = ordering.refer(
            encounter=encounter,
            kind=Referral.Kind.EXTERNAL,
            to_facility="Lagos University Teaching Hospital",
            to_clinician="Dr Adeyemi",
            speciality="Cardiology",
            reason="Murmur, for an echocardiogram.",
            urgency=Referral.Urgency.SOON,
            actor=doctor,
        )

    assert "Lagos University Teaching Hospital" in referral.destination
    assert referral.urgency == Referral.Urgency.SOON


def test_an_external_referral_needs_somewhere_to_go(hospital, department, doctor) -> None:
    from django.core.exceptions import ValidationError

    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(ValidationError):
        ordering.refer(
            encounter=encounter,
            kind=Referral.Kind.EXTERNAL,
            reason="For an opinion.",
            actor=doctor,
        )


def test_declining_a_referral_needs_a_reason(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        referral = ordering.refer(
            encounter=encounter,
            kind=Referral.Kind.INTERNAL,
            to_department=department,
            reason="Please review.",
            actor=doctor,
        )
        with pytest.raises(ordering.OrderError, match="why"):
            ordering.respond_to_referral(
                referral=referral, status=Referral.Status.DECLINED, actor=doctor
            )


def test_a_closed_referral_stays_closed(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        referral = ordering.refer(
            encounter=encounter,
            kind=Referral.Kind.INTERNAL,
            to_department=department,
            reason="Please review.",
            actor=doctor,
        )
        ordering.respond_to_referral(
            referral=referral, status=Referral.Status.COMPLETED, actor=doctor
        )
        with pytest.raises(ordering.OrderError, match="already closed"):
            ordering.respond_to_referral(
                referral=referral, status=Referral.Status.ACCEPTED, actor=doctor
            )


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


@pytest.fixture
def api_as_doctor(as_staff, hospital, doctor):
    return as_staff(doctor, branch=hospital.main_branch, organisation=hospital.organisation)


def test_the_whole_round_trip_over_http(api_as_doctor, hospital, department) -> None:
    encounter = arrive(hospital, department)

    added = api_as_doctor.post(
        reverse("api:v1:encounter-orders", args=[encounter.pk]),
        {"kind": "laboratory", "name": "Malaria parasite", "priority": "urgent"},
        format="json",
    )
    assert added.status_code == 201
    assert added.data["status"] == "draft"

    drug = api_as_doctor.post(
        reverse("api:v1:encounter-orders", args=[encounter.pk]),
        {
            "kind": "medication",
            "name": "Artemether-lumefantrine",
            "dose": "80/480 mg",
            "frequency": "Twice a day",
            "duration": "3 days",
        },
        format="json",
    )
    assert drug.status_code == 201
    assert "80/480 mg" in drug.data["summary"]

    listed = api_as_doctor.get(reverse("api:v1:encounter-orders", args=[encounter.pk]))
    assert listed.data["basket_count"] == 2

    signed = api_as_doctor.post(
        reverse("api:v1:encounter-orders-sign", args=[encounter.pk]), {}, format="json"
    )
    assert signed.status_code == 200
    assert [order["status"] for order in signed.data["orders"]] == ["ordered", "ordered"]

    cancelled = api_as_doctor.post(
        reverse("api:v1:order-detail", args=[added.data["id"]]),
        {"reason": "Ordered in error."},
        format="json",
    )
    assert cancelled.status_code == 200
    assert cancelled.data["status"] == "cancelled"


def test_the_api_refuses_a_blocked_basket(api_as_doctor, hospital, department) -> None:
    patient = make_patient(hospital)
    allergic_to(hospital, patient, "Penicillin")
    encounter = arrive(hospital, department, patient=patient)

    api_as_doctor.post(
        reverse("api:v1:encounter-orders", args=[encounter.pk]),
        {"kind": "medication", "name": "Augmentin 625 mg", "dose": "625 mg"},
        format="json",
    )
    refused = api_as_doctor.post(
        reverse("api:v1:encounter-orders-sign", args=[encounter.pk]), {}, format="json"
    )
    assert refused.status_code == 400
    assert refused.data["code"] == "cannot_sign"

    went_ahead = api_as_doctor.post(
        reverse("api:v1:encounter-orders-sign", args=[encounter.pk]),
        {"override_reason": "Childhood rash only, discussed with the patient."},
        format="json",
    )
    assert went_ahead.status_code == 200


def test_referring_over_http(api_as_doctor, hospital, department) -> None:
    encounter = arrive(hospital, department)
    response = api_as_doctor.post(
        reverse("api:v1:encounter-referrals", args=[encounter.pk]),
        {
            "kind": "external",
            "to_facility": "Lagos University Teaching Hospital",
            "speciality": "Cardiology",
            "reason": "Murmur, for an echocardiogram.",
            "urgency": "soon",
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["status"] == "sent"

    answered = api_as_doctor.post(
        reverse("api:v1:referral-detail", args=[response.data["id"]]),
        {"status": "completed", "note": "Seen on 12 November."},
        format="json",
    )
    assert answered.data["status"] == "completed"


def test_a_nurse_may_read_orders_but_not_write_them(as_staff, hospital, department, doctor) -> None:
    """
    A nurse giving a drug has to see what was ordered. Ordering it is a
    different act, and a different permission.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC", actor=doctor)

    nurse = hospital.staff("nurse")
    api = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)

    assert api.get(reverse("api:v1:encounter-orders", args=[encounter.pk])).status_code == 200
    assert (
        api.post(
            reverse("api:v1:encounter-orders", args=[encounter.pk]),
            {"kind": "laboratory", "name": "FBC"},
            format="json",
        ).status_code
        == 403
    )
    assert (
        api.post(
            reverse("api:v1:encounter-orders-sign", args=[encounter.pk]), {}, format="json"
        ).status_code
        == 403
    )
    assert (
        api.post(
            reverse("api:v1:encounter-referrals", args=[encounter.pk]),
            {"kind": "internal", "reason": "Please review."},
            format="json",
        ).status_code
        == 403
    )


def test_a_cashier_cannot_read_what_was_ordered(as_staff, hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    cashier = hospital.staff("cashier")
    api = as_staff(cashier, branch=hospital.main_branch, organisation=hospital.organisation)

    assert api.get(reverse("api:v1:encounter-orders", args=[encounter.pk])).status_code == 403


def test_another_hospital_cannot_reach_the_orders(
    as_staff, hospital, other_hospital, department, doctor
) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        ordering.draft(encounter=encounter, kind=OrderKind.LABORATORY, name="FBC", actor=doctor)

    outsider = other_hospital.staff("doctor")
    api = as_staff(
        outsider, branch=other_hospital.main_branch, organisation=other_hospital.organisation
    )
    assert api.get(reverse("api:v1:encounter-orders", args=[encounter.pk])).status_code == 404


def test_signing_is_audited_and_an_override_is_flagged(api_as_doctor, hospital, department) -> None:
    patient = make_patient(hospital)
    allergic_to(hospital, patient, "Penicillin")
    encounter = arrive(hospital, department, patient=patient)

    added = api_as_doctor.post(
        reverse("api:v1:encounter-orders", args=[encounter.pk]),
        {"kind": "medication", "name": "Augmentin 625 mg", "dose": "625 mg"},
        format="json",
    )
    api_as_doctor.post(
        reverse("api:v1:encounter-orders-sign", args=[encounter.pk]),
        {"override_reason": "Childhood rash only."},
        format="json",
    )

    with unscoped(reason="test: read the audit log"):
        event = AuditEvent.objects.filter(
            organisation=hospital.organisation,
            object_id=added.data["id"],
            action="clinical.order.create",
        ).first()

    assert event is not None
    assert event.changes["overridden"] is True
    assert "sensitive" in (event.flags or [])
