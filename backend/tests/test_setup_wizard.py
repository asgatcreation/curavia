"""
The setup wizard, and the price list it builds.

Two things carry the weight here: that running a step twice does not duplicate
anything (because people go backwards through wizards), and that the price
lookup answers the same question the same way every time, including months
later for a bill that has already been raised.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalogue.models import Payer, Price, Service
from apps.catalogue.pricing import PriceNotFound, resolve_price
from apps.core.models import Bed, Branch, Department
from apps.core.setup_steps import completed_steps, setup_is_complete
from apps.onboarding import setup
from apps.onboarding import templates_data as templates

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def api_as_owner(api, hospital):
    """An authenticated client acting as this hospital's owner."""
    user = hospital.staff("owner")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


# --------------------------------------------------------------------------
# The state endpoint
# --------------------------------------------------------------------------


def test_the_wizard_gets_everything_it_needs_in_one_request(api_as_owner) -> None:
    response = api_as_owner.get(reverse("api:v1:setup"))

    assert response.status_code == 200
    body = response.data
    assert body["complete"] is False
    assert body["next_step"] == "profile"
    assert len(body["steps"]) == 7
    assert {step["key"] for step in body["steps"]} >= {"profile", "branches", "services"}
    assert body["templates"]["departments"]
    assert body["templates"]["services"]
    # The hospital already has two branches from the factory.
    assert body["counts"]["branches"] == 2


def test_a_cashier_cannot_open_the_setup_wizard(api, hospital) -> None:
    """
    These steps decide what the hospital charges, so they are gated on
    org.settings.change rather than on being staff.
    """
    api.force_authenticate(user=hospital.staff("cashier"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    assert api.get(reverse("api:v1:setup")).status_code == 403


def test_the_templates_offered_follow_the_hospitals_size(api, hospital_solo) -> None:
    """A solo practice is never offered a theatre list or a ward."""
    api.force_authenticate(user=hospital_solo.staff("owner"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital_solo.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital_solo.main_branch.pk),
    )
    body = api.get(reverse("api:v1:setup")).data

    kinds = {entry["kind"] for entry in body["templates"]["departments"]}
    assert "ward" not in kinds
    assert "theatre" not in kinds


# --------------------------------------------------------------------------
# Applying steps
# --------------------------------------------------------------------------


def test_the_profile_step_saves_the_registration_numbers(api_as_owner, hospital) -> None:
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "profile"}),
        {
            "name": "Lifeline Specialist Hospital",
            "cac_number": "RC-1234567",
            "moh_licence_number": "LAG/MOH/2026/0099",
            "medical_director_mdcn": "MDCN/R/55555",
            "contact_phone": "08031234567",
        },
        format="json",
    )

    assert response.status_code == 200
    hospital.organisation.refresh_from_db()
    assert hospital.organisation.cac_number == "RC-1234567"
    assert hospital.organisation.medical_director_mdcn == "MDCN/R/55555"
    assert "profile" in completed_steps(hospital.organisation)


def test_applying_departments_twice_does_not_duplicate_them(api_as_owner, hospital) -> None:
    """
    The idempotence that matters.

    People go back a step in a wizard. Doing so must not leave two "General
    Outpatient" departments behind, which would then both appear in every
    clinic picker for the rest of the hospital's life.
    """
    url = reverse("api:v1:setup-step", kwargs={"step": "departments"})
    payload = {"branch": str(hospital.main_branch.pk), "codes": ["GOPD", "PAED", "LAB"]}

    first = api_as_owner.post(url, payload, format="json")
    second = api_as_owner.post(url, payload, format="json")

    assert first.status_code == second.status_code == 200
    # GOPD already exists — every hospital is provisioned with it — so the
    # first run creates two and updates one. What matters is the second run.
    assert first.data["created"] + first.data["updated"] == 3
    assert second.data["created"] == 0
    assert second.data["updated"] == 3

    with hospital.scope():
        assert Department.objects.filter(code="GOPD").count() == 1
        assert Department.objects.filter(code="PAED").count() == 1


def test_a_department_code_that_is_not_in_the_template_list_is_refused(
    api_as_owner, hospital
) -> None:
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "departments"}),
        {"branch": str(hospital.main_branch.pk), "codes": ["GOPD", "NOT-A-REAL-ONE"]},
        format="json",
    )
    assert response.status_code == 400
    assert "NOT-A-REAL-ONE" in response.data["detail"]


