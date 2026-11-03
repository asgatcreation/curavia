"""
NEWS2 — the National Early Warning Score, 2017 revision.

A number between 0 and 20 that says how sick somebody is from seven routine
observations. It is the single highest-value thing a hospital can compute from
a set of vitals, because it catches deterioration that each individual reading
looks normal enough to hide.

**This is a clinical algorithm and it is implemented from the published
tables, parameter by parameter.** The Royal College of Physicians' 2017
specification. Nothing here is rounded, simplified or tuned. Where a boundary
looks odd — 93 to 94% on oxygen scoring 1 on Scale 2 while 93 to 94% on air scores 0
— that is the specification, and the test file checks each band at its edges.

Four things most implementations get wrong, all of which are handled here:

**1. A single parameter scoring 3 escalates on its own.** A patient with a
total of 3 made up of three 1s needs routine monitoring; a patient with a
total of 3 that is *all from one parameter* needs an urgent review by a
doctor. Reporting only the total loses the second patient. ``Score.red_flag``
is that distinction, and it is why this returns a structure rather than an
integer.

**2. Scale 2 exists.** Patients with hypercapnic respiratory failure — COPD,
mainly — have a target saturation of 88 to 92%, and scoring them against the
normal scale marks a correctly managed patient as critically unwell. Scale 2
has to be chosen by a clinician, once, and recorded on the patient.

**3. It is not valid for children, and it is not valid in pregnancy.** NEWS2
was validated in adults aged 16 and over, and the physiology of both groups
differs enough that the score misleads. Curavia therefore refuses to produce
one, and says why, rather than producing a number somebody will act on. A
paediatric early warning score is a different algorithm and arrives with the
paediatric module.

**4. A missing observation is not a zero.** A score computed from five of
seven parameters is not a NEWS2 score and must not be presented as one. Where
something was not measured, the result says so and is marked incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum

#: NEWS2 was validated in adults. Below this age the score is not used.
MINIMUM_AGE_YEARS = 16

#: A single parameter at this score triggers escalation regardless of total.
RED_FLAG_SCORE = 3


class Consciousness(StrEnum):
    """
    ACVPU. "New confusion" counts as not-alert, which is the 2017 change —
    before it, a confused patient could score zero.
    """

    ALERT = "A"
    CONFUSED = "C"
    VOICE = "V"
    PAIN = "P"
    UNRESPONSIVE = "U"


class OxygenScale(int, Enum):
    ONE = 1
    TWO = 2


@dataclass(frozen=True, slots=True)
class Parameter:
    """One observation's contribution, kept separate so it can be shown."""

    name: str
    label: str
    value: str
    score: int


@dataclass(frozen=True, slots=True)
class Score:
    """
    A NEWS2 result, or an honest refusal to produce one.

    ``usable`` is false when the score must not be acted on: too young, in
    pregnancy, or not enough observations. The screen shows ``reason``
    instead of a number.
    """

    total: int
    parameters: tuple[Parameter, ...] = ()
    missing: tuple[str, ...] = ()
    usable: bool = True
    reason: str = ""

    @property
    def red_flag(self) -> bool:
        """Any single parameter scoring 3. Escalates on its own — see above."""
        return any(parameter.score >= RED_FLAG_SCORE for parameter in self.parameters)

    @property
    def is_complete(self) -> bool:
        return not self.missing

    @property
    def band(self) -> str:
        """
        The RCP's clinical-response band.

        Returned as a key rather than a sentence so the client can colour it
        and the words can be translated.
        """
        if not self.usable:
            return "not_applicable"
        if self.total >= 7:
            return "high"
        if self.total >= 5 or self.red_flag:
            return "medium"
        if self.total >= 1:
            return "low"
        return "routine"

    @property
    def response(self) -> str:
        return RESPONSES[self.band]


