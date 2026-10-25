"""
What the hospital sells, who pays for it, and at what price.

Three models and one rule.

**Service** is the thing: a consultation, a full blood count, a caesarean
section, a night in a private room. One list for the whole hospital, because a
test called "FBC" at Ikeja and "Full Blood Count" at Lekki is the same test and
has to report as one.

**Payer** is who settles the bill: the patient themselves, an HMO, the NHIA, or
a company with a staff scheme. Self-pay is a real Payer row rather than a null,
so a price list has no special case in it and a report can group by payer
without a `COALESCE`.

**Price** joins the two, optionally narrowed to one branch, and is
**effective-dated**. That last part is the rule, and it is why this is three
models rather than a `price` column on Service:

    A bill must always be re-computable from the prices that were in force on
    the day it was raised.

Hospitals raise a bill, an HMO queries it six weeks later, and somebody has to
answer "what did this cost in March". If a price is a column that gets
overwritten, that question has no answer and the hospital either eats the
difference or argues from memory. So a price change writes a new row with a new
`effective_from` and the old row stays exactly as it was.

Everything is integer kobo. See apps/core/utils.py.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BranchScoped, TenantScoped
from apps.core.utils import format_naira


class ServiceCategory(models.TextChoices):
    """
    Coarse grouping, for the price list and for revenue by department.

    Not a model: these are the lines a Nigerian hospital's revenue report
    actually has, they change about never, and a lookup table would mean a
    join on every bill line to produce a word.
    """

    CONSULTATION = "consultation", _("Consultations")
    PROCEDURE = "procedure", _("Procedures")
    LABORATORY = "laboratory", _("Laboratory tests")
    RADIOLOGY = "radiology", _("Imaging")
    PHARMACY = "pharmacy", _("Medicines and consumables")
    BED = "bed", _("Admission and bed charges")
    THEATRE = "theatre", _("Theatre")
    MATERNITY = "maternity", _("Maternity")
    NURSING = "nursing", _("Nursing and dressings")
    VACCINE = "vaccine", _("Immunisation")
    ADMIN = "admin", _("Administrative (reports, certificates)")
    OTHER = "other", _("Other")


class Service(TenantScoped):
    """
    One billable thing.

    Hospital-wide, not branch-wide: the same test must mean the same thing at
    every branch, or group reporting is meaningless. What varies per branch is
    the price, and that is on ``Price``.
    """

    code = models.CharField(
        max_length=32,
        help_text=_("Short code staff type, e.g. GOPD-NEW or FBC."),
    )
    name = models.CharField(max_length=160)
    category = models.CharField(
        max_length=24, choices=ServiceCategory, default=ServiceCategory.OTHER, db_index=True
    )
    #: Which module this belongs to, so a clinic with no laboratory never sees
    #: laboratory services in its price list.
    module = models.CharField(max_length=32, blank=True)
    description = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    #: Whether an order for this needs a doctor. Dressings do not; a CT does.
    requires_prescription = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    #: True for rows seeded from a template, so the wizard can tell a
    #: hospital's own additions from the list it was given.
    from_template = models.BooleanField(default=False)

    class Meta(TenantScoped.Meta):
        verbose_name = _("service")
        ordering = ("category", "sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "code"], name="unique_service_code_per_organisation"
            )
        ]
        indexes = [models.Index(fields=["organisation", "category", "is_active"])]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class Payer(TenantScoped):
    """
    Whoever settles the bill.

    Self-pay is a row, not a null. Every bill then has a payer, every price
    lookup is the same query, and "revenue by payer" needs no special case for
    the commonest payer of all.
    """

    class Kind(models.TextChoices):
        SELF = "self", _("Patient (self-pay)")
        HMO = "hmo", _("HMO")
        NHIA = "nhia", _("NHIA")
        CORPORATE = "corporate", _("Corporate / staff scheme")
        NGO = "ngo", _("NGO or donor programme")

    name = models.CharField(max_length=160)
    code = models.CharField(max_length=32)
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.HMO, db_index=True)

    #: The scheme's own identifiers and contacts. Claims need them; nothing
    #: clinical does.
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=24, blank=True)
    address = models.TextField(blank=True)

    #: How long after a visit a claim can still be filed. Nigerian HMOs
    #: commonly allow 30 or 60 days and refuse anything later, which is a
    #: deadline the system has to know to warn about.
    claim_window_days = models.PositiveSmallIntegerField(default=30)
    requires_preauthorisation = models.BooleanField(
        default=False,
        help_text=_("Needs an authorisation code before non-emergency treatment."),
    )
    #: Percentage the patient pays themselves on top, 0 to 100. Stored as a whole
    #: number: no Nigerian scheme has ever specified 12.5%.
    co_payment_percent = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0)]
    )

    is_active = models.BooleanField(default=True, db_index=True)
    from_template = models.BooleanField(default=False)

    class Meta(TenantScoped.Meta):
        verbose_name = _("payer")
        ordering = ("kind", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "code"], name="unique_payer_code_per_organisation"
            ),
            # Exactly one self-pay row per hospital. Two would mean two
            # different prices for a cash patient depending on which was
            # picked, which is the bug this constraint exists to prevent.
            models.UniqueConstraint(
                fields=["organisation"],
                condition=models.Q(kind="self"),
                name="one_self_pay_payer_per_organisation",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.co_payment_percent > 100:
            raise ValidationError(
                {"co_payment_percent": _("A co-payment cannot be more than 100 per cent.")}
            )


class Price(BranchScoped):
    """
    What one service costs one payer, from one date.

    Branch-scoped but with a nullable branch, which looks odd and is not: a
    price set for the whole hospital has ``branch = null``, and one set for a
    single branch names it. The lookup takes the most specific row that is in
    force — branch price first, then hospital price — which is how a hospital
    prices Lekki above Ikeja without re-entering nine hundred rows.

    Never updated in place. A price change writes a new row with a later
    ``effective_from``; the old row is what a bill raised last month was
    computed from, and it has to stay readable exactly as it was.
    """

    # Overridden to allow null. BranchScoped declares it non-null because
    # almost everything operational belongs to exactly one facility; a price
    # is the exception that genuinely applies hospital-wide.
    # django-stubs types the inherited field as non-null, so narrowing it to
    # nullable here is a genuine incompatibility at the type level and a
    # deliberate one at the model level. The ignore is on the assignment
    # rather than the module.
    branch = models.ForeignKey(  # type: ignore[assignment]
        "core.Branch",
        on_delete=models.PROTECT,
        related_name="prices",
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Leave empty to price this for every branch."),
    )

    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="prices")
    payer = models.ForeignKey(Payer, on_delete=models.PROTECT, related_name="prices")

    amount_kobo = models.BigIntegerField(validators=[MinValueValidator(0)])
    effective_from = models.DateField(default=timezone.localdate, db_index=True)
    #: Null means "still in force". Set when a later price supersedes it, so
    #: the history reads as a sequence rather than needing a window function.
    effective_to = models.DateField(null=True, blank=True)

    note = models.CharField(
        max_length=200, blank=True, help_text=_("Why it changed, for whoever asks later.")
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("price")
        ordering = ("service__name", "payer__name", "-effective_from")
        constraints = [
            models.UniqueConstraint(
                fields=["service", "payer", "branch", "effective_from"],
                name="one_price_per_service_payer_branch_and_date",
            ),
            # The same, for hospital-wide rows: a UniqueConstraint treats two
            # NULL branches as distinct, so without this a hospital-wide price
            # could be entered twice for the same day.
            models.UniqueConstraint(
                fields=["service", "payer", "effective_from"],
                condition=models.Q(branch__isnull=True),
                name="one_hospital_wide_price_per_service_payer_and_date",
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "service", "payer", "effective_from"]),
        ]

    def __str__(self) -> str:
        where = self.branch.name if self.branch is not None else "all branches"
        return (
            f"{self.service.code} · {self.payer.code} · {format_naira(self.amount_kobo)} ({where})"
        )

    def clean(self) -> None:
        super().clean()
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError(
                {"effective_to": _("A price cannot stop applying before it starts.")}
            )
