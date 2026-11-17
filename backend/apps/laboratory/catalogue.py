"""
Putting the starting catalogue into a hospital, and editing it afterwards.

Two jobs. ``ensure_panels`` is called when the setup wizard creates services,
and fills in the laboratory's side of whichever laboratory tests that hospital
chose. Everything else here is what the catalogue screen calls when a
supervisor edits a range.

**Idempotent, on purpose.** The wizard's service step can be re-run — a
hospital adds three more tests a week after going live — and running it again
must not duplicate analytes or quietly undo a supervisor's edits. So a panel
that already exists is left alone, and only a missing one is created. A
hospital that deletes an analyte it does not do has deleted it; re-running
setup is not an invitation to put it back.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.catalogue.models import Service, ServiceCategory
from apps.core.models import Organisation
from apps.laboratory import catalogue_data as seed
from apps.laboratory.models import Analyte, LabPanel, ReferenceRange


class CatalogueError(Exception):
    """Something a supervisor needs told in plain words."""


@dataclass(frozen=True, slots=True)
class SeedResult:
    panels: int
    analytes: int
    ranges: int

    @property
    def detail(self) -> str:
        return f"{self.panels} panels, {self.analytes} analytes, {self.ranges} ranges."


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


@transaction.atomic
def ensure_panels(organisation: Organisation) -> SeedResult:
    """
    Give every laboratory service that has a template its panel.

    Called from the wizard's service step, which already runs inside the
    hospital being built, so the ordinary tenant-scoped manager is correct
    here. The organisation is still passed explicitly on each write, the way
    ``apps.onboarding.setup`` does, so a row can never be created against
    whatever happened to be in scope.
    """
    services = {
        service.code: service
        for service in Service.objects.filter(
            organisation=organisation, category=ServiceCategory.LABORATORY
        )
    }
    if not services:
        return SeedResult(0, 0, 0)

    existing = set(
        LabPanel.objects.filter(organisation=organisation).values_list("service__code", flat=True)
    )

    panels = analytes = ranges = 0
    for code, template in seed.PANELS_BY_SERVICE_CODE.items():
        service = services.get(code)
        if service is None or code in existing:
            continue

        panel = LabPanel.objects.create(
            organisation=organisation,
            service=service,
            specimen=template.specimen,
            container=template.container,
            volume_ml=_decimal(template.volume_ml),
            fasting_required=template.fasting_required,
            preparation=template.preparation,
            handling=template.handling,
            turnaround_minutes=template.turnaround_minutes,
            from_template=True,
        )
        panels += 1

        for position, analyte_template in enumerate(template.analytes, start=1):
            analyte = Analyte.objects.create(
                organisation=organisation,
                panel=panel,
                code=analyte_template.code,
                name=analyte_template.name,
                value_type=analyte_template.value_type,
                unit=analyte_template.unit,
                decimal_places=analyte_template.decimal_places,
                options=[dict(option) for option in analyte_template.options],
                sort_order=position,
                from_template=True,
            )
            analytes += 1

            for range_template in analyte_template.ranges:
                ReferenceRange.objects.create(
                    organisation=organisation,
                    analyte=analyte,
                    sex=range_template.sex,
                    age_min_days=range_template.age_min_days,
                    age_max_days=range_template.age_max_days,
                    low=_decimal(range_template.low),
                    high=_decimal(range_template.high),
                    critical_low=_decimal(range_template.critical_low),
                    critical_high=_decimal(range_template.critical_high),
                    note=range_template.note,
                    from_template=True,
                )
                ranges += 1

    return SeedResult(panels, analytes, ranges)


# --------------------------------------------------------------------------
# Editing, from the catalogue screen
# --------------------------------------------------------------------------


def save_range(instance: ReferenceRange) -> ReferenceRange:
    """
    Validate and save one range, turning a model error into a plain sentence.

    ``full_clean`` is what enforces the no-overlap rule, so every write goes
    through here rather than calling ``save`` directly. The screen shows the
    message as it comes back.

    Saving also clears ``from_template``: once a supervisor has touched a
    row it is the hospital's, not ours, and ``unreviewed_count`` would
    otherwise keep nagging about ranges they have already reviewed.
    """
    instance.from_template = False
    try:
        instance.full_clean()
    except ValidationError as error:
        raise CatalogueError(_first_message(error)) from error
    instance.save()
    return instance


def save_analyte(instance: Analyte) -> Analyte:
    """The same, for an analyte and its list of coded answers."""
    instance.from_template = False
    try:
        instance.full_clean()
    except ValidationError as error:
        raise CatalogueError(_first_message(error)) from error
    instance.save()
    return instance


def _first_message(error: ValidationError) -> str:
    """
    One sentence out of a Django validation error.

    The screen puts this above the form. Field-by-field messages would be
    better, and the serializer already does that for field errors; this is
    for the rules that are about the row as a whole, such as the overlap.
    """
    if hasattr(error, "message_dict"):
        for messages in error.message_dict.values():
            if messages:
                return str(messages[0])
    return str(error.messages[0]) if error.messages else "That cannot be saved."


def panel_for_service(service: Service) -> LabPanel | None:
    """The panel, or None where the laboratory has not set this test up."""
    return LabPanel.objects.filter(service=service).first()


def unreviewed_count(organisation: Organisation) -> int:
    """
    How many ranges the hospital has never touched.

    The catalogue screen shows this as a nudge: Curavia's starting ranges are
    ordinary intervals, and the laboratory supervisor is the person who knows
    what their own analyser reports. See ``catalogue_data``.
    """
    return ReferenceRange.objects.filter(organisation=organisation, from_template=True).count()
