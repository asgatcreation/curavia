"""
Taking consent, and issuing documents.

Every rule about what may happen lives here rather than in a view, because
both the staff API and, later, the patient portal and the printing job need
the same answers, and a rule enforced in one view is a rule that is absent
from the other.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.documents import catalogue
from apps.documents.models import (
    Consent,
    ConsentMethod,
    ConsentStatus,
    Document,
    DocumentStatus,
    hash_content,
    new_reference,
    new_token,
)

logger = logging.getLogger("curavia.documents")


class DocumentError(Exception):
    """Refused for a reason worth showing the person who asked."""


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


def signature_for(*, user, membership=None) -> dict:
    """
    The lines that print under a signature, read now and stored.

    Falls back to the account's name alone when there is no membership: a
    document signed by somebody whose profile is incomplete should still be
    signed, with the name of whoever signed it, rather than refused.
    """
    name = (user.get_full_name() or user.email).strip() if user else ""
    return {
        "signature_name": name[:160],
        "signature_qualifications": (getattr(membership, "qualifications", "") or "")[:120],
        "signature_registration": (getattr(membership, "professional_registration", "") or "")[:60],
        "signature_role": (getattr(membership, "job_title", "") or "")[:120],
    }


@transaction.atomic
def take_consent(
    *,
    patient,
    form_key: str,
    given_by_name: str,
    actor,
    branch=None,
    encounter=None,
    method: str = ConsentMethod.TYPED,
    status: str = ConsentStatus.GIVEN,
    subject: str = "",
    given_by_relationship: str = "",
    witness_name: str = "",
    witness_role: str = "",
    refused_reason: str = "",
    extras: dict | None = None,
    membership=None,
) -> Consent:
    """
    Record that a patient was shown a form and what they said.

    The wording is copied onto the row here, and hashed, and from that
    moment the model's own ``save()`` refuses to let it change.
    """
    form = catalogue.consent_form(form_key)
    if form is None:
        raise DocumentError(f"There is no consent form called {form_key!r}.")
    if status not in ConsentStatus.values:
        raise DocumentError(f"There is no consent outcome called {status!r}.")
    if method not in ConsentMethod.values:
        raise DocumentError(f"There is no way of consenting called {method!r}.")

    given_by_name = given_by_name.strip()
    if not given_by_name:
        raise DocumentError("Write down who gave or refused this consent.")

    witness_name = witness_name.strip()
    # A thumbprint or a verbal agreement is only a record if somebody will
    # say they saw it. Without a witness it is the clinician's unsupported
    # word, which is the thing a consent form exists to avoid.
    if method in (ConsentMethod.THUMBPRINT, ConsentMethod.VERBAL) and not witness_name:
        raise DocumentError("A thumbprint or a verbal consent needs a witness's name.")

    if status == ConsentStatus.REFUSED and not refused_reason.strip():
        # Not a formality: "declined, wants to discuss with her husband" and
        # "declined, does not believe the test is accurate" lead the next
        # clinician to two different conversations.
        raise DocumentError("Write down what the patient said when they refused.")

    if form.key == "procedure" and not subject.strip():
        raise DocumentError("Name the operation or procedure being consented to.")

    wording = form.as_dict()
    signature = signature_for(user=actor, membership=membership)

    consent = Consent(
        organisation=patient.organisation,
        branch=branch or getattr(encounter, "branch", None) or patient.home_branch,
        patient=patient,
        encounter=encounter,
        form_key=form.key,
        form_version=form.version,
        wording=wording,
        status=status,
        method=method,
        subject=subject.strip()[:200],
        given_by_name=given_by_name[:200],
        given_by_relationship=given_by_relationship.strip()[:60],
        witness_name=witness_name[:200],
        witness_role=witness_role.strip()[:120],
        taken_by=actor,
        taken_by_name=signature["signature_name"],
        taken_by_role=signature["signature_role"],
        refused_reason=refused_reason.strip(),
        extras=extras or {},
        created_by=actor,
        updated_by=actor,
    )
    consent.content_hash = hash_content(wording)
    consent.save()
    logger.info("consent %s recorded for patient %s", form.key, patient.pk)
    return consent


@transaction.atomic
def withdraw_consent(*, consent: Consent, reason: str, actor=None) -> Consent:
    """
    The patient has changed their mind.

    The row stays and keeps its wording. Treatment given while the consent
    stood was consented to, and a record that erased the consent could not
    say so.
    """
    if consent.status != ConsentStatus.GIVEN:
        raise DocumentError("That consent was not given, so it cannot be withdrawn.")
    if consent.withdrawn_at is not None:
        raise DocumentError("That consent has already been withdrawn.")
    if not reason.strip():
        raise DocumentError("Write down what the patient said when they withdrew it.")

    consent.status = ConsentStatus.WITHDRAWN
    consent.withdrawn_at = timezone.now()
    consent.withdrawn_reason = reason.strip()
    consent.updated_by = actor
    consent.save(
        update_fields=["status", "withdrawn_at", "withdrawn_reason", "updated_by", "updated_at"]
    )
    return consent


def consents_for_patient(patient, *, form_key: str = "") -> list[Consent]:
    rows = Consent.objects.filter(patient=patient).select_related("taken_by")
    if form_key:
        rows = rows.filter(form_key=form_key)
    return list(rows)


def consents_for_encounter(encounter) -> list[Consent]:
    return list(Consent.objects.filter(encounter=encounter).select_related("taken_by"))


def active_consent(patient, form_key: str) -> Consent | None:
    """
    The one that currently stands, if any.

    Most recent first: a patient who refused in March and agreed in June has
    agreed, and a system that returned the refusal would block care they
    have since asked for.
    """
    return (
        Consent.objects.filter(
            patient=patient,
            form_key=form_key,
            status=ConsentStatus.GIVEN,
            withdrawn_at__isnull=True,
        )
        .order_by("-given_at")
        .first()
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _letterhead(branch, organisation) -> dict:
    """The hospital's details, frozen onto the document as they are today."""
    return {
        "hospital": organisation.name,
        "branch": branch.name if branch else "",
        "address": (getattr(branch, "address", "") or "").strip(),
        "state": getattr(branch, "state", "") or "",
        "lga": getattr(branch, "lga", "") or "",
        "phone": getattr(branch, "phone", "") or organisation.contact_phone,
        "email": organisation.contact_email,
    }


