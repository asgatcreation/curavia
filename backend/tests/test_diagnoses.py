"""
Coding a visit, and the problem list.

Every name here is synthetic.

The problem list is what this file is really about. A diagnosis stored is
easy; a list of what a patient *has* that is still true and still readable
after forty visits is the hard part, and almost every failure of a paper
record is a failure of that list — the same condition written down four
times, a resolved one that never got crossed off, a suspicion that hardened
into a diagnosis because nobody recorded that it was only ever a guess.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.clinical import diagnoses, icd10
from apps.clinical.models import Certainty, Diagnosis, Problem
from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.core.tenancy import unscoped
from apps.journey import service as journey
from apps.journey.models import Encounter
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


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Chinelo",
        "last_name": "Obi",
        "sex": Sex.FEMALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1979, 9, 3),
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


def another_visit(hospital, department, patient) -> Encounter:
    """
    Send them home and bring them back.

    Not a second `check_in`: the desk hands back the ticket somebody is
    already holding rather than issuing a second one, which is right at the
    desk and useless here.
    """
    with hospital.scope():
        open_now = Encounter.objects.filter(patient=patient, status=Encounter.Status.OPEN).first()
        if open_now is not None:
            journey.close_encounter(open_now)
        return journey.start_encounter(
            patient=patient, branch=hospital.main_branch, department=department
        )


@pytest.fixture
def doctor(hospital):
    return hospital.staff("doctor", first_name="Folake", last_name="Ilesanmi")


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------


def test_every_code_is_unique() -> None:
    codes = [item.code for item in icd10.CODES]
    assert len(codes) == len(set(codes))


def test_every_synonym_points_at_a_real_code() -> None:
    """A typo in the synonym table would silently drop a search term."""
    for word, codes in icd10.SYNONYMS.items():
        for code in codes:
            assert icd10.get(code) is not None, f"{word!r} points at missing {code}"


def test_searching_puts_the_right_thing_first() -> None:
    """
    A list where "A09 Diarrhoea" outranks "B54 Malaria" for the query
    "malaria" is a list that gets ignored, and an ignored code list means
    every patient ends up coded R50.
    """
    assert icd10.search("malaria")[0].code == "B54"
    assert icd10.search("typhoid")[0].code == "A01.0"
    assert icd10.search("asthma")[0].code == "J45"


def test_searching_by_the_code_itself_works() -> None:
    assert icd10.search("I10")[0].code == "I10"
    assert icd10.search("e11")[0].code == "E11"


def test_the_words_a_doctor_actually_types_find_the_code() -> None:
    """
    The gap between what a doctor says and what the WHO wrote is where
    coding quietly fails. Nobody types "Essential (primary) hypertension".
    """
    assert "I10" in [item.code for item in icd10.search("BP")]
    assert "E11" in [item.code for item in icd10.search("sugar")]
    assert "N39.0" in [item.code for item in icd10.search("UTI")]
    assert "B20" in [item.code for item in icd10.search("retroviral")]
    assert "D57.0" in [item.code for item in icd10.search("sickler")]


def test_an_empty_search_returns_nothing_rather_than_everything() -> None:
    assert icd10.search("") == []
    assert icd10.search("   ") == []


def test_nonsense_finds_nothing() -> None:
    assert icd10.search("qqzz") == []


# --------------------------------------------------------------------------
# Diagnoses
# --------------------------------------------------------------------------


def test_adding_a_code_stores_its_wording_too(hospital, department, doctor) -> None:
    """
    The catalogue may be corrected or replaced. What the doctor chose on the
    day has to read the same in five years' time.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnosis = diagnoses.add(encounter=encounter, code="B54", actor=doctor)

    assert diagnosis.code == "B54"
    assert diagnosis.title == "Unspecified malaria"
    assert diagnosis.certainty == Certainty.SUSPECTED
    assert diagnosis.chapter == "Infections"


