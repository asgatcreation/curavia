/**
 * A synthetic hospital, for the milestone 3 screens.
 *
 * EVERY NAME, NUMBER AND RECORD IN THIS FILE IS INVENTED. No real patient,
 * no real member of staff, no real hospital. The names are Nigerian because
 * the screens have to look right to a Nigerian matron; the people are not.
 *
 * Why fixtures and not the API: the clinical models — Patient, Encounter,
 * Prescription, LabOrder — arrive at milestones 4 to 7. Milestone 3 is the
 * visual sign-off, and it has to happen before forty screens are built, not
 * after. So these screens are built as real components with real layout,
 * real states and real edge cases, reading from this module. Wiring them to
 * the API later replaces the import and nothing else: the shapes below are
 * the shapes the serialisers will return.
 *
 * The edge cases are deliberate and should survive the swap:
 *
 *   - a patient whose allergy history has never been taken
 *   - a patient with no known allergies, stated rather than left blank
 *   - a prescription that collides with a recorded allergy
 *   - an item that is out of stock at this branch but held at another
 *   - a waiting time past the hour
 *   - a name long enough to break a narrow column
 *   - a self-paying patient beside an HMO one beside an NHIA one
 */

import type { BannerPatient } from "../ui/PatientBanner";
import type { StatusTone, TriageCode } from "../ui/status";

/** Minutes ago, as an ISO string, so "waiting 42m" is true whenever you look. */
function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function yearsAgo(years: number, month = 5, day = 14): string {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear() - years, month, day)).toISOString().slice(0, 10);
}

// --------------------------------------------------------------------------
// The hospital itself
// --------------------------------------------------------------------------

export const DEMO_ORGANISATION = {
  name: "Oakfield Medical Centre",
  group: "Oakfield Health Group",
  branches: [
    { id: "br-ikeja", name: "Ikeja", code: "IKJ", is_main: true },
    { id: "br-lekki", name: "Lekki", code: "LEK", is_main: false },
    { id: "br-abuja", name: "Abuja (Wuse II)", code: "ABJ", is_main: false },
  ],
} as const;

export const DEMO_USER = {
  full_name: "Dr Folake Ilesanmi",
  job_title: "Consultant, General Outpatient",
  initials: "FI",
} as const;

// --------------------------------------------------------------------------
// Front desk
// --------------------------------------------------------------------------

export type PayerKind = "Self-pay" | "HMO" | "NHIA" | "Corporate";

export interface WaitingPatient {
  id: string;
  token: string;
  full_name: string;
  mrn: string;
  date_of_birth: string;
  sex: "F" | "M";
  payer: string;
  payer_kind: PayerKind;
  reason: string;
  arrived_at: string;
  triage: TriageCode | null;
  /** Where the patient is right now, in the words the front desk uses. */
  stage: "Waiting for triage" | "Waiting for doctor" | "With doctor" | "Waiting for bill";
  balance_kobo: number;
}

