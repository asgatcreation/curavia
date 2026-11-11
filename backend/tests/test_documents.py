"""
Consent, and the paper the hospital signs.

Every name here is synthetic.

Two things this file is mostly about.

**A consent is a consent to particular words.** If the hospital can edit the
form afterwards, or the record renders today's wording instead of the one
the patient was shown, then the record proves nothing — and it proves
nothing in exactly the situation it exists for, which is somebody saying
"that is not what I agreed to".

**A sick note is forged in ten minutes in a text editor.** The QR code and
the public page are the answer, and the thing that makes them worth having
is what they refuse to say: an employer gets "yes, this hospital issued it,
and it still stands", and never the diagnosis.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.core.tenancy import unscoped
from apps.documents import catalogue
from apps.documents import service as documents
from apps.documents.models import (
    Consent,
    ConsentMethod,
    ConsentStatus,
    Document,
    DocumentStatus,
    hash_content,
    new_reference,
)
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


@pytest.fixture
def doctor(hospital):
    return hospital.staff("doctor", first_name="Folake", last_name="Ilesanmi")


@pytest.fixture
def clerk(hospital):
    return hospital.staff("front_desk", first_name="Amarachi", last_name="Eze")


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


# --------------------------------------------------------------------------
# The wordings
# --------------------------------------------------------------------------


def test_every_form_has_statements_and_a_declaration() -> None:
    for form in catalogue.CONSENT_FORMS:
        assert form.statements, f"{form.key} has nothing to read to the patient"
        assert form.declaration.strip(), f"{form.key} has nothing to agree to"


def test_form_keys_are_unique() -> None:
    keys = [form.key for form in catalogue.CONSENT_FORMS]
    assert len(keys) == len(set(keys))


def test_template_keys_are_unique() -> None:
    keys = [template.key for template in catalogue.DOCUMENT_TEMPLATES]
    assert len(keys) == len(set(keys))


def test_every_required_field_is_a_field_the_template_has() -> None:
    """A required field that is not on the form can never be filled in."""
    for template in catalogue.DOCUMENT_TEMPLATES:
        declared = {key for key, _label in template.fields}
        assert set(template.required) <= declared, template.key


def test_no_public_description_gives_away_a_diagnosis() -> None:
    """
    What the verification page says about a document is fixed here, so it is
    worth asserting that none of it is clinical.
    """
    for template in catalogue.DOCUMENT_TEMPLATES:
        text = template.public_description.lower()
        for leak in ("diagnos", "illness", "condition", "hiv", "positive"):
            assert leak not in text or "not shown" in text, template.key


# --------------------------------------------------------------------------
# Taking consent
# --------------------------------------------------------------------------


def test_the_wording_is_copied_onto_the_record(hospital, doctor) -> None:
    """
    Not referenced — copied.

    A consent that renders whatever the form says today is a consent to
    today's wording, which is not what the patient agreed to.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="data",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )

    form = catalogue.consent_form("data")
    assert form is not None
    assert consent.statements == list(form.statements)
    assert consent.declaration == form.declaration
    assert consent.content_hash == hash_content(form.as_dict())


def test_a_recorded_consent_cannot_be_reworded(hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="data",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )
        consent.wording = {**consent.wording, "declaration": "I agree to anything at all."}
        with pytest.raises(ValidationError):
            consent.save()


