"""
The group dashboard's figures.

The tests here are mostly about what the endpoint REFUSES to say. An owner
decides whether to put a second doctor on a Tuesday from this screen, so a
number that looks real and is not would be worse than no screen — and once
one tile is invented, none of the others can be trusted either.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.catalogue.models import Payer, Price, Service
from apps.core.api.group_views import PENDING_MEASURES
from apps.core.models import Bed, Department
from apps.core.setup_steps import REQUIRED_STEP_KEYS, mark_step

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def api_as_owner(api, hospital):
    user = hospital.staff("owner")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


def summary(api):
    response = api.get(reverse("api:v1:group-summary"))
    assert response.status_code == 200
    return response.data


# --------------------------------------------------------------------------
# What it counts
# --------------------------------------------------------------------------


def test_it_counts_the_hospitals_real_structure(api_as_owner, hospital) -> None:
    data = summary(api_as_owner)

    assert data["organisation"]["name"] == hospital.organisation.name
    assert data["totals"]["branches"] == 2
    assert data["totals"]["departments"] == 1
    assert len(data["branches"]) == 2
    # The main branch is listed first: it is the one an owner looks at.
    assert data["branches"][0]["is_main"] is True


def test_beds_are_counted_per_branch(api_as_owner, hospital) -> None:
    with hospital.scope():
        ward = Department.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            name="Male Ward",
            code="MW",
            kind=Department.Kind.WARD,
        )
        for number in range(1, 4):
            Bed.objects.create(
                organisation=hospital.organisation,
                branch=hospital.main_branch,
                ward=ward,
                name=f"MW-0{number}",
            )

    data = summary(api_as_owner)
    main = next(row for row in data["branches"] if row["is_main"])
    other = next(row for row in data["branches"] if not row["is_main"])

    assert main["beds"] == 3
    assert main["wards"] == 1
    assert other["beds"] == 0
    assert data["totals"]["beds"] == 3


def test_a_deactivated_bed_is_not_counted(api_as_owner, hospital) -> None:
    """A bed being deep-cleaned is not capacity."""
    with hospital.scope():
        ward = Department.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            name="Ward",
            code="W",
            kind=Department.Kind.WARD,
        )
        Bed.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            ward=ward,
            name="W-01",
            is_active=False,
        )

    assert summary(api_as_owner)["totals"]["beds"] == 0


def test_somebody_working_at_two_branches_counts_at_both(api_as_owner, hospital) -> None:
    """
    Staff are counted from the membership's branches, not from a column.

    A locum who covers both sites is on duty at both, and a per-branch number
    that said otherwise would under-report the smaller branch — which is the
    one an owner is deciding about.
    """
    hospital.staff("doctor", branches=[hospital.main_branch, hospital.second_branch])

    data = summary(api_as_owner)
    assert all(row["staff_active"] >= 1 for row in data["branches"])


def test_staff_who_have_not_enrolled_in_mfa_are_counted(api_as_owner, hospital) -> None:
    hospital.staff("nurse")
    data = summary(api_as_owner)
    assert data["totals"]["staff_without_mfa"] == data["totals"]["staff_active"]


# --------------------------------------------------------------------------
# What it refuses to say
# --------------------------------------------------------------------------


def test_the_figures_it_cannot_measure_are_named_rather_than_invented(
    api_as_owner,
) -> None:
    """
    The point of the whole endpoint.

    Patients waiting, bed occupancy and money have no source until later
    milestones. They come back as `pending`, with the milestone attached, so
    the screen can say so instead of showing a plausible number.
    """
    data = summary(api_as_owner)

    keys = {row["key"] for row in data["pending"]}
    assert {"waiting", "occupancy", "billed_today", "outstanding"} <= keys
    for row in data["pending"]:
        assert row["milestone"], f"{row['key']} does not say when it arrives"

    # And none of them has leaked into the real figures.
    assert keys.isdisjoint(data["totals"])


def test_no_patient_is_named_anywhere_in_the_response(api_as_owner, hospital) -> None:
    """
    The owner role sees counts across every branch without opening a record,
    and this endpoint has nothing to leak. Asserted rather than assumed,
    because a later addition could change that quietly.
    """
    doctor = hospital.staff("doctor")
    body = str(summary(api_as_owner))

    assert doctor.email not in body
    assert doctor.last_name not in body
    for word in ("patient_id", "mrn", "diagnosis", "date_of_birth"):
        assert word not in body.lower()


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


def test_unfinished_setup_is_the_first_thing_it_says(api_as_owner) -> None:
    messages = [alert["message"] for alert in summary(api_as_owner)["alerts"]]
    assert any("Setup is not finished" in message for message in messages)


def test_a_finished_setup_stops_nagging(api_as_owner, hospital) -> None:
    for key in REQUIRED_STEP_KEYS:
        mark_step(hospital.organisation, key)

    messages = [alert["message"] for alert in summary(api_as_owner)["alerts"]]
    assert not any("Setup is not finished" in message for message in messages)


def test_a_service_with_no_price_is_flagged_because_it_cannot_be_billed(
    api_as_owner, hospital
) -> None:
    with hospital.scope():
        Service.objects.create(
            organisation=hospital.organisation, code="ORPHAN", name="Unpriced thing"
        )

    alerts = summary(api_as_owner)["alerts"]
    assert any("no price" in alert["message"] for alert in alerts)
    assert any(alert["tone"] == "critical" for alert in alerts)


def test_a_priced_service_is_not_flagged(api_as_owner, hospital) -> None:
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

    messages = [alert["message"] for alert in summary(api_as_owner)["alerts"]]
    assert not any("no price" in message for message in messages)


def test_a_branch_with_no_departments_is_named(api_as_owner, hospital) -> None:
    alerts = summary(api_as_owner)["alerts"]
    # The factory gives the main branch one department and the second none.
    assert any(alert["branch"] == hospital.second_branch.name for alert in alerts)


# --------------------------------------------------------------------------
# Permissions and tenancy
# --------------------------------------------------------------------------


def test_a_cashier_cannot_see_the_group_dashboard(api, hospital) -> None:
    api.force_authenticate(user=hospital.staff("cashier"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    assert api.get(reverse("api:v1:group-summary")).status_code == 403


def test_it_never_counts_another_hospitals_branches(api_as_owner, hospital, other_hospital) -> None:
    other_hospital.staff("doctor")
    data = summary(api_as_owner)

    assert data["totals"]["branches"] == 2
    names = {row["name"] for row in data["branches"]}
    assert other_hospital.main_branch.name not in names or len(names) == 2


def test_every_pending_measure_is_shaped_the_way_the_client_expects() -> None:
    """A tile with no label or milestone renders as an empty box."""
    for measure in PENDING_MEASURES:
        assert measure["key"] and measure["label"] and measure["milestone"]
