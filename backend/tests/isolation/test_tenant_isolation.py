"""
The tenant isolation attack suite.

Every test here deliberately tries to reach another hospital's data. They are
written as attacks rather than as "does the filter work", because the filter
working is not the claim being made — the claim is that there is no route in.

Covered: the ORM, the HTTP API, forged headers, background jobs, the audit log,
and WebSocket group names. If any of these passes, a hospital can see another
hospital's patients, which is the single worst thing this system could do. So
these run on every push and a failure is a failing build, not a bug report.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import Role, StaffMembership
from apps.core import audit
from apps.core.models import Branch, Department
from apps.core.tasks import TenantTask
from apps.core.tenancy import TenantScopeMissing, unscoped

pytestmark = [pytest.mark.isolation, pytest.mark.django_db]


# --------------------------------------------------------------------------
# The ORM
# --------------------------------------------------------------------------


def test_querying_without_a_scope_raises_rather_than_leaking(hospital) -> None:
    """
    The whole design rests on this.

    A developer who forgets to establish a scope must get a crash. The failure
    mode we are refusing is the quiet one, where a queryset returns every
    hospital's rows and nobody notices for a year.
    """
    with pytest.raises(TenantScopeMissing) as exc:
        list(Branch.objects.all())

    # The message has to tell whoever hits it what to do about it.
    assert "tenant scope" in str(exc.value)
    assert "tenant_scope" in str(exc.value)


def test_scoped_query_sees_only_its_own_hospital(hospital, other_hospital) -> None:
    with hospital.scope():
        names = set(Branch.objects.values_list("name", flat=True))
        assert names == {"Main Branch", "Second Branch"}
        assert Branch.objects.count() == 2

    with other_hospital.scope():
        assert Branch.objects.count() == 2

    # Both hospitals have a branch called "Main Branch"; neither sees four.
    #
    # Scoped to these two organisations rather than counting every branch in
    # the database. A bare count() asserts "nothing else exists", which is a
    # claim about the whole test run, not about tenant isolation — and it broke
    # the moment another module's fixture committed a hospital of its own.
    with unscoped(reason="test: confirm both hospitals really do exist"):
        assert (
            Branch.unscoped.filter(
                organisation__in=[hospital.organisation, other_hospital.organisation]
            ).count()
            == 4
        )


def test_fetching_another_hospitals_row_by_its_id_fails(hospital, other_hospital) -> None:
    """
    Knowing the UUID of another hospital's branch must not be enough.

    This is why primary keys are UUIDv7 rather than integers — but guessability
    is defence in depth, not the defence.
    """
    target = other_hospital.main_branch.pk
    with hospital.scope(), pytest.raises(Branch.DoesNotExist):
        Branch.objects.get(pk=target)


def test_reverse_relations_are_scoped(hospital, other_hospital) -> None:
    """
    Reverse relations go through the scoped default manager.

    A model whose ``base_manager_name`` was wrong would leak here while every
    direct query still looked correct.
    """
    with hospital.scope():
        assert hospital.organisation.branch_set.count() == 2
        assert hospital.main_branch.department_set.count() == 1


def test_forward_foreign_keys_still_work(hospital) -> None:
    """
    The other half of that trade-off.

    ``base_manager_name = "unscoped"`` exists so ordinary attribute access does
    not explode. If this breaks, the admin and the migrations break with it.
    """
    with hospital.scope():
        branch = Branch.objects.first()
        assert branch is not None
        assert branch.organisation.pk == hospital.organisation.pk


def test_update_cannot_reach_across_hospitals(hospital, other_hospital) -> None:
    """A bulk update is scoped too — it is the same manager."""
    with hospital.scope():
        changed = Branch.objects.update(state="Ogun")
        assert changed == 2

    other_hospital.main_branch.refresh_from_db()
    assert other_hospital.main_branch.state == "Lagos"


def test_staff_memberships_are_scoped(hospital, other_hospital) -> None:
    hospital.staff("doctor")
    other_hospital.staff("doctor")
    with hospital.scope():
        assert StaffMembership.objects.count() == 1
    with other_hospital.scope():
        assert StaffMembership.objects.count() == 1


def test_roles_are_scoped(hospital, other_hospital) -> None:
    """
    Roles are per-hospital records, so one hospital renaming "Front desk"
    cannot affect another's.
    """
    with hospital.scope():
        mine = set(Role.objects.values_list("slug", flat=True))
    with other_hospital.scope():
        theirs = set(Role.objects.values_list("slug", flat=True))
    assert mine == theirs  # same templates
    with hospital.scope():
        assert Role.objects.count() == len(mine)  # but not twice as many rows


# --------------------------------------------------------------------------
# The HTTP API
# --------------------------------------------------------------------------


def test_api_lists_only_your_own_branches(hospital, other_hospital, as_staff) -> None:
    user = hospital.staff("owner")
    client = as_staff(user, organisation=hospital.organisation, branch=hospital.main_branch)

    response = client.get(reverse("api:v1:branch-list"))
    assert response.status_code == 200

    returned = {row["id"] for row in response.data["results"]}
    assert returned == {str(hospital.main_branch.pk), str(hospital.second_branch.pk)}
    assert str(other_hospital.main_branch.pk) not in returned


def test_api_detail_for_another_hospitals_object_is_404_not_403(
    hospital, other_hospital, as_staff
) -> None:
    """
    404, deliberately.

    A 403 would confirm that the object exists, which tells an attacker that a
    hospital with that branch id is a customer. The scoped queryset produces a
    404 naturally, which is the behaviour we want.
    """
    user = hospital.staff("owner")
    client = as_staff(user, organisation=hospital.organisation, branch=hospital.main_branch)

    url = reverse("api:v1:branch-detail", kwargs={"pk": other_hospital.main_branch.pk})
    assert client.get(url).status_code == 404


def test_forged_organisation_header_is_refused(hospital, other_hospital, as_staff) -> None:
    """
    The client does not get to choose its hospital.

    A staff member of one hospital asking, by header, to act as another gets
    403 — not a silent fall back to their own hospital, which would mask a
    broken or hostile client.
    """
    user = hospital.staff("owner")
    client = as_staff(user, organisation=other_hospital.organisation)

    response = client.get(reverse("api:v1:me"))
    assert response.status_code == 403
    assert "hospital" in str(response.data["detail"]).lower()


def test_forged_branch_header_is_refused(hospital, other_hospital, as_staff) -> None:
    user = hospital.staff("front_desk", branch=hospital.main_branch)
    client = as_staff(user, organisation=hospital.organisation, branch=other_hospital.main_branch)
    response = client.get(reverse("api:v1:me"))
    assert response.status_code == 403


def test_branch_header_for_a_branch_you_do_not_work_at_is_refused(hospital, as_staff) -> None:
    """Within the same hospital, a branch you are not a member of is still closed."""
    user = hospital.staff("front_desk", branch=hospital.main_branch)
    client = as_staff(user, organisation=hospital.organisation, branch=hospital.second_branch)
    assert client.get(reverse("api:v1:me")).status_code == 403


def test_branch_view_all_opens_every_branch_in_your_own_hospital_only(
    hospital, other_hospital, as_staff
) -> None:
    """
    ``branch.view_all`` widens access inside one hospital and never across two.
    """
    user = hospital.staff("auditor", branch=hospital.main_branch)
    client = as_staff(user, organisation=hospital.organisation, branch=hospital.second_branch)
    # Allowed: the auditor holds branch.view_all.
    assert client.get(reverse("api:v1:me")).status_code == 200

    client = as_staff(user, organisation=other_hospital.organisation)
    assert client.get(reverse("api:v1:me")).status_code == 403


def test_departments_are_filtered_to_the_active_branch(hospital, as_staff) -> None:
    with hospital.scope():
        Department.unscoped.create(
            organisation=hospital.organisation,
            branch=hospital.second_branch,
            name="Second Branch GOPD",
            code="GOPD2",
        )
    user = hospital.staff("facility_admin", branches=[hospital.main_branch, hospital.second_branch])
    # facility_admin holds branch.view_all, so it sees both.
    client = as_staff(user, organisation=hospital.organisation, branch=hospital.main_branch)
    response = client.get(reverse("api:v1:department-list"))
    assert response.status_code == 200
    assert response.data["count"] == 2

    # The front desk does not hold branch.view_all, so it sees one — but it
    # cannot read departments at all, which is itself the right answer.
    clerk = hospital.staff("front_desk", branch=hospital.main_branch)
    client = as_staff(clerk, organisation=hospital.organisation, branch=hospital.main_branch)
    assert client.get(reverse("api:v1:department-list")).status_code == 403


# --------------------------------------------------------------------------
# The audit log
# --------------------------------------------------------------------------


def test_audit_log_is_scoped_to_your_own_hospital(hospital, other_hospital, as_staff) -> None:
    """
    ``AuditEvent`` is not a tenant-scoped model — platform events have no
    hospital — so its filter is written by hand in the view. That makes it
    exactly the kind of place a leak hides, which is why it is tested here.
    """
    with hospital.scope():
        audit.record(
            "patient.view", organisation_id=hospital.organisation.pk, object_type="Patient"
        )
    with other_hospital.scope():
        audit.record(
            "patient.view", organisation_id=other_hospital.organisation.pk, object_type="Patient"
        )

    auditor = hospital.staff("auditor")
    client = as_staff(auditor, organisation=hospital.organisation, branch=hospital.main_branch)

    response = client.get(reverse("api:v1:audit-list"))
    assert response.status_code == 200
    organisations = {
        row.get("organisation") for row in response.data["results"] if "organisation" in row
    }
    assert other_hospital.organisation.pk not in organisations
    # Every row belongs to this hospital's chain.
    from apps.core.audit import AuditEvent

    returned_ids = [row["id"] for row in response.data["results"]]
    assert (
        AuditEvent.objects.filter(
            id__in=returned_ids, organisation=other_hospital.organisation
        ).count()
        == 0
    )


# --------------------------------------------------------------------------
# Background jobs
# --------------------------------------------------------------------------


def test_a_tenant_task_without_an_organisation_id_refuses_to_run() -> None:
    """
    A task body runs with no request, so the scope has to be passed explicitly.

    A task that forgot it would otherwise run unscoped — which with the manager
    guard means it crashes, but crashing inside a retrying worker at 2am is a
    worse failure than refusing at the call site.
    """
    from celery import shared_task

    @shared_task(base=TenantTask, name="tests.needs_scope")
    def needs_scope(*, organisation_id: str) -> str:
        return str(organisation_id)

    with pytest.raises(ValueError, match="organisation_id"):
        needs_scope.apply(kwargs={}).get()


def test_a_tenant_task_runs_inside_the_scope_it_was_given(hospital) -> None:
    from celery import shared_task

    @shared_task(base=TenantTask, name="tests.counts_branches")
    def counts_branches(*, organisation_id: str) -> int:
        return Branch.objects.count()

    result = counts_branches.apply(kwargs={"organisation_id": str(hospital.organisation.pk)})
    assert result.get() == 2


# --------------------------------------------------------------------------
# WebSockets
# --------------------------------------------------------------------------


def test_websocket_group_names_are_namespaced_by_hospital(hospital, other_hospital) -> None:
    """
    Live updates are the easiest place to leak across tenants, and the hardest
    to notice: no HTTP test would catch two hospitals sharing a group name.

    So the group name always contains the organisation id.
    """
    from apps.core.ws import NotificationConsumer

    def group_for(org_id, branch_id):
        consumer = NotificationConsumer(scope={})
        consumer.organisation_id = org_id
        consumer.branch_id = branch_id
        return consumer.group_name()

    mine = group_for(hospital.organisation.pk, hospital.main_branch.pk)
    theirs = group_for(other_hospital.organisation.pk, other_hospital.main_branch.pk)

    assert str(hospital.organisation.pk) in mine
    assert mine != theirs
    assert str(other_hospital.organisation.pk) not in mine


def test_branch_scoped_streams_include_the_branch(hospital) -> None:
    from apps.core.ws import TenantConsumer

    class WardBoard(TenantConsumer):
        group_suffix = "ward-board"
        branch_scoped = True

    consumer = WardBoard(scope={})
    consumer.organisation_id = hospital.organisation.pk
    consumer.branch_id = hospital.main_branch.pk
    name = consumer.group_name()

    assert name == (f"org.{hospital.organisation.pk}.branch.{hospital.main_branch.pk}.ward-board")


# --------------------------------------------------------------------------
# Platform staff
# --------------------------------------------------------------------------


def test_platform_staff_cannot_read_a_hospital_by_default(
    hospital, platform_support, as_staff
) -> None:
    """
    Curavia's own staff see no patient data unless the hospital grants it.

    This is the promise the platform level is sold on, so it is tested rather
    than asserted in a README.
    """
    client = as_staff(platform_support, organisation=hospital.organisation)
    response = client.get(reverse("api:v1:branch-list"))
    # No membership in that hospital, so no tenant scope and no access.
    assert response.status_code in {403, 404}


def test_support_grant_opens_access_and_expiry_closes_it(hospital, platform_support) -> None:
    from datetime import timedelta

    from django.utils import timezone

    from apps.core.models import SupportAccessGrant
    from apps.core.rules import is_platform_support_with_grant

    granter = hospital.staff("owner")

    with hospital.scope():
        assert not is_platform_support_with_grant(platform_support, hospital.main_branch)

        grant = SupportAccessGrant.unscoped.create(
            organisation=hospital.organisation,
            platform_user=platform_support,
            granted_by=granter,
            reason="Investigating a reported problem with the queue board",
            expires_at=timezone.now() + timedelta(hours=2),
        )
        assert is_platform_support_with_grant(platform_support, hospital.main_branch)

        # Expiry needs no job to have run.
        grant.expires_at = timezone.now() - timedelta(minutes=1)
        grant.save(update_fields=["expires_at"])
        assert not is_platform_support_with_grant(platform_support, hospital.main_branch)
