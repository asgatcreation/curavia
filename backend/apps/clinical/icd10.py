"""
The diagnosis catalogue: ICD-10, cut down to what a Nigerian clinic sees.

**Why a module and not a table.** Three reasons, the same ones that keep the
note templates in code.

A classification of disease is not hospital configuration. Every hospital
wants the identical list, and a tenant-scoped table would hold five hundred
identical copies of it, each able to drift.

A diagnosis stores the code **and the wording it was chosen under**. So a
correction here can never rewrite what a doctor recorded last year, which is
the property that matters, and the catalogue is free to change.

And this list has to be edited by somebody who can read the clinical
argument for an entry. A code added through an admin screen at two in the
morning, by somebody guessing, is how "malaria" ends up recorded under three
different codes and the monthly return to the ministry stops adding up.

**What is here.** Roughly a hundred and thirty codes covering what actually
walks into a Nigerian general practice, clinic or small hospital: malaria,
typhoid, hypertension, diabetes, the respiratory and gastrointestinal
presentations, obstetrics, the common injuries, and the Z codes for
immunisation and antenatal care. It is deliberately **not** the full ICD-10.
Fourteen thousand codes in a search box means a doctor picks the first
plausible one, and a register built from the first plausible code is worse
than a register built from a short, correct list.

The full set, or the NHIA's own list, drops in behind the same two functions
when a hospital needs it — ``search`` and ``get`` are the whole interface.

``chronic`` is the one clinical judgement encoded here: it marks the
conditions a patient *has* rather than ones they are *getting over*, and
confirming one opens an entry on their problem list. See
apps/clinical/diagnoses.py.
"""

from __future__ import annotations

from dataclasses import dataclass

