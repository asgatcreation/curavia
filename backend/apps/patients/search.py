"""
Finding a patient, and noticing when you are about to create a second one.

These are the same problem looked at from two ends. Search asks "is this
person already here?" when somebody is standing at the desk; duplicate
detection asks the same question when a clerk has already decided the answer
is no and started typing a new record.

**The search has to tolerate how names are actually given.** A patient called
Adebayo may be written Adebayou, Adebayo may be the surname or the first
name, a relative may give only the other name, and the phone number on the
record may belong to a neighbour. So the query runs two ways at once —
full-text over the weighted vector, and trigram similarity over the flattened
text — and takes the better score. Trigram alone is too loose on short
strings; full-text alone cannot cope with a misspelling.

**Duplicate detection is deliberately noisy and never blocking.** It shows
what it found and lets the clerk decide. A system that refuses to register
somebody because it thinks they might already exist produces a patient
registered under a slightly different name to get around it, which is the
duplicate it was trying to prevent plus a lie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    TrigramWordSimilarity,
)
from django.db.models import F, FloatField, Q, QuerySet
from django.db.models.functions import Greatest

from apps.patients.models import Patient

#: Below this, a trigram match is noise. Tuned on Nigerian names: 0.3 finds
#: "Adebayou" for "Adebayo" and "Chiamka" for "Chiamaka" without returning
#: every patient whose name contains an A.
SIMILARITY_FLOOR = 0.3

#: Duplicate detection is more suspicious than search, because the cost of
#: missing a duplicate is a split record and the cost of a false positive is
#: one glance at a list.
DUPLICATE_FLOOR = 0.4

#: Never scan the whole hospital for a short string. Two characters match
#: almost everything and the clerk has not finished typing.
MIN_QUERY_LENGTH = 2


def search_patients(query: str, *, limit: int = 25) -> QuerySet[Patient]:
    """
    Patients matching free text, best first.

    Runs inside the caller's tenant scope: ``Patient.objects`` refuses to
    answer without one, so this cannot reach another hospital's records.

    Merged records are excluded. An old MRN still resolves — see
    ``find_by_mrn`` — but it should not appear as a separate person in a
    list somebody is choosing from.
    """
    text = (query or "").strip()
    if len(text) < MIN_QUERY_LENGTH:
        return Patient.objects.none()

    # An MRN is distinctive enough that a partial match on it should win
    # outright, and clerks type them from a card far more often than they
    # type names.
    cleaned = text.lower()

    return (
        Patient.objects.filter(merged_into__isnull=True)
        .annotate(
            fulltext=SearchRank(F("search_vector"), SearchQuery(text, search_type="websearch")),
            # WORD similarity, not whole-string.
            #
            # search_text holds a name, two phone numbers and an MRN, so
            # comparing a one-word query against the whole of it scores
            # about 0.15 even for an exact surname — below any useful
            # threshold. Word similarity compares the query against the
            # closest word in the target, which is what a clerk typing one
            # name actually means.
            similarity=TrigramWordSimilarity(cleaned, "search_text"),
            score=Greatest(
                SearchRank(F("search_vector"), SearchQuery(text, search_type="websearch")),
                TrigramWordSimilarity(cleaned, "search_text"),
                output_field=FloatField(),
            ),
        )
        .filter(
            Q(similarity__gte=SIMILARITY_FLOOR)
            | Q(fulltext__gt=0)
            # Substring, for the case trigram is worst at: a short, exact
            # fragment such as the last four digits of a phone number.
            | Q(search_text__contains=cleaned)
        )
        .order_by("-score", "last_name", "first_name")[:limit]
    )


def find_by_mrn(mrn: str) -> Patient | None:
    """
    Resolve an MRN, following a merge.

    An MRN is written on a physical card that stays in a patient's wallet
    for years. When two records are merged the old number has to keep
    working, or somebody turns up with a card the system says does not
    exist.
    """
    patient = Patient.objects.filter(mrn__iexact=(mrn or "").strip()).first()
    if patient is None:
        return None
    # Follow the chain, with a bound: a cycle would be a bug elsewhere, and
    # an infinite loop at the front desk is worse than a wrong answer.
    seen = {patient.pk}
    while patient.merged_into_id is not None:
        if patient.merged_into_id in seen:  # pragma: no cover - defensive
            break
        seen.add(patient.merged_into_id)
        nxt = Patient.objects.filter(pk=patient.merged_into_id).first()
        if nxt is None:
            break
        patient = nxt
    return patient


@dataclass(frozen=True, slots=True)
class PossibleDuplicate:
    patient: Patient
    score: float
    #: What matched, in words, so the clerk can judge it rather than trust a
    #: number: "same phone number", "similar name, same date of birth".
    reasons: tuple[str, ...]

    @property
    def is_strong(self) -> bool:
        """Worth stopping for. A shared NIN or phone plus a similar name."""
        return self.score >= 0.75


def find_possible_duplicates(
    *,
    first_name: str,
    last_name: str,
    phone: str = "",
    date_of_birth: date | None = None,
    nin: str = "",
    limit: int = 5,
) -> list[PossibleDuplicate]:
    """
    Who this might already be.

    Scored rather than filtered, because the signals are not equal: a
    matching NIN is near-conclusive, a matching phone is strong but families
    share handsets, and a similar name alone is weak. The reasons are
    returned alongside so the clerk sees why each one is here.
    """
    name = f"{first_name} {last_name}".strip().lower()
    if not name:
        return []

    candidates = (
        Patient.objects.filter(merged_into__isnull=True)
        .annotate(similarity=TrigramWordSimilarity(name, "search_text"))
        .filter(
            Q(similarity__gte=DUPLICATE_FLOOR)
            | (Q(phone=phone) & ~Q(phone=""))
            | Q(identifiers__value=nin, identifiers__kind="nin") & ~Q(identifiers__value="")
        )
        .distinct()[: limit * 4]
    )

    scored: list[PossibleDuplicate] = []
    for candidate in candidates:
        score = float(getattr(candidate, "similarity", 0.0) or 0.0) * 0.5
        reasons: list[str] = []

        if score > 0:
            reasons.append("similar name")

        if phone and candidate.phone and candidate.phone == phone:
            score += 0.35
            reasons.append("same phone number")

        if date_of_birth and candidate.date_of_birth == date_of_birth:
            score += 0.2
            reasons.append("same date of birth")
        elif (
            date_of_birth
            and candidate.date_of_birth
            and candidate.date_of_birth.year == date_of_birth.year
        ):
            # Year alone is weak but common: a date of birth given as "1990"
            # by somebody who does not know the day.
            score += 0.05
            reasons.append("born the same year")

        if nin and candidate.identifiers.filter(kind="nin", value=nin).exists():
            score += 0.5
            reasons.append("same National Identification Number")

        if not reasons:
            continue
        scored.append(
            PossibleDuplicate(patient=candidate, score=min(score, 1.0), reasons=tuple(reasons))
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]
