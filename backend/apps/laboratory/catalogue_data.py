"""
The starting catalogue: what Curavia knows about the tests it already seeds.

``apps.onboarding.templates_data`` creates thirteen laboratory services for a
new hospital. This file says what each of them is made of — the specimen, the
analytes, the units and the reference ranges — so a hospital that finishes the
setup wizard has a laboratory that can issue a flagged result on day one,
instead of a supervisor typing in two hundred ranges before the first patient.

**These ranges are a starting point, not an authority.** Reference intervals
depend on the method and the analyser a laboratory actually uses, and every
row here is editable by the hospital's own laboratory supervisor, who is the
person qualified to decide. ``from_template`` marks what came from us, so the
catalogue screen can show a supervisor what they have never reviewed. The
values below are ordinary adult and paediatric intervals in SI units as used
in Nigerian hospital practice.

Two decisions in the data worth reading before changing it:

* **A reactive HIV screen is marked abnormal, not critical.** "Critical" in
  this system means somebody telephones the ordering doctor immediately
  (7c). A reactive screening result is confidential, is confirmed before it
  means anything, and is given to the patient in person with counselling —
  never announced down a telephone line to a ward. The flag reflects that.

* **A positive pregnancy test is marked normal.** It is not a disease. Both
  answers are normal results of a test that was asked a question, and
  printing "ABNORMAL" next to a wanted pregnancy would be both wrong and
  unkind.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.laboratory.models import OptionFlag, RangeSex, Specimen, ValueType

# Ages in days, written out so the bands below read as what they are.
MONTH = 30
YEAR = 365
NEWBORN_END = 28
INFANT_END = YEAR - 1
CHILD_5_END = 5 * YEAR
CHILD_12_END = 12 * YEAR
ADULT_START = 12 * YEAR + 1


@dataclass(frozen=True, slots=True)
class RangeTemplate:
    """One band. Decimals are strings so no float ever reaches the database."""

    low: str | None = None
    high: str | None = None
    critical_low: str | None = None
    critical_high: str | None = None
    sex: str = RangeSex.ANY
    age_min_days: int = 0
    age_max_days: int | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class AnalyteTemplate:
    code: str
    name: str
    unit: str = ""
    decimal_places: int = 1
    value_type: str = ValueType.NUMERIC
    options: tuple[dict[str, str], ...] = ()
    ranges: tuple[RangeTemplate, ...] = ()


@dataclass(frozen=True, slots=True)
class PanelTemplate:
    #: The ``catalogue.Service`` code this is the laboratory's side of.
    service_code: str
    specimen: str
    container: str = ""
    volume_ml: str | None = None
    turnaround_minutes: int = 240
    fasting_required: bool = False
    preparation: str = ""
    handling: str = ""
    analytes: tuple[AnalyteTemplate, ...] = field(default_factory=tuple)


def _coded(*pairs: tuple[str, str]) -> tuple[dict[str, str], ...]:
    """``_coded(("Negative", OptionFlag.NORMAL), ...)`` — shorter to read."""
    return tuple({"label": label, "flag": str(flag)} for label, flag in pairs)


NEGATIVE_POSITIVE = _coded(
    ("Negative", OptionFlag.NORMAL),
    ("Positive", OptionFlag.ABNORMAL),
)

DIPSTICK = _coded(
    ("Nil", OptionFlag.NORMAL),
    ("Trace", OptionFlag.ABNORMAL),
    ("+", OptionFlag.ABNORMAL),
    ("++", OptionFlag.ABNORMAL),
    ("+++", OptionFlag.ABNORMAL),
)

WIDAL_TITRES = _coded(
    ("Nil", OptionFlag.NORMAL),
    ("1:20", OptionFlag.NORMAL),
    ("1:40", OptionFlag.NORMAL),
    ("1:80", OptionFlag.ABNORMAL),
    ("1:160", OptionFlag.ABNORMAL),
    ("1:320", OptionFlag.ABNORMAL),
)


PANELS: tuple[PanelTemplate, ...] = (
    # ---------------------------------------------------------------- FBC
    PanelTemplate(
        service_code="FBC",
        specimen=Specimen.WHOLE_BLOOD_EDTA,
        container="Purple top (EDTA), 4 mL",
        volume_ml="4.0",
        turnaround_minutes=120,
        handling="Mix gently, do not shake. To the laboratory within 4 hours.",
        analytes=(
            AnalyteTemplate(
                code="HB",
                name="Haemoglobin",
                unit="g/dL",
                decimal_places=1,
                ranges=(
                    RangeTemplate("14.0", "22.0", critical_low="10.0", age_max_days=NEWBORN_END),
                    RangeTemplate(
                        "9.5",
                        "13.5",
                        critical_low="7.0",
                        age_min_days=NEWBORN_END + 1,
                        age_max_days=INFANT_END,
                    ),
                    RangeTemplate(
                        "11.0",
                        "14.0",
                        critical_low="7.0",
                        age_min_days=YEAR,
                        age_max_days=CHILD_5_END,
                    ),
                    RangeTemplate(
                        "11.5",
                        "15.5",
                        critical_low="7.0",
                        age_min_days=CHILD_5_END + 1,
                        age_max_days=CHILD_12_END,
                    ),
                    # A general adult band, then the two that override it.
                    # A patient whose sex has not been recorded matches this
                    # one — see RangeSex in models.py.
                    RangeTemplate(
                        "11.5",
                        "18.0",
                        critical_low="7.0",
                        critical_high="20.0",
                        age_min_days=ADULT_START,
                    ),
                    RangeTemplate(
                        "11.5",
                        "16.5",
                        critical_low="7.0",
                        critical_high="20.0",
                        sex=RangeSex.FEMALE,
                        age_min_days=ADULT_START,
                    ),
                    RangeTemplate(
                        "13.0",
                        "18.0",
                        critical_low="7.0",
                        critical_high="20.0",
                        sex=RangeSex.MALE,
                        age_min_days=ADULT_START,
                    ),
                ),
            ),
            AnalyteTemplate(
                code="PCV",
                name="Packed cell volume",
                unit="%",
                decimal_places=0,
                ranges=(
                    RangeTemplate("45", "65", critical_low="30", age_max_days=NEWBORN_END),
                    RangeTemplate(
                        "29",
                        "41",
                        critical_low="20",
                        age_min_days=NEWBORN_END + 1,
                        age_max_days=INFANT_END,
                    ),
                    RangeTemplate(
                        "33",
                        "45",
                        critical_low="20",
                        age_min_days=YEAR,
                        age_max_days=CHILD_12_END,
                    ),
                    RangeTemplate(
                        "36",
                        "54",
                        critical_low="20",
                        critical_high="60",
                        age_min_days=ADULT_START,
                    ),
                    RangeTemplate(
                        "36",
                        "46",
                        critical_low="20",
                        critical_high="60",
                        sex=RangeSex.FEMALE,
                        age_min_days=ADULT_START,
                    ),
                    RangeTemplate(
                        "40",
                        "54",
                        critical_low="20",
                        critical_high="60",
                        sex=RangeSex.MALE,
                        age_min_days=ADULT_START,
                    ),
                ),
            ),
            AnalyteTemplate(
                code="WBC",
                name="White cell count",
                unit="10⁹/L",
                decimal_places=1,
                ranges=(
                    RangeTemplate("9.0", "30.0", age_max_days=NEWBORN_END),
                    RangeTemplate(
                        "6.0", "17.5", age_min_days=NEWBORN_END + 1, age_max_days=INFANT_END
                    ),
                    RangeTemplate("5.0", "15.0", age_min_days=YEAR, age_max_days=CHILD_5_END),
                    RangeTemplate(
                        "4.5",
                        "13.5",
                        age_min_days=CHILD_5_END + 1,
                        age_max_days=CHILD_12_END,
                    ),
                    RangeTemplate(
                        "4.0",
                        "11.0",
                        critical_low="1.0",
                        critical_high="30.0",
                        age_min_days=ADULT_START,
                    ),
                ),
            ),
            AnalyteTemplate(
                code="PLT",
                name="Platelet count",
                unit="10⁹/L",
                decimal_places=0,
                ranges=(RangeTemplate("150", "450", critical_low="50", critical_high="1000"),),
            ),
            AnalyteTemplate(
                code="MCV",
                name="Mean cell volume",
                unit="fL",
                decimal_places=1,
                ranges=(RangeTemplate("80.0", "100.0", age_min_days=ADULT_START),),
            ),
            AnalyteTemplate(
                code="NEUT",
                name="Neutrophils",
                unit="%",
                decimal_places=0,
                ranges=(RangeTemplate("40", "75", age_min_days=ADULT_START),),
            ),
            AnalyteTemplate(
                code="LYMPH",
                name="Lymphocytes",
                unit="%",
                decimal_places=0,
                ranges=(RangeTemplate("20", "45", age_min_days=ADULT_START),),
            ),
            AnalyteTemplate(
                code="EOS",
                name="Eosinophils",
                unit="%",
                decimal_places=0,
                ranges=(RangeTemplate("1", "6", age_min_days=ADULT_START),),
            ),
        ),
    ),
    # ------------------------------------------------------------ PCV only
    PanelTemplate(
        service_code="PCV",
        specimen=Specimen.WHOLE_BLOOD_EDTA,
        container="Purple top (EDTA), 2 mL",
        volume_ml="2.0",
        turnaround_minutes=30,
        analytes=(
            AnalyteTemplate(
                code="PCV",
                name="Packed cell volume",
                unit="%",
                decimal_places=0,
                ranges=(
                    RangeTemplate("45", "65", critical_low="30", age_max_days=NEWBORN_END),
                    RangeTemplate(
                        "29",
                        "41",
                        critical_low="20",
                        age_min_days=NEWBORN_END + 1,
                        age_max_days=INFANT_END,
                    ),
                    RangeTemplate(
                        "33",
                        "45",
                        critical_low="20",
                        age_min_days=YEAR,
                        age_max_days=CHILD_12_END,
                    ),
                    RangeTemplate(
                        "36",
                        "54",
                        critical_low="20",
                        critical_high="60",
                        age_min_days=ADULT_START,
                    ),
                    RangeTemplate(
                        "36",
                        "46",
                        critical_low="20",
                        critical_high="60",
                        sex=RangeSex.FEMALE,
                        age_min_days=ADULT_START,
                    ),
                    RangeTemplate(
                        "40",
                        "54",
                        critical_low="20",
                        critical_high="60",
                        sex=RangeSex.MALE,
                        age_min_days=ADULT_START,
                    ),
                ),
            ),
        ),
    ),
    # ------------------------------------------------------------- malaria
    PanelTemplate(
        service_code="MP-RDT",
        specimen=Specimen.WHOLE_BLOOD_EDTA,
        container="Purple top (EDTA), 2 mL, or a finger prick",
        turnaround_minutes=30,
        analytes=(
            AnalyteTemplate(
                code="MP",
                name="Malaria parasite (rapid test)",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=NEGATIVE_POSITIVE,
            ),
        ),
    ),
    PanelTemplate(
        service_code="MP-MIC",
        specimen=Specimen.WHOLE_BLOOD_EDTA,
        container="Purple top (EDTA), 2 mL",
        volume_ml="2.0",
        turnaround_minutes=120,
        handling="Make the thick and thin films before the sample is 1 hour old.",
        analytes=(
            AnalyteTemplate(
                code="MPS",
                name="Malaria parasites seen",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=NEGATIVE_POSITIVE,
            ),
            AnalyteTemplate(
                code="SPECIES",
                name="Species and stage",
                value_type=ValueType.TEXT,
                decimal_places=0,
            ),
            AnalyteTemplate(
                code="DENSITY",
                name="Parasite density",
                unit="/µL",
                decimal_places=0,
                # No reference range: there is no normal number of malaria
                # parasites. The count guides treatment, and a density above
                # 250,000/µL is severe malaria, which is why that one
                # threshold is set and nothing else is.
                ranges=(RangeTemplate(high="0", critical_high="250000"),),
            ),
        ),
    ),
    # ------------------------------------------------------------ urinalysis
    PanelTemplate(
        service_code="URIN",
        specimen=Specimen.URINE,
        container="Universal bottle, 10 mL",
        volume_ml="10.0",
        turnaround_minutes=60,
        preparation="Midstream sample. Clean first.",
        handling="To the laboratory within 2 hours, or refrigerate.",
        analytes=(
            AnalyteTemplate(
                code="APPEAR",
                name="Appearance",
                value_type=ValueType.TEXT,
                decimal_places=0,
            ),
            AnalyteTemplate(
                code="PROT",
                name="Protein",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=DIPSTICK,
            ),
            AnalyteTemplate(
                code="GLUC",
                name="Glucose",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=DIPSTICK,
            ),
            AnalyteTemplate(
                code="NIT",
                name="Nitrites",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=NEGATIVE_POSITIVE,
            ),
            AnalyteTemplate(
                code="PH",
                name="pH",
                decimal_places=1,
                ranges=(RangeTemplate("4.5", "8.0"),),
            ),
            AnalyteTemplate(
                code="SG",
                name="Specific gravity",
                decimal_places=3,
                ranges=(RangeTemplate("1.003", "1.030"),),
            ),
            AnalyteTemplate(
                code="PUS",
                name="Pus cells",
                unit="/hpf",
                decimal_places=0,
                ranges=(RangeTemplate(high="5"),),
            ),
            AnalyteTemplate(
                code="MICRO",
                name="Microscopy",
                value_type=ValueType.TEXT,
                decimal_places=0,
            ),
        ),
    ),
    # ----------------------------------------------------------------- sugar
    PanelTemplate(
        service_code="FBS",
        specimen=Specimen.WHOLE_BLOOD_FLUORIDE,
        container="Grey top (fluoride oxalate), 2 mL",
        volume_ml="2.0",
        turnaround_minutes=60,
        fasting_required=True,
        preparation="Nothing to eat or drink except water for 8 hours beforehand.",
        analytes=(
            AnalyteTemplate(
                code="GLU",
                name="Glucose (fasting)",
                unit="mmol/L",
                decimal_places=1,
                ranges=(
                    RangeTemplate(
                        "3.9",
                        "5.5",
                        critical_low="2.5",
                        critical_high="25.0",
                        note="fasting",
                    ),
                ),
            ),
        ),
    ),
    # ------------------------------------------------------------ chemistry
    PanelTemplate(
        service_code="EUCR",
        specimen=Specimen.SERUM,
        container="Plain red top, 5 mL",
        volume_ml="5.0",
        turnaround_minutes=180,
        handling="Separate the serum within 2 hours; a haemolysed sample raises potassium.",
        analytes=(
            AnalyteTemplate(
                code="NA",
                name="Sodium",
                unit="mmol/L",
                decimal_places=0,
                ranges=(RangeTemplate("135", "145", critical_low="120", critical_high="160"),),
            ),
            AnalyteTemplate(
                code="K",
                name="Potassium",
                unit="mmol/L",
                decimal_places=1,
                ranges=(RangeTemplate("3.5", "5.1", critical_low="2.5", critical_high="6.5"),),
            ),
            AnalyteTemplate(
                code="CL",
                name="Chloride",
                unit="mmol/L",
                decimal_places=0,
                ranges=(RangeTemplate("98", "107"),),
            ),
            AnalyteTemplate(
                code="HCO3",
                name="Bicarbonate",
                unit="mmol/L",
                decimal_places=0,
                ranges=(RangeTemplate("22", "29", critical_low="10"),),
            ),
            AnalyteTemplate(
                code="UREA",
                name="Urea",
                unit="mmol/L",
                decimal_places=1,
                ranges=(RangeTemplate("2.5", "7.1", critical_high="30.0"),),
            ),
            AnalyteTemplate(
                code="CREAT",
                name="Creatinine",
                unit="µmol/L",
                decimal_places=0,
                ranges=(
                    RangeTemplate("45", "104", critical_high="500", age_min_days=ADULT_START),
                    RangeTemplate(
                        "45",
                        "84",
                        critical_high="500",
                        sex=RangeSex.FEMALE,
                        age_min_days=ADULT_START,
                    ),
                    RangeTemplate(
                        "59",
                        "104",
                        critical_high="500",
                        sex=RangeSex.MALE,
                        age_min_days=ADULT_START,
                    ),
                ),
            ),
        ),
    ),
    PanelTemplate(
        service_code="LFT",
        specimen=Specimen.SERUM,
        container="Plain red top, 5 mL",
        volume_ml="5.0",
        turnaround_minutes=240,
        analytes=(
            AnalyteTemplate(
                code="TBIL",
                name="Total bilirubin",
                unit="µmol/L",
                decimal_places=1,
                ranges=(
                    # A newborn's jaundice is measured against a different
                    # question entirely: not "is this normal" but "is this
                    # near the level for an exchange transfusion".
                    RangeTemplate(high="85.0", critical_high="340.0", age_max_days=NEWBORN_END),
                    RangeTemplate("3.4", "20.5", age_min_days=NEWBORN_END + 1),
                ),
            ),
            AnalyteTemplate(
                code="DBIL",
                name="Conjugated bilirubin",
                unit="µmol/L",
                decimal_places=1,
                ranges=(RangeTemplate(high="8.6"),),
            ),
            AnalyteTemplate(
                code="ALT",
                name="Alanine aminotransferase",
                unit="U/L",
                decimal_places=0,
                ranges=(
                    RangeTemplate(high="41", age_min_days=ADULT_START),
                    RangeTemplate(high="33", sex=RangeSex.FEMALE, age_min_days=ADULT_START),
                    RangeTemplate(high="41", sex=RangeSex.MALE, age_min_days=ADULT_START),
                ),
            ),
            AnalyteTemplate(
                code="AST",
                name="Aspartate aminotransferase",
                unit="U/L",
                decimal_places=0,
                ranges=(RangeTemplate(high="40"),),
            ),
            AnalyteTemplate(
                code="ALP",
                name="Alkaline phosphatase",
                unit="U/L",
                decimal_places=0,
                ranges=(RangeTemplate("30", "120", age_min_days=ADULT_START),),
            ),
            AnalyteTemplate(
                code="TP",
                name="Total protein",
                unit="g/L",
                decimal_places=0,
                ranges=(RangeTemplate("64", "83"),),
            ),
            AnalyteTemplate(
                code="ALB",
                name="Albumin",
                unit="g/L",
                decimal_places=0,
                ranges=(RangeTemplate("35", "52", critical_low="15"),),
            ),
        ),
    ),
    # ------------------------------------------------------------ screening
    PanelTemplate(
        service_code="HIV-SCR",
        specimen=Specimen.SERUM,
        container="Plain red top, 3 mL",
        volume_ml="3.0",
        turnaround_minutes=60,
        preparation="Counselling before and after, as the national guideline requires.",
        analytes=(
            AnalyteTemplate(
                code="HIV",
                name="HIV screening",
                value_type=ValueType.CODED,
                decimal_places=0,
                # Abnormal, deliberately not critical. See the module
                # docstring: critical means a telephone call to a ward.
                options=_coded(
                    ("Non-reactive", OptionFlag.NORMAL),
                    ("Reactive", OptionFlag.ABNORMAL),
                    ("Indeterminate", OptionFlag.ABNORMAL),
                ),
            ),
        ),
    ),
    PanelTemplate(
        service_code="HEP-BC",
        specimen=Specimen.SERUM,
        container="Plain red top, 3 mL",
        volume_ml="3.0",
        turnaround_minutes=60,
        analytes=(
            AnalyteTemplate(
                code="HBSAG",
                name="Hepatitis B surface antigen",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=_coded(
                    ("Non-reactive", OptionFlag.NORMAL),
                    ("Reactive", OptionFlag.ABNORMAL),
                ),
            ),
            AnalyteTemplate(
                code="HCV",
                name="Hepatitis C antibody",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=_coded(
                    ("Non-reactive", OptionFlag.NORMAL),
                    ("Reactive", OptionFlag.ABNORMAL),
                ),
            ),
        ),
    ),
    PanelTemplate(
        service_code="PREG",
        specimen=Specimen.URINE,
        container="Universal bottle, 10 mL",
        turnaround_minutes=20,
        preparation="An early morning sample is the most reliable.",
        analytes=(
            AnalyteTemplate(
                code="HCG",
                name="Pregnancy test",
                value_type=ValueType.CODED,
                decimal_places=0,
                # Both answers are normal. A pregnancy is not a disease.
                options=_coded(
                    ("Negative", OptionFlag.NORMAL),
                    ("Positive", OptionFlag.NORMAL),
                ),
            ),
        ),
    ),
    PanelTemplate(
        service_code="WIDAL",
        specimen=Specimen.SERUM,
        container="Plain red top, 3 mL",
        volume_ml="3.0",
        turnaround_minutes=120,
        analytes=(
            AnalyteTemplate(
                code="TO",
                name="S. typhi O",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=WIDAL_TITRES,
            ),
            AnalyteTemplate(
                code="TH",
                name="S. typhi H",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=WIDAL_TITRES,
            ),
            AnalyteTemplate(
                code="AO",
                name="S. paratyphi A O",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=WIDAL_TITRES,
            ),
            AnalyteTemplate(
                code="AH",
                name="S. paratyphi A H",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=WIDAL_TITRES,
            ),
        ),
    ),
    # ----------------------------------------------------------- blood bank
    PanelTemplate(
        service_code="GXM",
        specimen=Specimen.WHOLE_BLOOD_EDTA,
        container="Purple top (EDTA), 6 mL",
        volume_ml="6.0",
        turnaround_minutes=120,
        handling=(
            "Label at the bedside, in the patient's presence, before leaving it. "
            "A mislabelled cross-match sample is how a patient receives the wrong blood."
        ),
        analytes=(
            AnalyteTemplate(
                code="ABO",
                name="ABO group",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=_coded(
                    ("A", OptionFlag.NORMAL),
                    ("B", OptionFlag.NORMAL),
                    ("AB", OptionFlag.NORMAL),
                    ("O", OptionFlag.NORMAL),
                ),
            ),
            AnalyteTemplate(
                code="RH",
                name="Rhesus D",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=_coded(
                    ("Positive", OptionFlag.NORMAL),
                    ("Negative", OptionFlag.NORMAL),
                ),
            ),
            AnalyteTemplate(
                code="XM",
                name="Cross-match",
                value_type=ValueType.CODED,
                decimal_places=0,
                options=_coded(
                    ("Compatible", OptionFlag.NORMAL),
                    ("Incompatible", OptionFlag.CRITICAL),
                ),
            ),
        ),
    ),
)


#: Looked up by service code when the wizard creates services.
PANELS_BY_SERVICE_CODE: dict[str, PanelTemplate] = {p.service_code: p for p in PANELS}