#: (code, title, chapter, chronic)
_RAW: tuple[tuple[str, str, str, bool], ...] = (
    # ---------------------------------------------------- infectious disease
    ("A00", "Cholera", "Infections", False),
    ("A01.0", "Typhoid fever", "Infections", False),
    ("A09", "Diarrhoea and gastroenteritis of presumed infectious origin", "Infections", False),
    ("A15", "Respiratory tuberculosis", "Infections", True),
    ("A39", "Meningococcal infection", "Infections", False),
    ("A90", "Dengue fever", "Infections", False),
    ("B15", "Acute hepatitis A", "Infections", False),
    ("B16", "Acute hepatitis B", "Infections", False),
    ("B18.1", "Chronic viral hepatitis B", "Infections", True),
    ("B19", "Unspecified viral hepatitis", "Infections", False),
    ("B20", "HIV disease", "Infections", True),
    ("B35", "Dermatophytosis (ringworm)", "Infections", False),
    ("B37", "Candidiasis", "Infections", False),
    ("B50", "Plasmodium falciparum malaria", "Infections", False),
    ("B51", "Plasmodium vivax malaria", "Infections", False),
    ("B54", "Unspecified malaria", "Infections", False),
    ("B65", "Schistosomiasis", "Infections", False),
    ("B76", "Hookworm disease", "Infections", False),
    ("B77", "Ascariasis", "Infections", False),
    # ------------------------------------------------------------- neoplasms
    ("C18", "Malignant neoplasm of colon", "Cancers", True),
    ("C22", "Malignant neoplasm of liver", "Cancers", True),
    ("C50", "Malignant neoplasm of breast", "Cancers", True),
    ("C53", "Malignant neoplasm of cervix uteri", "Cancers", True),
    ("C61", "Malignant neoplasm of prostate", "Cancers", True),
    # --------------------------------------------------------- blood, immune
    ("D50", "Iron deficiency anaemia", "Blood", False),
    ("D57", "Sickle-cell disorders", "Blood", True),
    ("D57.0", "Sickle-cell crisis", "Blood", False),
    ("D64", "Anaemia, other and unspecified", "Blood", False),
    # ------------------------------------------------- endocrine, metabolic
    ("E03", "Hypothyroidism", "Endocrine", True),
    ("E05", "Thyrotoxicosis", "Endocrine", True),
    ("E10", "Type 1 diabetes mellitus", "Endocrine", True),
    ("E11", "Type 2 diabetes mellitus", "Endocrine", True),
    ("E43", "Severe protein-energy malnutrition", "Endocrine", False),
    ("E44", "Moderate protein-energy malnutrition", "Endocrine", False),
    ("E66", "Obesity", "Endocrine", True),
    ("E78", "Disorders of lipoprotein metabolism", "Endocrine", True),
    ("E86", "Volume depletion (dehydration)", "Endocrine", False),
    ("E87", "Other disorders of fluid and electrolyte balance", "Endocrine", False),
    # ------------------------------------------------------- mental health
    ("F10", "Mental and behavioural disorders due to alcohol", "Mental health", True),
    ("F20", "Schizophrenia", "Mental health", True),
    ("F31", "Bipolar affective disorder", "Mental health", True),
    ("F32", "Depressive episode", "Mental health", False),
    ("F41", "Other anxiety disorders", "Mental health", False),
    # ------------------------------------------------------- nervous system
    ("G40", "Epilepsy", "Nervous system", True),
    ("G43", "Migraine", "Nervous system", True),
    ("G44.2", "Tension-type headache", "Nervous system", False),
    ("G62", "Polyneuropathy", "Nervous system", True),
    # ------------------------------------------------------------ eye, ear
    ("H10", "Conjunctivitis", "Eye and ear", False),
    ("H25", "Age-related cataract", "Eye and ear", True),
    ("H40", "Glaucoma", "Eye and ear", True),
    ("H52", "Disorders of refraction and accommodation", "Eye and ear", True),
    ("H60", "Otitis externa", "Eye and ear", False),
    ("H66", "Suppurative and unspecified otitis media", "Eye and ear", False),
    ("H81", "Disorders of vestibular function", "Eye and ear", False),
    # ----------------------------------------------------------- circulation
    ("I10", "Essential (primary) hypertension", "Heart and circulation", True),
    ("I11", "Hypertensive heart disease", "Heart and circulation", True),
    ("I20", "Angina pectoris", "Heart and circulation", True),
    ("I21", "Acute myocardial infarction", "Heart and circulation", False),
    ("I25", "Chronic ischaemic heart disease", "Heart and circulation", True),
    ("I48", "Atrial fibrillation and flutter", "Heart and circulation", True),
    ("I50", "Heart failure", "Heart and circulation", True),
    ("I63", "Cerebral infarction", "Heart and circulation", False),
    ("I64", "Stroke, not specified as haemorrhage or infarction", "Heart and circulation", False),
    ("I83", "Varicose veins of lower extremities", "Heart and circulation", True),
    # ---------------------------------------------------------- respiratory
    ("J00", "Acute nasopharyngitis (common cold)", "Chest and breathing", False),
    ("J02", "Acute pharyngitis", "Chest and breathing", False),
    ("J03", "Acute tonsillitis", "Chest and breathing", False),
    ("J06", "Acute upper respiratory infection", "Chest and breathing", False),
    ("J18", "Pneumonia, organism unspecified", "Chest and breathing", False),
    ("J20", "Acute bronchitis", "Chest and breathing", False),
    ("J44", "Chronic obstructive pulmonary disease", "Chest and breathing", True),
    ("J45", "Asthma", "Chest and breathing", True),
    # ------------------------------------------------------------ digestive
    ("K02", "Dental caries", "Digestive and dental", False),
    ("K05", "Gingivitis and periodontal diseases", "Digestive and dental", False),
    ("K08", "Other disorders of teeth and supporting structures", "Digestive and dental", False),
    ("K21", "Gastro-oesophageal reflux disease", "Digestive and dental", True),
    ("K25", "Gastric ulcer", "Digestive and dental", False),
    ("K29", "Gastritis and duodenitis", "Digestive and dental", False),
    ("K30", "Functional dyspepsia", "Digestive and dental", False),
    ("K35", "Acute appendicitis", "Digestive and dental", False),
    ("K40", "Inguinal hernia", "Digestive and dental", False),
    ("K59.0", "Constipation", "Digestive and dental", False),
    ("K64", "Haemorrhoids", "Digestive and dental", False),
    ("K80", "Cholelithiasis (gallstones)", "Digestive and dental", False),
    # ----------------------------------------------------------------- skin
    ("L01", "Impetigo", "Skin", False),
    ("L02", "Cutaneous abscess, furuncle and carbuncle", "Skin", False),
    ("L03", "Cellulitis", "Skin", False),
    ("L20", "Atopic dermatitis", "Skin", True),
    ("L23", "Allergic contact dermatitis", "Skin", False),
    ("L30", "Other dermatitis", "Skin", False),
    ("L50", "Urticaria", "Skin", False),
    # ------------------------------------------------------ musculoskeletal
    ("M06", "Rheumatoid arthritis, other", "Bones and joints", True),
    ("M10", "Gout", "Bones and joints", True),
    ("M16", "Osteoarthritis of hip", "Bones and joints", True),
    ("M17", "Osteoarthritis of knee", "Bones and joints", True),
    ("M25.5", "Pain in joint", "Bones and joints", False),
    ("M54", "Dorsalgia (back pain)", "Bones and joints", False),
    ("M54.5", "Low back pain", "Bones and joints", False),
    ("M79.1", "Myalgia", "Bones and joints", False),
    # ------------------------------------------------------- genitourinary
    ("N18", "Chronic kidney disease", "Kidneys and urinary", True),
    ("N20", "Calculus of kidney and ureter", "Kidneys and urinary", False),
    ("N30", "Cystitis", "Kidneys and urinary", False),
    ("N39.0", "Urinary tract infection, site not specified", "Kidneys and urinary", False),
    ("N40", "Benign prostatic hyperplasia", "Kidneys and urinary", True),
    ("N70", "Salpingitis and oophoritis", "Women's health", False),
    ("N76", "Other inflammation of vagina and vulva", "Women's health", False),
    ("N92", "Excessive, frequent and irregular menstruation", "Women's health", False),
    ("N95", "Menopausal and other perimenopausal disorders", "Women's health", False),
    # ---------------------------------------------------- pregnancy, newborn
    ("O00", "Ectopic pregnancy", "Pregnancy and newborn", False),
    ("O03", "Spontaneous abortion", "Pregnancy and newborn", False),
    ("O14", "Pre-eclampsia", "Pregnancy and newborn", False),
    ("O15", "Eclampsia", "Pregnancy and newborn", False),
    ("O24.4", "Diabetes mellitus arising in pregnancy", "Pregnancy and newborn", False),
    ("O80", "Single spontaneous delivery", "Pregnancy and newborn", False),
    ("O82", "Single delivery by caesarean section", "Pregnancy and newborn", False),
    ("P07", "Preterm birth and low birth weight", "Pregnancy and newborn", False),
    ("P36", "Bacterial sepsis of newborn", "Pregnancy and newborn", False),
    ("P59", "Neonatal jaundice", "Pregnancy and newborn", False),
    # -------------------------------------------------- symptoms and signs
    ("R05", "Cough", "Symptoms", False),
    ("R06.0", "Dyspnoea", "Symptoms", False),
    ("R07.4", "Chest pain, unspecified", "Symptoms", False),
    ("R10", "Abdominal and pelvic pain", "Symptoms", False),
    ("R11", "Nausea and vomiting", "Symptoms", False),
    ("R42", "Dizziness and giddiness", "Symptoms", False),
    ("R50", "Fever of unknown origin", "Symptoms", False),
    ("R51", "Headache", "Symptoms", False),
    ("R53", "Malaise and fatigue", "Symptoms", False),
    ("R63.0", "Anorexia (loss of appetite)", "Symptoms", False),
    # ------------------------------------------------------------- injuries
    ("S01", "Open wound of head", "Injuries", False),
    ("S42", "Fracture of shoulder and upper arm", "Injuries", False),
    ("S52", "Fracture of forearm", "Injuries", False),
    ("S72", "Fracture of femur", "Injuries", False),
    ("T14.1", "Open wound of unspecified body region", "Injuries", False),
    ("T30", "Burn, body region unspecified", "Injuries", False),
    ("T63.0", "Toxic effect of snake venom", "Injuries", False),
    ("T78.4", "Allergy, unspecified", "Injuries", False),
    # ------------------------------------------- contact with health services
    ("Z00", "General examination without complaint or diagnosis", "Health services", False),
    ("Z11", "Special screening examination for infectious disease", "Health services", False),
    ("Z23", "Immunisation", "Health services", False),
    ("Z30", "Contraceptive management", "Health services", False),
    ("Z34", "Supervision of normal pregnancy", "Health services", False),
    ("Z76.0", "Issue of repeat prescription", "Health services", False),
)


