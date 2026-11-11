"""
The checks that run before an order can be signed.

One principle, and everything here follows from it:

    **A warning that can be dismissed by reflex will be dismissed by
    reflex.**

So a warning is not a dialog that appears after signing. It is attached to
the order line from the moment the drug is added, it says what the problem
is in a sentence, and a critical one stops the whole basket until the
clinician either removes the order or writes down why they are going ahead.
That sentence is then stored on the order, because "what was the doctor
told" is the question a review asks, and the allergy list may have changed
by the time anybody looks.

**What this is not.** It is not a drug interaction engine. Curavia has no
drug dictionary until the pharmacy module, so what runs here is an honest,
small thing: does the name of this medicine contain a substance the patient
reacts to, or something in the same family. The cross-sensitivity table
below is hand-written, short, and limited to the families where a mistake is
routinely fatal in a Nigerian ward — the penicillins above all, where
"allergic to penicillin" and a prescription for Augmentin is the classic
preventable death.

Being a short list is the point. A check that fires on everything teaches
people to click through it, which is worse than no check at all.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

#: Families where a reaction to one member means a real risk from the others.
#: Keyed by what gets written on an allergy card; the values are what gets
#: written on a prescription.
CROSS_SENSITIVITY: dict[str, tuple[str, ...]] = {
    "penicillin": (
        "amoxicillin",
        "amoxycillin",
        "ampicillin",
        "augmentin",
        "cloxacillin",
        "flucloxacillin",
        "benzylpenicillin",
        "phenoxymethylpenicillin",
        "piperacillin",
        "tazocin",
        "unasyn",
        # The brand names actually written on a Nigerian prescription. A
        # check that only knows the generic name misses the one somebody
        # types, which is the only name that matters here.
        "ampiclox",
        "amoxil",
        "moxypen",
        "clavulin",
    ),
    "sulphonamide": ("cotrimoxazole", "septrin", "sulfamethoxazole", "sulphadiazine"),
    "sulfonamide": ("cotrimoxazole", "septrin", "sulfamethoxazole", "sulphadiazine"),
    "aspirin": ("aspirin", "acetylsalicylic"),
    "nsaid": ("ibuprofen", "diclofenac", "naproxen", "piroxicam", "aspirin", "indomethacin"),
    "codeine": ("codeine", "co-codamol", "dihydrocodeine"),
    "quinine": ("quinine", "quinidine", "chloroquine"),
    "sulfa": ("cotrimoxazole", "septrin", "sulfamethoxazole"),
}

#: Severities that stop a basket rather than warn about it.
BLOCKING_SEVERITIES = ("severe", "anaphylaxis")


@dataclass(frozen=True)
class OrderWarning:
    """
    One thing wrong with an order.

    ``tone`` is "critical" when it must stop the basket and "warning" when
    it is for the clinician to weigh. The ``message`` is the whole of what
    is shown — written as a sentence, naming the substance and the reaction,
    because "ALLERGY ALERT" tells a tired doctor nothing they can act on.
    """

    kind: str
    tone: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def _words(text: str) -> set[str]:
    return {word for word in re.split(r"[^a-z0-9]+", text.lower()) if len(word) > 2}


def _matches(substance: str, drug: str) -> str | None:
    """
    Does this drug involve this substance? Returns what matched, or None.

    Word-level rather than a bare substring: "cefalexin" contains "alex" and
    a substring check on short strings produces warnings nobody believes.
    """
    substance_words = _words(substance)
    drug_words = _words(drug)
    if not substance_words or not drug_words:
        return None

    direct = substance_words & drug_words
    if direct:
        return sorted(direct)[0]

    for word in substance_words:
        for family, members in CROSS_SENSITIVITY.items():
            if word != family and not word.startswith(family):
                continue
            for member in members:
                if member in drug_words or member in drug.lower():
                    return member
    return None


def allergy_warnings(*, drug_name: str, allergies) -> list[OrderWarning]:
    """
    Check one medicine against a patient's recorded allergies.

    ``allergies`` is any iterable of objects with ``substance``,
    ``severity``, ``reaction`` and ``is_active`` — the patients app's model,
    or a stand-in in a test.
    """
    found: list[OrderWarning] = []
    for allergy in allergies:
        if not getattr(allergy, "is_active", True):
            continue
        matched = _matches(allergy.substance, drug_name)
        if matched is None:
            continue

        severity = (allergy.severity or "").lower()
        blocking = severity in BLOCKING_SEVERITIES
        reaction = f" ({allergy.reaction})" if allergy.reaction else ""
        same_family = matched.lower() not in allergy.substance.lower()

        if same_family:
            detail = (
                f"{drug_name} is in the same family as {allergy.substance}, "
                f"which this patient reacts to{reaction}."
            )
        else:
            detail = f"This patient is allergic to {allergy.substance}{reaction}."

        found.append(
            OrderWarning(
                kind="allergy",
                tone="critical" if blocking else "warning",
                message=(
                    f"{detail} Recorded as {severity or 'an allergy'}."
                    + (
                        " Remove it, or change the medicine, before signing."
                        if blocking
                        else " Check before signing."
                    )
                ),
            )
        )
    return found


def duplicate_warning(*, name: str, existing_names: list[str]) -> OrderWarning | None:
    """
    Already asked for on this visit.

    Not blocking: a repeat potassium at four hours is deliberate, and a
    system that refused it would be wrong more often than the doctor. It is
    still worth saying, because the commoner cause is two people ordering
    the same thing without knowing.
    """
    target = _words(name)
    for existing in existing_names:
        if target and target == _words(existing):
            return OrderWarning(
                kind="duplicate",
                tone="warning",
                message=f"{existing} has already been ordered on this visit.",
            )
    return None


__all__ = [
    "BLOCKING_SEVERITIES",
    "CROSS_SENSITIVITY",
    "OrderWarning",
    "allergy_warnings",
    "duplicate_warning",
]
