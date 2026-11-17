"""
Deciding which reference range applies, and what a result means against it.

This is the arithmetic at the centre of the laboratory module, kept in one
place and as pure functions. Nothing here touches the database beyond the
ranges it is handed, nothing here writes anything, and nothing here knows
about a result row — ``LabResult`` arrives in 7c and calls into this.

Keeping it separate is not tidiness. Flagging is the part that has to be
right: a wrongly flagged potassium is a patient who is not telephoned about.
Pure functions are the part of a system you can test exhaustively, and the
tests for this file read like a laboratory handbook.

**The order of judgement.** Critical is checked before abnormal, because a
result can be both and only one of them is worth interrupting a doctor for.
Within normal, a result equal to a limit is normal: a haemoglobin of exactly
11.5 against a band of 11.5 to 16.5 is not low. Critical thresholds are the
other way round — at or past the threshold is critical — because the
threshold is the point at which somebody is already in danger.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING

from django.utils import timezone

from apps.laboratory.models import Analyte, OptionFlag, RangeSex, ReferenceRange, ValueType

if TYPE_CHECKING:  # pragma: no cover - import for types only
    from apps.patients.models import Patient


class Level(StrEnum):
    """
    What a result is, once compared with the range that applies to it.

    ``UNKNOWN`` is a real answer and not a failure: it means the hospital has
    not set a range covering this patient, and the honest thing to print is
    the number with no flag beside it. Guessing would be worse.
    """

    NORMAL = "normal"
    LOW = "low"
    HIGH = "high"
    CRITICAL_LOW = "critical_low"
    CRITICAL_HIGH = "critical_high"
    ABNORMAL = "abnormal"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


#: The levels that mean "tell the ordering doctor now". 7c reads this.
CRITICAL_LEVELS: frozenset[Level] = frozenset(
    {Level.CRITICAL_LOW, Level.CRITICAL_HIGH, Level.CRITICAL}
)

#: The levels that print with a flag beside the number.
ABNORMAL_LEVELS: frozenset[Level] = frozenset(
    {Level.LOW, Level.HIGH, Level.ABNORMAL} | set(CRITICAL_LEVELS)
)

#: What a laboratory report prints in the flag column.
MARKERS: dict[Level, str] = {
    Level.NORMAL: "",
    Level.LOW: "L",
    Level.HIGH: "H",
    Level.CRITICAL_LOW: "LL",
    Level.CRITICAL_HIGH: "HH",
    Level.ABNORMAL: "A",
    Level.CRITICAL: "!!",
    Level.UNKNOWN: "",
}


@dataclass(frozen=True, slots=True)
class Flag:
    """One result, judged. ``applied`` is the range used, where there was one."""

    level: Level
    applied: ReferenceRange | None = None

    @property
    def is_critical(self) -> bool:
        return self.level in CRITICAL_LEVELS

    @property
    def is_abnormal(self) -> bool:
        return self.level in ABNORMAL_LEVELS

    @property
    def marker(self) -> str:
        return MARKERS[self.level]


# --------------------------------------------------------------------------
# Age
# --------------------------------------------------------------------------


def age_days(patient: Patient, *, on: dt.date | None = None) -> int | None:
    """
    Age in whole days, or None when the date of birth is not known.

    Days rather than years because this feeds reference ranges, where the
    first month of life has several bands of its own. ``triage.patient_age``
    answers the same question in years for NEWS2; both read the same field,
    and neither guesses when it is empty.
    """
    if not patient.date_of_birth:
        return None
    today = on or timezone.localdate()
    return max((today - patient.date_of_birth).days, 0)


# --------------------------------------------------------------------------
# Choosing the range
# --------------------------------------------------------------------------


def range_for(
    analyte: Analyte,
    *,
    sex: str | None,
    age_days: int | None,
    ranges: Iterable[ReferenceRange] | None = None,
) -> ReferenceRange | None:
    """
    The one range that applies to this patient, or None.

    A range recorded for the patient's own sex wins over one recorded as
    "any", which is how a hospital sets a general band and then overrides it
    for women. Within one sex value the model refuses overlapping age bands,
    so at each step there is at most one candidate and the answer does not
    depend on row order.

    Pass ``ranges`` when they are already loaded — the result screen in 7c
    reads a whole panel at once and must not issue a query per analyte.
    """
    candidates = list(ranges if ranges is not None else analyte.ranges.all())

    for wanted in _sex_preference(sex):
        for candidate in candidates:
            if candidate.sex == wanted and candidate.covers(age_days):
                return candidate
    return None


def _sex_preference(sex: str | None) -> tuple[str, ...]:
    """
    Which ``sex`` values to try, most specific first.

    A patient recorded as neither female nor male — ``Sex.UNKNOWN``, which is
    the default on an emergency registration where nobody has asked yet — can
    only match an "any" range. See ``RangeSex``.
    """
    if sex in (RangeSex.FEMALE, RangeSex.MALE):
        return (sex, RangeSex.ANY)
    return (RangeSex.ANY,)


# --------------------------------------------------------------------------
# Judging the value
# --------------------------------------------------------------------------


def flag(
    analyte: Analyte,
    value: Decimal | str | int | float | None,
    *,
    sex: str | None = None,
    age_days: int | None = None,
    ranges: Iterable[ReferenceRange] | None = None,
) -> Flag:
    """
    Judge one value for one analyte.

    Returns ``UNKNOWN`` rather than raising when the value cannot be read as
    a number or when no range covers the patient. A laboratory screen has to
    be able to show a result it cannot flag; refusing to render it would mean
    a scientist cannot save their work because the catalogue is incomplete.
    """
    if value is None or value == "":
        return Flag(Level.UNKNOWN)

    if analyte.value_type == ValueType.CODED:
        return _flag_coded(analyte, str(value))
    if analyte.value_type == ValueType.TEXT:
        return Flag(Level.UNKNOWN)

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Flag(Level.UNKNOWN)
    if not number.is_finite():
        return Flag(Level.UNKNOWN)

    applied = range_for(analyte, sex=sex, age_days=age_days, ranges=ranges)
    if applied is None:
        return Flag(Level.UNKNOWN)
    return Flag(_level_against(number, applied), applied)


def _level_against(number: Decimal, applied: ReferenceRange) -> Level:
    """Critical first, then the normal band. See the module docstring."""
    if applied.critical_low is not None and number <= applied.critical_low:
        return Level.CRITICAL_LOW
    if applied.critical_high is not None and number >= applied.critical_high:
        return Level.CRITICAL_HIGH
    if applied.low is not None and number < applied.low:
        return Level.LOW
    if applied.high is not None and number > applied.high:
        return Level.HIGH
    return Level.NORMAL


def _flag_coded(analyte: Analyte, value: str) -> Flag:
    """
    Match a coded answer against the analyte's own list.

    Compared case-insensitively on the wording, because the list is the only
    source of allowed answers and a scientist who types "reactive" has said
    the same thing as the option labelled "Reactive".
    """
    wanted = value.strip().casefold()
    for option in analyte.options:
        if str(option.get("label", "")).strip().casefold() != wanted:
            continue
        marked = option.get("flag")
        if marked == OptionFlag.CRITICAL:
            return Flag(Level.CRITICAL)
        if marked == OptionFlag.ABNORMAL:
            return Flag(Level.ABNORMAL)
        return Flag(Level.NORMAL)
    return Flag(Level.UNKNOWN)


# --------------------------------------------------------------------------
# What the screens ask for
# --------------------------------------------------------------------------


def describe(
    analyte: Analyte,
    *,
    sex: str | None,
    age_days: int | None,
    ranges: Iterable[ReferenceRange] | None = None,
) -> str:
    """
    The reference band as it prints beside a result: "11.5 - 16.5 g/dL".

    Empty when no range applies, so the report leaves the column blank rather
    than printing a range that was not used.
    """
    applied = range_for(analyte, sex=sex, age_days=age_days, ranges=ranges)
    if applied is None:
        return ""
    band = applied.normal_label()
    if not band:
        return ""
    parts = [band]
    if analyte.unit:
        parts.append(analyte.unit)
    text = " ".join(parts)
    return f"{text} ({applied.note})" if applied.note else text