@transaction.atomic
def draft_document(
    *,
    patient,
    template_key: str,
    actor,
    branch=None,
    encounter=None,
    content: dict | None = None,
) -> Document:
    """Start one. Nothing is promised to anybody until it is issued."""
    template = catalogue.document_template(template_key)
    if template is None:
        raise DocumentError(f"There is no document called {template_key!r}.")

    branch = branch or getattr(encounter, "branch", None) or patient.home_branch
    document = Document(
        organisation=patient.organisation,
        branch=branch,
        patient=patient,
        encounter=encounter,
        template_key=template.key,
        template_version=template.version,
        content=_clean_content(template, content or {}),
        wording=template.as_dict(),
        letterhead=_letterhead(branch, patient.organisation),
        created_by=actor,
        updated_by=actor,
    )
    document.save()
    return document


def _clean_content(template: catalogue.DocumentTemplate, content: dict) -> dict:
    """
    Keep only the fields this template declares.

    A client that posts an extra key is not refused — it is dropped. The
    alternative is a document whose hash covers something nobody meant to
    put on it.
    """
    allowed = {key for key, _label in template.fields}
    return {key: str(value) for key, value in content.items() if key in allowed and value}


@transaction.atomic
def update_draft(*, document: Document, content: dict, actor=None) -> Document:
    if document.status != DocumentStatus.DRAFT:
        raise DocumentError("That document has been issued. Revoke it and write a new one.")
    template = catalogue.document_template(document.template_key)
    if template is None:  # pragma: no cover - a template removed under a draft
        raise DocumentError("That document's wording no longer exists.")

    document.content = _clean_content(template, content)
    document.updated_by = actor
    document.save(update_fields=["content", "updated_by", "updated_at"])
    return document


@transaction.atomic
def issue(*, document: Document, actor, membership=None) -> Document:
    """
    Sign it, give it a reference and a token, and let it out of the building.

    Everything is re-checked here. The screen's copy of what is required is
    for showing; this is what decides.
    """
    if document.status == DocumentStatus.ISSUED:
        raise DocumentError("That document has already been issued.")
    if document.status == DocumentStatus.REVOKED:
        raise DocumentError("That document was revoked. Write a new one.")

    template = catalogue.document_template(document.template_key)
    if template is None:  # pragma: no cover
        raise DocumentError("That document's wording no longer exists.")

    missing = [
        label
        for key, label in template.fields
        if key in template.required and not str(document.content.get(key, "")).strip()
    ]
    if missing:
        raise DocumentError("This cannot be issued yet. Still needed: " + ", ".join(missing) + ".")

    locked = Document.objects.select_for_update().get(pk=document.pk)
    if locked.status == DocumentStatus.ISSUED:  # two clicks, two requests
        raise DocumentError("That document has already been issued.")

    for field, value in signature_for(user=actor, membership=membership).items():
        setattr(locked, field, value)
    locked.status = DocumentStatus.ISSUED
    locked.issued_at = timezone.now()
    locked.issued_by = actor
    locked.token = new_token()
    locked.content_hash = hash_content({"content": locked.content, "wording": locked.wording})

    # A reference is eight characters from a 28-letter alphabet, so a clash
    # is remote — but "remote" on a unique column is an error page for a
    # clinician holding a patient's sick note, so it is retried rather than
    # reasoned about.
    for attempt in range(5):
        locked.reference = new_reference()
        try:
            with transaction.atomic():
                locked.save()
            break
        except IntegrityError:
            if attempt == 4:
                raise DocumentError(
                    "That document could not be given a reference. Try again."
                ) from None

    logger.info("document %s issued as %s", locked.template_key, locked.reference)
    return locked