@dataclass(frozen=True)
class Code:
    code: str
    title: str
    chapter: str
    #: A condition the patient *has*, rather than one they are getting over.
    #: Confirming one opens an entry on their problem list.
    chronic: bool

    @property
    def label(self) -> str:
        return f"{self.code} · {self.title}"


CODES: tuple[Code, ...] = tuple(
    Code(code=code, title=title, chapter=chapter, chronic=chronic)
    for code, title, chapter, chronic in _RAW
)

_BY_CODE = {item.code: item for item in CODES}

#: Words a doctor types that are not the ICD wording. Searching for "malaria"
#: already works; searching for "BP" or "sugar" does not, and the gap between
#: what a doctor says and what the WHO wrote is where coding quietly fails.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "bp": ("I10",),
    "high blood pressure": ("I10", "I11"),
    "hypertensive": ("I10", "I11"),
    "sugar": ("E11", "E10"),
    "diabetic": ("E11", "E10"),
    "dm": ("E11", "E10"),
    "uti": ("N39.0", "N30"),
    "urti": ("J06",),
    "catarrh": ("J00", "J06"),
    "runny nose": ("J00",),
    "typhoid": ("A01.0",),
    "enteric fever": ("A01.0",),
    "malaria": ("B54", "B50"),
    "ssd": ("D57",),
    "sickler": ("D57", "D57.0"),
    "crisis": ("D57.0",),
    "retroviral": ("B20",),
    "rvd": ("B20",),
    "pud": ("K25", "K29"),
    "ulcer": ("K25",),
    "gastritis": ("K29",),
    "asthmatic": ("J45",),
    "stroke": ("I64", "I63"),
    "cva": ("I64", "I63"),
    "heart failure": ("I50",),
    "ccf": ("I50",),
    "ante natal": ("Z34",),
    "booking": ("Z34",),
    "waist pain": ("M54.5", "M54"),
    "body pain": ("M79.1",),
    "runs": ("A09",),
    "loose stool": ("A09",),
    "toothache": ("K02", "K08"),
    "snake bite": ("T63.0",),
}