def test_a_refusal_is_recorded_as_a_refusal(hospital, doctor) -> None:
    """
    "Offered and declined" is a clinical fact the next clinician needs.

    A system that recorded consent only when it was given would make a
    refusal indistinguishable from nobody having asked.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="hiv_test",
            given_by_name="Adebayo Okonkwo",
            status=ConsentStatus.REFUSED,
            refused_reason="Wants to discuss it with his wife first.",
            actor=doctor,
        )

    assert consent.status == ConsentStatus.REFUSED
    assert consent.is_active is False
    assert "wife" in consent.refused_reason


def test_a_refusal_must_say_what_the_patient_said(hospital, doctor) -> None:
    """
    "Declined, wants to think about it" and "declined, does not believe the
    test works" lead the next clinician to two different conversations.
    """
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(documents.DocumentError, match="refused"):
        documents.take_consent(
            patient=patient,
            form_key="hiv_test",
            given_by_name="Adebayo Okonkwo",
            status=ConsentStatus.REFUSED,
            actor=doctor,
        )


def test_a_thumbprint_needs_a_witness(hospital, doctor) -> None:
    """
    Without one it is the clinician's unsupported word, which is the thing a
    consent form exists to avoid.
    """
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(documents.DocumentError, match="witness"):
        documents.take_consent(
            patient=patient,
            form_key="treatment",
            given_by_name="Adebayo Okonkwo",
            method=ConsentMethod.THUMBPRINT,
            actor=doctor,
        )


def test_a_thumbprint_with_a_witness_is_accepted(hospital, doctor) -> None:
    """
    A large share of patients in a Nigerian general outpatient cannot write
    their name. A form that offers them only a signature box produces a
    forged signature or no consent at all.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="treatment",
            given_by_name="Adebayo Okonkwo",
            method=ConsentMethod.THUMBPRINT,
            witness_name="Amarachi Eze",
            witness_role="Staff nurse",
            actor=doctor,
        )

    assert consent.is_active
    assert consent.witness_name == "Amarachi Eze"


def test_consent_to_a_procedure_must_name_the_procedure(hospital, doctor) -> None:
    """ "Consent to a procedure" with no procedure named is worthless."""
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(documents.DocumentError, match=r"[Nn]ame the operation"):
        documents.take_consent(
            patient=patient,
            form_key="procedure",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )


def test_somebody_else_can_consent_for_the_patient(hospital, doctor) -> None:
    patient = make_patient(hospital, first_name="Chidinma", date_of_birth=dt.date(2021, 7, 9))
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="treatment",
            given_by_name="Ngozi Okonkwo",
            given_by_relationship="Mother",
            actor=doctor,
        )

    assert consent.given_by_name == "Ngozi Okonkwo"
    assert consent.given_by_relationship == "Mother"