def test_a_branch_belonging_to_another_hospital_is_refused(
    api_as_owner, hospital, other_hospital
) -> None:
    """
    The tenant boundary, at a wizard endpoint.

    The branch id is supplied by the caller, so this is exactly the shape of
    request an attacker would send: a valid-looking UUID that belongs to
    somebody else.
    """
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "departments"}),
        {"branch": str(other_hospital.main_branch.pk), "codes": ["GOPD", "PAED"]},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "setup_failed"
    # Nothing was written there. GOPD already exists in every provisioned
    # hospital, so the check is that the count did not move.
    with other_hospital.scope():
        assert Department.objects.count() == 1
        assert not Department.objects.filter(code="PAED").exists()


def test_branches_can_be_added_through_the_wizard(api_as_owner, hospital) -> None:
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "branches"}),
        {
            "rows": [
                {"name": "Ikeja", "code": "IKJ", "state": "Lagos", "lga": "Ikeja"},
                {"name": "Lekki", "code": "LEK", "state": "Lagos", "lga": "Eti-Osa"},
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    with hospital.scope():
        assert Branch.objects.filter(code="IKJ").exists()
        assert Branch.objects.filter(is_main=True).count() == 1


def test_two_branches_cannot_share_a_code(api_as_owner) -> None:
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "branches"}),
        {"rows": [{"name": "One", "code": "DUP"}, {"name": "Two", "code": "DUP"}]},
        format="json",
    )
    assert response.status_code == 400
    assert "DUP" in response.data["detail"]


# --------------------------------------------------------------------------
# Wards and beds
# --------------------------------------------------------------------------


def test_beds_are_created_and_numbered_from_the_ward_code(api_as_owner, hospital) -> None:
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "departments"}),
        {"branch": str(hospital.main_branch.pk), "codes": ["MW"]},
        format="json",
    )
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "wards"}),
        {
            "branch": str(hospital.main_branch.pk),
            "rows": [{"department_code": "MW", "kind": "general", "count": 4}],
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["created"] == 4
    with hospital.scope():
        names = sorted(Bed.objects.values_list("name", flat=True))
    assert names == ["MW-01", "MW-02", "MW-03", "MW-04"]


def test_running_the_bed_step_again_continues_the_numbering(api_as_owner, hospital) -> None:
    """
    Not idempotent, and deliberately so — but it must not collide.

    "Add four more beds" is a real thing a ward does, so a second run adds
    rather than refusing. What it must never do is try to create MW-01 twice.
    """
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "departments"}),
        {"branch": str(hospital.main_branch.pk), "codes": ["MW"]},
        format="json",
    )
    url = reverse("api:v1:setup-step", kwargs={"step": "wards"})
    payload = {
        "branch": str(hospital.main_branch.pk),
        "rows": [{"department_code": "MW", "kind": "general", "count": 2}],
    }
    api_as_owner.post(url, payload, format="json")
    api_as_owner.post(url, payload, format="json")

    with hospital.scope():
        names = sorted(Bed.objects.values_list("name", flat=True))
    assert names == ["MW-01", "MW-02", "MW-03", "MW-04"]


def test_beds_cannot_be_put_in_a_laboratory(api_as_owner, hospital) -> None:
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "departments"}),
        {"branch": str(hospital.main_branch.pk), "codes": ["LAB"]},
        format="json",
    )
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "wards"}),
        {
            "branch": str(hospital.main_branch.pk),
            "rows": [{"department_code": "LAB", "kind": "general", "count": 2}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert "not a ward" in response.data["detail"]


def test_a_bed_cannot_be_saved_against_a_ward_at_another_branch(hospital) -> None:
    """The model's own check, independent of the wizard."""
    from django.core.exceptions import ValidationError

    with hospital.scope():
        ward = Department.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            name="Male Ward",
            code="MW",
            kind=Department.Kind.WARD,
        )
        bed = Bed(
            organisation=hospital.organisation,
            branch=hospital.second_branch,
            ward=ward,
            name="MW-01",
        )
        with pytest.raises(ValidationError, match="different branch"):
            bed.full_clean()


# --------------------------------------------------------------------------
# Services and prices
# --------------------------------------------------------------------------


def test_the_services_step_creates_a_priced_list(api_as_owner, hospital) -> None:
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "services"}),
        {"rows": [{"code": "GOPD-NEW"}, {"code": "FBC", "price_kobo": 700_000}]},
        format="json",
    )

    assert response.status_code == 200
    with hospital.scope():
        assert Service.objects.count() == 2
        # The self-pay payer is created on demand, never asked about.
        self_pay = Payer.objects.get(kind=Payer.Kind.SELF)
        fbc = Service.objects.get(code="FBC")
        price = Price.objects.get(service=fbc, payer=self_pay)
        assert price.amount_kobo == 700_000
        assert price.branch_id is None  # hospital-wide


