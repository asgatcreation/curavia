"""
The laboratory catalogue: what a test is made of, and what normal means.

Every name here is synthetic.

This file is about one thing: **a result is judged against the right range.**
Everything else in the laboratory module is workflow and can be corrected by
a person. Flagging cannot, because nobody checks a number the system has
already called normal. So these tests are mostly about the two rules that
decide which range applies — no two ranges for one sex may overlap, and a
range for a patient's own sex beats a general one — and about the cases where
the honest answer is "no range applies" rather than a guess.

The age cases are the ones worth reading. A haemoglobin of 12 g/dL is normal
in a woman, normal in a four-year-old, and dangerously low in a newborn. One
number, three answers, and the only thing that distinguishes them is the
patient's date of birth.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.catalogue.models import Service, ServiceCategory
from apps.laboratory import catalogue, ranges
from apps.laboratory.catalogue_data import PANELS_BY_SERVICE_CODE
from apps.laboratory.models import (
    Analyte,
    LabPanel,
    OptionFlag,
    RangeSex,
    ReferenceRange,
    Specimen,
    ValueType,
)
from apps.laboratory.ranges import Level
from apps.patients.models import Sex

pytestmark = [pytest.mark.django_db]


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------

YEAR = 365
ADULT = 30 * YEAR


def make_service(hospital, *, code="FBC", name="Full blood count") -> Service:
    with hospital.scope():
        return Service.objects.create(
            organisation=hospital.organisation,
            code=code,
            name=name,
            category=ServiceCategory.LABORATORY,
            module="lab",
        )


def make_panel(hospital, **kwargs) -> LabPanel:
    service = kwargs.pop("service", None) or make_service(hospital, **kwargs)
    with hospital.scope():
        return LabPanel.objects.create(
            organisation=hospital.organisation,
            service=service,
            specimen=Specimen.WHOLE_BLOOD_EDTA,
            container="Purple top (EDTA), 4 mL",
        )


def make_analyte(hospital, panel, **kwargs) -> Analyte:
    fields = {
        "code": "HB",
        "name": "Haemoglobin",
        "unit": "g/dL",
        "decimal_places": 1,
        "value_type": ValueType.NUMERIC,
    }
    fields.update(kwargs)
    with hospital.scope():
        return Analyte.objects.create(organisation=hospital.organisation, panel=panel, **fields)


def add_range(hospital, analyte, **kwargs) -> ReferenceRange:
    """Create a range through the service layer, so validation runs."""
    with hospital.scope():
        band = ReferenceRange(organisation=hospital.organisation, analyte=analyte, **kwargs)
        return catalogue.save_range(band)


@pytest.fixture
def haemoglobin(hospital):
    """
    Haemoglobin with the bands Curavia ships: newborn, infant, child, then a
    general adult band with a female and a male band over the top of it.
    """
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="14.0", high="22.0", critical_low="10.0", age_max_days=28)
    add_range(
        hospital,
        analyte,
        low="9.5",
        high="13.5",
        critical_low="7.0",
        age_min_days=29,
        age_max_days=YEAR - 1,
    )
    add_range(
        hospital,
        analyte,
        low="11.0",
        high="14.0",
        critical_low="7.0",
        age_min_days=YEAR,
        age_max_days=12 * YEAR,
    )
    add_range(
        hospital,
        analyte,
        low="11.5",
        high="18.0",
        critical_low="7.0",
        critical_high="20.0",
        age_min_days=12 * YEAR + 1,
    )
    add_range(
        hospital,
        analyte,
        low="11.5",
        high="16.5",
        critical_low="7.0",
        critical_high="20.0",
        sex=RangeSex.FEMALE,
        age_min_days=12 * YEAR + 1,
    )
    add_range(
        hospital,
        analyte,
        low="13.0",
        high="18.0",
        critical_low="7.0",
        critical_high="20.0",
        sex=RangeSex.MALE,
        age_min_days=12 * YEAR + 1,
    )
    return analyte


# --------------------------------------------------------------------------
# Choosing the range: age
# --------------------------------------------------------------------------


def test_the_same_haemoglobin_is_judged_differently_at_different_ages(hospital, haemoglobin):
    """One number, three answers. This is the whole point of the module."""
    with hospital.scope():
        loaded = list(haemoglobin.ranges.all())

        newborn = ranges.flag(haemoglobin, "12.0", sex=Sex.FEMALE, age_days=3, ranges=loaded)
        child = ranges.flag(haemoglobin, "12.0", sex=Sex.FEMALE, age_days=4 * YEAR, ranges=loaded)
        adult = ranges.flag(haemoglobin, "12.0", sex=Sex.FEMALE, age_days=ADULT, ranges=loaded)

    assert newborn.level is Level.LOW
    assert child.level is Level.NORMAL
    assert adult.level is Level.NORMAL


def test_a_newborns_very_low_haemoglobin_is_critical_not_merely_low(hospital, haemoglobin):
    with hospital.scope():
        judged = ranges.flag(haemoglobin, "9.5", sex=Sex.MALE, age_days=2)
    assert judged.level is Level.CRITICAL_LOW
    assert judged.is_critical
    assert judged.marker == "LL"


def test_the_bands_leave_no_gap_a_real_patient_can_fall_into(hospital, haemoglobin):
    """
    Every age from birth to a hundred matches exactly one band.

    A gap is not a crash — it is an UNKNOWN flag on a result that should have
    been flagged, which nobody notices. So it is checked day by day across
    the boundaries and then yearly.
    """
    checked = [*range(40), YEAR - 1, YEAR, 12 * YEAR, 12 * YEAR + 1]
    checked += [year * YEAR for year in range(1, 100)]
    with hospital.scope():
        loaded = list(haemoglobin.ranges.all())
        missing = [
            age
            for age in checked
            if ranges.range_for(haemoglobin, sex=Sex.FEMALE, age_days=age, ranges=loaded) is None
        ]
    assert missing == []


def test_an_unknown_date_of_birth_matches_only_an_unlimited_band(hospital):
    """
    No date of birth must never be read as "probably an adult".

    Flagging a baby's result against an adult range is the one mistake this
    module exists to prevent, so the honest answer is no range at all.
    """
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="11.5", high="16.5", age_min_days=12 * YEAR + 1)
    with hospital.scope():
        assert ranges.range_for(analyte, sex=Sex.FEMALE, age_days=None) is None
        assert ranges.flag(analyte, "12.0", sex=Sex.FEMALE, age_days=None).level is Level.UNKNOWN

    # A band with no age limits at all does apply, because it says so.
    add_range(hospital, analyte, low="11.0", high="17.0", sex=RangeSex.MALE)
    with hospital.scope():
        applied = ranges.range_for(analyte, sex=Sex.MALE, age_days=None)
    assert applied is not None
    assert applied.sex == RangeSex.MALE


# --------------------------------------------------------------------------
# Choosing the range: sex
# --------------------------------------------------------------------------


def test_a_range_for_her_own_sex_beats_the_general_one(hospital, haemoglobin):
    """
    16.8 g/dL is above a woman's band and inside the general adult one.

    This is the specificity rule doing the work: a hospital sets a general
    band and overrides it for women, and the override has to win.
    """
    with hospital.scope():
        woman = ranges.flag(haemoglobin, "16.8", sex=Sex.FEMALE, age_days=ADULT)
        man = ranges.flag(haemoglobin, "16.8", sex=Sex.MALE, age_days=ADULT)

    assert woman.level is Level.HIGH
    assert woman.applied is not None and woman.applied.sex == RangeSex.FEMALE
    assert man.level is Level.NORMAL
    assert man.applied is not None and man.applied.sex == RangeSex.MALE


def test_a_patient_whose_sex_is_not_recorded_gets_the_general_band(hospital, haemoglobin):
    """
    An emergency registration has no sex yet. The system must not pick one.

    It falls back to the "any" band, which is wider, and that is the correct
    trade: a wider band under-flags, guessing the sex mis-flags.
    """
    with hospital.scope():
        judged = ranges.flag(haemoglobin, "12.5", sex=Sex.UNKNOWN, age_days=ADULT)
    assert judged.level is Level.NORMAL
    assert judged.applied is not None
    assert judged.applied.sex == RangeSex.ANY


# --------------------------------------------------------------------------
# The limits themselves
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7.0", Level.CRITICAL_LOW),  # at the threshold is already critical
        ("6.9", Level.CRITICAL_LOW),
        ("7.1", Level.LOW),
        ("11.4", Level.LOW),
        ("11.5", Level.NORMAL),  # at the limit is normal, not low
        ("14.0", Level.NORMAL),
        ("16.5", Level.NORMAL),
        ("16.6", Level.HIGH),
        ("19.9", Level.HIGH),
        ("20.0", Level.CRITICAL_HIGH),
    ],
)
def test_where_each_limit_falls(hospital, haemoglobin, value, expected):
    with hospital.scope():
        assert ranges.flag(haemoglobin, value, sex=Sex.FEMALE, age_days=ADULT).level is expected


def test_critical_is_decided_before_abnormal(hospital, haemoglobin):
    """A critical result is also low. Only one of them is worth a telephone call."""
    with hospital.scope():
        judged = ranges.flag(haemoglobin, "5.0", sex=Sex.FEMALE, age_days=ADULT)
    assert judged.level is Level.CRITICAL_LOW
    assert judged.is_critical and judged.is_abnormal


def test_a_one_sided_range_only_flags_the_side_it_has(hospital):
    """A platelet count has a lower limit that matters and an upper one that mostly does not."""
    analyte = make_analyte(hospital, make_panel(hospital), code="PLT", name="Platelets", unit="")
    add_range(hospital, analyte, low="150", critical_low="50")
    with hospital.scope():
        assert ranges.flag(analyte, "900", age_days=ADULT).level is Level.NORMAL
        assert ranges.flag(analyte, "120", age_days=ADULT).level is Level.LOW
        assert ranges.flag(analyte, "40", age_days=ADULT).level is Level.CRITICAL_LOW


# --------------------------------------------------------------------------
# Refusing an ambiguous catalogue
# --------------------------------------------------------------------------


def test_two_overlapping_ranges_for_the_same_sex_are_refused(hospital):
    """
    Two ranges that both apply would give one result two answers, and which
    one won would depend on row order.
    """
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="11.5", high="16.5", age_min_days=0, age_max_days=5000)

    with pytest.raises(catalogue.CatalogueError) as caught:
        add_range(hospital, analyte, low="12.0", high="17.0", age_min_days=4000)
    assert "overlap" in str(caught.value).lower()


def test_a_sex_specific_range_may_overlap_the_general_one(hospital):
    """Not ambiguous: specificity decides. This is the ordinary way to set them."""
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="11.5", high="18.0")
    add_range(hospital, analyte, low="11.5", high="16.5", sex=RangeSex.FEMALE)
    with hospital.scope():
        assert analyte.ranges.count() == 2


def test_touching_ranges_do_not_count_as_overlapping(hospital):
    """0-28 and 29-365 are adjacent, not overlapping, and must be allowed."""
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="14.0", high="22.0", age_max_days=28)
    add_range(hospital, analyte, low="9.5", high="13.5", age_min_days=29, age_max_days=YEAR)
    with hospital.scope():
        assert analyte.ranges.count() == 2


def test_two_open_ended_ranges_for_one_sex_are_refused(hospital):
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="11.5", high="16.5", age_min_days=6000)
    with pytest.raises(catalogue.CatalogueError):
        add_range(hospital, analyte, low="12.0", high="17.0", age_min_days=9000)


def test_editing_a_range_does_not_make_it_overlap_itself(hospital, haemoglobin):
    """
    The overlap check has to exclude the row being saved.

    Without that, a supervisor could never correct a range at all — the first
    edit would report it as clashing with itself.
    """
    with hospital.scope():
        band = haemoglobin.ranges.filter(sex=RangeSex.FEMALE).get()
        band.high = Decimal("16.9")
        catalogue.save_range(band)
        band.refresh_from_db()
    assert band.high == Decimal("16.9")


def test_a_range_whose_limits_cross_over_is_refused(hospital):
    analyte = make_analyte(hospital, make_panel(hospital))
    with pytest.raises(catalogue.CatalogueError):
        add_range(hospital, analyte, low="16.5", high="11.5")


def test_a_critical_level_inside_the_normal_band_is_refused(hospital):
    """A critical low above the bottom of normal would make normal results critical."""
    analyte = make_analyte(hospital, make_panel(hospital))
    with pytest.raises(catalogue.CatalogueError):
        add_range(hospital, analyte, low="11.5", high="16.5", critical_low="12.0")


def test_a_range_with_no_limits_at_all_is_refused(hospital):
    analyte = make_analyte(hospital, make_panel(hospital))
    with pytest.raises(catalogue.CatalogueError):
        add_range(hospital, analyte, note="says nothing")


def test_an_upper_age_below_the_lower_one_is_refused(hospital):
    analyte = make_analyte(hospital, make_panel(hospital))
    with pytest.raises(catalogue.CatalogueError):
        add_range(hospital, analyte, low="1", high="2", age_min_days=500, age_max_days=100)


def test_a_coded_analyte_cannot_have_a_reference_range(hospital):
    """A coded answer carries its flag on the option. A range would be meaningless."""
    analyte = make_analyte(
        hospital,
        make_panel(hospital),
        code="MP",
        name="Malaria parasite",
        unit="",
        value_type=ValueType.CODED,
        options=[
            {"label": "Negative", "flag": OptionFlag.NORMAL},
            {"label": "Positive", "flag": OptionFlag.ABNORMAL},
        ],
    )
    with pytest.raises(catalogue.CatalogueError):
        add_range(hospital, analyte, low="1", high="2")


# --------------------------------------------------------------------------
# Coded and free-text answers
# --------------------------------------------------------------------------


@pytest.fixture
def malaria(hospital):
    return make_analyte(
        hospital,
        make_panel(hospital, code="MP-RDT", name="Malaria rapid test"),
        code="MP",
        name="Malaria parasite",
        unit="",
        value_type=ValueType.CODED,
        decimal_places=0,
        options=[
            {"label": "Negative", "flag": OptionFlag.NORMAL},
            {"label": "Positive", "flag": OptionFlag.ABNORMAL},
        ],
    )


def test_a_coded_answer_is_flagged_from_its_own_option(hospital, malaria):
    with hospital.scope():
        assert ranges.flag(malaria, "Negative").level is Level.NORMAL
        assert ranges.flag(malaria, "Positive").level is Level.ABNORMAL


def test_a_coded_answer_is_matched_whatever_case_it_was_typed_in(hospital, malaria):
    with hospital.scope():
        assert ranges.flag(malaria, "positive").level is Level.ABNORMAL
        assert ranges.flag(malaria, "  POSITIVE ").level is Level.ABNORMAL


def test_an_answer_that_is_not_on_the_list_is_not_guessed_at(hospital, malaria):
    with hospital.scope():
        assert ranges.flag(malaria, "maybe").level is Level.UNKNOWN


def test_a_coded_analyte_needs_at_least_one_answer(hospital):
    panel = make_panel(hospital)
    with hospital.scope():
        analyte = Analyte(
            organisation=hospital.organisation,
            panel=panel,
            code="X",
            name="Nothing to pick",
            value_type=ValueType.CODED,
        )
        with pytest.raises(catalogue.CatalogueError):
            catalogue.save_analyte(analyte)


def test_an_answer_marked_with_a_meaningless_flag_is_refused(hospital):
    panel = make_panel(hospital)
    with hospital.scope():
        analyte = Analyte(
            organisation=hospital.organisation,
            panel=panel,
            code="X",
            name="Badly set up",
            value_type=ValueType.CODED,
            options=[{"label": "Reactive", "flag": "quite bad"}],
        )
        with pytest.raises(catalogue.CatalogueError):
            catalogue.save_analyte(analyte)


def test_free_text_is_never_flagged(hospital):
    """The judgement is in the words a scientist wrote, not in a comparison."""
    analyte = make_analyte(
        hospital,
        make_panel(hospital),
        code="MICRO",
        name="Microscopy",
        unit="",
        value_type=ValueType.TEXT,
    )
    with hospital.scope():
        assert ranges.flag(analyte, "Scanty pus cells seen.").level is Level.UNKNOWN


def test_a_value_that_is_not_a_number_does_not_raise(hospital, haemoglobin):
    """
    A screen has to be able to show a result it cannot flag.

    Refusing to render would mean a scientist cannot save their work because
    somebody typed a stray character.
    """
    with hospital.scope():
        assert ranges.flag(haemoglobin, "n/a", sex=Sex.FEMALE, age_days=ADULT).level is (
            Level.UNKNOWN
        )
        assert ranges.flag(haemoglobin, None, sex=Sex.FEMALE, age_days=ADULT).level is (
            Level.UNKNOWN
        )
        assert ranges.flag(haemoglobin, "", sex=Sex.FEMALE, age_days=ADULT).level is Level.UNKNOWN


# --------------------------------------------------------------------------
# Age from a date of birth
# --------------------------------------------------------------------------


def test_age_in_days_counts_from_the_date_of_birth(hospital):
    class FakePatient:
        date_of_birth = dt.date(2026, 1, 1)

    assert ranges.age_days(FakePatient(), on=dt.date(2026, 1, 1)) == 0
    assert ranges.age_days(FakePatient(), on=dt.date(2026, 1, 31)) == 30
    assert ranges.age_days(FakePatient(), on=dt.date(2027, 1, 1)) == 365


def test_an_age_is_none_when_the_date_of_birth_is_not_known(hospital):
    class FakePatient:
        date_of_birth = None

    assert ranges.age_days(FakePatient()) is None


# --------------------------------------------------------------------------
# What the labels say
# --------------------------------------------------------------------------


def test_a_band_reads_the_way_a_person_would_say_it(hospital, haemoglobin):
    with hospital.scope():
        labels = {band.band_label() for band in haemoglobin.ranges.all()}
    assert "up to 28 days" in labels
    assert any(label.startswith("Female") for label in labels)
    assert any("12 years and over" in label for label in labels)


def test_the_printed_reference_shows_the_band_that_was_used(hospital, haemoglobin):
    with hospital.scope():
        woman = ranges.describe(haemoglobin, sex=Sex.FEMALE, age_days=ADULT)
        newborn = ranges.describe(haemoglobin, sex=Sex.FEMALE, age_days=3)
        unknown = ranges.describe(haemoglobin, sex=Sex.FEMALE, age_days=None)

    assert woman == "11.5 - 16.5 g/dL"
    assert newborn == "14 - 22 g/dL"
    assert unknown == ""


def test_a_note_on_the_range_is_printed_with_it(hospital):
    analyte = make_analyte(hospital, make_panel(hospital), code="GLU", unit="mmol/L")
    add_range(hospital, analyte, low="3.9", high="5.5", note="fasting")
    with hospital.scope():
        assert ranges.describe(analyte, sex=Sex.MALE, age_days=ADULT) == (
            "3.9 - 5.5 mmol/L (fasting)"
        )


# --------------------------------------------------------------------------
# The starting catalogue
# --------------------------------------------------------------------------


def test_the_starting_catalogue_is_installed_for_the_tests_a_hospital_chose(hospital):
    """Only for services that exist: a clinic with no cross-matching gets no GXM panel."""
    make_service(hospital)
    make_service(hospital, code="EUCR", name="Electrolytes, urea and creatinine")

    with hospital.scope():
        result = catalogue.ensure_panels(hospital.organisation)
        codes = set(LabPanel.objects.values_list("service__code", flat=True))

    assert result.panels == 2
    assert codes == {"FBC", "EUCR"}


def test_installing_it_twice_does_not_duplicate_anything(hospital):
    make_service(hospital)
    with hospital.scope():
        first = catalogue.ensure_panels(hospital.organisation)
        second = catalogue.ensure_panels(hospital.organisation)
        analytes = Analyte.objects.count()

    assert first.panels == 1
    assert second.panels == 0
    assert analytes == first.analytes


def test_installing_it_again_leaves_an_edited_range_alone(hospital):
    """
    Re-running setup is not an invitation to undo a supervisor's work.

    A hospital adds three tests a week after going live; the ranges they have
    already corrected must survive it.
    """
    make_service(hospital)
    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)
        band = ReferenceRange.objects.filter(analyte__code="HB", sex=RangeSex.FEMALE).get()
        band.low = Decimal("10.0")
        catalogue.save_range(band)

        catalogue.ensure_panels(hospital.organisation)
        band.refresh_from_db()

    assert band.low == Decimal("10.0")
    assert band.from_template is False


def test_every_shipped_range_passes_the_rules_it_is_validated_against(hospital):
    """
    The starting catalogue must not contain a clash it would refuse by hand.

    Written as a test rather than trusted, because the data file is long and
    an overlapping band added to it would otherwise only surface as a wrong
    flag in a hospital.
    """
    for template in PANELS_BY_SERVICE_CODE.values():
        make_service(hospital, code=template.service_code, name=template.service_code)

    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)
        problems = []
        for band in ReferenceRange.objects.select_related("analyte").all():
            try:
                band.full_clean()
            except ValidationError as error:
                problems.append(f"{band.analyte.code} {band.band_label()}: {error.messages[0]}")

    assert problems == []


def test_the_shipped_catalogue_covers_every_laboratory_service_the_wizard_seeds(hospital):
    """
    A service with no panel is a test that can be billed and not reported.

    The wizard's own list is the source of truth, so this compares against it
    rather than against a copy.
    """
    from apps.onboarding import templates_data

    seeded = {
        template.code
        for template in templates_data.SERVICE_TEMPLATES
        if template.category == ServiceCategory.LABORATORY
    }
    assert seeded - set(PANELS_BY_SERVICE_CODE) == set()


def test_a_positive_pregnancy_test_is_not_an_abnormal_result(hospital):
    """It is not a disease, and printing ABNORMAL beside it would be wrong."""
    make_service(hospital, code="PREG", name="Pregnancy test")
    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)
        analyte = Analyte.objects.get(code="HCG")
        assert ranges.flag(analyte, "Positive").level is Level.NORMAL


def test_a_reactive_hiv_screen_is_abnormal_but_never_critical(hospital):
    """
    Critical means somebody telephones a ward. A reactive screen is
    confidential and is given to the patient in person, with counselling.
    """
    make_service(hospital, code="HIV-SCR", name="HIV screening")
    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)
        analyte = Analyte.objects.get(code="HIV")
        judged = ranges.flag(analyte, "Reactive")

    assert judged.level is Level.ABNORMAL
    assert judged.is_abnormal
    assert not judged.is_critical


def test_an_incompatible_cross_match_is_critical(hospital):
    make_service(hospital, code="GXM", name="Blood group and cross-match")
    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)
        analyte = Analyte.objects.get(code="XM")
        assert ranges.flag(analyte, "Incompatible").is_critical


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


def test_the_laboratory_can_read_the_catalogue(hospital, api, as_staff):
    make_service(hospital)
    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)

    as_staff(hospital.staff("lab_scientist"))
    response = api.get(reverse("api:v1:lab-panels"))

    assert response.status_code == 200
    assert [panel["code"] for panel in response.data["panels"]] == ["FBC"]
    assert response.data["panels"][0]["analyte_count"] == 8
    assert response.data["unreviewed_ranges"] > 0
    assert response.data["may_edit"] is False


def test_a_supervisor_is_told_they_may_edit_it(hospital, api, as_staff):
    make_service(hospital)
    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)

    as_staff(hospital.staff("lab_supervisor"))
    response = api.get(reverse("api:v1:lab-panels"))

    assert response.status_code == 200
    assert response.data["may_edit"] is True


def test_a_scientist_cannot_change_a_reference_range(hospital, api, as_staff):
    """A wrong range is a wrong flag on every result afterwards."""
    analyte = make_analyte(hospital, make_panel(hospital))
    band = add_range(hospital, analyte, low="11.5", high="16.5")

    as_staff(hospital.staff("lab_scientist"))
    response = api.patch(
        reverse("api:v1:lab-range-detail", args=[band.pk]), {"high": "20.0"}, format="json"
    )

    assert response.status_code == 403
    with hospital.scope():
        band.refresh_from_db()
    assert band.high == Decimal("16.5")


def test_a_supervisor_can_change_a_reference_range(hospital, api, as_staff):
    analyte = make_analyte(hospital, make_panel(hospital))
    band = add_range(hospital, analyte, low="11.5", high="16.5")

    as_staff(hospital.staff("lab_supervisor"))
    response = api.patch(
        reverse("api:v1:lab-range-detail", args=[band.pk]), {"high": "17.0"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["normal"] == "11.5 - 17"
    with hospital.scope():
        band.refresh_from_db()
    assert band.high == Decimal("17.0")
    assert band.from_template is False


def test_an_overlapping_range_is_refused_with_a_sentence_a_person_can_act_on(
    hospital, api, as_staff
):
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="14.0", high="22.0", age_max_days=28)

    as_staff(hospital.staff("lab_supervisor"))
    response = api.post(
        reverse("api:v1:lab-analyte-ranges", args=[analyte.pk]),
        {"low": "9.5", "high": "13.5", "age_min_days": 20, "age_max_days": 365},
        format="json",
    )

    assert response.status_code == 400
    assert "overlap" in response.data["detail"].lower()
    assert "up to 28 days" in response.data["detail"]


def test_the_preview_says_which_range_a_patient_would_be_judged_against(
    hospital, api, as_staff, haemoglobin
):
    """
    The endpoint that makes the specificity rule visible to a supervisor.

    A six-year-old girl and a thirty-year-old woman hit different bands, and
    the screen cannot work that out from a list of rows.
    """
    as_staff(hospital.staff("lab_supervisor"))
    url = reverse("api:v1:lab-analyte-preview", args=[haemoglobin.pk])

    child = api.post(url, {"age_days": 6 * YEAR, "sex": Sex.FEMALE, "value": "10.5"}, format="json")
    woman = api.post(url, {"age_days": ADULT, "sex": Sex.FEMALE, "value": "10.5"}, format="json")

    assert child.status_code == woman.status_code == 200
    assert child.data["applies"]["band"] == "1 year to 12 years"
    assert child.data["level"] == "low"
    assert woman.data["applies"]["band"] == "Female, 12 years and over"
    assert woman.data["level"] == "low"
    assert woman.data["describes"] == "11.5 - 16.5 g/dL"


def test_the_preview_is_honest_when_no_range_applies(hospital, api, as_staff, haemoglobin):
    as_staff(hospital.staff("lab_supervisor"))
    response = api.post(
        reverse("api:v1:lab-analyte-preview", args=[haemoglobin.pk]),
        {"sex": Sex.FEMALE, "value": "12.0"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["applies"] is None
    assert response.data["level"] == "unknown"
    assert response.data["describes"] == ""


def test_a_supervisor_can_add_an_analyte_a_hospital_does_its_own_way(hospital, api, as_staff):
    panel = make_panel(hospital)
    as_staff(hospital.staff("lab_supervisor"))

    response = api.post(
        reverse("api:v1:lab-panel-analytes", args=[panel.pk]),
        {"code": "RETIC", "name": "Reticulocytes", "unit": "%", "decimal_places": 1},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["from_template"] is False
    with hospital.scope():
        assert Analyte.objects.filter(panel=panel, code="RETIC").exists()


def test_one_hospitals_ranges_are_invisible_to_another(hospital, other_hospital, api, as_staff):
    analyte = make_analyte(hospital, make_panel(hospital))
    add_range(hospital, analyte, low="11.5", high="16.5")

    as_staff(other_hospital.staff("lab_supervisor"))
    listing = api.get(reverse("api:v1:lab-panels"))
    # The analyte route is write-only, so the isolation check is a write:
    # another hospital's supervisor must not be able to reach this row.
    detail = api.patch(
        reverse("api:v1:lab-analyte-detail", args=[analyte.pk]), {"unit": "x"}, format="json"
    )

    assert listing.status_code == 200
    assert listing.data["panels"] == []
    assert detail.status_code == 404


def test_a_doctor_may_read_the_catalogue_but_not_edit_it(hospital, api, as_staff):
    """A result's unit and band are part of reading the result."""
    make_service(hospital)
    with hospital.scope():
        catalogue.ensure_panels(hospital.organisation)
        panel = LabPanel.objects.get()

    as_staff(hospital.staff("doctor"))
    read = api.get(reverse("api:v1:lab-panel-detail", args=[panel.pk]))
    write = api.patch(
        reverse("api:v1:lab-panel-detail", args=[panel.pk]),
        {"turnaround_minutes": 10},
        format="json",
    )

    assert read.status_code == 200
    assert read.data["analytes"][0]["code"] == "HB"
    assert read.data["analytes"][0]["reference_ranges"]
    assert write.status_code == 403


def test_the_front_desk_cannot_read_the_catalogue_at_all(hospital, api, as_staff):
    as_staff(hospital.staff("front_desk"))
    response = api.get(reverse("api:v1:lab-panels"))
    assert response.status_code == 403