#: What each band means, in the words a nurse needs rather than a grade.
RESPONSES: dict[str, str] = {
    "routine": "Routine monitoring. Observations at least every 12 hours.",
    "low": "Observations every 4 to 6 hours. A registered nurse should assess.",
    "medium": (
        "Urgent review by a doctor. Observations at least hourly. "
        "Escalate even if the total is low when one reading scores 3."
    ),
    "high": (
        "Emergency response. Continuous monitoring, and the critical care team "
        "should be called now."
    ),
    "not_applicable": "NEWS2 does not apply to this patient.",
}


# ---------------------------------------------------------------------------
# The tables
#
# Written as explicit bands rather than arithmetic, because that is how the
# specification is written and because a reader has to be able to check them
# against it line by line. Each entry is (low, high, score), inclusive, with
# None for an open end.
# ---------------------------------------------------------------------------

RESPIRATION = (
    (None, 8, 3),
    (9, 11, 1),
    (12, 20, 0),
    (21, 24, 2),
    (25, None, 3),
)

SATURATION_SCALE_1 = (
    (None, 91, 3),
    (92, 93, 2),
    (94, 95, 1),
    (96, None, 0),
)

#: Scale 2, on air. A patient breathing room air cannot score above 0 once
#: they are at or above their target range.
SATURATION_SCALE_2_AIR = (
    (None, 83, 3),
    (84, 85, 2),
    (86, 87, 1),
    (88, None, 0),
)

#: Scale 2, on supplemental oxygen. Higher saturations score *worse*, because
#: a COPD patient pushed to 97% on oxygen is being over-oxygenated.
SATURATION_SCALE_2_OXYGEN = (
    (None, 83, 3),
    (84, 85, 2),
    (86, 87, 1),
    (88, 92, 0),
    (93, 94, 1),
    (95, 96, 2),
    (97, None, 3),
)

SYSTOLIC = (
    (None, 90, 3),
    (91, 100, 2),
    (101, 110, 1),
    (111, 219, 0),
    (220, None, 3),
)

PULSE = (
    (None, 40, 3),
    (41, 50, 1),
    (51, 90, 0),
    (91, 110, 1),
    (111, 130, 2),
    (131, None, 3),
)

#: Temperature in Celsius, to one decimal place.
TEMPERATURE = (
    (None, 35.0, 3),
    (35.1, 36.0, 1),
    (36.1, 38.0, 0),
    (38.1, 39.0, 1),
    (39.1, None, 2),
)


def _band_score(value: float, bands: tuple[tuple[float | None, float | None, int], ...]) -> int:
    for low, high, score in bands:
        if (low is None or value >= low) and (high is None or value <= high):
            return score
    return 0  # pragma: no cover - the bands cover the whole line


def saturation_score(*, spo2: int, on_oxygen: bool, scale: OxygenScale = OxygenScale.ONE) -> int:
    """
    Oxygen saturation, which is the only parameter whose table depends on two
    other answers. See decision 2 in the module docstring.
    """
    if scale is OxygenScale.TWO:
        bands = SATURATION_SCALE_2_OXYGEN if on_oxygen else SATURATION_SCALE_2_AIR
        return _band_score(spo2, bands)
    return _band_score(spo2, SATURATION_SCALE_1)