def get(code: str) -> Code | None:
    return _BY_CODE.get(code.strip().upper())


def search(query: str, *, limit: int = 20) -> list[Code]:
    """
    Find codes for what the doctor typed.

    Ordered by how good the match is, not alphabetically: an exact code first,
    then a title that starts with the words, then one that contains them.
    A list where "A09 Diarrhoea" outranks "B54 Malaria" for the query
    "malaria" is a list that gets ignored, and an ignored code list means
    every patient is coded R50.
    """
    text = " ".join(query.lower().split())
    if not text:
        return []

    # (score, tie-break, code). The tie-break keeps a synonym's own order:
    # "malaria" means B54 before B50 to a Nigerian clinician, and sorting
    # those two by their code alone would put the speciated one first.
    scored: dict[str, tuple[int, int, Code]] = {}

    def offer(item: Code, score: int, rank: int = 0) -> None:
        held = scored.get(item.code)
        if held is None or (score, rank) < held[:2]:
            scored[item.code] = (score, rank, item)

    for item in CODES:
        code = item.code.lower()
        title = item.title.lower()
        if code == text:
            offer(item, 0)
        elif code.startswith(text):
            offer(item, 1)
        elif title.startswith(text):
            offer(item, 2)
        elif text in title:
            offer(item, 3)
        elif item.chapter.lower().startswith(text):
            offer(item, 5)

    for word, codes in SYNONYMS.items():
        if word == text:
            # What the doctor typed IS the everyday name for these. That
            # beats a code whose official title merely contains the word:
            # typing "malaria" must offer unspecified malaria first, not
            # whichever species sorts earliest.
            score = 2
        elif word.startswith(text) or text in word:
            score = 4
        else:
            continue
        for rank, code in enumerate(codes):
            found = _BY_CODE.get(code)
            if found is not None:
                offer(found, score, rank)

    ranked = sorted(scored.values(), key=lambda row: (row[0], row[1], row[2].code))
    return [item for _score, _rank, item in ranked[:limit]]


def chapters() -> list[str]:
    seen: list[str] = []
    for item in CODES:
        if item.chapter not in seen:
            seen.append(item.chapter)
    return seen


__all__ = ["CODES", "SYNONYMS", "Code", "chapters", "get", "search"]