@transaction.atomic
def revoke(*, document: Document, reason: str, actor=None) -> Document:
    """
    Pull it back.

    The row stays, and so does the token. Somebody is holding a printed
    copy, and the verification page has to be able to tell them it is no
    longer good — which it cannot do for a document that has been deleted.
    """
    if document.status != DocumentStatus.ISSUED:
        raise DocumentError("Only an issued document can be revoked.")
    if not reason.strip():
        raise DocumentError("Say why this document is being revoked.")

    document.status = DocumentStatus.REVOKED
    document.revoked_at = timezone.now()
    document.revoked_reason = reason.strip()
    document.updated_by = actor
    document.save(
        update_fields=["status", "revoked_at", "revoked_reason", "updated_by", "updated_at"]
    )
    return document


def documents_for_patient(patient, *, limit: int = 100) -> list[Document]:
    return list(Document.objects.filter(patient=patient).select_related("issued_by")[:limit])


def documents_for_encounter(encounter) -> list[Document]:
    return list(Document.objects.filter(encounter=encounter).select_related("issued_by"))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def masked_name(full_name: str) -> str:
    """
    Enough to check against the paper, not enough to identify a stranger.

    "Adebayo Okonkwo" becomes "Adebayo O.". Somebody holding the document
    can confirm it matches; somebody who has merely guessed a URL learns a
    first name, which the page is going to have to give up anyway for the
    check to be worth anything.
    """
    parts = [part for part in full_name.split() if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def verify(token: str) -> dict[str, Any] | None:
    """
    What the public page is allowed to say.

    Four facts and no diagnosis. An employer checking a sick note has a real
    interest in whether it is genuine and none at all in what is wrong with
    the person, and a page that gave up the diagnosis would leave every
    honest patient worse off than a forged note would have.
    """
    from apps.core.tenancy import unscoped

    with unscoped(reason="document verification: a stranger with a phone has no session"):
        document = Document.unscoped.filter(token=token).select_related("patient").first()

    if document is None or not document.token:
        return None

    template = catalogue.document_template(document.template_key)
    issued = timezone.localtime(document.issued_at) if document.issued_at else None
    return {
        "valid": document.is_valid,
        "status": document.status,
        "kind": document.name,
        "description": template.public_description if template else "",
        "reference": document.reference,
        "hospital": document.letterhead.get("hospital", ""),
        "branch": document.letterhead.get("branch", ""),
        "patient": masked_name(document.patient.full_name),
        "issued_on": issued.strftime("%d %B %Y") if issued else "",
        "signed_by": document.signature_name,
        "revoked": document.revoked_at is not None,
        "revoked_on": (
            timezone.localtime(document.revoked_at).strftime("%d %B %Y")
            if document.revoked_at
            else ""
        ),
    }


@transaction.atomic
def record_verification(token: str) -> None:
    """
    Count a check without recording who made it.

    How often a document is looked up is useful to the hospital. Who looked
    it up is a stranger with a phone, and keeping that would turn a
    verification page into a tracking service.
    """
    from apps.core.tenancy import unscoped

    with unscoped(reason="document verification: counting a public check"):
        Document.unscoped.filter(token=token).update(
            verified_count=F("verified_count") + 1, last_verified_at=timezone.now()
        )


__all__ = [
    "DocumentError",
    "active_consent",
    "consents_for_encounter",
    "consents_for_patient",
    "documents_for_encounter",
    "documents_for_patient",
    "draft_document",
    "issue",
    "masked_name",
    "record_verification",
    "revoke",
    "signature_for",
    "take_consent",
    "update_draft",
    "verify",
    "withdraw_consent",
]
