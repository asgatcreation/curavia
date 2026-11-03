"""
Consultation notes: drafting, signing, and the fact that a signature holds.

Every name here is synthetic.

Most of this file is about one property: **a signed note cannot change.**
That is the whole value of the feature. A clinical record that can be edited
after the fact is worth nothing in a case review, and the person it fails
first is the clinician who wrote it. So the suite attacks it from several
directions — through the service, through the model, and by writing to the
row directly the way a careless piece of future code would.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import StaffMembership
from apps.clinical import note_templates, notes
from apps.clinical.models import ClinicalNote, hash_content
from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.core.tenancy import unscoped
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
        "first_name": "Ngozi",
        "last_name": "Okafor",
        "sex": Sex.FEMALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1991, 2, 14),
    }
    defaults.update(overrides)
    with hospital.scope():
        return register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(**defaults),
        )


def arrive(hospital, department, **overrides) -> Encounter:
    patient = make_patient(hospital, **overrides)
    with hospital.scope():
        booking.check_in(patient=patient, department=department, branch=hospital.main_branch)
        return Encounter.objects.select_related("patient").get(
            patient=patient, status=Encounter.Status.OPEN
        )


@pytest.fixture
def doctor(hospital):
    """A doctor with the profile that prints under a signature."""
    user = hospital.staff("doctor", first_name="Folake", last_name="Ilesanmi")
    with unscoped(reason="test setup: fill in a clinician's signature details"):
        StaffMembership.unscoped.filter(organisation=hospital.organisation, user=user).update(
            qualifications="MBBS, FWACP",
            professional_registration="MDCN 54321",
            job_title="Consultant Family Physician",
        )
    return user


@pytest.fixture
def membership_of(hospital):
    def _get(user) -> StaffMembership:
        with unscoped(reason="test setup: read a membership back"):
            return StaffMembership.unscoped.get(organisation=hospital.organisation, user=user)

    return _get


SOAP_CONTENT = {
    "subjective": "Three days of frontal headache, worse in the mornings.",
    "objective": "Alert. No focal neurological deficit. Fundi normal.",
    "assessment": "Tension-type headache on a background of untreated hypertension.",
    "plan": "Start amlodipine 5 mg daily. Review in two weeks.",
}


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


def test_every_template_has_unique_section_keys() -> None:
    """
    A duplicate key inside one template would silently lose a section's
    wording, because the content is a dict keyed by it.
    """
    for template in note_templates.TEMPLATES:
        keys = [section.key for section in template.sections]
        assert len(keys) == len(set(keys)), template.key


def test_the_specialty_templates_are_all_there() -> None:
    keys = {template.key for template in note_templates.TEMPLATES}
    assert {"soap", "dental", "eye", "physiotherapy"} <= keys


def test_the_dental_chart_has_thirty_two_teeth() -> None:
    assert len(note_templates.ADULT_TEETH) == 32
    assert len(set(note_templates.ADULT_TEETH)) == 32
    # FDI notation: quadrant then tooth, so every number is two digits.
    assert all(len(tooth) == 2 for tooth in note_templates.ADULT_TEETH)


# --------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------


def test_opening_a_draft_twice_gives_the_same_draft(hospital, department, doctor) -> None:
    """
    A doctor who presses "write a note" twice, or has the screen open on two
    machines, must not end up with one consultation split in half.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        first = notes.open_draft(encounter=encounter, author=doctor)
        second = notes.open_draft(encounter=encounter, author=doctor)

    assert first.pk == second.pk
    assert first.version == 1
    assert first.thread == first.pk


