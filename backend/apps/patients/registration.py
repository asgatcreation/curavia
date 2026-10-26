"""
Registering a patient.

One function, one transaction, because a patient with an MRN but no record —
or a record with no MRN — is worse than no patient at all. The MRN is spoken
aloud and written on a card the moment it is issued.

**Emergency quick-registration is the same function with less of it filled
in.** An unconscious man brought in by a neighbour gets a record in ten
seconds, flagged provisional, under a name the system invents and everyone
understands is temporary. The alternative hospitals actually use is treating
him with no record at all and writing the notes up afterwards from memory,
which is how a drug given in resus never reaches the chart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.models import Branch, Organisation
from apps.patients.models import (
    Allergy,
    NextOfKin,
    Patient,
    PatientIdentifier,
    Sex,
    next_medical_record_number,
)


class RegistrationError(Exception):
    """Something was wrong with what was sent."""


@dataclass(slots=True)
class RegistrationInput:
    first_name: str = ""
    last_name: str = ""
    other_names: str = ""
    sex: str = Sex.UNKNOWN
    date_of_birth: date | None = None
    date_of_birth_is_estimated: bool = False
    phone: str = ""
    alternate_phone: str = ""
    email: str = ""
    address: str = ""
    state: str = ""
    lga: str = ""
    blood_group: str = ""
    genotype: str = ""
    marital_status: str = ""
    occupation: str = ""
    default_payer_id: str | None = None
    identifiers: list[dict[str, Any]] = field(default_factory=list)
    next_of_kin: list[dict[str, Any]] = field(default_factory=list)
    allergies: list[dict[str, Any]] = field(default_factory=list)
    #: True when somebody has asked about allergies, whatever the answer.
    #: Distinct from the list being empty. See Patient.
    allergies_checked: bool = False
    notes: str = ""


@transaction.atomic
def register_patient(
    *,
    organisation: Organisation,
    branch: Branch,
    data: RegistrationInput,
    registered_by=None,
    provisional: bool = False,
) -> Patient:
    """
    Create a patient and everything that came with them.

    ``branch`` is where they are registering, which decides the MRN prefix
    and becomes their home branch. It is not a restriction on where they can
    be seen — a patient belongs to the hospital, not the building.
    """
    first_name = data.first_name.strip()
    last_name = data.last_name.strip()
    if not provisional and (not first_name or not last_name):
        raise RegistrationError("A patient needs a first name and a surname.")

    patient = Patient(
        organisation=organisation,
        mrn=next_medical_record_number(organisation.pk, branch.code),
        first_name=first_name,
        last_name=last_name,
        other_names=data.other_names.strip(),
        sex=data.sex or Sex.UNKNOWN,
        date_of_birth=data.date_of_birth,
        date_of_birth_is_estimated=data.date_of_birth_is_estimated,
        phone=data.phone.strip(),
        alternate_phone=data.alternate_phone.strip(),
        email=data.email.strip(),
        address=data.address.strip(),
        state=data.state.strip(),
        lga=data.lga.strip(),
        blood_group=data.blood_group,
        genotype=data.genotype,
        marital_status=data.marital_status,
        occupation=data.occupation.strip(),
        home_branch=branch,
        default_payer_id=data.default_payer_id,
        is_provisional=provisional,
        registered_by=registered_by,
        notes=data.notes.strip(),
        allergies_checked_at=timezone.now() if data.allergies_checked else None,
    )
    patient.full_clean(exclude=["search_vector", "search_text", "organisation"])
    patient.save()

    for row in data.identifiers:
        value = str(row.get("value", "")).strip()
        if not value:
            continue
        identifier = PatientIdentifier(
            organisation=organisation,
            patient=patient,
            kind=row.get("kind") or PatientIdentifier.Kind.OTHER,
            value=value,
            payer_id=row.get("payer_id") or None,
            note=str(row.get("note", ""))[:160],
        )
        identifier.full_clean(exclude=["organisation", "patient"])
        identifier.save()

    for index, row in enumerate(data.next_of_kin):
        name = str(row.get("name", "")).strip()
        phone = str(row.get("phone", "")).strip()
        if not name or not phone:
            continue
        NextOfKin.objects.create(
            organisation=organisation,
            patient=patient,
            name=name,
            relationship=str(row.get("relationship", "")).strip()[:60],
            phone=phone,
            alternate_phone=str(row.get("alternate_phone", "")).strip(),
            address=str(row.get("address", "")).strip(),
            is_primary=index == 0,
        )

    for row in data.allergies:
        substance = str(row.get("substance", "")).strip()
        if not substance:
            continue
        Allergy.objects.update_or_create(
            organisation=organisation,
            patient=patient,
            substance=substance,
            defaults={
                "kind": row.get("kind") or Allergy.Kind.DRUG,
                "reaction": str(row.get("reaction", ""))[:200],
                "severity": row.get("severity") or Allergy.Severity.MODERATE,
                "recorded_by": registered_by,
            },
        )

    # Recording any allergy is itself an allergy history having been taken.
    if data.allergies and patient.allergies_checked_at is None:
        patient.allergies_checked_at = timezone.now()
        patient.save(update_fields=["allergies_checked_at"])

    return patient


def register_unknown_patient(
    *,
    organisation: Organisation,
    branch: Branch,
    sex: str = Sex.UNKNOWN,
    estimated_age: int | None = None,
    note: str = "",
    registered_by=None,
) -> Patient:
    """
    Emergency quick-registration: a record in ten seconds.

    The name is generated — "Unknown Male 3" — and is deliberately obviously
    temporary, so nobody files it as real. The record is flagged provisional,
    which is what the merge tool and every "unidentified patients" list key
    on later.

    An estimated age is taken rather than a date of birth because that is
    what a paramedic can give, and it is stored as an estimate so a drug dose
    calculated from it carries the uncertainty with it.
    """
    # Keys as plain strings: `sex` arrives from a serialiser as a str, and a
    # dict keyed by the enum members would miss every lookup at runtime while
    # looking correct.
    label = {Sex.FEMALE.value: "Female", Sex.MALE.value: "Male"}.get(sex, "Patient")
    sequence = Patient.objects.filter(is_provisional=True).count() + 1

    date_of_birth = None
    if estimated_age is not None and 0 <= estimated_age < 130:
        today = timezone.localdate()
        date_of_birth = date(today.year - estimated_age, 1, 1)

    data = RegistrationInput(
        first_name="Unknown",
        last_name=f"{label} {sequence}",
        sex=sex,
        date_of_birth=date_of_birth,
        date_of_birth_is_estimated=date_of_birth is not None,
        notes=note,
    )
    return register_patient(
        organisation=organisation,
        branch=branch,
        data=data,
        registered_by=registered_by,
        provisional=True,
    )
