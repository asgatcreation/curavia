"""
Applying a wizard step.

The screens are thin; this is where the work happens. One function per step,
each of them idempotent and each wrapped in a transaction, because the person
doing this is often doing it at the end of a working day and will close the
laptop halfway through.

**Idempotent matters more than it sounds.** Somebody will accept the
department list, go back a step, and accept it again. Doing that must not
create a second "General Outpatient" — so every apply matches on the natural
key (a code, within this hospital) and updates rather than inserting blindly.

Everything written here is tenant-scoped through the ordinary managers. This
module is deliberately NOT on the cross-tenant allowlist: by the time the
wizard runs, the hospital exists and the caller is signed in to it, so there
is no excuse for an unscoped write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.catalogue.models import Payer, Price, Service
from apps.core.models import Bed, Branch, Department, Organisation
from apps.core.setup_steps import mark_step
from apps.onboarding import templates_data as templates


class SetupError(Exception):
    """Something was wrong with what was submitted."""


@dataclass(frozen=True, slots=True)
class StepResult:
    created: int = 0
    updated: int = 0
    detail: str = ""


# --------------------------------------------------------------------------
# 1. Profile
# --------------------------------------------------------------------------


@transaction.atomic
def apply_profile(organisation: Organisation, data: dict[str, Any]) -> StepResult:
    """
    The hospital's own details, including the registration numbers.

    The numbers are stored but NOT checked here. Verification against CAC, the
    state Ministry of Health and MDCN is a platform-level review with a human
    in it (milestone 12); pretending to validate a CAC number with a regular
    expression would be worse than storing it plainly, because it would look
    like it had been checked.
    """
    fields = [
        "name",
        "cac_number",
        "moh_licence_number",
        "medical_director_mdcn",
        "contact_email",
        "contact_phone",
    ]
    changed = []
    for key in fields:
        if key in data and data[key] is not None:
            value = str(data[key]).strip()
            if getattr(organisation, key) != value:
                setattr(organisation, key, value)
                changed.append(key)

    if "share_records_across_branches" in data:
        # Its own name rather than reusing `value` above: that one holds a
        # string, and a loop variable that changes type between branches is
        # how a type checker earns its keep.
        share = bool(data["share_records_across_branches"])
        if organisation.share_records_across_branches != share:
            organisation.share_records_across_branches = share
            changed.append("share_records_across_branches")

    if not organisation.name.strip():
        raise SetupError("The hospital needs a name.")

    if changed:
        organisation.save(update_fields=[*changed, "updated_at"])

    mark_step(organisation, "profile")
    return StepResult(updated=len(changed), detail="Hospital details saved.")


# --------------------------------------------------------------------------
# 2. Branches
# --------------------------------------------------------------------------


@transaction.atomic
def apply_branches(organisation: Organisation, rows: list[dict[str, Any]]) -> StepResult:
    """
    Add or update the hospital's locations.

    Matched on ``code``, which is what appears in a medical record number and
    is therefore the thing a hospital already treats as the branch's identity.
    Nothing is deleted here: a branch with a patient in it cannot be removed
    by editing a list, and the foreign keys are PROTECT for that reason.
    """
    created = updated = 0
    seen_codes: set[str] = set()

    for row in rows:
        code = str(row.get("code", "")).strip().upper()[:16]
        name = str(row.get("name", "")).strip()
        if not code or not name:
            raise SetupError("Every branch needs a name and a short code.")
        if code in seen_codes:
            raise SetupError(f"Two branches both use the code {code}.")
        seen_codes.add(code)

        defaults = {
            "name": name,
            "address": str(row.get("address", "")).strip(),
            "state": str(row.get("state", "")).strip(),
            "lga": str(row.get("lga", "")).strip(),
            "phone": str(row.get("phone", "")).strip(),
        }
        _branch, was_created = Branch.objects.update_or_create(
            organisation=organisation, code=code, defaults=defaults
        )
        created += int(was_created)
        updated += int(not was_created)

    # Exactly one main branch, always. A hospital with none would break scope
    # resolution for anybody who has not picked a branch yet.
    if not Branch.objects.filter(is_main=True).exists():
        first = Branch.objects.order_by("created_at").first()
        if first:
            Branch.objects.filter(pk=first.pk).update(is_main=True)

    mark_step(organisation, "branches")
    return StepResult(created=created, updated=updated, detail="Branches saved.")


# --------------------------------------------------------------------------
# 3. Departments
# --------------------------------------------------------------------------


@transaction.atomic
def apply_departments(organisation: Organisation, branch_id: str, codes: list[str]) -> StepResult:
    """
    Create the chosen departments at one branch, from the template list.

    Per branch rather than per hospital because a group's Lekki site may have
    no theatre while Ikeja does, and a department is where patients are
    actually seen.
    """
    branch = Branch.objects.filter(pk=branch_id).first()
    if branch is None:
        raise SetupError("That branch does not belong to this hospital.")

    by_code = {t.code: t for t in templates.DEPARTMENT_TEMPLATES}
    unknown = [code for code in codes if code not in by_code]
    if unknown:
        raise SetupError(f"Unknown department(s): {', '.join(sorted(unknown))}.")

    created = updated = 0
    for code in codes:
        template = by_code[code]
        _department, was_created = Department.objects.update_or_create(
            branch=branch,
            code=template.code,
            defaults={
                "organisation": organisation,
                "name": template.name,
                "kind": template.kind,
                "is_active": True,
            },
        )
        created += int(was_created)
        updated += int(not was_created)

    mark_step(organisation, "departments")
    return StepResult(created=created, updated=updated, detail="Departments saved.")


# --------------------------------------------------------------------------
# 4. Wards and beds
# --------------------------------------------------------------------------


@transaction.atomic
def apply_wards(
    organisation: Organisation, branch_id: str, rows: list[dict[str, Any]]
) -> StepResult:
    """
    Fill wards with beds.

    ``rows`` is ``[{"department_code": "MW", "kind": "general", "count": 8}]``.
    Beds are named by the ward code and a number — MW-01, MW-02 — because that
    is what gets written on the bed and said on a ward round.

    Existing beds are left alone and the numbering continues from the highest
    one already there, so running this twice adds the second batch rather than
    colliding or silently doing nothing.
    """
    branch = Branch.objects.filter(pk=branch_id).first()
    if branch is None:
        raise SetupError("That branch does not belong to this hospital.")

    valid_kinds = {choice.value for choice in Bed.Kind}
    created = 0

    for row in rows:
        code = str(row.get("department_code", "")).strip()
        kind = str(row.get("kind", Bed.Kind.GENERAL)).strip()
        count = int(row.get("count", 0))

        if count <= 0:
            continue
        if count > 500:
            raise SetupError("A single ward cannot be given more than 500 beds at once.")
        if kind not in valid_kinds:
            raise SetupError(f"Unknown bed type {kind!r}.")

        ward = Department.objects.filter(
            branch=branch, code=code, kind=Department.Kind.WARD
        ).first()
        if ward is None:
            raise SetupError(f"{code} is not a ward at this branch. Add it as a department first.")

        existing = set(Bed.objects.filter(ward=ward).values_list("name", flat=True))
        number = 1
        made = 0
        while made < count:
            name = f"{ward.code}-{number:02d}"
            if name not in existing:
                Bed.objects.create(
                    organisation=organisation,
                    branch=branch,
                    ward=ward,
                    name=name,
                    kind=kind,
                )
                existing.add(name)
                made += 1
                created += 1
            number += 1
            if number > 1000:  # pragma: no cover - guards a pathological state
                raise SetupError(f"Could not find free bed names in {ward.name}.")

    mark_step(organisation, "wards")
    return StepResult(created=created, detail="Beds created.")


# --------------------------------------------------------------------------
# 5. Services and prices
# --------------------------------------------------------------------------


@transaction.atomic
def apply_services(organisation: Organisation, rows: list[dict[str, Any]]) -> StepResult:
    """
    Create the chosen services and give each one a self-pay price.

    A service with no price is useless — the order screen cannot bill it — so
    the two are written together rather than leaving prices to a later step
    somebody will not reach.

    The price is hospital-wide (``branch = null``). Per-branch pricing is a
    real need for a group, and it is an edit to the price list later, not a
    question to ask during setup.
    """
    by_code = {t.code: t for t in templates.SERVICE_TEMPLATES}
    self_pay = ensure_self_pay_payer(organisation)

    created = updated = priced = 0
    for row in rows:
        code = str(row.get("code", "")).strip().upper()
        if not code:
            raise SetupError("Every service needs a code.")

        template = by_code.get(code)
        name = str(row.get("name", "") or (template.name if template else "")).strip()
        if not name:
            raise SetupError(f"Service {code} needs a name.")

        amount = row.get("price_kobo")
        if amount is None:
            amount = template.price_kobo if template else None
        if amount is None:
            raise SetupError(f"Service {code} needs a price.")
        amount = int(amount)
        if amount < 0:
            raise SetupError(f"Service {code} cannot have a negative price.")

        service, was_created = Service.objects.update_or_create(
            organisation=organisation,
            code=code,
            defaults={
                "name": name,
                "category": row.get("category") or (template.category if template else "other"),
                "module": (template.module if template else ""),
                "requires_prescription": bool(
                    row.get(
                        "requires_prescription",
                        template.requires_prescription if template else False,
                    )
                ),
                "is_active": True,
                "from_template": template is not None,
            },
        )
        created += int(was_created)
        updated += int(not was_created)

        # update_or_create on the price as well: re-running the step with a
        # different number should correct today's price, not stack a second
        # row with the same effective date (which the unique constraint would
        # refuse anyway).
        _price, price_created = Price.objects.update_or_create(
            service=service,
            payer=self_pay,
            branch=None,
            effective_from=timezone.localdate(),
            defaults={"organisation": organisation, "amount_kobo": amount},
        )
        priced += int(price_created)

    mark_step(organisation, "services")
    return StepResult(
        created=created, updated=updated, detail=f"{created + updated} services, {priced} priced."
    )


def ensure_self_pay_payer(organisation: Organisation) -> Payer:
    """
    Every hospital has exactly one self-pay payer, created on demand.

    It is not optional and it is not a choice, so it is never put in front of
    the person setting up: a patient paying cash is the commonest payer there
    is, and a price list needs something to hang its default on.
    """
    payer = Payer.objects.filter(kind=Payer.Kind.SELF).first()
    if payer is not None:
        return payer
    return Payer.objects.create(
        organisation=organisation,
        name="Patient (self-pay)",
        code="SELF",
        kind=Payer.Kind.SELF,
        requires_preauthorisation=False,
        claim_window_days=0,
        from_template=True,
    )


# --------------------------------------------------------------------------
# 6. Payers
# --------------------------------------------------------------------------


@transaction.atomic
def apply_payers(organisation: Organisation, rows: list[dict[str, Any]]) -> StepResult:
    """
    The HMOs and corporate clients this hospital bills.

    No tariffs are set here, deliberately. A hospital's rates with an HMO are
    negotiated and commercially confidential, and shipping a number would
    imply Curavia knows what it should be. What setup saves is the typing of
    the names; the rates are entered against the price list afterwards, where
    the hospital can see its own self-pay price beside them.
    """
    ensure_self_pay_payer(organisation)
    by_code = {t.code: t for t in templates.PAYER_TEMPLATES}

    created = updated = 0
    for row in rows:
        code = str(row.get("code", "")).strip().upper()
        if not code:
            raise SetupError("Every payer needs a code.")
        if code == "SELF":
            continue  # handled above, and there may only be one

        template = by_code.get(code)
        name = str(row.get("name", "") or (template.name if template else "")).strip()
        if not name:
            raise SetupError(f"Payer {code} needs a name.")

        _payer, was_created = Payer.objects.update_or_create(
            organisation=organisation,
            code=code,
            defaults={
                "name": name,
                "kind": row.get("kind") or (template.kind if template else Payer.Kind.HMO),
                "requires_preauthorisation": bool(
                    row.get(
                        "requires_preauthorisation",
                        template.requires_preauthorisation if template else True,
                    )
                ),
                "claim_window_days": int(
                    row.get("claim_window_days", template.claim_window_days if template else 30)
                ),
                "is_active": True,
                "from_template": template is not None,
            },
        )
        created += int(was_created)
        updated += int(not was_created)

    mark_step(organisation, "payers")
    return StepResult(created=created, updated=updated, detail="Payers saved.")


# --------------------------------------------------------------------------
# 7. Staff
# --------------------------------------------------------------------------


@transaction.atomic
def apply_staff(organisation: Organisation, rows: list[dict[str, Any]]) -> StepResult:
    """
    Finish the staff step.

    Invitations themselves are the next slice of milestone 4, so there is
    nothing to write yet and ``rows`` is ignored. This exists rather than
    being left out because every step in ``SETUP_STEPS`` must have an
    applier — a declared step with no way to apply it fails at the last
    click of a ten-minute wizard, which is exactly where a failure costs the
    most. A test asserts the two lists match.

    When invitations land, this grows a body and the signature does not
    change.
    """
    mark_step(organisation, "staff")
    return StepResult(detail="Setup finished. Invite your staff when you are ready.")
