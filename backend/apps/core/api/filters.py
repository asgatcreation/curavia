"""
Explicit filter sets.

``filterset_fields`` is convenient but wrong here. For a foreign key it builds
a ``ModelChoiceFilter``, which evaluates the related model's default manager to
populate its choices — and that manager refuses to run without a hospital in
scope, which is exactly the protection we want. Two consequences, both bad:
schema generation fails, and the published schema would otherwise advertise a
dropdown of every branch, which is a list of a hospital's facilities in a
document anyone with the API docs can read.

A ``UUIDFilter`` is the honest shape: the client already knows the branch id it
is working in, and the tenant-scoped queryset decides whether that id is
visible at all.
"""

from __future__ import annotations

from django_filters import rest_framework as filters

from apps.core.audit import AuditEvent
from apps.core.models import Department


class DepartmentFilterSet(filters.FilterSet):
    branch = filters.UUIDFilter(field_name="branch_id")
    kind = filters.ChoiceFilter(choices=Department.Kind.choices)
    is_active = filters.BooleanFilter()

    class Meta:
        model = Department
        fields = ["branch", "kind", "is_active"]


class AuditEventFilterSet(filters.FilterSet):
    """
    Filters for the audit screen.

    ``patient`` and ``actor`` are the two an auditor actually uses: "everything
    that happened to this patient" and "everything this member of staff did".
    ``flag`` surfaces the rows that deserve a second look — break-the-glass
    access, cross-branch reads, overridden warnings.
    """

    action = filters.CharFilter(lookup_expr="iexact")
    actor = filters.UUIDFilter(field_name="actor_id")
    patient = filters.UUIDFilter(field_name="patient_id")
    object_type = filters.CharFilter(lookup_expr="iexact")
    flag = filters.CharFilter(field_name="flags", lookup_expr="contains")
    since = filters.IsoDateTimeFilter(field_name="at", lookup_expr="gte")
    until = filters.IsoDateTimeFilter(field_name="at", lookup_expr="lte")

    class Meta:
        model = AuditEvent
        fields = ["action", "actor", "patient", "object_type", "flag", "since", "until"]
