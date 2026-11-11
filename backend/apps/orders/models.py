"""
What the doctor asks for: tests, scans, medicines, procedures, referrals.

**One table for every kind of order.** A laboratory request, a prescription
and a referral to physiotherapy are the same act — a clinician decides
something should happen and signs their name to it — and they are read back
together, in one list, in the order they were made. Separate tables per kind
would produce four screens showing a quarter of the story each, four ways of
asking "what was ordered on this visit", and four places to forget to check
the patient's allergies.

What differs by kind is a handful of fields. Medicines need a dose, a route,
a frequency and a duration; nothing else does. They sit on this table, null
for the other kinds, because the alternative — a JSON blob, or a second
table joined one-to-one — makes the pharmacy's query harder for no gain.

**Drafts are a basket.** Orders are created as drafts and signed together,
because a doctor thinks in a plan — "bloods, an antibiotic and something for
the fever" — and signing each one separately is three times the clicks for
one intention. Signing is what makes an order real: it sets ``ordered_at``,
and only then does anything downstream see it.

**Nothing is deleted.** A cancelled order keeps its row and its reason. "The
doctor ordered this and then stopped it" is exactly the sort of thing a case
review asks about, and in a hospital it is also how a patient gets charged
for a test that never happened.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BranchScoped


class OrderKind(models.TextChoices):
    """
    What is being asked for.

    These are the queues an order can land in, not the service categories in
    the price list — a dressing and a wound review are both ``NURSING`` here
    and priced quite differently.
    """

    LABORATORY = "laboratory", _("Laboratory test")
    RADIOLOGY = "radiology", _("Imaging")
    MEDICATION = "medication", _("Medicine")
    PROCEDURE = "procedure", _("Procedure")
    NURSING = "nursing", _("Nursing care")
    DIET = "diet", _("Diet")
    OTHER = "other", _("Other")


class OrderPriority(models.TextChoices):
    ROUTINE = "routine", _("Routine")
    URGENT = "urgent", _("Urgent")
    STAT = "stat", _("Immediately (stat)")


class OrderStatus(models.TextChoices):
    """
    Where an order has got to.

    Deliberately short. The laboratory, radiology and pharmacy modules each
    add their own internal steps in milestone 7 — collected, verified,
    dispensed — and those belong to the department doing the work. What the
    ordering doctor needs is four answers: is it still a draft, has it gone,
    is it being done, is it finished.
    """

    DRAFT = "draft", _("Draft")
    ORDERED = "ordered", _("Ordered")
    IN_PROGRESS = "in_progress", _("Being done")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


#: Statuses from which an order may still be stopped. Once something is
#: completed, stopping it is a fiction: the blood is drawn, the scan is done.
STOPPABLE = (OrderStatus.DRAFT, OrderStatus.ORDERED, OrderStatus.IN_PROGRESS)


class Order(BranchScoped):
    """One thing a clinician asked for. See the module docstring."""

    encounter = models.ForeignKey(
        "journey.Encounter", on_delete=models.PROTECT, related_name="orders"
    )
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="orders")

    kind = models.CharField(max_length=16, choices=OrderKind, db_index=True)
    #: What is being ordered, from the hospital's own list. Null for a
    #: medicine typed as free text, which a doctor must be able to do: a
    #: hospital whose pharmacy list is incomplete must not become a hospital
    #: where that drug cannot be prescribed.
    service = models.ForeignKey(
        "catalogue.Service", on_delete=models.PROTECT, null=True, blank=True, related_name="orders"
    )
    #: The wording at the time, like every other snapshot in Curavia.
    name = models.CharField(max_length=200)
    instructions = models.CharField(max_length=300, blank=True)
    #: Why it was asked for. On a laboratory form this is the clinical
    #: question, and a laboratory that cannot see it is guessing.
    reason = models.CharField(max_length=300, blank=True)

    priority = models.CharField(
        max_length=10, choices=OrderPriority, default=OrderPriority.ROUTINE, db_index=True
    )
    status = models.CharField(
        max_length=12, choices=OrderStatus, default=OrderStatus.DRAFT, db_index=True
    )

    # -- medicines only -----------------------------------------------------
    dose = models.CharField(max_length=60, blank=True, help_text=_("For example 500 mg."))
    route = models.CharField(max_length=40, blank=True, help_text=_("Oral, IV, IM, topical."))
    frequency = models.CharField(max_length=60, blank=True, help_text=_("Three times a day."))
    duration = models.CharField(max_length=60, blank=True, help_text=_("Five days."))
    quantity = models.PositiveSmallIntegerField(null=True, blank=True)

    # -- money --------------------------------------------------------------
    #: What it was expected to cost when it was ordered, so the patient can
    #: be told before they agree to it. The bill is raised in milestone 8 and
    #: re-prices from the price list in force on the day; this is an estimate
    #: and is named as one everywhere it is shown.
    estimate_kobo = models.PositiveIntegerField(null=True, blank=True)
    #: Set when the price list had nothing for this service, which the
    #: cashier needs to know before the patient is standing at the window.
    estimate_note = models.CharField(max_length=120, blank=True)

    # -- the safety check, recorded ------------------------------------------
    #: What the system warned about at the moment of signing, and whether the
    #: doctor went ahead anyway. Stored rather than recomputed: a warning is
    #: a fact about what somebody was told, and the allergy list may change.
    warnings = models.JSONField(default=list, blank=True)
    override_reason = models.CharField(max_length=300, blank=True)

    ordered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cancel_reason = models.CharField(max_length=300, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("order")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["encounter", "status"]),
            models.Index(fields=["organisation", "patient", "-created_at"]),
            # The laboratory's and pharmacy's own queue, in milestone 7.
            models.Index(fields=["branch", "kind", "status", "-ordered_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.name}"

    @property
    def is_draft(self) -> bool:
        return self.status == OrderStatus.DRAFT

    @property
    def is_live(self) -> bool:
        return self.status in (OrderStatus.ORDERED, OrderStatus.IN_PROGRESS)

    @property
    def summary(self) -> str:
        """The one line a pharmacist or nurse reads."""
        if self.kind == OrderKind.MEDICATION:
            parts = [self.name, self.dose, self.route, self.frequency, self.duration]
            return " · ".join(part for part in parts if part)
        return " · ".join(part for part in (self.name, self.instructions) if part)

    @property
    def blocking_warnings(self) -> list[dict]:
        return [warning for warning in self.warnings or [] if warning.get("tone") == "critical"]

    def clean(self) -> None:
        super().clean()
        if not self.name.strip():
            raise ValidationError({"name": _("An order needs to say what is being asked for.")})
        if self.kind == OrderKind.MEDICATION and not self.dose.strip():
            raise ValidationError({"dose": _("A medicine needs a dose.")})


class Referral(BranchScoped):
    """
    Sending a patient to somebody else.

    Internal and external in one table, for the same reason the orders are:
    it is one act with one meaning, and "who has this patient been referred
    to" is one question. The difference is where it lands — an internal
    referral appears on a department's own list inside Curavia, an external
    one becomes a letter the patient carries.

    The letter body is **written out and stored**, not assembled from the
    record when somebody prints it. A referral letter is a statement made on
    a date, and one that silently rewrites itself as the record changes is
    not a letter anybody can stand behind.
    """

    class Kind(models.TextChoices):
        INTERNAL = "internal", _("Within this hospital")
        EXTERNAL = "external", _("To another facility")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent")
        ACCEPTED = "accepted", _("Accepted")
        DECLINED = "declined", _("Declined")
        COMPLETED = "completed", _("Seen")
        CANCELLED = "cancelled", _("Cancelled")

    class Urgency(models.TextChoices):
        ROUTINE = "routine", _("Routine")
        SOON = "soon", _("Soon")
        URGENT = "urgent", _("Urgent")
        EMERGENCY = "emergency", _("Emergency")

    encounter = models.ForeignKey(
        "journey.Encounter", on_delete=models.PROTECT, related_name="referrals"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="referrals"
    )

    kind = models.CharField(max_length=10, choices=Kind, db_index=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.DRAFT, db_index=True)
    urgency = models.CharField(max_length=10, choices=Urgency, default=Urgency.ROUTINE)

    #: Internal: the department it goes to.
    to_department = models.ForeignKey(
        "core.Department", on_delete=models.PROTECT, null=True, blank=True, related_name="referrals"
    )
    #: External: free text, because the receiving hospital is not in anybody's
    #: database and waiting for it to be would stop the referral.
    to_facility = models.CharField(max_length=160, blank=True)
    to_clinician = models.CharField(max_length=160, blank=True)
    speciality = models.CharField(max_length=80, blank=True)

    reason = models.CharField(max_length=300)
    #: The letter as it was written. See the class docstring.
    letter = models.TextField(blank=True)

    referred_at = models.DateTimeField(null=True, blank=True, db_index=True)
    referred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    response_note = models.CharField(max_length=300, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("referral")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["organisation", "patient", "-created_at"]),
            models.Index(fields=["branch", "status", "-referred_at"]),
            models.Index(fields=["to_department", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} referral: {self.destination}"

    @property
    def destination(self) -> str:
        if self.kind == self.Kind.INTERNAL:
            return self.to_department.name if self.to_department else "—"
        parts = [self.to_clinician, self.to_facility]
        return ", ".join(part for part in parts if part) or (self.speciality or "—")

    def clean(self) -> None:
        super().clean()
        if self.kind == self.Kind.INTERNAL and self.to_department_id is None:
            raise ValidationError({"to_department": _("Which department is it going to?")})
        if self.kind == self.Kind.EXTERNAL and not (
            self.to_facility.strip() or self.to_clinician.strip()
        ):
            raise ValidationError({"to_facility": _("Say who the patient is being sent to.")})
        if not self.reason.strip():
            raise ValidationError({"reason": _("A referral needs a reason.")})


def default_letter(*, patient_name: str, hospital: str) -> str:
    """
    The opening a referral letter gets if nobody writes one.

    Deliberately thin — a greeting and nothing clinical. A letter with
    invented content would be worse than a blank one, and this is a prompt
    to the clinician rather than a draft of their opinion.
    """
    return (
        f"Dear colleague,\n\n"
        f"Thank you for seeing {patient_name}, who attends {hospital}.\n\n"
        f"I would be grateful for your opinion and further management.\n\n"
    )