export const DEMO_WAITING: WaitingPatient[] = [
  {
    id: "p-001",
    token: "A-014",
    full_name: "Chiamaka Nwachukwu-Oyelaran",
    mrn: "OMC/2026/004812",
    date_of_birth: yearsAgo(34, 2, 8),
    sex: "F",
    payer: "Hygeia HMO · Gold",
    payer_kind: "HMO",
    reason: "Headache, three days",
    arrived_at: minutesAgo(72),
    triage: "yellow",
    stage: "Waiting for doctor",
    balance_kobo: 0,
  },
  {
    id: "p-002",
    token: "A-015",
    full_name: "Ibrahim Danjuma",
    mrn: "OMC/2026/004813",
    date_of_birth: yearsAgo(61, 10, 2),
    sex: "M",
    payer: "Self-pay",
    payer_kind: "Self-pay",
    reason: "Chest pain, sudden onset",
    arrived_at: minutesAgo(8),
    triage: "red",
    stage: "With doctor",
    balance_kobo: 4_500_00,
  },
  {
    id: "p-003",
    token: "A-016",
    full_name: "Blessing Udoka",
    mrn: "OMC/2026/004814",
    date_of_birth: yearsAgo(27, 6, 19),
    sex: "F",
    payer: "NHIA",
    payer_kind: "NHIA",
    reason: "Antenatal booking",
    arrived_at: minutesAgo(41),
    triage: "green",
    stage: "Waiting for doctor",
    balance_kobo: 0,
  },
  {
    id: "p-004",
    token: "A-017",
    full_name: "Emeka Okonkwo",
    mrn: "OMC/2026/004815",
    date_of_birth: yearsAgo(3, 0, 30),
    sex: "M",
    payer: "Dangote Group · Staff scheme",
    payer_kind: "Corporate",
    reason: "Fever and vomiting",
    arrived_at: minutesAgo(22),
    triage: "orange",
    stage: "Waiting for doctor",
    balance_kobo: 0,
  },
  {
    id: "p-005",
    token: "A-018",
    full_name: "Hauwa Sanusi",
    mrn: "OMC/2026/004816",
    date_of_birth: yearsAgo(45, 8, 11),
    sex: "F",
    payer: "Self-pay",
    payer_kind: "Self-pay",
    reason: "Follow-up, hypertension",
    arrived_at: minutesAgo(5),
    triage: null,
    stage: "Waiting for triage",
    balance_kobo: 12_000_00,
  },
  {
    id: "p-006",
    token: "A-019",
    full_name: "Tunde Afolabi",
    mrn: "OMC/2026/004817",
    date_of_birth: yearsAgo(52, 3, 4),
    sex: "M",
    payer: "Reliance HMO · Silver",
    payer_kind: "HMO",
    reason: "Back pain",
    arrived_at: minutesAgo(96),
    triage: "green",
    stage: "Waiting for bill",
    balance_kobo: 8_750_00,
  },
];

// --------------------------------------------------------------------------
// Consultation — one patient, in depth
// --------------------------------------------------------------------------

export const DEMO_CONSULTATION_PATIENT: BannerPatient = {
  full_name: "Chiamaka Nwachukwu-Oyelaran",
  mrn: "OMC/2026/004812",
  date_of_birth: yearsAgo(34, 2, 8),
  sex: "F",
  allergies: ["Penicillin — rash", "Sulfonamides — anaphylaxis"],
  allergies_checked: true,
  payer: "Hygeia HMO · Gold",
  encounter_label: "Outpatient · today",
};

export interface Vital {
  label: string;
  value: string;
  unit: string;
  tone: StatusTone;
}

export const DEMO_VITALS: Vital[] = [
  { label: "Blood pressure", value: "148/94", unit: "mmHg", tone: "warning" },
  { label: "Pulse", value: "96", unit: "bpm", tone: "stable" },
  { label: "Temperature", value: "37.8", unit: "°C", tone: "warning" },
  { label: "SpO₂", value: "98", unit: "%", tone: "stable" },
  { label: "Respiratory rate", value: "18", unit: "/min", tone: "stable" },
  { label: "Weight", value: "71.4", unit: "kg", tone: "neutral" },
];

export const DEMO_NEWS2 = 3;

/** Demo encounter events for the Consultation screen. The real patient
 * timeline is `TimelineEntry` in api/patients.ts. */
export interface DemoTimelineEntry {
  at: string;
  who: string;
  what: string;
  detail?: string;
}

export const DEMO_TIMELINE: DemoTimelineEntry[] = [
  { at: minutesAgo(72), who: "Front desk · Ngozi Bello", what: "Registered and checked in" },
  {
    at: minutesAgo(64),
    who: "Triage · Nurse Amarachi Eze",
    what: "Vitals recorded",
    detail: "BP 148/94, temp 37.8 °C, NEWS2 3",
  },
  { at: minutesAgo(12), who: "Dr Folake Ilesanmi", what: "Consultation started" },
  {
    at: minutesAgo(40),
    who: "Laboratory · Segun Ogundipe",
    what: "Malaria parasite (RDT) resulted",
    detail: "Negative",
  },
];

export interface OrderDraft {
  id: string;
  kind: "Laboratory" | "Radiology" | "Medicine" | "Procedure";
  name: string;
  detail: string;
  price_kobo: number;
  /** Set when the order collides with something already on the record. */
  conflict?: { tone: StatusTone; message: string };
  covered_by_payer: boolean;
}

