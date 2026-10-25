"""
The imports a hospital actually arrives with.

Two, at this milestone, and they are the two that exist as a spreadsheet in
every hospital that has ever quoted a price or paid a salary:

    services   the price list
    staff      the people, as invitations

Patients are milestone 5 and are deliberately not here. A patient import
needs duplicate detection and a merge tool, and shipping one without them
produces a database with three of everybody — which is far more expensive to
undo than to never do.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.accounts.models import Role
from apps.catalogue.models import Payer, Price, Service, ServiceCategory
from apps.core.models import Branch, Organisation
from apps.onboarding.setup import ensure_self_pay_payer

from .base import ColumnSpec, ImportSpec, PreparedRow

# --------------------------------------------------------------------------
# Shared parsing
# --------------------------------------------------------------------------


def parse_money_to_kobo(raw: str, *, column: str) -> int:
    """
    A price as somebody typed it, in whole kobo.

    Accepts "6500", "6,500", "₦6,500.00", "N6500", "6 500" — all of which
    turn up in a real price list. Rejects anything else loudly rather than
    guessing, because a price read as zero is a service given away.

    Never a float. Decimal all the way to the integer, like every other
    amount in the system.
    """
    cleaned = (
        raw.replace("₦", "")  # ₦
        .replace("NGN", "")
        .replace("ngn", "")
        .replace(",", "")
        .replace(" ", "")
        .replace("\u00a0", "")  # NBSP, from a pasted web table
        .strip()
    )
    # A leading N is the handwritten naira sign. Only stripped when a digit
    # follows, so a column holding "N/A" still fails rather than becoming 0.
    if cleaned[:1] in {"N", "n"} and cleaned[1:2].isdigit():
        cleaned = cleaned[1:]

    if not cleaned:
        raise ValueError(f"{column}: this row has no price.")

    # Parenthesised negatives are an accounting convention, and a negative
    # price is never what was meant.
    if cleaned.startswith("(") and cleaned.endswith(")"):
        raise ValueError(f"{column}: a price cannot be negative.")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"{column}: {raw!r} is not a price we can read.") from None

    if amount < 0:
        raise ValueError(f"{column}: a price cannot be negative.")
    if amount > Decimal("100000000"):
        raise ValueError(f"{column}: {raw!r} looks too large to be a price.")

    return int((amount * 100).to_integral_value())


def _required(values: dict[str, str], key: str, column: str) -> str:
    value = (values.get(key) or "").strip()
    if not value:
        raise ValueError(f"{column}: this row is missing it.")
    return value


# --------------------------------------------------------------------------
# Services and prices
# --------------------------------------------------------------------------

SERVICE_COLUMNS = (
    ColumnSpec(
        "code",
        "Code",
        aliases=("code", "service code", "item code", "sku"),
        help_text="Your own short code. If you do not have one, we make it from the name.",
        example="FBC",
        required=False,
    ),
    ColumnSpec(
        "name",
        "Service name",
        aliases=("name", "service", "description", "item", "test", "procedure"),
        example="Full blood count",
    ),
    ColumnSpec(
        "price",
        "Price",
        aliases=("price", "amount", "cost", "rate", "tariff", "fee", "charge"),
        help_text="In naira. ₦6,500 and 6500 both work.",
        example="6,500",
    ),
    ColumnSpec(
        "category",
        "Category",
        required=False,
        aliases=("category", "group", "type", "department", "section"),
        help_text="Laboratory, consultation, imaging… Guessed from the name when blank.",
        example="Laboratory",
    ),
)

#: Words in a service name that give away what it is, when the file has no
#: category column. Checked in order, so the more specific words come first.
CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("consultation", ServiceCategory.CONSULTATION),
    ("review", ServiceCategory.CONSULTATION),
    ("x-ray", ServiceCategory.RADIOLOGY),
    ("xray", ServiceCategory.RADIOLOGY),
    ("scan", ServiceCategory.RADIOLOGY),
    ("ultrasound", ServiceCategory.RADIOLOGY),
    ("uss", ServiceCategory.RADIOLOGY),
    ("ecg", ServiceCategory.RADIOLOGY),
    ("delivery", ServiceCategory.MATERNITY),
    ("antenatal", ServiceCategory.MATERNITY),
    ("caesar", ServiceCategory.THEATRE),
    ("theatre", ServiceCategory.THEATRE),
    ("surgery", ServiceCategory.THEATRE),
    ("ward", ServiceCategory.BED),
    ("bed", ServiceCategory.BED),
    ("admission", ServiceCategory.BED),
    ("dressing", ServiceCategory.NURSING),
    ("injection", ServiceCategory.NURSING),
    ("infusion", ServiceCategory.NURSING),
    ("vaccine", ServiceCategory.VACCINE),
    ("immunis", ServiceCategory.VACCINE),
    ("test", ServiceCategory.LABORATORY),
    ("blood", ServiceCategory.LABORATORY),
    ("urine", ServiceCategory.LABORATORY),
    ("malaria", ServiceCategory.LABORATORY),
    ("screening", ServiceCategory.LABORATORY),
    ("profile", ServiceCategory.LABORATORY),
)

VALID_CATEGORIES = {choice.value for choice in ServiceCategory}


def guess_category(name: str, given: str) -> str:
    """
    The category column when there is one, a guess from the name when not.

    A guess is better than "Other" for every row, because a price list
    grouped as one lump cannot be reported on by department — but it is only
    a guess, and the preview shows it so the person can see what it decided.
    """
    stated = (given or "").strip().lower()
    if stated in VALID_CATEGORIES:
        return stated
    for word, category in CATEGORY_HINTS:
        if word in stated or word in name.lower():
            return category
    return ServiceCategory.OTHER


def _code_from_name(name: str) -> str:
    """A usable code for a file that has none: initials, then a shortening."""
    words = [word for word in name.replace("-", " ").split() if word.isalnum()]
    if len(words) >= 2:
        candidate = "".join(word[0] for word in words[:4]).upper()
    else:
        candidate = name[:8].upper().replace(" ", "")
    return "".join(character for character in candidate if character.isalnum())[:32] or "ITEM"


def validate_service(
    organisation: Organisation, values: dict[str, str], row_number: int
) -> PreparedRow:
    name = _required(values, "name", "Service name")
    price_kobo = parse_money_to_kobo(_required(values, "price", "Price"), column="Price")

    code = (values.get("code") or "").strip().upper()
    if not code:
        code = _code_from_name(name)
    if len(code) > 32:
        raise ValueError("Code: that code is longer than 32 characters.")

    category = guess_category(name, values.get("category", ""))
    existing = Service.objects.filter(code=code).first()

    naira = price_kobo // 100
    return PreparedRow(
        row_number=row_number,
        values={
            "code": code,
            "name": name[:160],
            "category": category,
            "price_kobo": price_kobo,
            "exists": existing is not None,
        },
        summary=(
            f"{'Update' if existing else 'Add'} {code} — {name[:60]} at ₦{naira:,} ({category})"
        ),
    )


@transaction.atomic
def write_services(organisation: Organisation, rows: Sequence[PreparedRow]) -> tuple[int, int]:
    """
    Write the services and their self-pay prices.

    The same `update_or_create` shape the setup wizard uses, so a hospital
    that half-set-up through the wizard and then imported a full price list
    gets one list rather than two.
    """
    from django.utils import timezone

    self_pay = ensure_self_pay_payer(organisation)
    created = updated = 0

    for row in rows:
        service, was_created = Service.objects.update_or_create(
            organisation=organisation,
            code=row.values["code"],
            defaults={
                "name": row.values["name"],
                "category": row.values["category"],
                "is_active": True,
                "from_template": False,
            },
        )
        created += int(was_created)
        updated += int(not was_created)

        Price.objects.update_or_create(
            service=service,
            payer=self_pay,
            branch=None,
            effective_from=timezone.localdate(),
            defaults={
                "organisation": organisation,
                "amount_kobo": row.values["price_kobo"],
                "note": "Imported from a spreadsheet",
            },
        )

    return created, updated


SERVICES_IMPORT = ImportSpec(
    kind="services",
    title="Services and prices",
    blurb=(
        "Your price list. One row per thing you charge for. Prices become your "
        "self-pay rate; HMO rates are entered against them afterwards."
    ),
    columns=SERVICE_COLUMNS,
    validate=validate_service,
    write=write_services,
)


# --------------------------------------------------------------------------
# Staff
# --------------------------------------------------------------------------

STAFF_COLUMNS = (
    ColumnSpec(
        "email",
        "Email",
        aliases=("email", "e-mail", "mail", "address"),
        example="nurse@yourhospital.test",
    ),
    ColumnSpec(
        "first_name",
        "First name",
        required=False,
        aliases=("first name", "firstname", "given name", "other names"),
        example="Amarachi",
    ),
    ColumnSpec(
        "last_name",
        "Surname",
        required=False,
        aliases=("surname", "last name", "lastname", "family name"),
        example="Eze",
    ),
    ColumnSpec(
        "role",
        "Role",
        aliases=("role", "designation", "position", "cadre", "job role"),
        help_text="Must match one of your roles — Nurse, Doctor, Cashier, and so on.",
        example="Nurse",
    ),
    ColumnSpec(
        "job_title",
        "Job title",
        required=False,
        aliases=("job title", "title", "post"),
        example="Staff nurse",
    ),
)


def validate_staff(
    organisation: Organisation, values: dict[str, str], row_number: int
) -> PreparedRow:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from apps.accounts.models import Invitation, StaffMembership

    email = _required(values, "email", "Email").lower()
    try:
        validate_email(email)
    except ValidationError:
        raise ValueError(f"Email: {email!r} is not an email address.") from None

    role_name = _required(values, "role", "Role")
    role = (
        Role.objects.filter(name__iexact=role_name).first()
        or Role.objects.filter(slug__iexact=role_name.replace(" ", "_")).first()
    )
    if role is None:
        raise ValueError(
            f"Role: there is no role called {role_name!r}. Check the spelling against "
            "your roles list."
        )
    if role.slug == "owner":
        # Not a technical limit — a deliberate one. Importing a spreadsheet
        # that silently grants full access to forty people is the single
        # worst thing this feature could do, and the staff screen can still
        # invite an owner one at a time, deliberately.
        raise ValueError(
            "Role: owner access cannot be granted by import. Invite them from the "
            "staff screen instead."
        )

    if StaffMembership.objects.filter(user__email=email, is_active=True).exists():
        raise ValueError("Email: that person is already on your staff list.")
    if Invitation.objects.filter(email=email, status=Invitation.Status.PENDING).exists():
        raise ValueError("Email: an invitation is already open for that address.")

    name = " ".join(
        part for part in (values.get("first_name", ""), values.get("last_name", "")) if part
    ).strip()
    return PreparedRow(
        row_number=row_number,
        values={
            "email": email,
            "first_name": (values.get("first_name") or "").strip(),
            "last_name": (values.get("last_name") or "").strip(),
            "job_title": (values.get("job_title") or "").strip(),
            "role_id": str(role.pk),
            "exists": False,
        },
        summary=f"Invite {name or email} as {role.name}",
    )


def write_staff(organisation: Organisation, rows: Sequence[PreparedRow]) -> tuple[int, int]:
    """
    Send one invitation per row.

    Each row needs to know who is doing the inviting, which the validator
    cannot know — it has no request. The view puts ``invited_by`` and
    ``invited_by_name`` on every prepared row before calling this; see
    ``CommitImportView``.

    NOT in a single transaction, deliberately. Each invitation sends an
    email, and an email cannot be rolled back — so a failure on row 38 must
    leave rows 1 to 37 invited rather than pretending they were not. The
    caller reports which rows failed.
    """
    from apps.accounts import invitations as invites

    branch_ids = [str(branch.pk) for branch in Branch.objects.all()]
    created = 0

    for row in rows:
        issued = invites.invite_staff(
            organisation=organisation,
            email=row.values["email"],
            role_ids=[row.values["role_id"]],
            branch_ids=branch_ids,
            invited_by=row.values["invited_by"],
            first_name=row.values["first_name"],
            last_name=row.values["last_name"],
            job_title=row.values["job_title"],
        )
        invites.send_invitation_email(
            issued,
            hospital_name=organisation.name,
            inviter=row.values["invited_by_name"],
        )
        created += 1

    return created, 0


STAFF_IMPORT = ImportSpec(
    kind="staff",
    title="Staff",
    blurb=(
        "One row per person. Each gets an email asking them to set their own "
        "password and enrol in two-factor authentication."
    ),
    columns=STAFF_COLUMNS,
    validate=validate_staff,
    write=write_staff,
)


# --------------------------------------------------------------------------
# Payers
# --------------------------------------------------------------------------

PAYER_COLUMNS = (
    ColumnSpec(
        "name", "Name", aliases=("name", "hmo", "scheme", "company", "client"), example="Hygeia HMO"
    ),
    ColumnSpec(
        "code",
        "Code",
        required=False,
        aliases=("code", "short code", "abbreviation"),
        example="HYGEIA",
    ),
    ColumnSpec(
        "kind",
        "Type",
        required=False,
        aliases=("type", "kind", "category"),
        help_text="HMO, NHIA, corporate or NGO. Defaults to HMO.",
        example="HMO",
    ),
)

VALID_PAYER_KINDS = {choice.value for choice in Payer.Kind}


def validate_payer(
    organisation: Organisation, values: dict[str, str], row_number: int
) -> PreparedRow:
    name = _required(values, "name", "Name")
    code = (values.get("code") or "").strip().upper() or _code_from_name(name)

    kind = (values.get("kind") or "").strip().lower()
    if kind in {"corporate client", "company", "corporate"}:
        kind = Payer.Kind.CORPORATE
    if kind not in VALID_PAYER_KINDS:
        kind = Payer.Kind.HMO
    if kind == Payer.Kind.SELF:
        raise ValueError("Type: self-pay is created automatically and there can only be one.")

    existing = Payer.objects.filter(code=code).first()
    return PreparedRow(
        row_number=row_number,
        values={"name": name[:160], "code": code, "kind": kind, "exists": existing is not None},
        summary=f"{'Update' if existing else 'Add'} {name[:60]} ({kind.upper()})",
    )


@transaction.atomic
def write_payers(organisation: Organisation, rows: Sequence[PreparedRow]) -> tuple[int, int]:
    created = updated = 0
    for row in rows:
        _payer, was_created = Payer.objects.update_or_create(
            organisation=organisation,
            code=row.values["code"],
            defaults={
                "name": row.values["name"],
                "kind": row.values["kind"],
                "is_active": True,
                "from_template": False,
            },
        )
        created += int(was_created)
        updated += int(not was_created)
    return created, updated


PAYERS_IMPORT = ImportSpec(
    kind="payers",
    title="HMOs and corporate clients",
    blurb="The schemes you bill. Rates are entered against your price list afterwards.",
    columns=PAYER_COLUMNS,
    validate=validate_payer,
    write=write_payers,
)


IMPORTS: dict[str, ImportSpec] = {
    spec.kind: spec for spec in (SERVICES_IMPORT, STAFF_IMPORT, PAYERS_IMPORT)
}
