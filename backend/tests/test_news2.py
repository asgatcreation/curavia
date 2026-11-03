"""
NEWS2, checked against the published tables.

This file is the reason the algorithm can be trusted. Every parameter is
tested at both edges of every band, because an off-by-one at a boundary is
the kind of error that produces a plausible score for a patient who is
deteriorating — which is worse than no score at all.

The Royal College of Physicians' 2017 specification is the source. Where a
boundary looks strange, the test states the specification rather than
smoothing it.

No database: this is arithmetic, and it is tested as arithmetic.
"""

from __future__ import annotations

import pytest

from apps.clinical import news2
from apps.clinical.news2 import OxygenScale, score


def total(**kwargs) -> int:
    return score(**kwargs).total


# --------------------------------------------------------------------------
# Respiration rate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (4, 3),
        (8, 3),
        (9, 1),
        (11, 1),
        (12, 0),
        (20, 0),
        (21, 2),
        (24, 2),
        (25, 3),
        (40, 3),
    ],
)
def test_respiration_rate_bands(rate: int, expected: int) -> None:
    result = score(respiration_rate=rate)
    assert result.parameters[0].score == expected


# --------------------------------------------------------------------------
# Oxygen saturation, Scale 1
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spo2", "expected"),
    [(85, 3), (91, 3), (92, 2), (93, 2), (94, 1), (95, 1), (96, 0), (100, 0)],
)
def test_saturation_scale_one_bands(spo2: int, expected: int) -> None:
    assert news2.saturation_score(spo2=spo2, on_oxygen=False) == expected


# --------------------------------------------------------------------------
# Oxygen saturation, Scale 2
#
# For hypercapnic respiratory failure — COPD, mainly — where the target range
# is 88 to 92%. The counter-intuitive part is that a HIGH saturation on
# oxygen scores badly, because that patient is being over-oxygenated.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spo2", "expected"),
    [(80, 3), (83, 3), (84, 2), (85, 2), (86, 1), (87, 1), (88, 0), (99, 0)],
)
def test_saturation_scale_two_on_air(spo2: int, expected: int) -> None:
    """On air, anything at or above the target range scores nothing."""
    assert news2.saturation_score(spo2=spo2, on_oxygen=False, scale=OxygenScale.TWO) == expected


@pytest.mark.parametrize(
    ("spo2", "expected"),
    [
        (83, 3),
        (84, 2),
        (86, 1),
        (88, 0),
        (92, 0),
        (93, 1),
        (94, 1),
        (95, 2),
        (96, 2),
        (97, 3),
        (100, 3),
    ],
)
def test_saturation_scale_two_on_oxygen(spo2: int, expected: int) -> None:
    """
    97% on oxygen scores 3 on Scale 2.

    That is the specification and it is the point of Scale 2: pushing a COPD
    patient to a normal saturation with oxygen is dangerous, and the normal
    scale would have scored it as perfect.
    """
    assert news2.saturation_score(spo2=spo2, on_oxygen=True, scale=OxygenScale.TWO) == expected


def test_scale_two_rescues_a_correctly_managed_copd_patient() -> None:
    """
    The same reading, scored both ways.

    A COPD patient sitting at 89% on oxygen is where they should be. On
    Scale 1 that is 3 points for the saturation; on Scale 2 it is none.
    """
    on_scale_one = score(spo2=89, on_oxygen=True)
    on_scale_two = score(spo2=89, on_oxygen=True, scale=OxygenScale.TWO)

    assert on_scale_one.parameters[0].score == 3
    assert on_scale_two.parameters[0].score == 0


# --------------------------------------------------------------------------
# Air or oxygen
# --------------------------------------------------------------------------


def test_being_on_oxygen_scores_two_by_itself() -> None:
    """
    A parameter in its own right, not a modifier. A patient with otherwise
    perfect numbers scores 2 simply for needing oxygen.
    """
    assert total(on_oxygen=True) == 2
    assert total(on_oxygen=False) == 0