def test_a_service_with_no_price_anywhere_is_refused(api_as_owner) -> None:
    """A service that silently costs nothing is how a hospital gives one away."""
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "services"}),
        {"rows": [{"code": "CUSTOM-THING", "name": "Something we invented"}]},
        format="json",
    )
    assert response.status_code == 400
    assert "price" in response.data["detail"]


def test_re_running_the_services_step_corrects_the_price(api_as_owner, hospital) -> None:
    url = reverse("api:v1:setup-step", kwargs={"step": "services"})
    api_as_owner.post(url, {"rows": [{"code": "FBC", "price_kobo": 600_000}]}, format="json")
    api_as_owner.post(url, {"rows": [{"code": "FBC", "price_kobo": 650_000}]}, format="json")

    with hospital.scope():
        prices = Price.objects.filter(service__code="FBC")
        # One row, corrected — not two rows with the same effective date.
        assert prices.count() == 1
        assert prices.first().amount_kobo == 650_000


def test_payers_are_created_without_tariffs(api_as_owner, hospital) -> None:
    """
    Setup saves the typing of HMO names and nothing else.

    A hospital's rates are negotiated and confidential; shipping a number
    would imply Curavia knows what they should be.
    """
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "payers"}),
        {"rows": [{"code": "HYGEIA"}, {"code": "NHIA"}]},
        format="json",
    )

    assert response.status_code == 200
    with hospital.scope():
        hygeia = Payer.objects.get(code="HYGEIA")
        assert hygeia.kind == Payer.Kind.HMO
        assert hygeia.requires_preauthorisation is True
        assert not Price.objects.filter(payer=hygeia).exists()


def test_a_hospital_has_exactly_one_self_pay_payer(api_as_owner, hospital) -> None:
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "payers"}),
        {"rows": [{"code": "SELF", "name": "Cash"}, {"code": "HYGEIA"}]},
        format="json",
    )
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "services"}),
        {"rows": [{"code": "FBC"}]},
        format="json",
    )
    with hospital.scope():
        assert Payer.objects.filter(kind=Payer.Kind.SELF).count() == 1


# --------------------------------------------------------------------------
# Price resolution
# --------------------------------------------------------------------------


@pytest.fixture
def priced(hospital):
    """A hospital with one service, a self-pay price and an HMO."""
    with hospital.scope():
        self_pay = Payer.objects.create(
            organisation=hospital.organisation, name="Self", code="SELF", kind=Payer.Kind.SELF
        )
        hmo = Payer.objects.create(
            organisation=hospital.organisation, name="Hygeia", code="HYG", kind=Payer.Kind.HMO
        )
        service = Service.objects.create(
            organisation=hospital.organisation, code="FBC", name="Full blood count"
        )
        Price.objects.create(
            organisation=hospital.organisation,
            service=service,
            payer=self_pay,
            branch=None,
            amount_kobo=650_000,
        )
        yield hospital, service, self_pay, hmo


def test_a_payer_with_no_price_of_its_own_falls_back_to_self_pay(priced) -> None:
    """
    The fallback that stops a hospital entering 900 rows per HMO.

    A scheme the hospital has not priced separately is charged the hospital's
    own rate, which is what happens at the desk today.
    """
    hospital, service, _self_pay, hmo = priced
    with hospital.scope():
        result = resolve_price(service_id=service.pk, payer_id=hmo.pk)

    assert result.amount_kobo == 650_000
    assert "self-pay rate" in result.source


