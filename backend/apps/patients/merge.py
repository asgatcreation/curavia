"""
Merging two records that turned out to be the same person.

Duplicates are not an edge case. A patient arrives without their card, the
clerk cannot find them because the surname was spelled differently the first
time, and a second record is created. Three months later a doctor is looking
at half a history — and the half they cannot see is where the penicillin
allergy was recorded.

So this is the most dangerous button in the front-desk module, and the design
answers that in four ways:

**1. Nothing is deleted.** The losing record stays in the table with its MRN
and a ``merged_into`` pointer. An MRN lives in a patient's wallet for years;
a merge that made an old card stop working would send somebody away from the
desk. ``find_by_mrn`` follows the pointer, so the card still opens the right
record.

**2. Nothing is overwritten.** The surviving record keeps every value it has.
Blanks on it are filled from the loser. A merge can only ever add
information, which means the worst case of a wrong merge is a record with a
stranger's phone number in it — not a record with the right phone number
gone.

**3. It is reversible.** Children are *moved*, not copied, and the
``PatientMerge`` row records exactly what moved and which fields were filled,
with their previous values. Reversal puts all of it back. Copying would have
been easier and would have made reversal guesswork: two identical allergy
rows and no way to know which one was there first.

**4. Clinically unsafe outcomes are impossible rather than discouraged.** A
merge never clears a death flag, and never loses an allergy. Where the two
records disagree about severity, the worse one wins — in both directions, so
reversing a merge cannot quietly downgrade a warning either.

What a merge deliberately does NOT do: decide which record should win. That
is a judgement about which history is the real one, and it belongs to the
health records officer looking at both. ``suggest_winner`` offers an opinion
and the API makes the caller state their choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.patients.models import (
    Allergy,
    NextOfKin,
    Patient,
    PatientIdentifier,
    PatientMerge,
)

#: Fields on the surviving record that a blank will be filled from.
#:
#: Demographic only. Nothing here is a clinical judgement — blood group and
#: genotype are included because they are laboratory facts recorded at
#: registration, and a record that has one when the other does not is almost
#: always the same test written down once.
FILLABLE_FIELDS: tuple[str, ...] = (
    "other_names",
    "date_of_birth",
    "phone",
    "alternate_phone",
    "email",
    "address",
    "state",
    "lga",
    "blood_group",
    "genotype",
    "marital_status",
    "occupation",
    "default_payer_id",
    "home_branch_id",
)

#: Worst first. Used in both directions: a merge raises the surviving
#: record's severity to the worse of the two, and a reversal puts the
#: original back rather than keeping the raised one.
SEVERITY_ORDER: tuple[str, ...] = (
    Allergy.Severity.ANAPHYLAXIS,
    Allergy.Severity.SEVERE,
    Allergy.Severity.MODERATE,
    Allergy.Severity.MILD,
)


def _severity_rank(value: str) -> int:
    """Lower is worse. An unrecognised value is treated as the mildest."""
    try:
        return SEVERITY_ORDER.index(value)  # type: ignore[arg-type]
    except ValueError:  # pragma: no cover - defensive
        return len(SEVERITY_ORDER)


class MergeError(Exception):
    """The merge was refused. The message is shown to the clerk."""


# ---------------------------------------------------------------------------
# Previewing
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MergePlan:
    """
    What a merge would do, before it does it.

    Returned to the screen so the clerk confirms an outcome rather than an
    intention. Every list here is in the words a clerk would use, because
    the person pressing the button is not reading field names.
    """

    winner: Patient
    loser: Patient
    #: field name → (what the winner has now, what it would become)
    fills: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    moves: dict[str, int] = field(default_factory=dict)
    #: Allergies the surviving record already has, where the two disagree.
    severity_changes: list[dict[str, str]] = field(default_factory=list)
    #: Substances on both records. Nothing is lost — the losing record is
    #: kept — but the clerk should see that a row stayed behind.
    allergies_already_present: list[str] = field(default_factory=list)
    #: Things worth saying out loud before the button is pressed.
    warnings: list[str] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return not self.warnings


def suggest_winner(a: Patient, b: Patient) -> tuple[Patient, Patient]:
    """
    Which of two records should probably survive, and which should fold in.

    An opinion, not a decision — the API makes the caller name the winner
    explicitly. The ordering is: a confirmed identity beats a provisional
    one, then the older registration wins, because the longer history is
    usually attached to it and because the older MRN is the one more likely
    to be written on a card somebody still carries.
    """
    if a.is_provisional != b.is_provisional:
        return (b, a) if a.is_provisional else (a, b)
    return (a, b) if a.registered_at <= b.registered_at else (b, a)


def plan_merge(*, winner: Patient, loser: Patient) -> MergePlan:
    """
    Work out what merging ``loser`` into ``winner`` would change.

    Pure: reads both records and returns a description. ``merge_patients``
    calls this too, so what the clerk approved is what gets applied.
    """
    _refuse_impossible(winner=winner, loser=loser)

    plan = MergePlan(winner=winner, loser=loser)

    for name in FILLABLE_FIELDS:
        mine = getattr(winner, name)
        theirs = getattr(loser, name)
        if _is_blank(mine) and not _is_blank(theirs):
            plan.fills[name] = (mine, theirs)

    plan.moves = {
        "identifiers": loser.identifiers.count(),
        "next_of_kin": loser.next_of_kin.count(),
        "allergies": 0,
    }

    existing = {row.substance.strip().lower(): row for row in winner.allergies.all()}
    for row in loser.allergies.all():
        key = row.substance.strip().lower()
        twin = existing.get(key)
        if twin is None:
            plan.moves["allergies"] += 1
            continue
        plan.allergies_already_present.append(row.substance)
        if _severity_rank(row.severity) < _severity_rank(twin.severity):
            plan.severity_changes.append(
                {
                    "substance": twin.substance,
                    "was": twin.get_severity_display(),
                    "becomes": row.get_severity_display(),
                }
            )

    # Warnings, in the order a records officer would want to hear them.
    if loser.is_deceased and not winner.is_deceased:
        plan.warnings.append(
            "The record being merged is marked deceased. The kept record will be too."
        )
    if winner.sex != loser.sex and "U" not in {winner.sex, loser.sex}:
        plan.warnings.append(
            f"These records disagree about sex ({winner.get_sex_display()} and "
            f"{loser.get_sex_display()}). The kept record is not changed."
        )
    if (
        winner.date_of_birth
        and loser.date_of_birth
        and winner.date_of_birth != loser.date_of_birth
        and not winner.date_of_birth_is_estimated
        and not loser.date_of_birth_is_estimated
    ):
        plan.warnings.append(
            "Both records have a confirmed date of birth and they do not match. "
            "The kept record is not changed."
        )
    if winner.allergies_checked_at is None and loser.allergies_checked_at is not None:
        plan.warnings.append(
            "The allergy history was taken on the record being merged, not the one being kept. "
            "It will be carried across."
        )
    return plan


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _refuse_impossible(*, winner: Patient, loser: Patient) -> None:
    """
    The checks that are about correctness, not judgement.

    Raised rather than warned, because none of these has a sensible outcome
    for the clerk to approve.
    """
    if winner.pk == loser.pk:
        raise MergeError("Those are the same record.")
    if winner.organisation_id != loser.organisation_id:  # pragma: no cover - manager prevents it
        raise MergeError("Those records belong to different hospitals.")
    if loser.merged_into_id is not None:
        raise MergeError("That record has already been merged into another one.")
    if winner.merged_into_id is not None:
        raise MergeError(
            "The record you are keeping has itself been merged away. "
            "Merge into the surviving record instead."
        )


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


@transaction.atomic
def merge_patients(
    *,
    winner: Patient,
    loser: Patient,
    actor=None,
    reason: str = "",
) -> PatientMerge:
    """
    Fold ``loser`` into ``winner`` and record how to undo it.

    One transaction. A half-done merge — children moved, pointer not set —
    would leave a patient whose allergies live on a record nobody can reach,
    which is the exact failure this whole feature exists to prevent.

    Both records are locked. Two clerks merging the same pair at the same
    moment would otherwise each see an unmerged record and both proceed.
    """
    # Re-read under a lock, and plan from the locked rows: a plan built from
    # a stale copy would move children that have since moved.
    winner = Patient.objects.select_for_update().get(pk=winner.pk)
    loser = Patient.objects.select_for_update().get(pk=loser.pk)

    plan = plan_merge(winner=winner, loser=loser)

    manifest: dict[str, Any] = {
        "filled": {},
        "moved": {"identifiers": [], "next_of_kin": [], "allergies": []},
        "severity_raised": [],
        "allergy_history_taken_at": None,
        "marked_deceased": False,
    }

    # -- fields -------------------------------------------------------------
    changed: list[str] = []
    for name, (was, becomes) in plan.fills.items():
        setattr(winner, name, becomes)
        manifest["filled"][name] = {"was": _jsonable(was), "now": _jsonable(becomes)}
        changed.append(name)

    # Allergy history. Not in FILLABLE_FIELDS because it is not demographic:
    # carrying it across is what turns "nobody asked" into "asked, and the
    # answer is on the other record", and the banner reads it.
    if winner.allergies_checked_at is None and loser.allergies_checked_at is not None:
        winner.allergies_checked_at = loser.allergies_checked_at
        manifest["allergy_history_taken_at"] = loser.allergies_checked_at.isoformat()
        changed.append("allergies_checked_at")

    # Death is never un-set by a merge, in either direction.
    if loser.is_deceased and not winner.is_deceased:
        winner.is_deceased = True
        winner.died_on = winner.died_on or loser.died_on
        manifest["marked_deceased"] = True
        changed += ["is_deceased", "died_on"]

    if changed:
        winner.save(update_fields=sorted(set(changed)))

    # -- children -----------------------------------------------------------
    #
    # Moved by primary key so the manifest can move them back. `.update()`
    # rather than saving each row: these carry no logic, and a patient with
    # forty lab-scheme numbers should not be forty writes.
    for model, key in ((PatientIdentifier, "identifiers"), (NextOfKin, "next_of_kin")):
        ids = list(model.objects.filter(patient=loser).values_list("pk", flat=True))
        if ids:
            model.objects.filter(pk__in=ids).update(patient=winner)
            manifest["moved"][key] = [str(value) for value in ids]

    existing = {row.substance.strip().lower(): row for row in winner.allergies.all()}
    for row in list(loser.allergies.all()):
        twin = existing.get(row.substance.strip().lower())
        if twin is None:
            # Free to move: the surviving record has nothing by that name.
            Allergy.objects.filter(pk=row.pk).update(patient=winner)
            manifest["moved"]["allergies"].append(str(row.pk))
            continue
        # A collision. The row stays on the losing record — which is kept, so
        # nothing is lost — and the surviving record's severity is raised to
        # the worse of the two. Never lowered.
        if _severity_rank(row.severity) < _severity_rank(twin.severity):
            manifest["severity_raised"].append(
                {"allergy": str(twin.pk), "was": twin.severity, "now": row.severity}
            )
            Allergy.objects.filter(pk=twin.pk).update(severity=row.severity)

    # -- the pointer --------------------------------------------------------
    #
    # Last, so a failure anywhere above rolls back a record that is still
    # reachable on its own. `.update()` deliberately: the trigger maintaining
    # search_text runs in the database, so there is nothing Python has to do.
    Patient.objects.filter(pk=loser.pk).update(merged_into=winner)

    return PatientMerge.objects.create(
        organisation_id=winner.organisation_id,
        winner=winner,
        loser=loser,
        by=actor,
        reason=reason.strip(),
        manifest=manifest,
    )


@transaction.atomic
def reverse_merge(merge: PatientMerge, *, actor=None, reason: str = "") -> PatientMerge:
    """
    Put both records back the way they were.

    Refused once the surviving record has been merged again, because the
    children this merge moved may have moved on from there and putting them
    back would unpick somebody else's decision as well as this one.

    Rows the manifest names but that no longer exist are skipped rather than
    raising: a next of kin deleted after the merge is gone, and a reversal
    that refused on that basis would strand the two records together for
    good.
    """
    if merge.reversed_at is not None:
        raise MergeError("That merge has already been reversed.")

    winner = Patient.objects.select_for_update().get(pk=merge.winner_id)
    loser = Patient.objects.select_for_update().get(pk=merge.loser_id)

    if winner.merged_into_id is not None:
        raise MergeError(
            "The kept record has since been merged into another one. Reverse that merge first."
        )
    if loser.merged_into_id != winner.pk:
        raise MergeError("That record is no longer merged into this one.")

    manifest: dict[str, Any] = merge.manifest or {}
    moved: dict[str, Any] = manifest.get("moved", {})

    for model, key in (
        (PatientIdentifier, "identifiers"),
        (NextOfKin, "next_of_kin"),
        (Allergy, "allergies"),
    ):
        ids = moved.get(key) or []
        if ids:
            model.objects.filter(pk__in=ids, patient=winner).update(patient=loser)

    # Severity, back down to what it was. The merge only ever raised it, so
    # this cannot be the step that loses a warning — and it is guarded on the
    # current value, so a clinician who has since reviewed the allergy and
    # set a severity themselves is not overruled by an old manifest.
    for entry in manifest.get("severity_raised", []) or []:
        Allergy.objects.filter(pk=entry["allergy"], severity=entry["now"]).update(
            severity=entry["was"]
        )

    restored: list[str] = []
    for name, values in (manifest.get("filled") or {}).items():
        if name not in FILLABLE_FIELDS:  # pragma: no cover - manifest is ours
            continue
        # Only if nobody has typed over it since. The merge filled a blank;
        # if the field now holds something else, a human put it there.
        if _jsonable(getattr(winner, name)) == values.get("now"):
            setattr(winner, name, _restore(name, values.get("was")))
            restored.append(name)

    if manifest.get("allergy_history_taken_at") and winner.allergies_checked_at is not None:
        winner.allergies_checked_at = None
        restored.append("allergies_checked_at")

    # The only path by which Curavia ever clears a death flag, and it exists
    # because this merge is what set it. Guarded on the losing record still
    # being the source: if the kept record has since been confirmed deceased
    # on its own, a human set that and the manifest says nothing about it.
    if manifest.get("marked_deceased") and winner.is_deceased and loser.is_deceased:
        winner.is_deceased = False
        winner.died_on = None
        restored += ["is_deceased", "died_on"]

    if restored:
        winner.save(update_fields=sorted(set(restored)))

    Patient.objects.filter(pk=loser.pk).update(merged_into=None)

    merge.reversed_at = timezone.now()
    merge.reversed_by = actor
    merge.reversal_reason = reason.strip()
    merge.save(update_fields=["reversed_at", "reversed_by", "reversal_reason", "updated_at"])
    return merge


def _jsonable(value: Any) -> Any:
    """Manifest values go through JSON, so dates and UUIDs become strings."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _restore(name: str, value: Any) -> Any:
    """
    Turn a manifest value back into something the field will accept.

    A blank CharField round-trips as ``""`` and a null date as ``None``;
    those are the only two shapes a fill can have restored, because a fill
    only ever happens over a blank.
    """
    if value in (None, ""):
        return None if name.endswith("_id") or name == "date_of_birth" else ""
    return value  # pragma: no cover - a fill is always over a blank