# --------------------------------------------------------------------------
# Blood pressure, pulse, consciousness, temperature
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("systolic", "expected"),
    [(80, 3), (90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3)],
)
def test_systolic_bands(systolic: int, expected: int) -> None:
    assert score(systolic_bp=systolic).parameters[0].score == expected


def test_very_high_blood_pressure_scores_the_same_as_very_low() -> None:
    """220 and 80 both score 3. Both are emergencies, in opposite directions."""
    assert total(systolic_bp=220) == total(systolic_bp=80) == 3


@pytest.mark.parametrize(
    ("pulse", "expected"),
    [
        (35, 3),
        (40, 3),
        (41, 1),
        (50, 1),
        (51, 0),
        (90, 0),
        (91, 1),
        (110, 1),
        (111, 2),
        (130, 2),
        (131, 3),
    ],
)
def test_pulse_bands(pulse: int, expected: int) -> None:
    assert score(pulse=pulse).parameters[0].score == expected


@pytest.mark.parametrize(
    ("letter", "expected"),
    [("A", 0), ("C", 3), ("V", 3), ("P", 3), ("U", 3)],
)
def test_consciousness_bands(letter: str, expected: int) -> None:
    """
    New confusion scores 3, which is the 2017 change. Before it, a confused
    patient could score nothing at all.
    """
    assert score(consciousness=letter).parameters[0].score == expected


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [
        (34.0, 3),
        (35.0, 3),
        (35.1, 1),
        (36.0, 1),
        (36.1, 0),
        (38.0, 0),
        (38.1, 1),
        (39.0, 1),
        (39.1, 2),
        (41.0, 2),
    ],
)
def test_temperature_bands(temperature: float, expected: int) -> None:
    assert score(temperature=temperature).parameters[0].score == expected


def test_a_high_fever_scores_two_not_three() -> None:
    """
    The one band that does not go to 3 at its top. Fever is a worrying sign
    but a less immediately lethal one than hypothermia, which does.
    """
    assert total(temperature=40.0) == 2
    assert total(temperature=34.5) == 3


# --------------------------------------------------------------------------
# The aggregate, and the thing most implementations get wrong
# --------------------------------------------------------------------------


def test_a_well_patient_scores_nothing() -> None:
    result = score(
        respiration_rate=16,
        spo2=98,
        on_oxygen=False,
        systolic_bp=120,
        pulse=72,
        consciousness="A",
        temperature=36.8,
    )
    assert result.total == 0
    assert result.band == "routine"
    assert result.red_flag is False
    assert result.is_complete


def test_a_single_parameter_scoring_three_escalates_on_its_own() -> None:
    """
    **The thing most implementations get wrong.**

    This patient totals 3 — the same as somebody with three mild
    abnormalities — but all of it comes from one reading, and the RCP says
    that needs an urgent review by a doctor. Reporting the total alone loses
    this patient.
    """
    one_bad = score(
        respiration_rate=16,
        spo2=98,
        on_oxygen=False,
        systolic_bp=120,
        pulse=38,  # scores 3 by itself
        consciousness="A",
        temperature=36.8,
    )
    three_mild = score(
        respiration_rate=10,  # 1
        spo2=94,  # 1
        on_oxygen=False,
        systolic_bp=105,  # 1
        pulse=72,
        consciousness="A",
        temperature=36.8,
    )

    assert one_bad.total == three_mild.total == 3
    assert one_bad.red_flag is True
    assert three_mild.red_flag is False
    assert one_bad.band == "medium", "a single 3 must escalate"
    assert three_mild.band == "low"


@pytest.mark.parametrize(
    ("points", "expected_band"),
    [
        (0, "routine"),
        (1, "low"),
        (4, "low"),
        (5, "medium"),
        (6, "medium"),
        (7, "high"),
        (15, "high"),
    ],
)
def test_the_response_bands(points: int, expected_band: int) -> None:
    from apps.clinical.news2 import Parameter, Score

    # Built directly so the band logic is tested without having to find a
    # set of observations that happens to total each number.
    result = Score(total=points, parameters=(Parameter("x", "x", "x", min(points, 2)),))
    assert result.band == expected_band