def test_a_draft_keeps_only_the_sections_its_template_has(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        note = notes.autosave(
            note=note,
            content={**SOAP_CONTENT, "secret_field": "injected", "plan": "  Review.  "},
            actor=doctor,
        )

    assert "secret_field" not in note.content
    assert note.content["plan"] == "Review.", "whitespace is trimmed"


def test_an_untouched_section_stores_nothing(hospital, department, doctor) -> None:
    """
    Not an empty string. An eye examination with four sections left blank
    should store three words, not four empty boxes to render.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor, template_key="eye")
        note = notes.autosave(
            note=note,
            content={"complaint": "Blurred vision, left eye.", "fundus": "   ", "anterior": ""},
            actor=doctor,
        )

    assert note.content == {"complaint": "Blurred vision, left eye."}


def test_the_dental_chart_keeps_only_real_teeth(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor, template_key="dental")
        note = notes.autosave(
            note=note,
            content={"chart": {"16": "caries", "99": "caries", "36": "filled"}},
            actor=doctor,
        )

    assert note.content["chart"] == {"16": "caries", "36": "filled"}


def test_nobody_types_into_somebody_elses_draft(hospital, department, doctor) -> None:
    """
    A note is one clinician's statement of what they found. A colleague with
    something to add writes their own, which is also how it works on paper.
    """
    encounter = arrive(hospital, department)
    other = hospital.staff("doctor", first_name="Chidi", last_name="Eze")
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        with pytest.raises(notes.NoteError, match="somebody else's draft"):
            notes.autosave(note=note, content=SOAP_CONTENT, actor=other)


def test_a_draft_cannot_be_opened_on_a_closed_visit(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        Encounter.objects.filter(pk=encounter.pk).update(status=Encounter.Status.CLOSED)
        encounter.refresh_from_db()
        with pytest.raises(notes.NoteError, match="closed"):
            notes.open_draft(encounter=encounter, author=doctor)


def test_a_template_that_does_not_exist_is_refused(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope(), pytest.raises(notes.NoteError, match="no note template"):
        notes.open_draft(encounter=encounter, author=doctor, template_key="astrology")


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------


def test_signing_stamps_the_clinicians_details(hospital, department, doctor, membership_of) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        notes.autosave(note=note, content=SOAP_CONTENT, actor=doctor)
        signed = notes.sign(note=note, actor=doctor, membership=membership_of(doctor))

    assert signed.is_signed
    assert signed.signature_name == "Folake Ilesanmi"
    assert signed.signature_qualifications == "MBBS, FWACP"
    assert signed.signature_registration == "MDCN 54321"
    assert signed.signature_role == "Consultant Family Physician"
    assert signed.content_hash == hash_content(SOAP_CONTENT)

    block = signed.signature_block()
    assert "Folake Ilesanmi" in block
    assert "MDCN 54321" in block
    assert any(line.startswith("Reference ") for line in block)


def test_the_stamp_is_in_the_hospitals_own_clock(hospital, department, doctor) -> None:
    """
    Found in the browser: the stamp read 08:50 while the list beside it read
    09:50, because the stored time is UTC and Lagos is an hour ahead. An
    hour's disagreement on the face of a signed document is the kind of
    detail a lawyer reads out.
    """
    from django.utils import timezone

    signed = _signed_note(hospital, department, doctor)
    expected = timezone.localtime(signed.signed_at).strftime("Signed %d %b %Y at %H:%M")
    assert expected in signed.signature_block()


def test_a_draft_has_no_signature_block(hospital, department, doctor) -> None:
    """So that a screenshot of a draft cannot be passed off as a signed note."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        notes.autosave(note=note, content=SOAP_CONTENT, actor=doctor)
    assert note.signature_block() == []


def test_an_empty_note_cannot_be_signed(hospital, department, doctor) -> None:
    """
    A signature over nothing is worse than no note, because the record then
    says a clinician examined somebody and wrote down what they found.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        with pytest.raises(notes.NoteError, match="nothing in this note"):
            notes.sign(note=note, actor=doctor)


def test_only_the_author_signs(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    other = hospital.staff("doctor", first_name="Chidi", last_name="Eze")
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        notes.autosave(note=note, content=SOAP_CONTENT, actor=doctor)
        with pytest.raises(notes.NoteError, match="signed by the person who wrote it"):
            notes.sign(note=note, actor=other)


def test_signing_twice_is_refused(hospital, department, doctor) -> None:
    """Two clicks, two requests, one signature."""
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        notes.autosave(note=note, content=SOAP_CONTENT, actor=doctor)
        signed = notes.sign(note=note, actor=doctor)
        with pytest.raises(notes.NoteLocked):
            notes.sign(note=signed, actor=doctor)


def test_signing_without_a_profile_still_names_the_signer(hospital, department) -> None:
    """
    An incomplete profile must not stop a consultation being signed. The name
    is what matters; the registration number is printed when it is known.
    """
    locum = hospital.staff("doctor", first_name="Bola", last_name="Adeyemi")
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=locum)
        notes.autosave(note=note, content=SOAP_CONTENT, actor=locum)
        signed = notes.sign(note=note, actor=locum, membership=None)

    assert signed.signature_name == "Bola Adeyemi"
    assert signed.signature_registration == ""


# --------------------------------------------------------------------------
# The lock
# --------------------------------------------------------------------------


def _signed_note(hospital, department, doctor) -> ClinicalNote:
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        notes.autosave(note=note, content=SOAP_CONTENT, actor=doctor)
        return notes.sign(note=note, actor=doctor)


def test_a_signed_note_refuses_to_be_rewritten(hospital, department, doctor) -> None:
    signed = _signed_note(hospital, department, doctor)
    with hospital.scope(), pytest.raises(notes.NoteLocked, match="amendment"):
        notes.autosave(note=signed, content={"plan": "Something else."}, actor=doctor)


def test_the_model_itself_refuses_the_rewrite(hospital, department, doctor) -> None:
    """
    The guard that holds for code written later by somebody who has not read
    the service. A rule that lives in one function is a rule that gets walked
    around the first time anybody reaches for ``.save()``.
    """
    signed = _signed_note(hospital, department, doctor)
    with hospital.scope():
        signed.content = {"plan": "Rewritten after the complaint arrived."}
        with pytest.raises(ValidationError, match="signed"):
            signed.save()


def test_tampering_with_the_row_directly_is_detectable(hospital, department, doctor) -> None:
    """
    ``.update()`` goes round ``save()`` — it is SQL. So the hash is the
    backstop: whatever wrote to the row, the note no longer verifies.
    """
    signed = _signed_note(hospital, department, doctor)
    with hospital.scope():
        assert notes.verify(signed)
        ClinicalNote.objects.filter(pk=signed.pk).update(content={"plan": "Not what was signed."})
        signed.refresh_from_db()
        assert notes.verify(signed) is False


def test_a_signed_note_cannot_be_discarded(hospital, department, doctor) -> None:
    signed = _signed_note(hospital, department, doctor)
    with hospital.scope(), pytest.raises(notes.NoteLocked, match="Amend"):
        notes.discard_draft(note=signed, actor=doctor)


def test_a_discarded_draft_frees_the_slot(hospital, department, doctor) -> None:
    """
    Discarding archives rather than deletes — "a doctor started a note and
    abandoned it" is sometimes the question — and the next draft must still
    open.
    """
    encounter = arrive(hospital, department)
    with hospital.scope():
        first = notes.open_draft(encounter=encounter, author=doctor)
        notes.discard_draft(note=first, actor=doctor)
        second = notes.open_draft(encounter=encounter, author=doctor)
        remaining = notes.for_encounter(encounter, reader=doctor)

    assert second.pk != first.pk
    assert remaining == [second]


# --------------------------------------------------------------------------
# Amendments
# --------------------------------------------------------------------------


def test_an_amendment_is_a_new_version_and_leaves_the_original_alone(
    hospital, department, doctor, membership_of
) -> None:
    signed = _signed_note(hospital, department, doctor)
    original_hash = signed.content_hash

    with hospital.scope():
        amendment = notes.amend(
            note=signed,
            reason="The blood pressure in the plan was the wrong reading.",
            actor=doctor,
        )
        # Starts as a copy, so a sentence is corrected rather than a
        # consultation retyped.
        assert amendment.content == signed.content
        assert amendment.version == 2
        assert amendment.thread == signed.thread
        assert amendment.status == ClinicalNote.Status.DRAFT
        assert amendment.is_current is False, "an abandoned amendment must not replace the note"

        notes.autosave(
            note=amendment,
            content={**SOAP_CONTENT, "plan": "Start amlodipine 10 mg daily. Review in a week."},
            actor=doctor,
        )
        signed_amendment = notes.sign(
            note=amendment, actor=doctor, membership=membership_of(doctor)
        )
        signed.refresh_from_db()

    assert signed_amendment.is_current is True
    assert signed.is_current is False
    assert signed.content == SOAP_CONTENT, "the original wording is untouched"
    assert signed.content_hash == original_hash
    assert signed.is_signed, "and it keeps its own signature"

    with hospital.scope():
        versions = notes.history(signed)
    assert [note.version for note in versions] == [1, 2]
    assert versions[1].amendment_reason.startswith("The blood pressure")


def test_an_amendment_needs_a_reason(hospital, department, doctor) -> None:
    signed = _signed_note(hospital, department, doctor)
    with hospital.scope(), pytest.raises(notes.NoteError, match="why"):
        notes.amend(note=signed, reason="   ", actor=doctor)


def test_a_draft_is_edited_not_amended(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        note = notes.open_draft(encounter=encounter, author=doctor)
        with pytest.raises(notes.NoteError, match="still a draft"):
            notes.amend(note=note, reason="Because.", actor=doctor)


def test_only_one_unsigned_amendment_at_a_time(hospital, department, doctor) -> None:
    signed = _signed_note(hospital, department, doctor)
    with hospital.scope():
        notes.amend(note=signed, reason="First correction.", actor=doctor)
        with pytest.raises(notes.NoteError, match="already an unsigned amendment"):
            notes.amend(note=signed, reason="Second correction.", actor=doctor)


def test_a_colleague_may_amend_when_the_author_has_left(
    hospital, department, doctor, membership_of
) -> None:
    """
    The amendment carries the name of whoever signs it, never the original
    author's. ``clinical.note.amend`` is a separate permission for exactly
    this case.
    """
    signed = _signed_note(hospital, department, doctor)
    colleague = hospital.staff("doctor", first_name="Chidi", last_name="Eze")

    with hospital.scope():
        amendment = notes.amend(
            note=signed, reason="Author has left the hospital.", actor=colleague
        )
        notes.autosave(
            note=amendment, content={**SOAP_CONTENT, "plan": "Corrected."}, actor=colleague
        )
        signed_amendment = notes.sign(
            note=amendment, actor=colleague, membership=membership_of(colleague)
        )

    assert signed_amendment.signature_name == "Chidi Eze"
    assert signed_amendment.author_id == colleague.pk


# --------------------------------------------------------------------------
# Who can see what
# --------------------------------------------------------------------------


def test_other_peoples_drafts_are_not_in_the_list(hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)
    colleague = hospital.staff("doctor", first_name="Chidi", last_name="Eze")
    with hospital.scope():
        mine = notes.open_draft(encounter=encounter, author=doctor)
        notes.autosave(note=mine, content=SOAP_CONTENT, actor=doctor)
        theirs = notes.open_draft(encounter=encounter, author=colleague, template_key="follow_up")

        visible_to_me = notes.for_encounter(encounter, reader=doctor)
        visible_to_them = notes.for_encounter(encounter, reader=colleague)

    assert mine in visible_to_me and theirs not in visible_to_me
    assert theirs in visible_to_them and mine not in visible_to_them


def test_a_patients_history_is_signed_notes_only(hospital, department, doctor) -> None:
    signed = _signed_note(hospital, department, doctor)
    with hospital.scope():
        second = arrive(hospital, department, first_name="Ngozi", last_name="Okafor")
        draft = notes.open_draft(encounter=second, author=doctor)
        notes.autosave(note=draft, content=SOAP_CONTENT, actor=doctor)

        history = notes.for_patient(signed.patient)

    assert [note.pk for note in history] == [signed.pk]


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


@pytest.fixture
def api_as_doctor(as_staff, hospital, doctor):
    return as_staff(doctor, branch=hospital.main_branch, organisation=hospital.organisation)


def test_the_templates_are_served_with_the_dental_chart(api_as_doctor) -> None:
    response = api_as_doctor.get(reverse("api:v1:note-templates"))
    assert response.status_code == 200
    assert len(response.data["teeth"]) == 32
    assert "caries" in response.data["tooth_findings"]

    dental = next(item for item in response.data["templates"] if item["key"] == "dental")
    chart = next(section for section in dental["sections"] if section["key"] == "chart")
    assert chart["kind"] == "teeth"


def test_the_whole_round_trip_over_http(api_as_doctor, hospital, department, doctor) -> None:
    encounter = arrive(hospital, department)

    opened = api_as_doctor.post(
        reverse("api:v1:consultation-notes", args=[encounter.pk]),
        {"template_key": "soap"},
        format="json",
    )
    assert opened.status_code == 201
    note_id = opened.data["id"]
    assert opened.data["may_edit"] is True
    assert opened.data["signature"] == []

    saved = api_as_doctor.patch(
        reverse("api:v1:clinical-note", args=[note_id]),
        {"content": SOAP_CONTENT},
        format="json",
    )
    assert saved.status_code == 200
    assert saved.data["content"]["plan"].startswith("Start amlodipine")

    signed = api_as_doctor.post(reverse("api:v1:clinical-note-sign", args=[note_id]))
    assert signed.status_code == 200
    assert signed.data["status"] == "signed"
    assert signed.data["may_edit"] is False
    assert "MDCN 54321" in signed.data["signature"]

    # Signed, so a second save is refused with a sentence rather than a 500.
    blocked = api_as_doctor.patch(
        reverse("api:v1:clinical-note", args=[note_id]),
        {"content": {"plan": "Changed my mind."}},
        format="json",
    )
    assert blocked.status_code == 409
    assert blocked.data["code"] == "note_locked"

    amended = api_as_doctor.post(
        reverse("api:v1:clinical-note-amend", args=[note_id]),
        {"reason": "Dose corrected."},
        format="json",
    )
    assert amended.status_code == 201
    assert amended.data["version"] == 2

    read = api_as_doctor.get(reverse("api:v1:clinical-note", args=[note_id]))
    assert read.status_code == 200
    assert [version["version"] for version in read.data["versions"]] == [1, 2]


def test_a_nurse_may_read_a_note_but_not_write_one(as_staff, hospital, department, doctor) -> None:
    """
    The line the whole permission design is built to hold. ``.read``,
    ``.write`` and ``.sign`` are separate codes, and the nurse holds only
    the first.
    """
    signed = _signed_note(hospital, department, doctor)
    nurse = hospital.staff("nurse")
    api = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)

    assert (
        api.get(reverse("api:v1:consultation-notes", args=[signed.encounter_id])).status_code == 200
    )
    assert (
        api.post(
            reverse("api:v1:consultation-notes", args=[signed.encounter_id]), {}, format="json"
        ).status_code
        == 403
    )
    assert api.post(reverse("api:v1:clinical-note-sign", args=[signed.pk])).status_code == 403
    assert (
        api.post(
            reverse("api:v1:clinical-note-amend", args=[signed.pk]),
            {"reason": "Because."},
            format="json",
        ).status_code
        == 403
    )


def test_a_cashier_cannot_read_a_note_at_all(as_staff, hospital, department, doctor) -> None:
    signed = _signed_note(hospital, department, doctor)
    cashier = hospital.staff("cashier")
    api = as_staff(cashier, branch=hospital.main_branch, organisation=hospital.organisation)

    assert (
        api.get(reverse("api:v1:consultation-notes", args=[signed.encounter_id])).status_code == 403
    )


def test_a_sensitive_note_is_withheld_without_the_permission(
    as_staff, api_as_doctor, hospital, department, doctor
) -> None:
    """
    The note's existence is never hidden — a record with invisible entries is
    a record nobody can trust — only its wording.
    """
    encounter = arrive(hospital, department)
    opened = api_as_doctor.post(
        reverse("api:v1:consultation-notes", args=[encounter.pk]), {}, format="json"
    )
    note_id = opened.data["id"]
    api_as_doctor.patch(
        reverse("api:v1:clinical-note", args=[note_id]),
        {"content": SOAP_CONTENT, "is_sensitive": True},
        format="json",
    )
    api_as_doctor.post(reverse("api:v1:clinical-note-sign", args=[note_id]))

    nurse = hospital.staff("nurse")
    api = as_staff(nurse, branch=hospital.main_branch, organisation=hospital.organisation)
    response = api.get(reverse("api:v1:consultation-notes", args=[encounter.pk]))

    assert response.status_code == 200
    row = response.data["notes"][0]
    assert row["is_sensitive"] is True
    assert "subjective" not in row["content"]
    assert "_redacted" in row["content"]

    # The doctor, who holds clinical.sensitive.view, sees the words. Signed
    # back in deliberately: `as_staff` re-authenticates one shared client, so
    # the nurse above is now who `api_as_doctor` is pointing at.
    as_doctor = as_staff(doctor, branch=hospital.main_branch, organisation=hospital.organisation)
    mine = as_doctor.get(reverse("api:v1:consultation-notes", args=[encounter.pk]))
    assert mine.data["notes"][0]["content"]["subjective"].startswith("Three days")


def test_another_hospital_cannot_reach_the_note(
    as_staff, hospital, other_hospital, department, doctor
) -> None:
    signed = _signed_note(hospital, department, doctor)
    outsider = other_hospital.staff("doctor")
    api = as_staff(
        outsider, branch=other_hospital.main_branch, organisation=other_hospital.organisation
    )

    assert api.get(reverse("api:v1:clinical-note", args=[signed.pk])).status_code == 404


def test_an_unsigned_draft_is_not_readable_by_a_colleague(
    as_staff, hospital, department, doctor
) -> None:
    encounter = arrive(hospital, department)
    with hospital.scope():
        draft = notes.open_draft(encounter=encounter, author=doctor)

    colleague = hospital.staff("doctor", first_name="Chidi", last_name="Eze")
    api = as_staff(colleague, branch=hospital.main_branch, organisation=hospital.organisation)
    response = api.get(reverse("api:v1:clinical-note", args=[draft.pk]))

    assert response.status_code == 403
    assert response.data["code"] == "draft"


# --------------------------------------------------------------------------
# The audit log
# --------------------------------------------------------------------------


def test_signing_is_audited_and_autosaving_is_not(
    api_as_doctor, hospital, department, doctor
) -> None:
    """
    A doctor typing for twenty minutes would otherwise produce two hundred
    audit rows that say nothing, and burying the signature among them is how
    an audit log stops being usable.
    """
    encounter = arrive(hospital, department)
    note_id = api_as_doctor.post(
        reverse("api:v1:consultation-notes", args=[encounter.pk]), {}, format="json"
    ).data["id"]

    for words in ("First draft.", "Second draft.", "Third draft."):
        api_as_doctor.patch(
            reverse("api:v1:clinical-note", args=[note_id]),
            {"content": {"subjective": words}},
            format="json",
        )
    api_as_doctor.post(reverse("api:v1:clinical-note-sign", args=[note_id]))

    with unscoped(reason="test: read the audit log"):
        actions = list(
            AuditEvent.objects.filter(
                organisation=hospital.organisation, object_id=note_id
            ).values_list("action", flat=True)
        )

    assert actions.count("clinical.note.sign") == 1
    assert "clinical.note.start" in actions
    assert not any(action.startswith("clinical.note.autosave") for action in actions)


def test_reading_a_note_is_audited(api_as_doctor, hospital, department, doctor) -> None:
    """A chart is the clinical record, and every look at one is recorded."""
    signed = _signed_note(hospital, department, doctor)
    api_as_doctor.get(reverse("api:v1:clinical-note", args=[signed.pk]))

    with unscoped(reason="test: read the audit log"):
        assert AuditEvent.objects.filter(
            organisation=hospital.organisation,
            object_id=signed.pk,
            action__contains="view",
        ).exists()
