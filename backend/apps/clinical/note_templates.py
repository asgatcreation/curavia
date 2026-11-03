"""
The shapes a consultation note can take.

A general practitioner writes SOAP — subjective, objective, assessment,
plan. A dentist writes a chart of thirty-two teeth. An ophthalmologist writes
two columns, right eye and left, and the whole examination is paired. A
physiotherapist writes a problem, a measurement, and what was done in the
session. Forcing all four into four textareas labelled S, O, A and P is how
hospital software ends up with everything typed into one box, which destroys
the only thing a structured note is for: being read back.

**Templates are code, not rows.** Three reasons. They are clinical
definitions rather than hospital configuration, so every hospital wants the
same dental chart and none of them wants to type it in. A template that lives
in a migration has to be migrated again every time a section is renamed,
across every tenant, while a template in code is one diff. And a signed note
stores its own content with its own keys, so a template changing later cannot
alter what a clinician signed — which is the property that matters most and
the one a mutable table makes hard to hold.

A hospital that needs its own template gets one by a pull request, which for
a clinical form is the right amount of friction. ``NoteTemplate`` as a tenant
table arrives if and when a customer actually asks.

The section ``kind`` is what the screen draws:

``text``
    One textarea. The content is a string.
``fields``
    A short row of labelled one-line inputs — visual acuity, pressures,
    ranges of movement. The content is ``{field_key: str}``.
``teeth``
    The dental chart: FDI notation, two arches. The content is
    ``{tooth: finding}``, holding only the teeth with something wrong, so an
    examination of a healthy mouth stores nothing rather than thirty-two
    copies of the word "normal".
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: FDI two-digit notation, the one Nigerian dental schools teach. The first
#: digit is the quadrant, clockwise from the patient's upper right; the
#: second is the tooth, counting from the midline outwards.
UPPER_RIGHT = ("18", "17", "16", "15", "14", "13", "12", "11")
UPPER_LEFT = ("21", "22", "23", "24", "25", "26", "27", "28")
LOWER_LEFT = ("31", "32", "33", "34", "35", "36", "37", "38")
LOWER_RIGHT = ("48", "47", "46", "45", "44", "43", "42", "41")

ADULT_TEETH = UPPER_RIGHT + UPPER_LEFT + LOWER_RIGHT + LOWER_LEFT

#: What a dentist marks on a tooth. Short, because it is drawn in a 32-cell
#: grid on a phone as well as a desktop.
TOOTH_FINDINGS = (
    "caries",
    "filled",
    "missing",
    "crown",
    "root canal",
    "mobile",
    "impacted",
    "for extraction",
)


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    kind: str = "text"
    hint: str = ""
    rows: int = 4
    #: For ``kind="fields"``: the one-line inputs, in order.
    fields: tuple[tuple[str, str], ...] = ()
    #: Suggested wording offered as buttons. Never filled in automatically —
    #: a note that writes itself is a note nobody read.
    phrases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    discipline: str
    summary: str
    sections: tuple[Section, ...] = field(default_factory=tuple)

    def section(self, key: str) -> Section | None:
        return next((item for item in self.sections if item.key == key), None)


SOAP = Template(
    key="soap",
    name="General consultation (SOAP)",
    discipline="General",
    summary="The default. Subjective, objective, assessment, plan.",
    sections=(
        Section(
            key="subjective",
            label="Subjective",
            hint="What the patient tells you, in their words where it matters.",
            rows=6,
        ),
        Section(
            key="objective",
            label="Objective",
            hint="What you found on examination. Vitals are already on the record.",
            rows=5,
        ),
        Section(
            key="assessment",
            label="Assessment",
            hint="What you think is going on, and what else it could be.",
            rows=4,
        ),
        Section(
            key="plan",
            label="Plan",
            hint="Investigations, treatment, advice given, and when to come back.",
            rows=5,
        ),
    ),
)

FOLLOW_UP = Template(
    key="follow_up",
    name="Follow-up visit",
    discipline="General",
    summary="For a review. Shorter, because most of the story is already written.",
    sections=(
        Section(key="progress", label="Since the last visit", rows=5),
        Section(key="examination", label="Examination today", rows=4),
        Section(key="plan", label="Plan", rows=4),
    ),
)

DENTAL = Template(
    key="dental",
    name="Dental examination",
    discipline="Dentistry",
    summary="A chart of the whole mouth, plus the findings in words.",
    sections=(
        Section(key="complaint", label="Complaint", rows=3),
        Section(
            key="chart",
            label="Dental chart",
            kind="teeth",
            hint="Mark only the teeth with something wrong. FDI notation.",
        ),
        Section(
            key="oral",
            label="Soft tissues, occlusion and hygiene",
            rows=4,
            phrases=(
                "Oral hygiene fair. Generalised calculus.",
                "Gingivae healthy, no bleeding on probing.",
                "Occlusion within normal limits.",
            ),
        ),
        Section(key="treatment", label="Treatment plan", rows=4),
    ),
)

EYE = Template(
    key="eye",
    name="Eye examination",
    discipline="Ophthalmology",
    summary="Paired, right and left, because an eye examination always is.",
    sections=(
        Section(key="complaint", label="Complaint", rows=3),
        Section(
            key="acuity",
            label="Visual acuity and pressures",
            kind="fields",
            hint="Unaided, with correction, and intraocular pressure in mmHg.",
            fields=(
                ("right_unaided", "Right, unaided"),
                ("right_corrected", "Right, corrected"),
                ("right_iop", "Right IOP"),
                ("left_unaided", "Left, unaided"),
                ("left_corrected", "Left, corrected"),
                ("left_iop", "Left IOP"),
            ),
        ),
        Section(key="anterior", label="Anterior segment", rows=4),
        Section(key="fundus", label="Fundus", rows=4),
        Section(key="refraction", label="Refraction and prescription", rows=3),
        Section(key="plan", label="Assessment and plan", rows=4),
    ),
)

PHYSIOTHERAPY = Template(
    key="physiotherapy",
    name="Physiotherapy session",
    discipline="Physiotherapy",
    summary="One session: where they are now, what was done, what is next.",
    sections=(
        Section(key="problem", label="Problem and history", rows=4),
        Section(
            key="measures",
            label="Measurements",
            kind="fields",
            hint="Only what you measured today.",
            fields=(
                ("pain", "Pain (0-10)"),
                ("rom", "Range of movement"),
                ("power", "Muscle power (MRC)"),
                ("gait", "Gait / mobility"),
            ),
        ),
        Section(key="session", label="Treatment given this session", rows=4),
        Section(key="home", label="Home programme", rows=3),
        Section(key="goals", label="Goals and review", rows=3),
    ),
)

ANTENATAL = Template(
    key="antenatal",
    name="Antenatal visit",
    discipline="Obstetrics",
    summary="A booking or review visit. NEWS2 does not apply in pregnancy.",
    sections=(
        Section(
            key="pregnancy",
            label="This pregnancy",
            kind="fields",
            fields=(
                ("gestation", "Gestation (weeks)"),
                ("parity", "Parity"),
                ("fundal_height", "Fundal height (cm)"),
                ("presentation", "Presentation"),
                ("fetal_heart", "Fetal heart"),
            ),
        ),
        Section(key="findings", label="Findings and concerns", rows=4),
        Section(key="plan", label="Plan and next visit", rows=4),
    ),
)

TEMPLATES: tuple[Template, ...] = (
    SOAP,
    FOLLOW_UP,
    DENTAL,
    EYE,
    PHYSIOTHERAPY,
    ANTENATAL,
)

DEFAULT_TEMPLATE = SOAP.key

_BY_KEY = {item.key: item for item in TEMPLATES}


def get(key: str) -> Template | None:
    return _BY_KEY.get(key)


def as_payload(template: Template) -> dict:
    """One template, in the shape the screen draws it from."""
    return {
        "key": template.key,
        "name": template.name,
        "discipline": template.discipline,
        "summary": template.summary,
        "sections": [
            {
                "key": section.key,
                "label": section.label,
                "kind": section.kind,
                "hint": section.hint,
                "rows": section.rows,
                "fields": [{"key": key, "label": label} for key, label in section.fields],
                "phrases": list(section.phrases),
            }
            for section in template.sections
        ],
    }


def catalogue() -> list[dict]:
    return [as_payload(template) for template in TEMPLATES]


__all__ = [
    "ADULT_TEETH",
    "DEFAULT_TEMPLATE",
    "TEMPLATES",
    "TOOTH_FINDINGS",
    "Section",
    "Template",
    "as_payload",
    "catalogue",
    "get",
]