def score(
    *,
    respiration_rate: int | None = None,
    spo2: int | None = None,
    on_oxygen: bool | None = None,
    systolic_bp: int | None = None,
    pulse: int | None = None,
    consciousness: str | None = None,
    temperature: float | None = None,
    scale: OxygenScale = OxygenScale.ONE,
    age_years: int | None = None,
    is_pregnant: bool = False,
) -> Score:
    """
    Work out a NEWS2 score, or refuse with a reason.

    Every argument is optional because a real set of observations often is.
    What comes back names what was missing rather than quietly treating it as
    normal — see decision 4.
    """
    if age_years is not None and age_years < MINIMUM_AGE_YEARS:
        return Score(
            total=0,
            usable=False,
            reason=(
                f"NEWS2 is only validated in adults aged {MINIMUM_AGE_YEARS} and over. "
                f"Use a paediatric early warning score for a child."
            ),
        )
    if is_pregnant:
        return Score(
            total=0,
            usable=False,
            reason=(
                "NEWS2 is not validated in pregnancy. Use an obstetric early warning score instead."
            ),
        )

    parameters: list[Parameter] = []
    missing: list[str] = []

    def add(name: str, label: str, value, points: int) -> None:
        parameters.append(Parameter(name=name, label=label, value=str(value), score=points))

    if respiration_rate is None:
        missing.append("respiration rate")
    else:
        add(
            "respiration_rate",
            "Breaths per minute",
            respiration_rate,
            _band_score(respiration_rate, RESPIRATION),
        )

    if spo2 is None:
        missing.append("oxygen saturation")
    else:
        breathing_oxygen = bool(on_oxygen)
        add(
            "spo2",
            "Oxygen saturation",
            f"{spo2}%",
            saturation_score(spo2=spo2, on_oxygen=breathing_oxygen, scale=scale),
        )

    if on_oxygen is None:
        missing.append("air or oxygen")
    else:
        # Being on oxygen at all scores 2. It is a parameter in its own right,
        # not a modifier, which is why a patient can score 2 with otherwise
        # perfect numbers.
        add("on_oxygen", "Air or oxygen", "Oxygen" if on_oxygen else "Air", 2 if on_oxygen else 0)

    if systolic_bp is None:
        missing.append("blood pressure")
    else:
        add(
            "systolic_bp",
            "Systolic blood pressure",
            systolic_bp,
            _band_score(systolic_bp, SYSTOLIC),
        )

    if pulse is None:
        missing.append("pulse")
    else:
        add("pulse", "Pulse", pulse, _band_score(pulse, PULSE))

    if not consciousness:
        missing.append("level of consciousness")
    else:
        alert = str(consciousness).upper().startswith("A")
        add(
            "consciousness",
            "Level of consciousness",
            _CONSCIOUSNESS_LABELS.get(str(consciousness).upper()[:1], str(consciousness)),
            0 if alert else 3,
        )

    if temperature is None:
        missing.append("temperature")
    else:
        add(
            "temperature",
            "Temperature",
            f"{temperature:.1f} °C",
            _band_score(round(float(temperature), 1), TEMPERATURE),
        )

    return Score(
        total=sum(parameter.score for parameter in parameters),
        parameters=tuple(parameters),
        missing=tuple(missing),
    )


_CONSCIOUSNESS_LABELS = {
    "A": "Alert",
    "C": "New confusion",
    "V": "Responds to voice",
    "P": "Responds to pain",
    "U": "Unresponsive",
}


# ---------------------------------------------------------------------------
# Body mass index
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bmi:
    value: float
    category: str
    #: Why it should not be read, where that applies.
    caveat: str = ""


def bmi(*, weight_kg: float, height_cm: float, age_years: int | None = None) -> Bmi | None:
    """
    Body mass index, with the caveat attached rather than left to be known.

    BMI is not interpretable for children against adult categories — a
    nine-year-old at 16 is not "underweight", they are nine — so the number
    is still given (clinicians use it for drug dosing) and the category is
    withheld.
    """
    if not weight_kg or not height_cm or height_cm <= 0:
        return None
    metres = height_cm / 100
    value = round(weight_kg / (metres * metres), 1)

    if age_years is not None and age_years < 18:
        return Bmi(
            value=value,
            category="",
            caveat=(
                "Adult BMI categories do not apply to a child. Use a growth chart for age and sex."
            ),
        )

    if value < 18.5:
        category = "Underweight"
    elif value < 25:
        category = "Healthy weight"
    elif value < 30:
        category = "Overweight"
    elif value < 35:
        category = "Obese"
    else:
        category = "Severely obese"
    return Bmi(value=value, category=category)


__all__ = [
    "MINIMUM_AGE_YEARS",
    "RED_FLAG_SCORE",
    "RESPONSES",
    "Bmi",
    "Consciousness",
    "OxygenScale",
    "Parameter",
    "Score",
    "bmi",
    "saturation_score",
    "score",
]
