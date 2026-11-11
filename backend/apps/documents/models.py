"""
Paper that crosses the hospital's boundary.

Two models, one app, because they are the same problem in opposite
directions. A consent form is a document the patient signs on the way in; a
sick note is a document the hospital signs on the way out. Both need a
wording fixed before anybody saw it, a verbatim copy of that wording taken
at the moment of signing, a hash over the copy, a short reference a human
can read aloud, and a rule that says a signed thing never changes. Building
that twice would have meant two subtly different versions of the only part
that matters.

**The snapshot is the record.** Neither model points at its wording in
``catalogue.py`` and reads it back later. Both copy the sentences onto the
row. A consent that renders today's wording is a consent to whatever the
form says today, which is not what the patient agreed to, and a sick note
that re-renders is a note whose meaning an employer cannot pin down.

**Nothing signed is edited.** ``save()`` on both models refuses to persist a
signed row whose content no longer matches its hash — the same guard
``ClinicalNote`` uses, for the same reason. A document that can be quietly
rewritten after a dispute is not evidence, and the person it protects first
is the clinician who signed it.

**Withdrawal and revocation are events, not deletions.** A consent the
patient later withdrew is not a consent that never happened: the treatment
given while it stood was lawful, and the record has to be able to say both
things. Same for a certificate the hospital pulls back — the copy in
somebody's hand still exists, so the verification page has to be able to
say "this was issued, and it has since been revoked", which it cannot do if
the row is gone.
"""

from __future__ import annotations

import hashlib
import json
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BranchScoped

#: Characters a reference is built from. No O, 0, I, 1 or 5/S: a reference
#: exists to be read off a printed page over a bad phone line, and those are
#: the pairs people get wrong.
_REFERENCE_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY2346789"


def hash_content(content: dict | None) -> str:
    """
    A stable fingerprint of what a document says.

    Canonical JSON — keys sorted, no insignificant whitespace — so the same
    content hashes the same however the client ordered its fields, and so a
    hash computed years later still matches. Identical to the clinical
    note's, deliberately: a reader should not have to work out which of two
    hashing schemes produced a given reference.
    """
    canonical = json.dumps(content or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_reference() -> str:
    """Eight characters, in two groups of four, e.g. ``K7PQ-3MXD``."""
    picked = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(8))
    return f"{picked[:4]}-{picked[4:]}"


def new_token() -> str:
    """
    What goes in the QR code.

    Long and random rather than derived from the reference, because the
    reference is printed in a readable font on a document people photocopy
    and the token is the whole of the authorisation for the public
    verification page. Guessing one must not be possible from seeing another.
    """
    return secrets.token_urlsafe(24)


class ConsentMethod(models.TextChoices):
    """
    How the agreement was given.

    ``THUMBPRINT`` is not a fallback for a system that forgot about it. A
    large share of patients in a Nigerian general outpatient department
    cannot write their name, and a form that offers them only a signature
    box produces either a forged signature or no consent at all. A
    thumbprint with a named witness is the honest record of what happened,
    and it is what the paper form in the same clinic has always used.
    """

    SIGNED = "signed", _("Signed on screen")
    TYPED = "typed", _("Name typed by the patient")
    THUMBPRINT = "thumbprint", _("Thumbprint, with a witness")
    VERBAL = "verbal", _("Given verbally, with a witness")


class ConsentStatus(models.TextChoices):
    GIVEN = "given", _("Given")
    REFUSED = "refused", _("Refused")
    WITHDRAWN = "withdrawn", _("Withdrawn")