def test_a_deteriorating_patient_reaches_the_emergency_band() -> None:
    """A realistic set: septic, hypotensive, tachycardic, confused."""
    result = score(
        respiration_rate=26,  # 3
        spo2=93,  # 2
        on_oxygen=True,  # 2
        systolic_bp=88,  # 3
        pulse=125,  # 2
        consciousness="C",  # 3
        temperature=39.4,  # 2
    )
    assert result.total == 17
    assert result.band == "high"
    assert "critical care" in result.response


# --------------------------------------------------------------------------
# When NEWS2 must refuse
# --------------------------------------------------------------------------


def test_it_refuses_for_a_child() -> None:
    """
    NEWS2 was validated in adults aged 16 and over. A number produced for a
    three-year-old is a number somebody will act on.
    """
    result = score(
        respiration_rate=30,
        spo2=97,
        on_oxygen=False,
        systolic_bp=90,
        pulse=130,
        consciousness="A",
        temperature=37.2,
        age_years=3,
    )
    assert result.usable is False
    assert result.total == 0
    assert result.band == "not_applicable"
    assert "paediatric" in result.reason


def test_it_applies_from_sixteen() -> None:
    assert score(pulse=72, age_years=16).usable is True
    assert score(pulse=72, age_years=15).usable is False


def test_it_refuses_in_pregnancy() -> None:
    result = score(pulse=120, age_years=28, is_pregnant=True)
    assert result.usable is False
    assert "obstetric" in result.reason


def test_a_missing_observation_is_not_treated_as_normal() -> None:
    """
    A score from five of seven parameters is not a NEWS2 score. What comes
    back names what is missing rather than quietly scoring it zero.
    """
    result = score(respiration_rate=16, pulse=72, temperature=36.8)

    assert result.is_complete is False
    assert "oxygen saturation" in result.missing
    assert "blood pressure" in result.missing
    assert "level of consciousness" in result.missing
    assert len(result.missing) == 4


def test_the_working_is_shown_parameter_by_parameter() -> None:
    """
    A nurse looking at a 7 needs to know which reading produced it.
    """
    result = score(
        respiration_rate=26,
        spo2=98,
        on_oxygen=False,
        systolic_bp=120,
        pulse=72,
        consciousness="A",
        temperature=36.8,
    )
    by_name = {parameter.name: parameter for parameter in result.parameters}

    assert by_name["respiration_rate"].score == 3
    assert by_name["respiration_rate"].value == "26"
    assert by_name["pulse"].score == 0
    assert by_name["consciousness"].value == "Alert"


# --------------------------------------------------------------------------
# BMI
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("weight", "height", "expected_category"),
    [
        (50, 175, "Underweight"),
        (70, 175, "Healthy weight"),
        (85, 175, "Overweight"),
        (95, 175, "Obese"),
        (120, 175, "Severely obese"),
    ],
)
def test_bmi_categories(weight: float, height: float, expected_category: str) -> None:
    result = news2.bmi(weight_kg=weight, height_cm=height, age_years=34)
    assert result is not None
    assert result.category == expected_category


def test_bmi_withholds_the_category_for_a_child() -> None:
    """
    A nine-year-old at 16 is not "underweight", they are nine. The number is
    still given, because clinicians dose drugs from it.
    """
    result = news2.bmi(weight_kg=28, height_cm=132, age_years=9)
    assert result is not None
    assert result.value == 16.1
    assert result.category == ""
    assert "growth chart" in result.caveat


def test_bmi_needs_both_measurements() -> None:
    assert news2.bmi(weight_kg=70, height_cm=0) is None
    assert news2.bmi(weight_kg=0, height_cm=175) is None
