"""
The wordings: what a patient agrees to, and what a certificate says.

**These live in code, not in the database.** A hospital cannot edit them,
and that is the point. Consent is an agreement to a particular form of
words, so wording that can be changed after the fact is wording that proves
nothing — the question a court asks is "what was this person actually
shown", and the only honest answer comes from a text that was fixed before
they were shown it and copied verbatim onto their record at the moment they
agreed. The same argument applies to a sick note: a certificate whose
sentences a clinic can rewrite is a certificate an employer cannot rely on.

Editing a wording here is therefore a deployment, and an old consent keeps
the words it was given under. ``version`` exists so that two records signed
under different wordings are distinguishable years later without having to
compare paragraphs by eye.

**The English is deliberately plain.** A consent form written in the
language of a medical defence union is a form that is signed without being
read, which is worse than no form, because it manufactures a record of an
understanding that did not happen. These are written to be read aloud to
somebody who left school at fourteen, and the Yoruba, Hausa and Igbo
translations that milestone 12 adds will be made from these sentences
rather than from a legal draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConsentForm:
    """One thing a patient can be asked to agree to."""

    key: str
    name: str
    #: The one line the clinician picks it by.
    summary: str
    #: Read to the patient, in order. Each is a complete sentence.
    statements: tuple[str, ...]
    #: The sentence the patient is agreeing to when they sign.
    declaration: str
    version: int = 1
    #: True where a second person must sign as well. Used where the law or
    #: ordinary prudence expects it — anaesthesia, transfusion, surgery.
    witness_expected: bool = False

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "statements": list(self.statements),
            "declaration": self.declaration,
            "version": self.version,
            "witness_expected": self.witness_expected,
        }


CONSENT_FORMS: tuple[ConsentForm, ...] = (
    ConsentForm(
        key="treatment",
        name="General treatment",
        summary="Ordinary examination and treatment at this hospital.",
        statements=(
            "I have come to this hospital to be examined and treated.",
            (
                "A doctor or nurse may examine me, which can include touching the part of my body "
                "that is giving trouble."
            ),
            "I may be asked to have tests, such as blood or urine tests, and to take medicines.",
            "Anything I am told to take, I can ask what it is for, and I can say no.",
            "Saying no to one thing does not mean I will be refused other care.",
        ),
        declaration=(
            "I agree to be examined and treated here. I understand I can "
            "change my mind at any time and say so."
        ),
    ),
    ConsentForm(
        key="procedure",
        name="Surgery or procedure",
        summary="A named operation or procedure, with its risks.",
        statements=(
            "The doctor has told me the name of the operation or procedure I am to have.",
            (
                "The doctor has told me why it is being done and what is likely to happen if it is "
                "not done."
            ),
            (
                "The doctor has told me what can go wrong, including the serious things, even if "
                "they are rare."
            ),
            (
                "I know that an operation can find something unexpected, and that the doctor may "
                "then have to do something that was not planned in order to keep me safe."
            ),
            "I have had the chance to ask questions and my questions have been answered.",
            "No one has promised me a particular result.",
        ),
        declaration=(
            "I agree to the operation or procedure named on this form, and to "
            "anything that must be done during it to keep me safe."
        ),
        witness_expected=True,
    ),
    ConsentForm(
        key="anaesthesia",
        name="Anaesthesia",
        summary="Being put to sleep, or numbed, for a procedure.",
        statements=(
            (
                "An anaesthetist has explained how I will be put to sleep, or how the part of my "
                "body being operated on will be made numb."
            ),
            (
                "I have told them about every medicine I take, including anything bought without a "
                "prescription and any herbal preparation."
            ),
            (
                "I have told them about any reaction I or my close family have had to an "
                "anaesthetic before."
            ),
            "I know that feeling sick, a sore throat and a headache afterwards are common.",
            (
                "I know that serious problems are rare but do happen, and that the anaesthetist "
                "stays with me throughout."
            ),
        ),
        declaration="I agree to have the anaesthetic that has been explained to me.",
        witness_expected=True,
    ),
    ConsentForm(
        key="transfusion",
        name="Blood transfusion",
        summary="Receiving blood or a blood product.",
        statements=(
            (
                "The doctor has told me why I need blood and what is likely to happen if I do not "
                "have it."
            ),
            "The blood will be cross-matched for me and screened before it is given.",
            (
                "I know that screening greatly reduces the risk of infection but cannot remove it "
                "entirely."
            ),
            (
                "I know a reaction can happen during a transfusion, and that I will be watched "
                "while it runs."
            ),
            "I have been told if there is anything else that could be given instead.",
            (
                "If I refuse blood for religious or any other reason, I may say so and my care "
                "will continue."
            ),
        ),
        declaration="I agree to be given blood or a blood product if it is needed.",
        witness_expected=True,
    ),
    ConsentForm(
        key="hiv_test",
        name="HIV test",
        summary="Testing for HIV, with counselling.",
        statements=(
            "Someone has talked to me about the test before I decided.",
            "I know what a positive result would mean and what treatment is available here.",
            (
                "I know a negative result today does not cover a recent exposure, and that the "
                "test may need repeating."
            ),
            (
                "My result is confidential. It goes on my record and is seen only by the staff "
                "caring for me."
            ),
            "I can refuse this test and still be treated for everything else.",
        ),
        declaration="I agree to be tested for HIV.",
    ),
    ConsentForm(
        key="data",
        name="How my information is used",
        summary="Record keeping under the Nigeria Data Protection Act 2023.",
        statements=(
            "This hospital keeps a record of my care, on paper and on a computer.",
            (
                "My record is used to treat me, to bill me or my HMO, and to meet the hospital's "
                "legal duties."
            ),
            (
                "Staff see only what they need in order to do their work, and every time my record "
                "is opened it is logged."
            ),
            (
                "My information is not sold, and it is not given to an employer, a landlord or a "
                "family member without my say-so, unless the law requires it."
            ),
            (
                "I can ask to see my record, ask for a mistake in it to be corrected, and ask who "
                "has looked at it."
            ),
            "My record is kept for as long as the law requires even if I stop coming here.",
        ),
        declaration=(
            "I understand how this hospital keeps and uses my information, and I agree to it."
        ),
    ),
    ConsentForm(
        key="photography",
        name="Photographs",
        summary="Clinical photographs, and whether they may be used to teach.",
        statements=(
            (
                "A photograph may be taken of the part of my body being treated, so that the "
                "change over time can be seen."
            ),
            "The photograph is part of my record and is kept as securely as the rest of it.",
            "It may be shown to other staff treating me.",
            (
                "It will not be used for teaching, publication or advertising unless I agree to "
                "that separately here."
            ),
            (
                "I can withdraw this agreement later, and the photograph will stop being used for "
                "anything beyond my own care."
            ),
        ),
        declaration=("I agree to clinical photographs being taken as part of my care."),
    ),
)

_CONSENT_BY_KEY = {form.key: form for form in CONSENT_FORMS}


def consent_catalogue() -> list[dict]:
    return [form.as_dict() for form in CONSENT_FORMS]


def consent_form(key: str) -> ConsentForm | None:
    return _CONSENT_BY_KEY.get(key)


# ---------------------------------------------------------------------------
# Documents the hospital issues
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentTemplate:
    """
    One kind of paper the hospital hands out.

    ``fields`` are what the clinician fills in. ``fixed_text`` is the part
    that is the same on every copy and is not the clinician's to edit —
    the sentence an employer is actually relying on.
    """

    key: str
    name: str
    summary: str
    fields: tuple[tuple[str, str], ...]
    fixed_text: tuple[str, ...] = ()
    #: Shown to whoever scans the QR code. Never includes a diagnosis.
    public_description: str = ""
    version: int = 1
    #: Fields that must be filled in before it can be signed.
    required: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "fields": [{"key": key, "label": label} for key, label in self.fields],
            "fixed_text": list(self.fixed_text),
            "version": self.version,
            "required": list(self.required),
        }


DOCUMENT_TEMPLATES: tuple[DocumentTemplate, ...] = (
    DocumentTemplate(
        key="sick_note",
        name="Sick note",
        summary="Excuse duty: this patient was unfit for work or school.",
        fields=(
            ("from_date", "Unfit from"),
            ("to_date", "Unfit until"),
            ("resume_date", "May resume on"),
            ("advice", "Advice to the employer or school"),
        ),
        required=("from_date", "to_date"),
        fixed_text=(
            "This patient was seen at this hospital on the date shown and was "
            "found to be unfit for work or school for the period stated.",
            "No diagnosis is given on this note. A diagnosis is confidential "
            "to the patient, and an employer does not need it in order to act "
            "on this certificate.",
        ),
        public_description="A sick note covering a stated period.",
    ),
    DocumentTemplate(
        key="fitness",
        name="Certificate of fitness",
        summary="Fit for work, school, sport or travel.",
        fields=(
            ("purpose", "Fit for what"),
            ("findings", "What was examined"),
            ("restrictions", "Restrictions, if any"),
            ("valid_until", "Valid until"),
        ),
        required=("purpose",),
        fixed_text=(
            "This patient was examined at this hospital on the date shown and "
            "was found fit for the purpose stated, subject to any restriction "
            "written on this certificate.",
            "A certificate of fitness describes the patient on the day of "
            "examination. It is not a prediction of their health afterwards.",
        ),
        public_description="A certificate of fitness for a stated purpose.",
    ),
    DocumentTemplate(
        key="medical_report",
        name="Medical report",
        summary="A full account of the illness and treatment, for the patient.",
        fields=(
            ("history", "History"),
            ("examination", "Examination"),
            ("investigations", "Investigations"),
            ("diagnosis", "Diagnosis"),
            ("treatment", "Treatment given"),
            ("progress", "Progress and outcome"),
            ("recommendation", "Recommendation"),
        ),
        required=("history", "diagnosis"),
        fixed_text=(
            "This report is issued at the request of the patient named on it "
            "and covers the care given at this hospital.",
        ),
        public_description=(
            "A medical report. Its contents are confidential to the patient and are not shown here."
        ),
    ),
    DocumentTemplate(
        key="referral_letter",
        name="Referral letter",
        summary="A letter to another clinician, for the patient to carry.",
        fields=(
            ("to", "To"),
            ("reason", "Reason for referral"),
            ("history", "Relevant history"),
            ("findings", "Findings so far"),
            ("treatment", "Treatment given so far"),
            ("request", "What is being asked for"),
        ),
        required=("to", "reason"),
        public_description="A referral letter to another clinician.",
    ),
    DocumentTemplate(
        key="attendance",
        name="Proof of attendance",
        summary="Confirms only that the patient attended, and when.",
        fields=(("seen_for", "Seen in"), ("note", "Note")),
        fixed_text=(
            "This confirms that the patient named on this document attended "
            "this hospital on the date shown. Nothing about their health is "
            "stated here.",
        ),
        public_description="Confirmation that the patient attended on a stated date.",
    ),
)

_DOCUMENT_BY_KEY = {template.key: template for template in DOCUMENT_TEMPLATES}


def document_catalogue() -> list[dict]:
    return [template.as_dict() for template in DOCUMENT_TEMPLATES]


def document_template(key: str) -> DocumentTemplate | None:
    return _DOCUMENT_BY_KEY.get(key)


__all__ = [
    "CONSENT_FORMS",
    "DOCUMENT_TEMPLATES",
    "ConsentForm",
    "DocumentTemplate",
    "consent_catalogue",
    "consent_form",
    "document_catalogue",
    "document_template",
]