def test_a_payer_price_beats_the_self_pay_price(priced) -> None:
    hospital, service, _self_pay, hmo = priced
    with hospital.scope():
        Price.objects.create(
            organisation=hospital.organisation,
            service=service,
            payer=hmo,
            branch=None,
            amount_kobo=500_000,
        )
        result = resolve_price(service_id=service.pk, payer_id=hmo.pk)

    assert result.amount_kobo == 500_000
    assert result.branch_specific is False


def test_a_branch_price_beats_a_hospital_wide_one(priced) -> None:
    hospital, service, _self_pay, hmo = priced
    with hospital.scope():
        Price.objects.create(
            organisation=hospital.organisation,
            service=service,
            payer=hmo,
            branch=hospital.main_branch,
            amount_kobo=900_000,
        )
        Price.objects.create(
            organisation=hospital.organisation,
            service=service,
            payer=hmo,
            branch=None,
            amount_kobo=500_000,
        )
        result = resolve_price(
            service_id=service.pk, payer_id=hmo.pk, branch_id=hospital.main_branch.pk
        )

    assert result.amount_kobo == 900_000
    assert result.branch_specific is True


def test_a_bill_can_still_be_recomputed_from_last_months_prices(priced) -> None:
    """
    The reason prices are effective-dated rather than a column.

    A hospital raises a bill, an HMO queries it six weeks later, and somebody
    has to answer what it cost on the day. With an overwritten column that
    question has no answer.
    """
    hospital, service, self_pay, _hmo = priced
    today = timezone.localdate()
    last_month = today - timedelta(days=30)

    with hospital.scope():
        # The old price, which was in force then and stopped yesterday.
        Price.objects.filter(service=service, payer=self_pay).update(
            effective_from=last_month, effective_to=today - timedelta(days=1)
        )
        # Today's price.
        Price.objects.create(
            organisation=hospital.organisation,
            service=service,
            payer=self_pay,
            branch=None,
            amount_kobo=800_000,
            effective_from=today,
        )

        now_price = resolve_price(service_id=service.pk, payer_id=self_pay.pk)
        then_price = resolve_price(
            service_id=service.pk, payer_id=self_pay.pk, on=last_month + timedelta(days=5)
        )

    assert now_price.amount_kobo == 800_000
    assert then_price.amount_kobo == 650_000


def test_an_unpriced_service_raises_rather_than_costing_nothing(priced) -> None:
    hospital, _service, _self_pay, _hmo = priced
    with hospital.scope():
        unpriced = Service.objects.create(
            organisation=hospital.organisation, code="NEW", name="Something new"
        )
        with pytest.raises(PriceNotFound):
            resolve_price(service_id=unpriced.pk)


def test_one_hospitals_price_list_is_invisible_to_another(hospital, other_hospital) -> None:
    """The tenant guard, on the new models."""
    with hospital.scope():
        payer = Payer.objects.create(
            organisation=hospital.organisation, name="Self", code="SELF", kind=Payer.Kind.SELF
        )
        service = Service.objects.create(
            organisation=hospital.organisation, code="FBC", name="Full blood count"
        )
        Price.objects.create(
            organisation=hospital.organisation,
            service=service,
            payer=payer,
            branch=None,
            amount_kobo=650_000,
        )

    with other_hospital.scope():
        assert Service.objects.count() == 0
        assert Price.objects.count() == 0
        assert not Service.objects.filter(pk=service.pk).exists()
        with pytest.raises(PriceNotFound):
            resolve_price(service_id=service.pk)


# --------------------------------------------------------------------------
# Finishing
# --------------------------------------------------------------------------


def test_an_optional_step_can_be_skipped_but_a_required_one_cannot(api_as_owner) -> None:
    skipped = api_as_owner.post(
        reverse("api:v1:setup-step-skip", kwargs={"step": "wards"}), {}, format="json"
    )
    assert skipped.status_code == 200

    refused = api_as_owner.post(
        reverse("api:v1:setup-step-skip", kwargs={"step": "profile"}), {}, format="json"
    )
    assert refused.status_code == 400
    assert refused.data["code"] == "step_required"