def test_a_code_that_is_not_in_the_list_is_refused(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(diagnoses.DiagnosisError, match="not a code"):
        diagnoses.add(encounter=encounter, code="ZZ99", actor=doctor)


def test_adding_the_same_code_again_updates_it(hospital, department, doctor) -> None:
    """
    A doctor who suspected malaria in the morning and confirmed it after the
    blood film is doing exactly this. Making them delete and re-add would
    lose when it was first considered.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        first = diagnoses.add(encounter=encounter, code="B54", actor=doctor)
        second = diagnoses.add(
            encounter=encounter, code="B54", certainty=Certainty.CONFIRMED, actor=doctor
        )
        rows = diagnoses.for_encounter(encounter)

    assert first.pk == second.pk
    assert second.certainty == Certainty.CONFIRMED
    assert second.diagnosed_at == first.diagnosed_at
    assert len(rows) == 1


def test_only_one_diagnosis_is_primary(hospital, department, doctor) -> None:
    """
    Two "main reasons for attending" means every count the hospital reports
    is wrong by an unknown amount.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        malaria = diagnoses.add(encounter=encounter, code="B54", is_primary=True, actor=doctor)
        diagnoses.add(encounter=encounter, code="I10", is_primary=True, actor=doctor)
        rows = diagnoses.for_encounter(encounter)

    primaries = [row for row in rows if row.is_primary]
    assert len(primaries) == 1
    assert primaries[0].code == "I10"
    malaria.refresh_from_db()
    assert malaria.is_primary is False


def test_the_primary_diagnosis_is_listed_first(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="R50", actor=doctor)
        diagnoses.add(encounter=encounter, code="B54", is_primary=True, actor=doctor)
        rows = diagnoses.for_encounter(encounter)

    assert rows[0].code == "B54"


def test_something_ruled_out_cannot_be_the_main_diagnosis(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnosis = diagnoses.add(encounter=encounter, code="A01.0", actor=doctor)
        diagnoses.rule_out(diagnosis=diagnosis, actor=doctor)
        with pytest.raises(diagnoses.DiagnosisError, match="ruled out"):
            diagnoses.set_primary(diagnosis=diagnosis, actor=doctor)


def test_ruling_out_keeps_the_record(hospital, department, doctor) -> None:
    """
    "Not typhoid, checked" is often the most useful line in a record, and a
    record that drops it invites the same test to be ordered twice.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnosis = diagnoses.add(encounter=encounter, code="A01.0", actor=doctor)
        diagnoses.rule_out(
            diagnosis=diagnosis, actor=doctor, note="Widal negative, blood film positive."
        )
        rows = diagnoses.for_encounter(encounter)

    assert len(rows) == 1
    assert rows[0].certainty == Certainty.RULED_OUT
    assert "Widal" in rows[0].note


def test_a_code_entered_in_error_is_archived_not_deleted(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnosis = diagnoses.add(encounter=encounter, code="B54", actor=doctor)
        diagnoses.remove(diagnosis=diagnosis, actor=doctor)

        assert diagnoses.for_encounter(encounter) == []
        assert Diagnosis.objects.filter(pk=diagnosis.pk).exists()


def test_a_removed_code_can_be_added_again(hospital, department, doctor) -> None:
    """The unique constraint has to ignore archived rows, or a correction
    locks the code out of that visit forever."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        first = diagnoses.add(encounter=encounter, code="B54", actor=doctor)
        diagnoses.remove(diagnosis=first, actor=doctor)
        second = diagnoses.add(encounter=encounter, code="B54", actor=doctor)

    assert second.pk != first.pk


def test_a_closed_visit_cannot_be_coded(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        Encounter.objects.filter(pk=encounter.pk).update(status=Encounter.Status.CLOSED)
        encounter.refresh_from_db()
        with pytest.raises(diagnoses.DiagnosisError, match="closed"):
            diagnoses.add(encounter=encounter, code="B54", actor=doctor)


# --------------------------------------------------------------------------
# The problem list
# --------------------------------------------------------------------------


def test_confirming_a_chronic_condition_opens_a_problem(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="E11", certainty=Certainty.CONFIRMED, actor=doctor)
        problems = diagnoses.problem_list(encounter.patient)

    assert [problem.code for problem in problems] == ["E11"]
    assert problems[0].is_active
    assert problems[0].onset_on is not None


def test_a_suspicion_does_not_reach_the_problem_list(hospital, department, doctor) -> None:
    """
    The next doctor reads that list as what the patient HAS. A suspicion on
    it hardens into a diagnosis nobody ever made.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="E11", certainty=Certainty.SUSPECTED, actor=doctor)
        assert diagnoses.problem_list(encounter.patient) == []


def test_a_short_illness_does_not_reach_the_problem_list(hospital, department, doctor) -> None:
    """Malaria is confirmed constantly and nobody *has* malaria."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="B54", certainty=Certainty.CONFIRMED, actor=doctor)
        assert diagnoses.problem_list(encounter.patient) == []


def test_the_same_condition_is_never_listed_twice(hospital, department, doctor) -> None:
    """
    Four visits, four confirmations, one line. A problem list with "Type 2
    diabetes" on it four times is a problem list nobody reads.
    """
    patient = make_patient(hospital)
    for _visit in range(4):
        encounter = another_visit(hospital, department, patient)
        with hospital.scope():
            diagnoses.add(
                encounter=encounter, code="E11", certainty=Certainty.CONFIRMED, actor=doctor
            )

    with hospital.scope():
        problems = diagnoses.problem_list(patient)

    assert len(problems) == 1
    assert problems[0].code == "E11"


def test_resolving_keeps_the_row_and_the_date(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="A15", certainty=Certainty.CONFIRMED, actor=doctor)
        problem = diagnoses.problem_list(encounter.patient)[0]
        diagnoses.resolve_problem(
            problem=problem, actor=doctor, note="Completed six months of treatment."
        )
        problems = diagnoses.problem_list(encounter.patient)

    assert len(problems) == 1
    assert problems[0].status == Problem.Status.RESOLVED
    assert problems[0].resolved_on is not None
    assert "six months" in problems[0].note


def test_a_problem_that_comes_back_is_the_same_problem(hospital, department, doctor) -> None:
    patient = make_patient(hospital)
    first_visit = arrive(hospital, department, patient=patient)
    with hospital.scope():
        diagnoses.add(
            encounter=first_visit, code="A15", certainty=Certainty.CONFIRMED, actor=doctor
        )
        problem = diagnoses.problem_list(patient)[0]
        diagnoses.resolve_problem(problem=problem, actor=doctor)

    second_visit = another_visit(hospital, department, patient)
    with hospital.scope():
        diagnoses.add(
            encounter=second_visit, code="A15", certainty=Certainty.CONFIRMED, actor=doctor
        )
        problems = diagnoses.problem_list(patient)

    assert len(problems) == 1, "the same condition returning is not a second problem"
    assert problems[0].pk == problem.pk
    assert problems[0].status == Problem.Status.ACTIVE
    assert problems[0].resolved_on is None


def test_ruling_out_stands_the_problem_down_without_claiming_recovery(
    hospital, department, doctor
) -> None:
    """
    Inactive, not resolved. The patient did not get better — the condition
    was never there, and saying otherwise is a different lie.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnosis = diagnoses.add(
            encounter=encounter, code="J45", certainty=Certainty.CONFIRMED, actor=doctor
        )
        diagnoses.rule_out(diagnosis=diagnosis, actor=doctor)
        problems = diagnoses.problem_list(encounter.patient)

    assert problems[0].status == Problem.Status.INACTIVE
    assert problems[0].resolved_on is None


def test_a_condition_the_patient_arrives_with_can_be_added_directly(hospital, doctor) -> None:
    """
    "Hypertensive for eight years, on amlodipine" was never diagnosed here
    and still has to be on the list before anybody prescribes anything.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        problem = diagnoses.add_problem(
            organisation_id=hospital.organisation.pk,
            patient=patient,
            code="I10",
            note="Diagnosed elsewhere in 2018, on amlodipine 5 mg.",
            onset_on=dt.date(2018, 4, 1),
            actor=doctor,
        )
        problems = diagnoses.problem_list(patient)

    assert problem.onset_on == dt.date(2018, 4, 1)
    assert [row.code for row in problems] == ["I10"]
    assert problems[0].opened_by_diagnosis_id is None


def test_the_active_problems_come_first(hospital, department, doctor) -> None:
    """
    The active list is what somebody reads in the seconds before the
    consultation. History belongs underneath it, never mixed in.
    """
    patient = make_patient(hospital)
    encounter = arrive(hospital, department, patient=patient)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="A15", certainty=Certainty.CONFIRMED, actor=doctor)
        tuberculosis = diagnoses.problem_list(patient)[0]
        diagnoses.resolve_problem(problem=tuberculosis, actor=doctor)

        diagnoses.add(encounter=encounter, code="E11", certainty=Certainty.CONFIRMED, actor=doctor)
        problems = diagnoses.problem_list(patient)
        active_only = diagnoses.problem_list(patient, include_closed=False)

    assert [problem.code for problem in problems] == ["E11", "A15"]
    assert active_only == [problems[0]]


def test_resolving_twice_is_refused(hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        problem = diagnoses.add_problem(
            organisation_id=hospital.organisation.pk,
            patient=patient,
            code="I10",
            actor=doctor,
        )
        diagnoses.resolve_problem(problem=problem, actor=doctor)
        with pytest.raises(diagnoses.DiagnosisError, match="already"):
            diagnoses.resolve_problem(problem=problem, actor=doctor)


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


@pytest.fixture
def api_as_doctor(as_staff, hospital, doctor):
    return as_staff(doctor, branch=hospital.main_branch, organisation=hospital.organisation)


def test_the_search_endpoint_answers_what_a_doctor_types(api_as_doctor) -> None:
    response = api_as_doctor.get(reverse("api:v1:icd-search"), {"q": "sugar"})
    assert response.status_code == 200
    assert "E11" in [row["code"] for row in response.data["results"]]
    assert response.data["total_in_catalogue"] == len(icd10.CODES)


def test_the_search_endpoint_is_empty_without_a_query(api_as_doctor) -> None:
    response = api_as_doctor.get(reverse("api:v1:icd-search"))
    assert response.status_code == 200
    assert response.data["results"] == []


def test_the_whole_round_trip_over_http(api_as_doctor, hospital, department) -> None:
    encounter = arrive(hospital, department)

    added = api_as_doctor.post(
        reverse("api:v1:encounter-diagnoses", args=[encounter.pk]),
        {"code": "E11", "certainty": "confirmed", "is_primary": True},
        format="json",
    )
    assert added.status_code == 201
    assert added.data["is_primary"] is True
    assert added.data["label"] == "E11 · Type 2 diabetes mellitus"

    listed = api_as_doctor.get(reverse("api:v1:encounter-diagnoses", args=[encounter.pk]))
    assert [row["code"] for row in listed.data["diagnoses"]] == ["E11"]

    problems = api_as_doctor.get(reverse("api:v1:patient-problems", args=[encounter.patient_id]))
    assert [row["code"] for row in problems.data["problems"]] == ["E11"]
    problem_id = problems.data["problems"][0]["id"]

    resolved = api_as_doctor.post(
        reverse("api:v1:problem-detail", args=[problem_id]),
        {"action": "resolve"},
        format="json",
    )
    assert resolved.status_code == 200
    assert resolved.data["status"] == "resolved"

    reopened = api_as_doctor.post(
        reverse("api:v1:problem-detail", args=[problem_id]),
        {"action": "reopen"},
        format="json",
    )
    assert reopened.data["status"] == "active"

    ruled_out = api_as_doctor.post(
        reverse("api:v1:diagnosis-detail", args=[added.data["id"]]),
        {"action": "rule_out", "note": "Fasting sugars normal on review."},
        format="json",
    )
    assert ruled_out.status_code == 200
    assert ruled_out.data["certainty"] == "ruled_out"


def test_an_unknown_action_says_so(api_as_doctor, hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnosis = diagnoses.add(encounter=encounter, code="B54", actor=doctor)

    response = api_as_doctor.post(
        reverse("api:v1:diagnosis-detail", args=[diagnosis.pk]),
        {"action": "obliterate"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == "unknown_action"


def test_a_nurse_may_read_the_problem_list_but_not_change_it(
    as_staff, hospital, department, doctor
) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="I10", certainty=Certainty.CONFIRMED, actor=doctor)

    nurse = hospital.staff("nurse")
    api = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)

    assert (
        api.get(reverse("api:v1:patient-problems", args=[encounter.patient_id])).status_code == 200
    )
    assert (
        api.post(
            reverse("api:v1:patient-problems", args=[encounter.patient_id]),
            {"code": "E11"},
            format="json",
        ).status_code
        == 403
    )
    assert (
        api.post(
            reverse("api:v1:encounter-diagnoses", args=[encounter.pk]),
            {"code": "E11"},
            format="json",
        ).status_code
        == 403
    )


def test_a_cashier_cannot_see_what_is_wrong_with_anybody(
    as_staff, hospital, department, doctor
) -> None:
    """The line the whole permission design is built to hold."""
    encounter = arrive(hospital, department)
    cashier = hospital.staff("cashier")
    api = as_staff(cashier, branch=hospital.main_branch, organisation=hospital.organisation)

    assert (
        api.get(reverse("api:v1:patient-problems", args=[encounter.patient_id])).status_code == 403
    )
    assert api.get(reverse("api:v1:icd-search"), {"q": "hiv"}).status_code == 403


def test_another_hospital_cannot_reach_the_problem_list(
    as_staff, hospital, other_hospital, department, doctor
) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        diagnoses.add(encounter=encounter, code="I10", certainty=Certainty.CONFIRMED, actor=doctor)

    outsider = other_hospital.staff("doctor")
    api = as_staff(
        outsider, branch=other_hospital.main_branch, organisation=other_hospital.organisation
    )
    response = api.get(reverse("api:v1:patient-problems", args=[encounter.patient_id]))
    assert response.status_code == 404


def test_coding_a_visit_is_audited(api_as_doctor, hospital, department) -> None:
    encounter = arrive(hospital, department)
    added = api_as_doctor.post(
        reverse("api:v1:encounter-diagnoses", args=[encounter.pk]),
        {"code": "B54", "certainty": "confirmed"},
        format="json",
    )

    with unscoped(reason="test: read the audit log"):
        assert AuditEvent.objects.filter(
            organisation=hospital.organisation,
            object_id=added.data["id"],
            action="clinical.diagnosis.record",
        ).exists()
