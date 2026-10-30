"""
Merging duplicate records, the lifetime timeline, and the patient card.

Every name here is synthetic.

These tests are weighted towards what a wrong merge destroys rather than
towards the happy path. A merge that works is a merge nobody notices; a
merge that dropped an allergy, cleared a death flag, broke an old card, or
could not be undone is the kind of bug that reaches a patient. So most of
what follows is an attempt to make the merge lose something.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import AuditEvent
from apps.patients import card as patient_card
from apps.patients import timeline as patient_timeline
from apps.patients.merge import (
    MergeError,
    merge_patients,
    plan_merge,
    reverse_merge,
    suggest_winner,
)
from apps.patients.models import Allergy, NextOfKin, Patient, PatientIdentifier, PatientMerge, Sex
from apps.patients.registration import RegistrationInput, register_patient, register_unknown_patient
from apps.patients.search import find_by_mrn, search_patients

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def api_as_records(api, hospital):
    """A front desk clerk: holds patients.merge per the permissions matrix."""
    user = hospital.staff("front_desk")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


@pytest.fixture
def api_as_doctor(api, hospital):
    """A doctor: holds clinical.note.read, so may open the timeline."""
    user = hospital.staff("doctor")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Adebayo",
        "last_name": "Okonkwo",
        "sex": Sex.MALE,
        "phone": "08031234567",
        "date_of_birth": date(1990, 5, 14),
    }
    defaults.update(overrides)
    with hospital.scope():
        return register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(**defaults),
        )


def make_pair(hospital):
    """
    The classic duplicate: same person, surname spelled differently, and the
    second registration has information the first one does not.
    """
    first = make_patient(hospital, phone="08031234567", address="")
    second = make_patient(
        hospital,
        last_name="Okonkw",
        phone="08031234567",
        address="12 Allen Avenue, Ikeja",
        email="",
        occupation="Trader",
    )
    return first, second


# --------------------------------------------------------------------------
# What a merge must never lose
# --------------------------------------------------------------------------


def test_the_old_mrn_still_opens_the_surviving_record(hospital) -> None:
    """
    The single most important property of a merge.

    An MRN lives in a wallet for years. If merging made an old card stop
    working, the patient holding it would be turned away from the desk —
    which is a worse outcome than the duplicate the merge was fixing.
    """
    keeper, duplicate = make_pair(hospital)
    old_number = duplicate.mrn

    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        found = find_by_mrn(old_number)

    assert found is not None
    assert found.pk == keeper.pk
    assert found.mrn == keeper.mrn


def test_a_merge_never_overwrites_information_it_only_fills_blanks(hospital) -> None:
    """
    The rule that makes a wrong merge survivable.

    The worst case of merging two strangers is then a record with somebody
    else's address in it. It is never a record whose own address has gone.
    """
    keeper = make_patient(hospital, phone="08031234567", occupation="Teacher")
    duplicate = make_patient(
        hospital, last_name="Okonkw", phone="08039999999", occupation="Trader", address="12 Allen"
    )

    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        keeper.refresh_from_db()

    assert keeper.phone == "08031234567", "an existing value was overwritten"
    assert keeper.occupation == "Teacher", "an existing value was overwritten"
    assert keeper.address == "12 Allen", "a blank was not filled"


def test_an_allergy_on_the_merged_record_reaches_the_surviving_one(hospital) -> None:
    """
    The reason this feature exists at all.

    A split history hides the allergy in the half nobody is looking at.
    """
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        Allergy.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            substance="Penicillin",
            reaction="Swelling",
            severity=Allergy.Severity.ANAPHYLAXIS,
        )
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        substances = list(keeper.allergies.values_list("substance", flat=True))

    assert substances == ["Penicillin"]


def test_where_the_two_records_disagree_the_worse_severity_wins(hospital) -> None:
    """
    A merge may raise a warning. It must never lower one.

    Both records say penicillin; one says a rash and the other says
    anaphylaxis. Keeping the surviving record's milder answer because it got
    there first would be a clinical decision made by a tie-break rule.
    """
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        Allergy.objects.create(
            organisation=hospital.organisation,
            patient=keeper,
            substance="Penicillin",
            severity=Allergy.Severity.MILD,
        )
        Allergy.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            substance="Penicillin",
            severity=Allergy.Severity.ANAPHYLAXIS,
        )
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        kept = keeper.allergies.get(substance="Penicillin")

    assert kept.severity == Allergy.Severity.ANAPHYLAXIS


def test_a_merge_never_clears_a_death_flag(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        Patient.objects.filter(pk=duplicate.pk).update(is_deceased=True, died_on=date(2026, 1, 9))
        duplicate.refresh_from_db()
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        keeper.refresh_from_db()

    assert keeper.is_deceased is True
    assert keeper.died_on == date(2026, 1, 9)


def test_the_allergy_history_being_taken_carries_across(hospital) -> None:
    """
    "Nobody has asked" must not survive a merge with a record where somebody
    did ask. The patient banner draws those two states differently, and the
    whole point of that distinction is that it is trustworthy.
    """
    keeper = make_patient(hospital)
    duplicate = make_patient(hospital, last_name="Okonkw")
    with hospital.scope():
        assert keeper.allergies_checked_at is None
        Patient.objects.filter(pk=duplicate.pk).update(allergies_checked_at=timezone.now())
        duplicate.refresh_from_db()
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        keeper.refresh_from_db()

    assert keeper.allergies_known is True


def test_identifiers_and_next_of_kin_move_across(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        PatientIdentifier.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            kind=PatientIdentifier.Kind.NIN,
            value="12345678901",
        )
        NextOfKin.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            name="Ngozi Okonkwo",
            relationship="Wife",
            phone="08037654321",
        )
        merge_patients(winner=keeper, loser=duplicate, reason="same person")

        assert keeper.identifiers.count() == 1
        assert keeper.next_of_kin.count() == 1
        assert duplicate.identifiers.count() == 0


def test_a_merged_record_disappears_from_search_but_is_not_deleted(hospital) -> None:
    """
    Both halves matter. It must not appear as a second person in a list the
    clerk is choosing from, and it must still be in the table, because its
    number still has to resolve and the merge still has to be reversible.
    """
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        results = list(search_patients("Okonkwo"))
        assert [p.pk for p in results] == [keeper.pk]
        assert Patient.objects.filter(pk=duplicate.pk).exists()


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_record_cannot_be_merged_into_itself(hospital) -> None:
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(MergeError, match="same record"):
        merge_patients(winner=patient, loser=patient, reason="x")


def test_an_already_merged_record_cannot_be_merged_again(hospital) -> None:
    """
    Otherwise a chain forms, and a chain is what makes a reversal ambiguous:
    the children this merge moved may since have moved on from the record
    they landed on.
    """
    keeper, duplicate = make_pair(hospital)
    third = make_patient(hospital, first_name="Adebayo", last_name="Okonkwu")
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        duplicate.refresh_from_db()
        with pytest.raises(MergeError, match="already been merged"):
            merge_patients(winner=third, loser=duplicate, reason="again")


def test_merging_into_a_record_that_was_itself_merged_away_is_refused(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    third = make_patient(hospital, last_name="Okonkwa")
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        duplicate.refresh_from_db()
        with pytest.raises(MergeError, match="merged away"):
            merge_patients(winner=duplicate, loser=third, reason="wrong direction")


def test_a_patient_of_another_hospital_is_simply_not_found(hospital, other_hospital) -> None:
    """
    The tenancy boundary, from the merge's point of view. The scoped manager
    is what enforces it, so the API cannot even assemble the pair.
    """
    mine = make_patient(hospital)
    theirs = make_patient(other_hospital)
    with hospital.scope():
        assert Patient.objects.filter(pk=theirs.pk).first() is None
        assert Patient.objects.filter(pk=mine.pk).first() is not None


# --------------------------------------------------------------------------
# Reversal
# --------------------------------------------------------------------------


def test_reversing_a_merge_puts_everything_back(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        PatientIdentifier.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            kind=PatientIdentifier.Kind.NIN,
            value="12345678901",
        )
        Allergy.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            substance="Sulfa",
            severity=Allergy.Severity.SEVERE,
        )
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")
        keeper.refresh_from_db()
        assert keeper.address == "12 Allen Avenue, Ikeja"

        reverse_merge(record, reason="not the same person after all")

        keeper.refresh_from_db()
        duplicate.refresh_from_db()
        counts = {
            "kept identifiers": keeper.identifiers.count(),
            "kept allergies": keeper.allergies.count(),
            "restored identifiers": duplicate.identifiers.count(),
            "restored allergies": duplicate.allergies.count(),
        }

    assert duplicate.merged_into_id is None
    assert keeper.address == "", "a filled field was not restored"
    assert counts == {
        "kept identifiers": 0,
        "kept allergies": 0,
        "restored identifiers": 1,
        "restored allergies": 1,
    }


def test_reversal_restores_a_severity_the_merge_raised(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        mine = Allergy.objects.create(
            organisation=hospital.organisation,
            patient=keeper,
            substance="Penicillin",
            severity=Allergy.Severity.MILD,
        )
        Allergy.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            substance="Penicillin",
            severity=Allergy.Severity.ANAPHYLAXIS,
        )
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")
        mine.refresh_from_db()
        assert mine.severity == Allergy.Severity.ANAPHYLAXIS

        reverse_merge(record, reason="wrong patient")
        mine.refresh_from_db()

    assert mine.severity == Allergy.Severity.MILD


def test_reversal_leaves_a_value_somebody_typed_after_the_merge_alone(hospital) -> None:
    """
    The merge filled a blank. If a human has since typed something else
    there, that is their value and a reversal must not blank it.
    """
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")
        Patient.objects.filter(pk=keeper.pk).update(address="4 Opebi Road, Ikeja")

        reverse_merge(record, reason="wrong patient")
        keeper.refresh_from_db()

    assert keeper.address == "4 Opebi Road, Ikeja"


def test_a_merge_cannot_be_reversed_twice(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")
        reverse_merge(record, reason="wrong patient")
        with pytest.raises(MergeError, match="already been reversed"):
            reverse_merge(record, reason="again")


def test_reversal_is_refused_once_the_surviving_record_was_merged_onward(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    third = make_patient(hospital, last_name="Okonkwa")
    with hospital.scope():
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")
        merge_patients(winner=third, loser=keeper, reason="and these two as well")
        with pytest.raises(MergeError, match="since been merged"):
            reverse_merge(record, reason="unpick the first one")


def test_reversal_only_clears_a_death_flag_the_merge_itself_set(hospital) -> None:
    """
    The one path in Curavia that ever un-sets a death, guarded so it cannot
    be the path that hides one. If the kept record has since been confirmed
    deceased on its own, reversing the merge leaves the flag alone.
    """
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        Patient.objects.filter(pk=duplicate.pk).update(is_deceased=True, died_on=date(2026, 1, 9))
        duplicate.refresh_from_db()
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")

        # Somebody corrects the losing record: that death was recorded in
        # error there. The kept record's flag is now its own.
        Patient.objects.filter(pk=duplicate.pk).update(is_deceased=False, died_on=None)

        reverse_merge(record, reason="wrong patient")
        keeper.refresh_from_db()

    assert keeper.is_deceased is True


def test_reversal_survives_a_child_row_deleted_in_the_meantime(hospital) -> None:
    """
    A manifest naming a row that no longer exists must not strand the two
    records together for good.
    """
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        kin = NextOfKin.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            name="Ngozi Okonkwo",
            relationship="Wife",
            phone="08037654321",
        )
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")
        NextOfKin.objects.filter(pk=kin.pk).delete()

        reverse_merge(record, reason="wrong patient")
        duplicate.refresh_from_db()

    assert duplicate.merged_into_id is None


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


def test_the_preview_says_what_would_be_filled_and_what_would_move(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        PatientIdentifier.objects.create(
            organisation=hospital.organisation,
            patient=duplicate,
            kind=PatientIdentifier.Kind.NIN,
            value="12345678901",
        )
        plan = plan_merge(winner=keeper, loser=duplicate)

    assert "address" in plan.fills
    assert "occupation" in plan.fills
    assert plan.moves["identifiers"] == 1
    assert "phone" not in plan.fills, "both records have a phone; nothing to fill"


def test_the_preview_warns_when_the_records_disagree_about_sex(hospital) -> None:
    keeper = make_patient(hospital, sex=Sex.MALE)
    duplicate = make_patient(hospital, last_name="Okonkw", sex=Sex.FEMALE)
    with hospital.scope():
        plan = plan_merge(winner=keeper, loser=duplicate)

    assert not plan.is_safe
    assert any("disagree about sex" in warning for warning in plan.warnings)


def test_the_preview_warns_about_two_different_confirmed_birth_dates(hospital) -> None:
    keeper = make_patient(hospital, date_of_birth=date(1990, 5, 14))
    duplicate = make_patient(hospital, last_name="Okonkw", date_of_birth=date(1974, 2, 2))
    with hospital.scope():
        plan = plan_merge(winner=keeper, loser=duplicate)

    assert any("date of birth" in warning for warning in plan.warnings)


def test_a_confirmed_identity_is_suggested_over_a_provisional_one(hospital) -> None:
    """
    An emergency registration folded into the real record, not the other way
    round — which would leave the hospital's surviving record named
    "Unknown Male 1".
    """
    real = make_patient(hospital)
    with hospital.scope():
        unknown = register_unknown_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            sex=Sex.MALE,
            estimated_age=40,
        )
        winner, loser = suggest_winner(unknown, real)

    assert winner.pk == real.pk
    assert loser.pk == unknown.pk


def test_otherwise_the_older_registration_is_suggested(hospital) -> None:
    first = make_patient(hospital)
    second = make_patient(hospital, last_name="Okonkw")
    with hospital.scope():
        Patient.objects.filter(pk=first.pk).update(
            registered_at=timezone.now() - timedelta(days=400)
        )
        first.refresh_from_db()
        winner, _loser = suggest_winner(second, first)

    assert winner.pk == first.pk


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------


def test_the_merge_endpoint_previews_then_merges(api_as_records, hospital) -> None:
    keeper, duplicate = make_pair(hospital)

    preview = api_as_records.post(
        reverse("api:v1:patient-merge-preview"),
        {"winner_id": str(keeper.pk), "loser_id": str(duplicate.pk)},
        format="json",
    )
    assert preview.status_code == 200, preview.data
    assert preview.data["suggested_winner_id"] == str(keeper.pk)
    assert [row["field"] for row in preview.data["fills"]] == sorted(
        row["field"] for row in preview.data["fills"]
    )

    done = api_as_records.post(
        reverse("api:v1:patient-merge"),
        {
            "winner_id": str(keeper.pk),
            "loser_id": str(duplicate.pk),
            "reason": "Same person — card reissued after a name change.",
        },
        format="json",
    )
    assert done.status_code == 201, done.data
    with hospital.scope():
        duplicate.refresh_from_db()
    assert duplicate.merged_into_id == keeper.pk


def test_the_merge_endpoint_insists_on_a_reason(api_as_records, hospital) -> None:
    """
    Not for the audit log's sake — it would record one anyway — but because
    being made to write the sentence is the step at which somebody notices
    these are two different people.
    """
    keeper, duplicate = make_pair(hospital)
    response = api_as_records.post(
        reverse("api:v1:patient-merge"),
        {"winner_id": str(keeper.pk), "loser_id": str(duplicate.pk), "reason": "dup"},
        format="json",
    )
    assert response.status_code == 400
    assert "reason" in response.data["fields"]


def test_a_nurse_cannot_merge_records(api, hospital) -> None:
    """
    patients.merge is owner, facility_admin and front_desk per
    docs/permissions-matrix.yml. A nurse holds patients.view and every
    clinical code, and still may not do this.
    """
    keeper, duplicate = make_pair(hospital)
    api.force_authenticate(user=hospital.staff("nurse"))
    api.credentials(HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk))

    response = api.post(
        reverse("api:v1:patient-merge"),
        {
            "winner_id": str(keeper.pk),
            "loser_id": str(duplicate.pk),
            "reason": "these look like the same person",
        },
        format="json",
    )
    assert response.status_code == 403


def test_the_merge_is_in_the_audit_log_with_both_numbers(api_as_records, hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    api_as_records.post(
        reverse("api:v1:patient-merge"),
        {
            "winner_id": str(keeper.pk),
            "loser_id": str(duplicate.pk),
            "reason": "Same person, two registrations.",
        },
        format="json",
    )
    event = AuditEvent.objects.filter(action="patient.merge").first()
    assert event is not None
    assert event.changes["kept"] == keeper.mrn
    assert event.changes["merged"] == duplicate.mrn
    assert event.reason == "Same person, two registrations."


def test_the_merge_history_endpoint_offers_the_reversal(api_as_records, hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    created = api_as_records.post(
        reverse("api:v1:patient-merge"),
        {
            "winner_id": str(keeper.pk),
            "loser_id": str(duplicate.pk),
            "reason": "Same person, two registrations.",
        },
        format="json",
    )
    merge_id = created.data["id"]

    history = api_as_records.get(reverse("api:v1:patient-merges", args=[keeper.pk]))
    assert history.status_code == 200
    assert history.data[0]["id"] == merge_id
    assert history.data[0]["can_reverse"] is True

    undone = api_as_records.post(
        reverse("api:v1:patient-merge-reverse", args=[merge_id]),
        {"reason": "The clerk picked the wrong record."},
        format="json",
    )
    assert undone.status_code == 200, undone.data
    assert AuditEvent.objects.filter(action="patient.merge.reverse").exists()

    with hospital.scope():
        duplicate.refresh_from_db()
    assert duplicate.merged_into_id is None


# --------------------------------------------------------------------------
# The timeline
# --------------------------------------------------------------------------


def test_the_timeline_starts_at_registration(hospital) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        entries = patient_timeline.build_timeline(patient)

    assert [entry.kind for entry in entries] == ["registered"]
    assert patient.mrn in entries[0].title


def test_the_timeline_is_newest_first(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        entries = patient_timeline.build_timeline(keeper)

    assert entries[0].kind == "merge_in"
    assert entries[-1].kind == "registered"
    assert all(entries[i].at >= entries[i + 1].at for i in range(len(entries) - 1)), (
        "the timeline is out of order"
    )


def test_a_severe_allergy_is_pinned_on_the_timeline(hospital) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        Allergy.objects.create(
            organisation=hospital.organisation,
            patient=patient,
            substance="Penicillin",
            severity=Allergy.Severity.ANAPHYLAXIS,
        )
        entries = patient_timeline.build_timeline(patient)

    allergy = next(entry for entry in entries if entry.kind == "allergy")
    assert allergy.is_pinned is True
    assert allergy.tone == "critical"


def test_no_known_allergies_is_its_own_timeline_entry(hospital) -> None:
    """
    Distinct from an empty timeline. "Asked, and the answer was none" is a
    clinical fact worth a line; "nobody asked" is the absence of one.
    """
    patient = make_patient(hospital, allergies_checked=True)
    with hospital.scope():
        entries = patient_timeline.build_timeline(patient)

    assert any(entry.kind == "allergies_none" for entry in entries)


def test_the_merged_away_record_says_where_its_history_went(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        entries = patient_timeline.build_timeline(duplicate)

    out = next(entry for entry in entries if entry.kind == "merge_out")
    assert keeper.mrn in out.title
    assert out.is_pinned is True


def test_a_reversed_merge_shows_both_the_merge_and_the_reversal(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        record = merge_patients(winner=keeper, loser=duplicate, reason="same person")
        reverse_merge(record, reason="Wrong record.")
        kinds = [entry.kind for entry in patient_timeline.build_timeline(keeper)]

    assert "merge_in" in kinds
    assert "merge_reversed" in kinds


def test_a_broken_timeline_source_does_not_take_the_history_with_it(hospital) -> None:
    """
    A doctor needs the rest of the history more than they need to know one
    module is broken. So a failing source becomes one entry, not a 500.
    """
    patient = make_patient(hospital)

    def explodes(_patient):
        raise RuntimeError("the lab module is having a day")

    patient_timeline.SOURCES.append(explodes)
    try:
        with hospital.scope():
            entries = patient_timeline.build_timeline(patient)
    finally:
        patient_timeline.SOURCES.remove(explodes)

    kinds = [entry.kind for entry in entries]
    assert "registered" in kinds
    assert "source_failed" in kinds


def test_the_timeline_endpoint_names_what_it_cannot_show_yet(api_as_doctor, hospital) -> None:
    """
    An empty-looking timeline must not read as "nothing ever happened to
    this patient". The response says which modules will fill it and when.
    """
    patient = make_patient(hospital)
    response = api_as_doctor.get(reverse("api:v1:patient-timeline", args=[patient.pk]))

    assert response.status_code == 200, response.data
    assert response.data["entries"][0]["kind"] == "registered"
    keys = {row["key"] for row in response.data["pending"]}
    assert {"encounters", "lab", "prescriptions"} <= keys


def test_the_front_desk_cannot_open_the_clinical_timeline(api_as_records, hospital) -> None:
    """
    It needs clinical.note.read as well as patients.view, and the front desk
    holds only the second. See the view's docstring for why that is decided
    now rather than when the timeline fills up.
    """
    patient = make_patient(hospital)
    response = api_as_records.get(reverse("api:v1:patient-timeline", args=[patient.pk]))
    assert response.status_code == 403


def test_reading_the_timeline_is_audited(api_as_doctor, hospital) -> None:
    patient = make_patient(hospital)
    api_as_doctor.get(reverse("api:v1:patient-timeline", args=[patient.pk]))

    event = AuditEvent.objects.filter(patient_id=patient.pk, action="patient.view").first()
    assert event is not None
    # Field NAMES, never values — an audit log full of patient history would
    # be a second copy of the thing it is protecting.
    assert event.changes["read"] == ["timeline"]


# --------------------------------------------------------------------------
# The card
# --------------------------------------------------------------------------


def test_the_card_qr_carries_no_personal_information(hospital) -> None:
    """
    The whole design of the card code. Somebody who finds a lost card should
    learn nothing about its owner, and the payload is not a URL — MRNs are
    sequential, so a URL carrying one would have other patients as its
    neighbours.
    """
    patient = make_patient(hospital, first_name="Chiamaka", last_name="Eze")
    with hospital.scope():
        card = patient_card.build_card(patient)

    assert card.payload == f"CURAVIA:{hospital.organisation.slug}:{patient.mrn}"
    assert "Chiamaka" not in card.payload
    assert "Eze" not in card.payload
    assert "http" not in card.payload.lower()
    assert str(patient.pk) not in card.payload


def test_the_card_renders_an_svg_sized_in_millimetres(hospital) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        card = patient_card.build_card(patient)

    assert card.qr_svg.startswith("<svg")
    assert "<?xml" not in card.qr_svg, "an XML declaration cannot be inlined in HTML"
    assert f'width="{patient_card.QR_MILLIMETRES}mm"' in card.qr_svg
    assert 'role="img"' in card.qr_svg


def test_the_svg_draws_exactly_the_matrix_the_encoder_produced() -> None:
    """
    The QR itself is the library's; the SVG around it is ours.

    `qr_svg` strips the XML declaration and rewrites the width and height,
    and a regex that also caught the viewBox would silently rescale the
    code — it would still look like a QR and would no longer scan. So this
    checks the rendering against the matrix: one subpath per dark module,
    and a viewBox still measured in modules.
    """
    import re

    import qrcode

    payload = "CURAVIA:lifeline-specialist-hospital:MAIN/2026/000001"
    svg = patient_card.qr_svg(payload)

    code = qrcode.QRCode(error_correction=patient_card.ERROR_CORRECTION, border=0)
    code.add_data(payload)
    code.make(fit=True)
    matrix = code.get_matrix()
    modules = len(matrix)
    dark = sum(1 for row in matrix for cell in row if cell)

    assert f'viewBox="0 0 {modules} {modules}"' in svg, (
        "the viewBox no longer matches the module count, so the code is rescaled"
    )
    assert len(re.findall(r"M\d", svg)) == dark


def test_the_printed_name_puts_the_surname_first_in_capitals(hospital) -> None:
    patient = make_patient(hospital, first_name="Adebayo", last_name="Okonkwo")
    with hospital.scope():
        card = patient_card.build_card(patient)

    assert card.printed_name == "OKONKWO, Adebayo"


def test_a_scanned_payload_resolves_back_to_the_patient(api_as_records, hospital) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        payload = patient_card.payload_for(patient, slug=hospital.organisation.slug)

    response = api_as_records.post(reverse("api:v1:patient-scan"), {"code": payload}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["mrn"] == patient.mrn
    assert response.data["resolved_from_merge"] is False


def test_an_old_card_scans_to_the_surviving_record(api_as_records, hospital) -> None:
    """
    The card in somebody's wallet, after their records were merged. This is
    the moment the whole "nothing is deleted" rule is paying for.
    """
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        payload = patient_card.payload_for(duplicate, slug=hospital.organisation.slug)
        merge_patients(winner=keeper, loser=duplicate, reason="same person")

    response = api_as_records.post(reverse("api:v1:patient-scan"), {"code": payload}, format="json")
    assert response.status_code == 200
    assert response.data["mrn"] == keeper.mrn
    assert response.data["resolved_from_merge"] is True


def test_a_card_from_another_hospital_is_refused_in_plain_words(api_as_records, hospital) -> None:
    patient = make_patient(hospital)
    response = api_as_records.post(
        reverse("api:v1:patient-scan"),
        {"code": f"CURAVIA:some-other-clinic:{patient.mrn}"},
        format="json",
    )
    assert response.status_code == 404
    assert response.data["code"] == "other_hospital"


def test_a_bare_mrn_typed_by_hand_also_scans(api_as_records, hospital) -> None:
    patient = make_patient(hospital)
    response = api_as_records.post(
        reverse("api:v1:patient-scan"), {"code": patient.mrn}, format="json"
    )
    assert response.status_code == 200
    assert response.data["mrn"] == patient.mrn


def test_pointing_the_scanner_at_something_else_gives_a_plain_answer(api_as_records) -> None:
    for nonsense in ("", "https://example.com/promo", "CURAVIA:", "{]["):
        response = api_as_records.post(
            reverse("api:v1:patient-scan"), {"code": nonsense}, format="json"
        )
        assert response.status_code == 404, nonsense
        assert "detail" in response.data


def test_no_card_is_printed_for_a_merged_away_record(hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        duplicate.refresh_from_db()
        with pytest.raises(patient_card.CardError, match="merged into another"):
            patient_card.build_card(duplicate)


def test_no_card_is_printed_for_a_provisional_record(hospital) -> None:
    """
    A card asserts that the hospital knows who this is. "Unknown Male 1" is
    the opposite of that.
    """
    with hospital.scope():
        unknown = register_unknown_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            sex=Sex.MALE,
        )
        with pytest.raises(patient_card.CardError, match="no confirmed identity"):
            patient_card.build_card(unknown)


def test_printing_a_card_is_audited(api_as_records, hospital) -> None:
    """
    A card takes a name and a number out of the building, so issuing one is
    a disclosure.
    """
    patient = make_patient(hospital)
    response = api_as_records.get(reverse("api:v1:patient-card", args=[patient.pk]))

    assert response.status_code == 200, response.data
    assert response.data["printed_name"] == "OKONKWO, Adebayo"
    assert response.data["hospital_name"] == hospital.organisation.name
    event = AuditEvent.objects.filter(action="patient.card.print").first()
    assert event is not None
    assert event.changes["mrn"] == patient.mrn


def test_the_merge_table_is_scoped_to_one_hospital(hospital, other_hospital) -> None:
    keeper, duplicate = make_pair(hospital)
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
    with other_hospital.scope():
        assert PatientMerge.objects.count() == 0
    with hospital.scope():
        assert PatientMerge.objects.count() == 1
