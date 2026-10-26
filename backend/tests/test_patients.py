"""
Patients: registration, numbering, search and duplicates.

Every name here is synthetic.

The search tests are the ones that matter most. A front desk that cannot find
an existing patient registers them again, and a duplicate record is the most
expensive mistake in this system — it splits a clinical history in two, and
the half the doctor is not looking at is the half with the allergy in it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.patients.models import (
    Allergy,
    MedicalRecordCounter,
    NextOfKin,
    Patient,
    PatientIdentifier,
    Sex,
    next_medical_record_number,
)
from apps.patients.registration import (
    RegistrationError,
    RegistrationInput,
    register_patient,
    register_unknown_patient,
)
from apps.patients.search import (
    find_by_mrn,
    find_possible_duplicates,
    search_patients,
)

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def api_as_desk(api, hospital):
    user = hospital.staff("front_desk")
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


# --------------------------------------------------------------------------
# Medical record numbers
# --------------------------------------------------------------------------


def test_the_mrn_carries_the_branch_and_the_year(hospital) -> None:
    """
    IKJ/2026/000001 — the format a paper record room already uses.

    The staff reading this out over a phone expect the branch code and the
    year, because that is how the physical folders are filed.
    """
    patient = make_patient(hospital)
    branch_code = hospital.main_branch.code
    year = timezone.localdate().year

    assert patient.mrn.startswith(f"{branch_code}/{year}/")
    assert patient.mrn.endswith("000001")


def test_numbers_run_in_sequence_within_a_hospital(hospital) -> None:
    first = make_patient(hospital)
    second = make_patient(hospital, first_name="Chiamaka", last_name="Eze")

    assert first.mrn.endswith("000001")
    assert second.mrn.endswith("000002")


def test_the_sequence_restarts_each_year(hospital) -> None:
    with hospital.scope():
        last_year = next_medical_record_number(
            hospital.organisation.pk, "IKJ", year=timezone.localdate().year - 1
        )
        this_year = next_medical_record_number(hospital.organisation.pk, "IKJ")

    assert last_year.endswith("000001")
    assert this_year.endswith("000001")
    assert last_year != this_year


def test_one_hospitals_numbering_tells_another_nothing(hospital, other_hospital) -> None:
    """
    An MRN is printed on a card a patient carries. A number that leaked how
    many patients every other hospital on Curavia had registered would be a
    commercial disclosure on a piece of paper in somebody's wallet — which
    is why this is a per-hospital counter and not a database sequence.
    """
    for _ in range(5):
        make_patient(hospital, first_name="A", last_name=f"Patient{_}")

    theirs = make_patient(other_hospital, first_name="B", last_name="First")
    assert theirs.mrn.endswith("000001")

    assert MedicalRecordCounter.objects.filter(organisation=hospital.organisation).exists()


def test_two_clerks_registering_at_once_never_share_a_number(hospital) -> None:
    """
    The row lock, exercised.

    A collision is not a database problem but a wrong-patient one: the number
    is spoken aloud and written on a card as it is issued.
    """
    with hospital.scope():
        numbers = {next_medical_record_number(hospital.organisation.pk, "IKJ") for _ in range(20)}
    assert len(numbers) == 20


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_a_patient_needs_only_a_name(hospital) -> None:
    """
    No date of birth, no phone, no address.

    A form that refuses to save without them produces invented data, which
    is worse than missing data because nobody can tell which is which.
    """
    with hospital.scope():
        patient = register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(first_name="Ngozi", last_name="Bello"),
        )

    assert patient.date_of_birth is None
    assert patient.phone == ""
    assert patient.sex == Sex.UNKNOWN


def test_a_patient_without_a_name_is_refused(hospital) -> None:
    with hospital.scope(), pytest.raises(RegistrationError, match="first name and a surname"):
        register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(first_name="", last_name=""),
        )


def test_registration_stores_the_whole_form_in_one_transaction(hospital) -> None:
    with hospital.scope():
        patient = register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(
                first_name="Chiamaka",
                last_name="Nwachukwu",
                other_names="Adaeze",
                sex=Sex.FEMALE,
                phone="08021111111",
                identifiers=[{"kind": "nin", "value": "12345678901"}],
                next_of_kin=[
                    {"name": "Emeka Nwachukwu", "relationship": "Husband", "phone": "08022222222"}
                ],
                allergies=[{"substance": "Penicillin", "reaction": "Rash", "severity": "moderate"}],
            ),
        )

        assert patient.full_name == "Chiamaka Adaeze Nwachukwu"
        assert PatientIdentifier.objects.filter(patient=patient, kind="nin").count() == 1
        assert NextOfKin.objects.get(patient=patient).is_primary is True
        assert Allergy.objects.get(patient=patient).substance == "Penicillin"


def test_recording_an_allergy_counts_as_having_asked(hospital) -> None:
    """
    The distinction the patient banner is built on.

    "No known allergies" and "nobody has asked" are different facts, and a
    doctor about to prescribe needs to know which one they are looking at.
    """
    with hospital.scope():
        asked = register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(
                first_name="A",
                last_name="Asked",
                allergies=[{"substance": "Sulfonamides"}],
            ),
        )
        not_asked = register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(first_name="B", last_name="Unasked"),
        )
        none_known = register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(first_name="C", last_name="NoneKnown", allergies_checked=True),
        )

        assert asked.allergies_known is True
        assert not_asked.allergies_known is False
        assert none_known.allergies_known is True
        assert none_known.allergies.count() == 0


def test_a_future_date_of_birth_is_refused(hospital) -> None:
    from django.core.exceptions import ValidationError

    with hospital.scope(), pytest.raises(ValidationError):
        register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(
                first_name="Time",
                last_name="Traveller",
                date_of_birth=timezone.localdate() + timedelta(days=1),
            ),
        )


# --------------------------------------------------------------------------
# Emergency quick-registration
# --------------------------------------------------------------------------


def test_an_unconscious_patient_gets_a_record_in_ten_seconds(hospital) -> None:
    """
    The alternative hospitals actually use is no record at all, with the
    notes written up afterwards from memory — which is how a drug given in
    resus never reaches the chart.
    """
    with hospital.scope():
        patient = register_unknown_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            sex=Sex.MALE,
            estimated_age=45,
            note="Brought in by a neighbour, unresponsive",
        )

    assert patient.is_provisional is True
    assert patient.first_name == "Unknown"
    assert "Male" in patient.last_name
    assert patient.mrn
    # The age is stored as an estimate, so a dose calculated from it carries
    # the uncertainty with it.
    assert patient.date_of_birth_is_estimated is True
    assert patient.date_of_birth is not None


def test_each_unidentified_patient_gets_a_distinct_name(hospital) -> None:
    with hospital.scope():
        first = register_unknown_patient(
            organisation=hospital.organisation, branch=hospital.main_branch, sex=Sex.MALE
        )
        second = register_unknown_patient(
            organisation=hospital.organisation, branch=hospital.main_branch, sex=Sex.MALE
        )
    assert first.last_name != second.last_name


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


def test_a_misspelled_surname_still_finds_the_patient(hospital) -> None:
    """
    The whole reason for the trigram index.

    A clerk who hears "Adebayo" and types "Adebayou" must still find him, or
    they will register him again.
    """
    make_patient(hospital, first_name="Adebayo", last_name="Ilesanmi")

    with hospital.scope():
        found = list(search_patients("Adebayou"))

    assert len(found) == 1
    assert found[0].first_name == "Adebayo"


def test_a_phone_number_fragment_finds_the_patient(hospital) -> None:
    make_patient(hospital, phone="08031234567")
    with hospital.scope():
        assert len(list(search_patients("1234567"))) == 1


def test_an_mrn_finds_the_patient(hospital) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        found = list(search_patients(patient.mrn))
    assert [p.pk for p in found] == [patient.pk]


def test_a_surname_finds_everybody_who_has_it(hospital) -> None:
    make_patient(hospital, first_name="Adebayo", last_name="Okonkwo")
    make_patient(hospital, first_name="Chidi", last_name="Okonkwo", phone="08039999999")
    make_patient(hospital, first_name="Ngozi", last_name="Bello", phone="08038888888")

    with hospital.scope():
        found = list(search_patients("Okonkwo"))
    assert len(found) == 2


def test_an_other_name_finds_the_patient(hospital) -> None:
    """
    A relative at the desk will often give the name the family uses, which
    is frequently the middle name.
    """
    make_patient(hospital, first_name="Chiamaka", last_name="Nwachukwu", other_names="Adaeze")
    with hospital.scope():
        assert len(list(search_patients("Adaeze"))) == 1


def test_a_very_short_query_returns_nothing(hospital) -> None:
    """Two characters match almost everybody, and the clerk is still typing."""
    make_patient(hospital)
    with hospital.scope():
        assert list(search_patients("a")) == []


def test_search_never_reaches_another_hospital(hospital, other_hospital) -> None:
    make_patient(other_hospital, first_name="Adebayo", last_name="Okonkwo")

    with hospital.scope():
        assert list(search_patients("Adebayo")) == []


def test_a_merged_record_does_not_appear_as_a_separate_person(hospital) -> None:
    keeper = make_patient(hospital, first_name="Adebayo", last_name="Okonkwo")
    duplicate = make_patient(
        hospital, first_name="Adebayo", last_name="Okonkwo", phone="08039999999"
    )
    with hospital.scope():
        duplicate.merged_into = keeper
        duplicate.save(update_fields=["merged_into"])
        found = list(search_patients("Adebayo"))

    assert [p.pk for p in found] == [keeper.pk]


def test_an_old_mrn_on_a_card_still_resolves_after_a_merge(hospital) -> None:
    """
    An MRN lives in a patient's wallet for years. When two records are
    merged the old number has to keep working, or somebody turns up with a
    card the system says does not exist.
    """
    keeper = make_patient(hospital, first_name="Adebayo", last_name="Okonkwo")
    duplicate = make_patient(
        hospital, first_name="Adebayo", last_name="Okonkwo", phone="08039999999"
    )
    old_number = duplicate.mrn

    with hospital.scope():
        duplicate.merged_into = keeper
        duplicate.save(update_fields=["merged_into"])
        resolved = find_by_mrn(old_number)

    assert resolved is not None
    assert resolved.pk == keeper.pk


def test_the_search_index_is_maintained_by_the_database(hospital) -> None:
    """
    The trigger, not Python.

    A `.update()` bypasses save(), and a patient who silently cannot be found
    is the worst possible failure for this feature — so the index has to be
    maintained wherever the row was written from.
    """
    patient = make_patient(hospital, first_name="Adebayo", last_name="Okonkwo")

    with hospital.scope():
        Patient.objects.filter(pk=patient.pk).update(last_name="Oyelaran")
        assert list(search_patients("Oyelaran"))
        assert not list(search_patients("Okonkwo"))


# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------


def test_the_same_person_registering_twice_is_noticed(hospital) -> None:
    make_patient(hospital, first_name="Adebayo", last_name="Okonkwo", phone="08031234567")

    with hospital.scope():
        matches = find_possible_duplicates(
            first_name="Adebayo",
            last_name="Okonkwo",
            phone="08031234567",
            date_of_birth=date(1990, 5, 14),
        )

    assert matches
    assert matches[0].is_strong is True
    assert "same phone number" in matches[0].reasons
    assert "same date of birth" in matches[0].reasons


def test_a_shared_nin_is_near_conclusive(hospital) -> None:
    with hospital.scope():
        register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(
                first_name="Chiamaka",
                last_name="Eze",
                identifiers=[{"kind": "nin", "value": "12345678901"}],
            ),
        )
        # A different spelling of the name, no phone — only the NIN matches.
        matches = find_possible_duplicates(
            first_name="Chiamaca", last_name="Eze", nin="12345678901"
        )

    assert matches
    assert "same National Identification Number" in matches[0].reasons


def test_two_different_people_with_a_common_surname_are_not_flagged(hospital) -> None:
    """
    Families share surnames and handsets. A detector that flagged every
    Okonkwo would be ignored within a week.
    """
    make_patient(hospital, first_name="Adebayo", last_name="Okonkwo", phone="08031111111")

    with hospital.scope():
        matches = find_possible_duplicates(
            first_name="Folake", last_name="Okonkwo", phone="08032222222"
        )

    assert not any(match.is_strong for match in matches)


def test_duplicate_detection_never_reaches_another_hospital(hospital, other_hospital) -> None:
    make_patient(other_hospital, first_name="Adebayo", last_name="Okonkwo", phone="08031234567")
    with hospital.scope():
        assert (
            find_possible_duplicates(first_name="Adebayo", last_name="Okonkwo", phone="08031234567")
            == []
        )


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------


def test_the_front_desk_can_register_and_find_a_patient(api_as_desk, hospital) -> None:
    created = api_as_desk.post(
        reverse("api:v1:patient-register"),
        {
            "first_name": "Blessing",
            "last_name": "Udoka",
            "sex": "F",
            "phone": "08037777777",
            "date_of_birth": "1998-06-19",
        },
        format="json",
    )

    assert created.status_code == 201
    assert created.data["mrn"]
    assert created.data["full_name"] == "Blessing Udoka"

    found = api_as_desk.get(reverse("api:v1:patient-search"), {"q": "Udoka"})
    assert found.status_code == 200
    assert [row["mrn"] for row in found.data] == [created.data["mrn"]]


def test_registering_a_likely_duplicate_stops_and_shows_what_it_found(
    api_as_desk, hospital
) -> None:
    """
    Stopped once, never blocked. The clerk then either opens that record or
    says no and sends the same request again.
    """
    payload = {
        "first_name": "Adebayo",
        "last_name": "Okonkwo",
        "phone": "08031234567",
        "date_of_birth": "1990-05-14",
    }
    first = api_as_desk.post(reverse("api:v1:patient-register"), payload, format="json")
    assert first.status_code == 201

    second = api_as_desk.post(reverse("api:v1:patient-register"), payload, format="json")
    assert second.status_code == 409
    assert second.data["code"] == "possible_duplicate"
    assert second.data["matches"][0]["reasons"]

    confirmed = api_as_desk.post(
        reverse("api:v1:patient-register"),
        {**payload, "confirmed_not_duplicate": True},
        format="json",
    )
    assert confirmed.status_code == 201
    with hospital.scope():
        assert Patient.objects.filter(last_name="Okonkwo").count() == 2


def test_reading_a_patient_record_is_written_to_the_audit_log(api_as_desk, hospital) -> None:
    """The promise the hash chain exists to keep."""
    from apps.core.audit import AuditEvent

    patient = make_patient(hospital)
    api_as_desk.get(reverse("api:v1:patient-detail", kwargs={"pk": str(patient.pk)}))

    with hospital.scope():
        event = AuditEvent.objects.filter(action="patient.view").order_by("-sequence").first()

    assert event is not None
    assert str(event.patient_id) == str(patient.pk)
    # Field NAMES, never values.
    assert "mrn" in event.changes["read"]
    assert patient.mrn not in str(event.changes)


def test_a_search_is_audited_as_a_search_not_as_nine_reads(api_as_desk, hospital) -> None:
    from apps.core.audit import AuditEvent

    make_patient(hospital, first_name="Adebayo", last_name="Okonkwo")
    api_as_desk.get(reverse("api:v1:patient-search"), {"q": "Okonkwo"})

    with hospital.scope():
        assert AuditEvent.objects.filter(action="patient.search").exists()
        assert not AuditEvent.objects.filter(action="patient.view").exists()


def test_a_cashier_sees_who_the_patient_is_and_nothing_clinical(api, hospital) -> None:
    """
    `patients.view` is broad on purpose — a cashier has to identify the
    person paying — but it grants the DEMOGRAPHIC record only.

    The clinical fields are removed from the response rather than hidden in
    the client, so there is nothing to find in the network tab. See
    docs/permissions-matrix.yml.
    """
    with hospital.scope():
        patient = register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(
                first_name="Adebayo",
                last_name="Okonkwo",
                genotype="AS",
                blood_group="O+",
                allergies=[{"substance": "Penicillin"}],
            ),
        )

    api.force_authenticate(user=hospital.staff("cashier"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    response = api.get(reverse("api:v1:patient-detail", kwargs={"pk": str(patient.pk)}))

    assert response.status_code == 200
    assert response.data["mrn"] == patient.mrn
    for clinical in ("genotype", "blood_group", "allergies", "allergies_checked_at"):
        assert clinical not in response.data, f"a cashier can see {clinical}"
    assert "Penicillin" not in str(response.data)


def test_a_doctor_sees_the_clinical_fields(api, hospital) -> None:
    with hospital.scope():
        patient = register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(
                first_name="Adebayo",
                last_name="Okonkwo",
                genotype="AS",
                allergies=[{"substance": "Penicillin"}],
            ),
        )

    api.force_authenticate(user=hospital.staff("doctor"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    response = api.get(reverse("api:v1:patient-detail", kwargs={"pk": str(patient.pk)}))

    assert response.status_code == 200
    assert response.data["genotype"] == "AS"
    assert [row["substance"] for row in response.data["allergies"]] == ["Penicillin"]


def test_another_hospitals_patient_is_simply_not_found(api_as_desk, other_hospital) -> None:
    theirs = make_patient(other_hospital)
    response = api_as_desk.get(reverse("api:v1:patient-detail", kwargs={"pk": str(theirs.pk)}))
    assert response.status_code == 404


def test_quick_registration_through_the_api(api_as_desk, hospital) -> None:
    response = api_as_desk.post(
        reverse("api:v1:patient-register-quick"),
        {"sex": "M", "estimated_age": 45, "note": "Unresponsive, brought in by neighbour"},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["is_provisional"] is True
    assert response.data["first_name"] == "Unknown"


def test_an_mrn_lookup_says_when_it_followed_a_merge(api_as_desk, hospital) -> None:
    keeper = make_patient(hospital, first_name="Adebayo", last_name="Okonkwo")
    duplicate = make_patient(
        hospital, first_name="Adebayo", last_name="Okonkwo", phone="08039999999"
    )
    with hospital.scope():
        duplicate.merged_into = keeper
        duplicate.save(update_fields=["merged_into"])

    response = api_as_desk.get(reverse("api:v1:patient-by-mrn", kwargs={"mrn": duplicate.mrn}))

    assert response.status_code == 200
    assert response.data["mrn"] == keeper.mrn
    assert response.data["resolved_from_merge"] is True


def test_a_patient_cannot_be_registered_at_another_hospitals_branch(
    api_as_desk, hospital, other_hospital
) -> None:
    response = api_as_desk.post(
        reverse("api:v1:patient-register"),
        {
            "first_name": "Somebody",
            "last_name": "Else",
            "branch_id": str(other_hospital.main_branch.pk),
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "branch_required"
    with other_hospital.scope():
        assert not Patient.objects.filter(last_name="Else").exists()