export const DEMO_ORDER_DRAFT: OrderDraft[] = [
  {
    id: "o-1",
    kind: "Laboratory",
    name: "Full blood count",
    detail: "Sample: EDTA · Routine",
    price_kobo: 6_500_00,
    covered_by_payer: true,
  },
  {
    id: "o-2",
    kind: "Medicine",
    name: "Amoxicillin 500 mg capsule",
    detail: "1 cap three times daily × 5 days (15 caps)",
    price_kobo: 2_400_00,
    // The point of the whole screen: the system knows before the doctor signs.
    conflict: {
      tone: "critical",
      message: "Patient is allergic to Penicillin (rash). Amoxicillin is a penicillin.",
    },
    covered_by_payer: true,
  },
  {
    id: "o-3",
    kind: "Medicine",
    name: "Paracetamol 1 g tablet",
    detail: "1 tab four times daily × 3 days (12 tabs)",
    price_kobo: 900_00,
    covered_by_payer: true,
  },
  {
    id: "o-4",
    kind: "Radiology",
    name: "Chest X-ray, PA view",
    detail: "Routine · Ikeja imaging suite",
    price_kobo: 18_000_00,
    conflict: {
      tone: "warning",
      message: "Needs pre-authorisation from Hygeia HMO. Expect up to 24 hours.",
    },
    covered_by_payer: false,
  },
];

// --------------------------------------------------------------------------
// Pharmacy queue
// --------------------------------------------------------------------------

export interface PrescriptionLine {
  name: string;
  dose: string;
  quantity: string;
  /** Stock on hand at this branch, after the ledger is summed. */
  stock: number;
  stock_elsewhere?: { branch: string; quantity: number };
  expiring_batch?: string;
  allergy_conflict?: string;
}

export interface PharmacyJob {
  id: string;
  patient: string;
  mrn: string;
  prescriber: string;
  received_at: string;
  payer: string;
  payer_kind: PayerKind;
  priority: "Routine" | "Urgent";
  state: "New" | "Being prepared" | "Ready" | "Blocked";
  lines: PrescriptionLine[];
  total_kobo: number;
}

export const DEMO_PHARMACY: PharmacyJob[] = [
  {
    id: "rx-1",
    patient: "Ibrahim Danjuma",
    mrn: "OMC/2026/004813",
    prescriber: "Dr Yusuf Abubakar",
    received_at: minutesAgo(2),
    payer: "Self-pay",
    payer_kind: "Self-pay",
    priority: "Urgent",
    state: "New",
    lines: [
      { name: "Aspirin 300 mg tablet", dose: "Stat", quantity: "1 tab", stock: 420 },
      {
        name: "Glyceryl trinitrate 500 µg SL",
        dose: "Sublingual, stat",
        quantity: "1 tab",
        stock: 18,
        expiring_batch: "Batch NF-2211 expires in 26 days",
      },
    ],
    total_kobo: 3_200_00,
  },
  {
    id: "rx-2",
    patient: "Chiamaka Nwachukwu-Oyelaran",
    mrn: "OMC/2026/004812",
    prescriber: "Dr Folake Ilesanmi",
    received_at: minutesAgo(9),
    payer: "Hygeia HMO · Gold",
    payer_kind: "HMO",
    priority: "Routine",
    state: "Blocked",
    lines: [
      {
        name: "Amoxicillin 500 mg capsule",
        dose: "1 cap TDS × 5 days",
        quantity: "15 caps",
        stock: 260,
        allergy_conflict: "Allergic to Penicillin (rash)",
      },
      { name: "Paracetamol 1 g tablet", dose: "1 tab QDS × 3 days", quantity: "12 tabs", stock: 1_840 },
    ],
    total_kobo: 3_300_00,
  },
  {
    id: "rx-3",
    patient: "Blessing Udoka",
    mrn: "OMC/2026/004814",
    prescriber: "Dr Kelechi Chukwuma",
    received_at: minutesAgo(18),
    payer: "NHIA",
    payer_kind: "NHIA",
    priority: "Routine",
    state: "Being prepared",
    lines: [
      { name: "Folic acid 5 mg tablet", dose: "1 tab daily × 30 days", quantity: "30 tabs", stock: 640 },
      {
        name: "Ferrous sulphate 200 mg",
        dose: "1 tab BD × 30 days",
        quantity: "60 tabs",
        stock: 0,
        stock_elsewhere: { branch: "Lekki", quantity: 310 },
      },
    ],
    total_kobo: 1_950_00,
  },
  {
    id: "rx-4",
    patient: "Emeka Okonkwo",
    mrn: "OMC/2026/004815",
    prescriber: "Dr Folake Ilesanmi",
    received_at: minutesAgo(31),
    payer: "Dangote Group · Staff scheme",
    payer_kind: "Corporate",
    priority: "Routine",
    state: "Ready",
    lines: [
      {
        name: "Artemether-lumefantrine 20/120",
        dose: "Paediatric, by weight × 3 days",
        quantity: "6 tabs",
        stock: 95,
      },
    ],
    total_kobo: 4_100_00,
  },
];

