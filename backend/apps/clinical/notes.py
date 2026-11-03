"""
Writing, signing and amending a consultation note.

The rules this module exists to hold, and nothing else is allowed to:

1. **Only a draft can be written to.** Everything that writes content goes
   through ``autosave``, which refuses a signed note. The model refuses it
   too, because a rule in one function is a rule somebody will route around.
2. **Signing is the only thing that makes a note evidence.** It snapshots who
   signed it, hashes the wording, and closes the note.
3. **Changing a signed note means adding a version.** ``amend`` opens version
   N+1 with a reason; the earlier version keeps its signature and stays
   readable. Nothing is ever overwritten and nothing is ever deleted.
4. **An unsigned note is nobody's evidence.** It is marked as a draft
   wherever it appears, it is only visible to its author, and no other module
   reads it.

Autosave deliberately writes **no audit event per save**. A doctor typing for
twenty minutes would otherwise produce two hundred audit rows that say
nothing, and burying the signature among them is how an audit log becomes
unusable. What is audited is what has meaning: the note being started, signed,
amended, and read.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.clinical import note_templates
from apps.clinical.models import ClinicalNote, hash_content
from apps.journey.models import Encounter

logger = logging.getLogger("curavia.clinical")

#: The longest a single section may be. Generous — a complicated admission
#: note runs long — but not unbounded, because the field is rendered into a
#: PDF and shown on a phone.
MAX_SECTION_CHARACTERS = 20_000


class NoteError(Exception):
    """Refused. The message is shown to the clinician as written."""


class NoteLocked(NoteError):
    """The note is signed. Nothing may change its wording."""


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def clean_content(template: note_templates.Template, content: dict | None) -> dict:
    """
    Keep what belongs to this template and throw the rest away.

    A section the template does not have is dropped rather than rejected:
    a client left open while a template gained a section should not have its
    whole consultation refused, and silently storing keys nobody will ever
    render is how a note becomes unreadable later.

    Empty sections are dropped as well, so an untouched eye examination
    stores nothing instead of six empty strings.
    """
    if content is None:
        return {}
    if not isinstance(content, dict):
        raise NoteError("The note could not be read. Nothing was saved.")

    cleaned: dict = {}
    for section in template.sections:
        value = content.get(section.key)
        if value is None:
            continue

        if section.kind == "text":
            if not isinstance(value, str):
                raise NoteError(f"{section.label} has to be text.")
            text = value.strip()[:MAX_SECTION_CHARACTERS]
            if text:
                cleaned[section.key] = text
            continue

        if not isinstance(value, dict):
            raise NoteError(f"{section.label} could not be read. Nothing was saved.")

        allowed = _allowed_keys(section)
        inner = {}
        for key, item in value.items():
            if allowed is not None and key not in allowed:
                continue
            if not isinstance(item, str):
                raise NoteError(f"{section.label} has to be text.")
            text = item.strip()[:500]
            if text:
                inner[key] = text
        if inner:
            cleaned[section.key] = inner

    return cleaned


def _allowed_keys(section: note_templates.Section) -> set[str] | None:
    if section.kind == "fields":
        return {key for key, _label in section.fields}
    if section.kind == "teeth":
        return set(note_templates.ADULT_TEETH)
    return None


def is_empty(content: dict | None) -> bool:
    return not clean_of_blanks(content)


def clean_of_blanks(content: dict | None) -> dict:
    if not content:
        return {}
    return {key: value for key, value in content.items() if value}


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


@transaction.atomic
def open_draft(
    *,
    encounter: Encounter,
    author,
    template_key: str = note_templates.DEFAULT_TEMPLATE,
) -> ClinicalNote:
    """
    The author's draft for this visit, opening one if there is none.

    Idempotent: a doctor who presses "write a note" twice, or has the screen
    open on two machines, gets the one draft back rather than two halves of
    one consultation.
    """
    template = note_templates.get(template_key)
    if template is None:
        raise NoteError(f"There is no note template called {template_key!r}.")
    if encounter.status != Encounter.Status.OPEN:
        raise NoteError("That visit is closed. A closed visit's notes can still be read.")

    existing = ClinicalNote.objects.filter(
        encounter=encounter,
        author=author,
        template_key=template_key,
        status=ClinicalNote.Status.DRAFT,
        archived_at__isnull=True,
    ).first()
    if existing is not None:
        return existing

    note = ClinicalNote(
        organisation_id=encounter.organisation_id,
        branch_id=encounter.branch_id,
        encounter=encounter,
        patient_id=encounter.patient_id,
        author=author,
        template_key=template_key,
    )
    # The id exists before the row does — see apps.core.utils.uuid7 — so a
    # first version can be its own thread without a second write.
    note.thread = note.pk
    note.save()
    return note


def autosave(*, note: ClinicalNote, content: dict | None, actor) -> ClinicalNote:
    """
    Replace a draft's wording. The only thing that writes content.

    Refuses anybody but the author. A note is a statement by one clinician
    about what they found, and a second person typing into it would be
    putting words in their mouth — if a colleague has something to add, they
    write their own note, which is also how it works on paper.
    """
    if note.is_signed:
        raise NoteLocked(
            "This note is signed, so its wording cannot be changed. Add an amendment instead."
        )
    if actor is not None and note.author_id != actor.pk:
        raise NoteError("This is somebody else's draft. Write your own note for this visit.")

    template = note_templates.get(note.template_key)
    if template is None:  # pragma: no cover - a template removed under a draft
        raise NoteError("The template this note was started from no longer exists.")

    note.content = clean_content(template, content)
    note.last_saved_at = timezone.now()
    note.updated_by = actor
    note.save(update_fields=["content", "last_saved_at", "updated_by", "updated_at"])
    return note


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def signature_for(*, user, membership=None) -> dict:
    """
    The four lines that print under a signature, read now and stored.

    Falls back to the account's name and nothing else when there is no
    membership: a note signed by somebody whose profile is incomplete should
    still be signed, with the name of whoever signed it, rather than refused.
    """
    name = (user.get_full_name() or user.email).strip() if user else ""
    return {
        "signature_name": name[:160],
        "signature_qualifications": (getattr(membership, "qualifications", "") or "")[:120],
        "signature_registration": (getattr(membership, "professional_registration", "") or "")[:60],
        "signature_role": (getattr(membership, "job_title", "") or "")[:120],
    }


@transaction.atomic
def sign(*, note: ClinicalNote, actor, membership=None) -> ClinicalNote:
    """
    Close the note and put a name to it.

    Signing is refused on an empty note. A signature over nothing is worse
    than no note at all, because the record then says a clinician examined
    somebody and wrote down what they found.

    Once signed, the hash covers the wording, and the note's own ``save()``
    refuses any later write that would change it.
    """
    if note.is_signed:
        raise NoteLocked("This note is already signed.")
    if actor is not None and note.author_id != actor.pk:
        raise NoteError("A note is signed by the person who wrote it.")
    if is_empty(note.content):
        raise NoteError("There is nothing in this note yet.")
    if note.version > 1 and not note.amendment_reason.strip():
        raise NoteError("Say why this note is being amended before signing it.")

    locked = ClinicalNote.objects.select_for_update().get(pk=note.pk)
    if locked.is_signed:  # two clicks, two requests, one signature
        raise NoteLocked("This note is already signed.")

    now = timezone.now()
    for field, value in signature_for(user=actor, membership=membership).items():
        setattr(locked, field, value)
    locked.status = ClinicalNote.Status.SIGNED
    locked.signed_at = now
    locked.signed_by = actor
    locked.content_hash = hash_content(locked.content)
    # An amendment is not current until it is signed — see `amend` — so
    # signing is where it takes over.
    locked.is_current = True
    locked.save(
        update_fields=[
            "status",
            "signed_at",
            "signed_by",
            "content_hash",
            "is_current",
            "signature_name",
            "signature_qualifications",
            "signature_registration",
            "signature_role",
            "updated_at",
        ]
    )

    # The newly signed version becomes the one screens show, and the version
    # it amends stops being current. Done here rather than at `amend` time so
    # that an amendment abandoned as a draft leaves the record alone.
    if locked.supersedes_id:
        ClinicalNote.objects.filter(pk=locked.supersedes_id).update(
            is_current=False, updated_at=now
        )

    logger.info("clinical note %s signed by %s", locked.pk, getattr(actor, "pk", None))
    return locked


@transaction.atomic
def amend(*, note: ClinicalNote, reason: str, actor, membership=None) -> ClinicalNote:
    """
    Open the next version of a signed note.

    The new version starts as a copy of the signed one, so the clinician
    corrects a sentence rather than retyping a consultation, and it is a
    draft until it is signed in its own right. The version it amends is
    untouched — signature, hash and wording — and stays readable with its
    own signature block.

    A hospital may allow somebody other than the author to amend
    (``clinical.note.amend`` is a separate permission, held by the owner and
    doctors), because the author may have left. The amendment carries the
    name of whoever signs it, not the original author's.
    """
    if not note.is_signed:
        raise NoteError("This note is still a draft. Edit it instead of amending it.")
    if not reason.strip():
        raise NoteError("Say why the note is being amended.")

    existing = ClinicalNote.objects.filter(
        thread=note.thread, status=ClinicalNote.Status.DRAFT, archived_at__isnull=True
    ).first()
    if existing is not None:
        raise NoteError(
            "There is already an unsigned amendment to this note. Finish or discard it first."
        )

    newest = ClinicalNote.objects.filter(thread=note.thread).order_by("-version").first() or note

    amendment = ClinicalNote(
        organisation_id=note.organisation_id,
        branch_id=note.branch_id,
        encounter_id=note.encounter_id,
        patient_id=note.patient_id,
        author=actor,
        template_key=note.template_key,
        content=dict(newest.content or {}),
        thread=note.thread,
        version=newest.version + 1,
        supersedes=newest,
        amendment_reason=reason.strip()[:300],
        is_sensitive=note.is_sensitive,
        # Not current until it is signed: an abandoned amendment must not
        # replace a signed note on anybody's screen.
        is_current=False,
    )
    amendment.save()
    return amendment


@transaction.atomic
def discard_draft(*, note: ClinicalNote, actor) -> None:
    """
    Throw away an unsigned draft.

    Archived rather than deleted, like everything else: "a doctor started a
    note and abandoned it" is itself sometimes the question being asked. A
    signed note can never be discarded by anybody.
    """
    if note.is_signed:
        raise NoteLocked("A signed note cannot be discarded. Amend it instead.")
    if actor is not None and note.author_id != actor.pk:
        raise NoteError("This is somebody else's draft.")
    note.archive(by=actor)


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


def for_encounter(encounter: Encounter, *, reader=None) -> list[ClinicalNote]:
    """
    The notes on one visit, newest first.

    Other people's unsigned drafts are left out. A draft is not evidence and
    reading half of a colleague's thinking is how a wrong impression travels
    — but the reader's own drafts are included, because that is how they get
    back to what they were writing.
    """
    rows = (
        ClinicalNote.objects.filter(encounter=encounter, archived_at__isnull=True)
        .select_related("author", "signed_by")
        .order_by("-last_saved_at")
    )
    reader_id = getattr(reader, "pk", None)
    return [
        note
        for note in rows
        if note.is_signed or (reader_id is not None and note.author_id == reader_id)
    ]


def history(note: ClinicalNote) -> list[ClinicalNote]:
    """Every version of one note, oldest first."""
    return list(
        ClinicalNote.objects.filter(thread=note.thread)
        .select_related("author", "signed_by")
        .order_by("version")
    )


def for_patient(patient, *, limit: int = 40) -> list[ClinicalNote]:
    """Signed notes across every visit — the clinical history."""
    return list(
        ClinicalNote.objects.filter(
            patient=patient,
            status=ClinicalNote.Status.SIGNED,
            is_current=True,
            archived_at__isnull=True,
        )
        .select_related("author", "signed_by", "encounter")
        .order_by("-signed_at")[:limit]
    )


def verify(note: ClinicalNote) -> bool:
    """
    Does a signed note's wording still match what was signed?

    Returns True for a draft, which has nothing to verify. Used by the
    verification page behind the QR code on a printed note, and by a test
    that tries to tamper with a signed row directly through SQL.
    """
    if not note.is_signed:
        return True
    return hash_content(note.content) == note.content_hash


__all__ = [
    "MAX_SECTION_CHARACTERS",
    "NoteError",
    "NoteLocked",
    "amend",
    "autosave",
    "clean_content",
    "discard_draft",
    "for_encounter",
    "for_patient",
    "history",
    "is_empty",
    "open_draft",
    "sign",
    "signature_for",
    "verify",
]