def test_the_wizard_reports_itself_complete_once_the_required_steps_are_done(
    api_as_owner, hospital
) -> None:
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "profile"}),
        {"name": hospital.organisation.name},
        format="json",
    )
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "branches"}),
        {"rows": [{"name": "Main", "code": "MAIN"}]},
        format="json",
    )
    api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "departments"}),
        {"branch": str(hospital.main_branch.pk), "codes": ["GOPD"]},
        format="json",
    )
    last = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "services"}),
        {"rows": [{"code": "GOPD-NEW"}]},
        format="json",
    )

    assert last.data["complete"] is True
    assert last.data["next_step"] in {"wards", "payers", "staff"}
    hospital.organisation.refresh_from_db()
    assert setup_is_complete(hospital.organisation) is True


def test_every_template_code_is_unique_within_its_list() -> None:
    """
    A duplicate code in a template list would silently overwrite a row
    during setup, because the applier matches on code.
    """
    for name, entries in (
        ("departments", templates.DEPARTMENT_TEMPLATES),
        ("services", templates.SERVICE_TEMPLATES),
        ("payers", templates.PAYER_TEMPLATES),
    ):
        codes = [entry.code for entry in entries]
        assert len(codes) == len(set(codes)), f"duplicate code in the {name} templates"


def test_every_service_template_names_a_real_module() -> None:
    from apps.core.modules import ALL_MODULE_CODES

    for template in templates.SERVICE_TEMPLATES:
        assert not template.module or template.module in ALL_MODULE_CODES, (
            f"{template.code} is attached to module {template.module!r}, which does not exist"
        )


def test_every_ward_bed_template_points_at_a_ward_department() -> None:
    wards = {t.code for t in templates.DEPARTMENT_TEMPLATES if t.kind == Department.Kind.WARD}
    for template in templates.WARD_BED_TEMPLATES:
        assert template.department_code in wards, (
            f"{template.department_code} has bed defaults but is not a ward department"
        )


def test_every_bed_kind_in_the_templates_exists() -> None:
    valid = {choice.value for choice in Bed.Kind}
    for template in templates.WARD_BED_TEMPLATES:
        for kind, _count in template.beds:
            assert kind in valid, f"{kind!r} is not a bed kind"


def test_setup_module_writes_nothing_unscoped() -> None:
    """
    By the time the wizard runs the hospital exists and the caller is signed
    in to it, so there is no excuse for an unscoped write. This is the guard
    that keeps it that way — the allowlist script checks file names, and this
    checks the one file most likely to be tempted.
    """
    source = setup.__file__ or ""
    assert source
    with open(source, encoding="utf-8") as handle:
        body = handle.read()

    # Built from pieces rather than written out, so this file does not itself
    # trip scripts/check-cross-tenant-allowlist.sh — which scans for the
    # literal strings and would otherwise flag the test that enforces them.
    manager = "." + "unscoped"
    context_manager = "unscoped" + "("
    assert manager not in body, f"{source} uses the unscoped manager"
    assert context_manager not in body, f"{source} uses the unscoped context manager"


def test_every_declared_step_has_a_way_to_apply_it() -> None:
    """
    The regression test for a bug found by clicking through the wizard.

    "staff" was in SETUP_STEPS but not in the applier map, so the last button
    of a seven-step wizard answered "there is no setup step called 'staff'".
    A declared step with no applier fails at the most expensive possible
    moment, and the two lists are in different files, so nothing but a test
    keeps them together.

    Skip-only steps are allowed to have no applier, but they must then be
    optional — a required step that can only be skipped is a contradiction.
    """
    from apps.core.setup_steps import SETUP_STEPS
    from apps.onboarding.api.setup_views import APPLY

    for step in SETUP_STEPS:
        assert step.key in APPLY or not step.required, (
            f"Setup step {step.key!r} is required but has no applier in "
            f"apps/onboarding/api/setup_views.APPLY, so the wizard cannot finish it."
        )

    unknown = set(APPLY) - {step.key for step in SETUP_STEPS}
    assert not unknown, f"APPLY has appliers for steps that do not exist: {sorted(unknown)}"


def test_the_last_step_of_the_wizard_actually_works(api_as_owner) -> None:
    """Clicked by every hospital that completes setup, and it 400'd."""
    response = api_as_owner.post(
        reverse("api:v1:setup-step", kwargs={"step": "staff"}), {"rows": []}, format="json"
    )
    assert response.status_code == 200
    assert response.data["next_step"] != "staff"