// --------------------------------------------------------------------------
// Journey board — one row per patient, one column per department
// --------------------------------------------------------------------------

export const JOURNEY_STAGES = [
  "Registered",
  "Triage",
  "Doctor",
  "Laboratory",
  "Pharmacy",
  "Cashier",
] as const;

export type JourneyStage = (typeof JOURNEY_STAGES)[number];

export interface JourneyRow {
  id: string;
  token: string;
  patient: string;
  mrn: string;
  triage: TriageCode | null;
  /** The stage the patient is sitting in now. */
  current: JourneyStage;
  /** Stages already finished, in order. */
  done: JourneyStage[];
  /** Stages this patient will never visit — not ordered for them. */
  skipped: JourneyStage[];
  since: string;
  /** Who is holding them up, when something is. */
  blocked_by?: string;
}

export const DEMO_JOURNEY: JourneyRow[] = [
  {
    id: "j-1",
    token: "A-015",
    patient: "Ibrahim Danjuma",
    mrn: "OMC/2026/004813",
    triage: "red",
    current: "Doctor",
    done: ["Registered", "Triage"],
    skipped: [],
    since: minutesAgo(6),
  },
  {
    id: "j-2",
    token: "A-014",
    patient: "Chiamaka Nwachukwu-Oyelaran",
    mrn: "OMC/2026/004812",
    triage: "yellow",
    current: "Pharmacy",
    done: ["Registered", "Triage", "Doctor", "Laboratory"],
    skipped: [],
    since: minutesAgo(9),
    blocked_by: "Allergy conflict — pharmacist querying the prescriber",
  },
  {
    id: "j-3",
    token: "A-016",
    patient: "Blessing Udoka",
    mrn: "OMC/2026/004814",
    triage: "green",
    current: "Pharmacy",
    done: ["Registered", "Triage", "Doctor"],
    skipped: ["Laboratory"],
    since: minutesAgo(18),
    blocked_by: "Ferrous sulphate out of stock at Ikeja",
  },
  {
    id: "j-4",
    token: "A-017",
    patient: "Emeka Okonkwo",
    mrn: "OMC/2026/004815",
    triage: "orange",
    current: "Cashier",
    done: ["Registered", "Triage", "Doctor", "Pharmacy"],
    skipped: ["Laboratory"],
    since: minutesAgo(4),
  },
  {
    id: "j-5",
    token: "A-018",
    patient: "Hauwa Sanusi",
    mrn: "OMC/2026/004816",
    triage: null,
    current: "Triage",
    done: ["Registered"],
    skipped: [],
    since: minutesAgo(5),
  },
  {
    id: "j-6",
    token: "A-019",
    patient: "Tunde Afolabi",
    mrn: "OMC/2026/004817",
    triage: "green",
    current: "Cashier",
    done: ["Registered", "Triage", "Doctor", "Laboratory", "Pharmacy"],
    skipped: [],
    since: minutesAgo(38),
    blocked_by: "Waiting for HMO authorisation code",
  },
];

// --------------------------------------------------------------------------
// Group dashboard
//
// Nothing here any more, deliberately.
//
// The group dashboard now reads /api/v1/reporting/group/ and shows only what
// the system can actually measure, with the rest named as "not measured yet"
// and the milestone it arrives at. The fixtures that used to live here —
// branch revenue, a billed-by-hour chart, a payer mix — were removed with
// that change rather than left behind: a fixture that looks exactly like a
// real API response is the easiest thing in a codebase to wire up by
// accident, and an invented revenue figure on an owner's dashboard is the
// one mistake this product cannot afford.
//
// They come back as real numbers with billing, at milestone 8.