def test_an_unknown_form_is_refused(hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(documents.DocumentError, match="no consent form"):
        documents.take_consent(
            patient=patient,
            form_key="organ_donation",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )


def test_withdrawing_keeps_the_row_and_the_words(hospital, doctor) -> None:
    """
    Treatment given while the consent stood was consented to. A record that
    erased the consent could not say so.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="photography",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )
        words = list(consent.statements)
        documents.withdraw_consent(
            consent=consent, reason="No longer wants photographs kept.", actor=doctor
        )
        consent.refresh_from_db()

    assert consent.status == ConsentStatus.WITHDRAWN
    assert consent.is_active is False
    assert consent.statements == words
    assert "photographs" in consent.withdrawn_reason


def test_withdrawing_needs_a_reason(hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="data",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )
        with pytest.raises(documents.DocumentError, match="withdrew"):
            documents.withdraw_consent(consent=consent, reason="   ", actor=doctor)


def test_a_refusal_cannot_be_withdrawn(hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="hiv_test",
            given_by_name="Adebayo Okonkwo",
            status=ConsentStatus.REFUSED,
            refused_reason="Not today.",
            actor=doctor,
        )
        with pytest.raises(documents.DocumentError, match="not given"):
            documents.withdraw_consent(consent=consent, reason="Changed.", actor=doctor)


def test_the_latest_answer_is_the_one_that_stands(hospital, doctor) -> None:
    """
    A patient who refused in March and agreed in June has agreed. Returning
    the refusal would block care they have since asked for.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        documents.take_consent(
            patient=patient,
            form_key="hiv_test",
            given_by_name="Adebayo Okonkwo",
            status=ConsentStatus.REFUSED,
            refused_reason="Not today.",
            actor=doctor,
        )
        later = documents.take_consent(
            patient=patient,
            form_key="hiv_test",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )
        standing = documents.active_consent(patient, "hiv_test")

    assert standing is not None
    assert standing.pk == later.pk


def test_a_withdrawn_consent_no_longer_stands(hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="photography",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )
        documents.withdraw_consent(consent=consent, reason="Changed her mind.", actor=doctor)
        assert documents.active_consent(patient, "photography") is None


def test_the_signature_is_the_taker_not_the_patient(hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        consent = documents.take_consent(
            patient=patient,
            form_key="data",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )

    assert consent.taken_by_name == "Folake Ilesanmi"
    assert consent.given_by_name == "Adebayo Okonkwo"


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


def _sick_note(hospital, doctor, department, **content):
    encounter = arrive(hospital, department)
    body = {"from_date": "3 November 2026", "to_date": "7 November 2026"}
    body.update(content)
    with hospital.scope():
        return encounter, documents.draft_document(
            patient=encounter.patient,
            encounter=encounter,
            template_key="sick_note",
            content=body,
            actor=doctor,
        )


def test_a_draft_has_no_reference_and_no_signature(hospital, doctor, department) -> None:
    """A draft must not be made to look issued by a screenshot."""
    _, document = _sick_note(hospital, doctor, department)

    assert document.status == DocumentStatus.DRAFT
    assert document.reference == ""
    assert document.token == ""
    assert document.signature_block() == []


def test_issuing_gives_it_a_reference_a_token_and_a_name(hospital, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    assert document.status == DocumentStatus.ISSUED
    assert len(document.reference) == 9  # XXXX-XXXX
    assert document.token
    assert document.is_valid
    assert "Folake Ilesanmi" in document.signature_block()[0]


def test_a_reference_avoids_the_letters_people_misread() -> None:
    """
    It is read off a photocopy down a bad telephone line. O/0, I/1 and S/5
    are where that goes wrong.
    """
    for _ in range(200):
        reference = new_reference()
        assert not set(reference) & set("O0I1S5BZ")


def test_a_document_cannot_be_issued_without_what_it_needs(hospital, doctor, department) -> None:
    """A sick note with no dates on it is not a sick note."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        document = documents.draft_document(
            patient=encounter.patient,
            encounter=encounter,
            template_key="sick_note",
            content={"advice": "Light duties."},
            actor=doctor,
        )
        with pytest.raises(documents.DocumentError, match="Unfit from"):
            documents.issue(document=document, actor=doctor)


def test_an_issued_document_cannot_be_reworded(hospital, doctor, department) -> None:
    """
    Somebody is holding a printed copy. A document that can be quietly
    rewritten after a dispute is not evidence.
    """
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)
        document.content = {**document.content, "to_date": "30 November 2026"}
        with pytest.raises(ValidationError):
            document.save()


def test_an_issued_document_cannot_be_edited_through_the_service(
    hospital, doctor, department
) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)
        with pytest.raises(documents.DocumentError, match="Revoke it"):
            documents.update_draft(
                document=document, content={"to_date": "30 November 2026"}, actor=doctor
            )


def test_issuing_twice_is_refused(hospital, doctor, department) -> None:
    """Two clicks, two requests, one certificate."""
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)
        with pytest.raises(documents.DocumentError, match="already been issued"):
            documents.issue(document=document, actor=doctor)


def test_a_field_the_template_does_not_have_is_dropped(hospital, doctor, department) -> None:
    """
    Otherwise the hash covers something nobody meant to put on the document.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        document = documents.draft_document(
            patient=encounter.patient,
            encounter=encounter,
            template_key="sick_note",
            content={"from_date": "3 November 2026", "diagnosis": "HIV"},
            actor=doctor,
        )

    assert "diagnosis" not in document.content
    assert document.content["from_date"] == "3 November 2026"


def test_revoking_keeps_the_row_and_the_token(hospital, doctor, department) -> None:
    """
    Somebody is holding a printed copy. The verification page has to be able
    to tell them it is no longer good, which it cannot do for a deleted row.
    """
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)
        token = document.token
        documents.revoke(document=document, reason="Issued to the wrong patient.", actor=doctor)
        document.refresh_from_db()

    assert document.status == DocumentStatus.REVOKED
    assert document.token == token
    assert document.is_valid is False

    found = documents.verify(token)
    assert found is not None
    assert found["valid"] is False
    assert found["revoked"] is True


def test_revoking_needs_a_reason(hospital, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)
        with pytest.raises(documents.DocumentError, match="why"):
            documents.revoke(document=document, reason="", actor=doctor)


def test_a_draft_cannot_be_revoked(hospital, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope(), pytest.raises(documents.DocumentError, match="issued document"):
        documents.revoke(document=document, reason="Mistake.", actor=doctor)


def test_the_letterhead_is_frozen_at_issue(hospital, doctor, department) -> None:
    """
    A branch that moves premises next year must not change the address on a
    document issued from the old one.
    """
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)
        branch = hospital.main_branch
        branch.address = "Somewhere else entirely"
        branch.save(update_fields=["address"])
        document.refresh_from_db()

    assert document.letterhead["address"] != "Somewhere else entirely"
    assert document.letterhead["hospital"] == hospital.organisation.name


def test_the_stamp_is_in_the_hospitals_own_clock(hospital, doctor, department) -> None:
    """
    The same hour's disagreement that was caught on the clinical note: a
    stored datetime is UTC, and only localtime puts it back into Lagos.
    """
    from django.utils import timezone

    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    local = timezone.localtime(document.issued_at)
    assert f"{local:%H:%M}" in " ".join(document.signature_block())


# --------------------------------------------------------------------------
# What a stranger with a phone may see
# --------------------------------------------------------------------------


def test_verification_never_gives_up_the_diagnosis(hospital, doctor, department) -> None:
    """
    The whole point. An employer checking a sick note has a real interest in
    whether it is genuine and none at all in what is wrong with the person.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        document = documents.draft_document(
            patient=encounter.patient,
            encounter=encounter,
            template_key="medical_report",
            content={
                "history": "Three days of fever.",
                "diagnosis": "Retroviral disease, newly diagnosed.",
                "treatment": "Started on antiretrovirals.",
            },
            actor=doctor,
        )
        document = documents.issue(document=document, actor=doctor)

    found = documents.verify(document.token)
    assert found is not None
    blob = " ".join(str(value).lower() for value in found.values())
    for leak in ("retroviral", "antiretroviral", "fever", "three days"):
        assert leak not in blob


def test_verification_says_enough_to_be_worth_something(hospital, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    found = documents.verify(document.token)
    assert found is not None
    assert found["valid"] is True
    assert found["reference"] == document.reference
    assert found["hospital"] == hospital.organisation.name
    assert found["signed_by"] == "Folake Ilesanmi"
    assert found["issued_on"]


def test_the_patients_name_is_masked(hospital, doctor, department) -> None:
    """
    Enough for somebody holding the paper to check it matches; not enough
    for somebody who guessed a URL to identify a stranger.
    """
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    found = documents.verify(document.token)
    assert found is not None
    assert found["patient"] == "Adebayo O."
    assert "Okonkwo" not in found["patient"]


@pytest.mark.parametrize(
    ("full", "masked"),
    [
        ("Adebayo Okonkwo", "Adebayo O."),
        ("Ngozi", "Ngozi"),
        ("Folake Adebisi Ilesanmi", "Folake I."),
        ("", ""),
    ],
)
def test_masking_handles_the_names_that_actually_turn_up(full: str, masked: str) -> None:
    assert documents.masked_name(full) == masked


def test_a_draft_token_verifies_as_not_issued(hospital, doctor, department) -> None:
    """A draft has no token at all, so there is nothing to look up."""
    _sick_note(hospital, doctor, department)
    assert documents.verify("") is None
    assert documents.verify("nonsense-token") is None


def test_verification_is_counted_but_the_checker_is_not_recorded(
    hospital, doctor, department
) -> None:
    """
    How often a document is checked is useful. Who checked it is a stranger
    with a phone, and keeping that turns this into a tracking service.
    """
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    documents.record_verification(document.token)
    documents.record_verification(document.token)
    with unscoped(reason="test: read the counter back"):
        fresh = Document.objects.get(pk=document.pk)

    assert fresh.verified_count == 2
    assert fresh.last_verified_at is not None
    assert not hasattr(fresh, "verified_by")


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


def test_the_whole_round_trip_over_http(hospital, api, as_staff, doctor, department) -> None:
    encounter = arrive(hospital, department)
    as_staff(doctor, branch=hospital.main_branch)

    taken = api.post(
        reverse("api:v1:patient-consents", args=[encounter.patient_id]),
        {
            "form_key": "treatment",
            "given_by_name": "Adebayo Okonkwo",
            "encounter_id": str(encounter.pk),
        },
        format="json",
    )
    assert taken.status_code == 201, taken.data
    assert taken.data["is_active"] is True

    drafted = api.post(
        reverse("api:v1:patient-documents", args=[encounter.patient_id]),
        {
            "template_key": "sick_note",
            "encounter_id": str(encounter.pk),
            "content": {"from_date": "3 November 2026", "to_date": "7 November 2026"},
        },
        format="json",
    )
    assert drafted.status_code == 201, drafted.data
    assert drafted.data["reference"] == ""

    issued = api.post(reverse("api:v1:document-issue", args=[drafted.data["id"]]))
    assert issued.status_code == 200, issued.data
    assert issued.data["still_valid"] is True
    assert issued.data["verify_url"].endswith("/")

    paperwork = api.get(reverse("api:v1:encounter-paperwork", args=[encounter.pk]))
    assert paperwork.status_code == 200
    assert len(paperwork.data["consents"]) == 1
    assert len(paperwork.data["documents"]) == 1


def test_the_public_check_needs_no_account(hospital, api, as_staff, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    api.force_authenticate(user=None)
    response = api.get(reverse("api:v1:document-verify-api", args=[document.token]))
    assert response.status_code == 200, response.data
    assert response.data["valid"] is True


def test_the_public_page_renders_for_a_stranger(hospital, client, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    response = client.get(reverse("documents:verify", args=[document.token]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "genuine" in body
    assert document.reference in body
    assert "Okonkwo" not in body


def test_an_unknown_code_is_not_a_crash(hospital, client) -> None:
    response = client.get(reverse("documents:verify", args=["definitely-not-a-token"]))
    assert response.status_code == 404
    assert "No document with that code" in response.content.decode()


def test_the_printable_page_carries_its_own_qr_code(hospital, client, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    response = client.get(reverse("documents:printable", args=[document.token]))
    assert response.status_code == 200
    body = response.content.decode()
    # Inline SVG, so a laptop with no network prints the same page.
    assert "<svg" in body
    assert document.reference in body
    assert "Adebayo Okonkwo" in body


def test_a_field_the_hospital_does_not_know_is_left_off(
    hospital, client, doctor, department
) -> None:
    """
    A certificate that prints "Sex: Unknown" is a certificate an employer
    looks at twice. The row comes off instead.
    """
    patient = make_patient(hospital, sex=Sex.UNKNOWN)
    encounter = arrive(hospital, department, patient)
    with hospital.scope():
        document = documents.draft_document(
            patient=patient,
            encounter=encounter,
            template_key="sick_note",
            content={"from_date": "3 November 2026", "to_date": "7 November 2026"},
            actor=doctor,
        )
        document = documents.issue(document=document, actor=doctor)

    body = client.get(reverse("documents:printable", args=[document.token])).content.decode()
    assert "Unknown" not in body
    assert "Hospital number" in body


def test_a_draft_cannot_be_printed(hospital, client, doctor, department) -> None:
    """Printing a draft would produce a document that looks signed."""
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document.token = "a-token-somebody-guessed"
        document.save(update_fields=["token"])

    response = client.get(reverse("documents:printable", args=[document.token]))
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Who may do what
# --------------------------------------------------------------------------


def test_a_clerk_may_take_consent_but_not_issue_a_certificate(
    hospital, api, as_staff, clerk, doctor, department
) -> None:
    """
    Registration asks about record keeping, so a clerk must be able to
    record the answer. Signing a certificate puts a registration number on
    paper that leaves the building, so a clerk must not.
    """
    encounter = arrive(hospital, department)
    as_staff(clerk, branch=hospital.main_branch)

    taken = api.post(
        reverse("api:v1:patient-consents", args=[encounter.patient_id]),
        {"form_key": "data", "given_by_name": "Adebayo Okonkwo"},
        format="json",
    )
    assert taken.status_code == 201, taken.data

    drafted = api.post(
        reverse("api:v1:patient-documents", args=[encounter.patient_id]),
        {"template_key": "sick_note", "content": {"from_date": "3 Nov", "to_date": "7 Nov"}},
        format="json",
    )
    assert drafted.status_code == 403


def test_a_clerk_may_not_issue_a_document_somebody_else_drafted(
    hospital, api, as_staff, clerk, doctor, department
) -> None:
    _, document = _sick_note(hospital, doctor, department)
    as_staff(clerk, branch=hospital.main_branch)

    response = api.post(reverse("api:v1:document-issue", args=[document.pk]))
    assert response.status_code == 403


def test_another_hospital_cannot_reach_the_paperwork(
    hospital, other_hospital, api, as_staff, doctor, department
) -> None:
    _, document = _sick_note(hospital, doctor, department)
    with hospital.scope():
        document = documents.issue(document=document, actor=doctor)

    intruder = other_hospital.staff("doctor", first_name="Bola", last_name="Adeyemi")
    as_staff(intruder, branch=other_hospital.main_branch, organisation=other_hospital.organisation)

    response = api.get(reverse("api:v1:document-detail", args=[document.pk]))
    assert response.status_code == 404


def test_issuing_and_revoking_are_audited(hospital, api, as_staff, doctor, department) -> None:
    _, document = _sick_note(hospital, doctor, department)
    as_staff(doctor, branch=hospital.main_branch)

    assert api.post(reverse("api:v1:document-issue", args=[document.pk])).status_code == 200
    revoked = api.post(
        reverse("api:v1:document-revoke", args=[document.pk]),
        {"reason": "Wrong dates."},
        format="json",
    )
    assert revoked.status_code == 200, revoked.data

    with unscoped(reason="test: read the audit log"):
        actions = set(
            AuditEvent.objects.filter(object_id=document.pk).values_list("action", flat=True)
        )
    assert {"document.issue", "document.revoke"} <= actions


def test_taking_consent_is_audited(hospital, api, as_staff, doctor, department) -> None:
    encounter = arrive(hospital, department)
    as_staff(doctor, branch=hospital.main_branch)

    taken = api.post(
        reverse("api:v1:patient-consents", args=[encounter.patient_id]),
        {"form_key": "data", "given_by_name": "Adebayo Okonkwo"},
        format="json",
    )
    assert taken.status_code == 201

    with unscoped(reason="test: read the audit log"):
        assert AuditEvent.objects.filter(action="consent.take").exists()


def test_the_consent_catalogue_is_readable_by_a_nurse(hospital, api, as_staff) -> None:
    nurse = hospital.staff("nurse", first_name="Hauwa", last_name="Bello")
    as_staff(nurse, branch=hospital.main_branch)

    response = api.get(reverse("api:v1:consent-forms"))
    assert response.status_code == 200
    assert {form["key"] for form in response.data["forms"]} >= {"treatment", "data"}


def test_a_consent_belongs_to_one_hospital(hospital, other_hospital, doctor) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        documents.take_consent(
            patient=patient,
            form_key="data",
            given_by_name="Adebayo Okonkwo",
            actor=doctor,
        )

    with other_hospital.scope():
        assert Consent.objects.count() == 0