class Consent(BranchScoped):
    """
    What one patient agreed to, in the words they were given.

    Refusal is a first-class outcome, not an absence. "The patient was
    offered an HIV test and declined" is a clinical fact that the next
    clinician needs, and a system that records consent only when it is given
    makes a refusal indistinguishable from nobody having asked.
    """

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="consents"
    )
    #: Null for the consents taken at registration, which belong to the
    #: patient rather than to any one visit.
    encounter = models.ForeignKey(
        "journey.Encounter",
        on_delete=models.PROTECT,
        related_name="consents",
        null=True,
        blank=True,
    )

    form_key = models.CharField(max_length=32)
    form_version = models.PositiveSmallIntegerField(default=1)
    #: The wording, copied verbatim: name, statements, declaration.
    wording = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)

    status = models.CharField(max_length=16, choices=ConsentStatus, default=ConsentStatus.GIVEN)
    method = models.CharField(max_length=16, choices=ConsentMethod, default=ConsentMethod.TYPED)

    #: What the procedure actually was, where the form names one. Stored
    #: because "consent to a procedure" with no procedure named is worthless.
    subject = models.CharField(max_length=200, blank=True)

    #: Who agreed. Usually the patient; a parent or next of kin where the
    #: patient cannot, with the relationship written down.
    given_by_name = models.CharField(max_length=200)
    given_by_relationship = models.CharField(max_length=60, blank=True)

    witness_name = models.CharField(max_length=200, blank=True)
    witness_role = models.CharField(max_length=120, blank=True)

    #: The clinician who took it, and what they were at the time.
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="consents_taken"
    )
    taken_by_name = models.CharField(max_length=200, blank=True)
    taken_by_role = models.CharField(max_length=120, blank=True)

    given_at = models.DateTimeField(default=timezone.now)

    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_reason = models.TextField(blank=True)
    refused_reason = models.TextField(blank=True)

    #: Set where the form offers something beyond ordinary care — teaching
    #: use of a photograph, for instance — which the patient agrees to
    #: separately or not at all.
    extras = models.JSONField(default=dict, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("consent")
        ordering = ("-given_at",)
        indexes = [
            models.Index(fields=["patient", "form_key"]),
            models.Index(fields=["encounter"]),
        ]

    def __str__(self) -> str:
        return f"{self.form_key} — {self.given_by_name} ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        return self.status == ConsentStatus.GIVEN and self.withdrawn_at is None

    @property
    def statements(self) -> list[str]:
        value = self.wording.get("statements", [])
        return list(value) if isinstance(value, list) else []

    @property
    def declaration(self) -> str:
        return str(self.wording.get("declaration", ""))

    @property
    def form_name(self) -> str:
        return str(self.wording.get("name", self.form_key))

    def save(self, *args, **kwargs) -> None:
        if self.content_hash and hash_content(self.wording) != self.content_hash:
            raise ValidationError(
                _(
                    "This consent records words the patient was given. They cannot "
                    "be changed — withdraw it and take consent again instead."
                )
            )
        super().save(*args, **kwargs)


class DocumentStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    ISSUED = "issued", _("Issued")
    REVOKED = "revoked", _("Revoked")


class Document(BranchScoped):
    """
    Something the hospital signed and handed over.

    The QR code is the reason this model exists in the shape it does. A sick
    note in Nigeria is forged routinely — in a text editor, in ten minutes —
    and an employer has no way to tell. So every issued document carries a
    reference and a token, and the token addresses a public page that says
    only four things: that this hospital issued it, on this date, to a
    patient whose name begins thus, and whether it is still valid.

    What the public page deliberately does not say is what is wrong with the
    patient. An employer checking a sick note has a legitimate interest in
    whether it is genuine and none at all in the diagnosis, and a
    verification page that leaked the diagnosis would make every honest
    patient worse off than a forged note would.
    """

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="documents"
    )
    encounter = models.ForeignKey(
        "journey.Encounter",
        on_delete=models.PROTECT,
        related_name="documents",
        null=True,
        blank=True,
    )

    template_key = models.CharField(max_length=32)
    template_version = models.PositiveSmallIntegerField(default=1)
    #: What the clinician filled in, keyed by the template's field keys.
    content = models.JSONField(default=dict, blank=True)
    #: The template's own wording, copied at the moment of issue.
    wording = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)

    status = models.CharField(max_length=16, choices=DocumentStatus, default=DocumentStatus.DRAFT)

    #: Blank until issued. Unique across every hospital, because the
    #: verification page is reached without knowing which hospital to ask.
    reference = models.CharField(max_length=16, blank=True, db_index=True)
    token = models.CharField(max_length=64, blank=True, db_index=True)

    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documents_issued",
        null=True,
        blank=True,
    )
    #: The signature block, snapshotted the way a clinical note's is: what
    #: the signer was called and qualified as on the day, not today.
    signature_name = models.CharField(max_length=200, blank=True)
    signature_qualifications = models.CharField(max_length=200, blank=True)
    signature_registration = models.CharField(max_length=80, blank=True)
    signature_role = models.CharField(max_length=120, blank=True)

    #: Where the hospital's details are frozen too. A branch that moves
    #: premises next year must not change the address on a document issued
    #: from the old one.
    letterhead = models.JSONField(default=dict, blank=True)

    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.TextField(blank=True)

    #: Counted rather than logged in detail: how often a document is checked
    #: is useful, and who checked it is a stranger with a phone.
    verified_count = models.PositiveIntegerField(default=0)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("document")
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["reference"],
                condition=models.Q(status="issued"),
                name="document_reference_unique_when_issued",
            ),
        ]
        indexes = [
            models.Index(fields=["patient", "template_key"]),
            models.Index(fields=["encounter"]),
        ]

    def __str__(self) -> str:
        return f"{self.template_key} {self.reference or '(draft)'}"

    @property
    def is_issued(self) -> bool:
        return self.status == DocumentStatus.ISSUED

    @property
    def is_valid(self) -> bool:
        return self.is_issued and self.revoked_at is None

    @property
    def name(self) -> str:
        return str(self.wording.get("name", self.template_key))

    def signature_block(self) -> list[str]:
        """
        The stamp, as the lines it prints.

        Empty until issued, so a draft cannot be made to look like a signed
        document by a screenshot.
        """
        if not self.is_issued:
            return []
        lines = [self.signature_name]
        if self.signature_qualifications:
            lines.append(self.signature_qualifications)
        if self.signature_registration:
            lines.append(self.signature_registration)
        if self.signature_role:
            lines.append(self.signature_role)
        if self.issued_at:
            # The hospital's own clock, not UTC — the same hour's
            # disagreement that was caught on the clinical note's stamp.
            issued = timezone.localtime(self.issued_at)
            lines.append(f"Issued {issued:%d %b %Y at %H:%M}")
        if self.reference:
            lines.append(f"Reference {self.reference}")
        return [line for line in lines if line]

    def save(self, *args, **kwargs) -> None:
        if (
            self.status == DocumentStatus.ISSUED
            and self.content_hash
            and hash_content({"content": self.content, "wording": self.wording})
            != self.content_hash
        ):
            raise ValidationError(
                _(
                    "This document has been issued and somebody may be holding a "
                    "copy. Its wording cannot be changed — revoke it and issue a "
                    "new one instead."
                )
            )
        super().save(*args, **kwargs)


__all__ = [
    "Consent",
    "ConsentMethod",
    "ConsentStatus",
    "Document",
    "DocumentStatus",
    "hash_content",
    "new_reference",
    "new_token",
]
