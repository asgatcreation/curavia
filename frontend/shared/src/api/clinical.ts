/**
 * Observations, NEWS2 and triage.
 *
 * `Score.usable` is the field that matters most here. NEWS2 is not validated
 * under 16 or in pregnancy, so the server refuses to produce a number for
 * those patients and sends a reason instead. A client that treated `total`
 * as the answer would show a three-year-old a score of 0 and call it
 * routine.
 *
 * `red_flag` is the second: one reading scoring 3 escalates on its own, so a
 * screen that only draws the total loses that patient.
 */

import type { TriageCode } from "../ui/status";
import type { ApiClient } from "./client";

// `TriageCode` is the design system's, not a second copy: the colours a
// screen can draw and the categories the server accepts have to be the same
// list, and two declarations would drift.
export type { TriageCode };

export type Band = "routine" | "low" | "medium" | "high" | "not_applicable";

export interface ScoreParameter {
  name: string;
  label: string;
  value: string;
  score: number;
}

export interface Score {
  total: number;
  band: Band;
  /** What the band means, in the words a nurse needs. */
  response: string;
  /** One reading scored 3. Escalates whatever the total says. */
  red_flag: boolean;
  /** False when the score must not be acted on — see `reason`. */
  usable: boolean;
  reason: string;
  missing: string[];
  is_complete: boolean;
  parameters: ScoreParameter[];
}

export interface Bmi {
  value: number;
  /** Blank for a child: adult categories do not apply. */
  category: string;
  caveat: string;
}

export interface Observation {
  id: string;
  taken_at: string;
  taken_by_name: string;
  respiration_rate: number | null;
  spo2: number | null;
  on_oxygen: boolean | null;
  oxygen_litres: string | null;
  systolic_bp: number | null;
  diastolic_bp: number | null;
  blood_pressure: string;
  pulse: number | null;
  temperature: string | null;
  consciousness: string;
  weight_kg: string | null;
  height_cm: string | null;
  blood_glucose: string | null;
  pain_score: number | null;
  news2_score: number | null;
  news2_red_flag: boolean;
  news2_band: Band | "";
  score_version: string;
  oxygen_scale: number;
  note: string;
  /** False once a later reading corrected this one. Still shown. */
  is_current: boolean;
  correction_reason: string;
  bmi: Bmi | null;
}

export interface TriageRow {
  id: string;
  encounter_id: string;
  patient_name: string;
  mrn: string;
  category: TriageCode;
  complaint: string;
  assessed_at: string;
  minutes_waiting: number;
  /** Null for blue: non-urgent makes no promise, so it cannot break one. */
  target_minutes: number | null;
  is_overdue: boolean;
  news2: number | null;
  news2_red_flag: boolean;
  stage: string;
}

export interface TriageAssessment {
  id: string;
  encounter: string;
  patient_name: string;
  patient_mrn: string;
  category: TriageCode;
  category_label: string;
  complaint: string;
  assessed_at: string;
  target_minutes: number | null;
  revision_reason: string;
  revised_at: string | null;
}

export interface NursingNote {
  id: string;
  kind: string;
  kind_label: string;
  body: string;
  written_at: string;
  written_by_name: string;
  amends: string | null;
}

export interface ObservationInput {
  respiration_rate?: number | null;
  spo2?: number | null;
  on_oxygen?: boolean | null;
  systolic_bp?: number | null;
  diastolic_bp?: number | null;
  pulse?: number | null;
  temperature?: string | number | null;
  consciousness?: string;
  weight_kg?: string | number | null;
  height_cm?: string | number | null;
  blood_glucose?: string | number | null;
  pain_score?: number | null;
  oxygen_scale?: number;
  note?: string;
  supersedes_id?: string;
  correction_reason?: string;
  /** Only on the preview endpoint, so the score knows the patient's age. */
  patient_id?: string;
}

export class ClinicalApi {
  constructor(private readonly client: ApiClient) {}

  waitingRoom(): Promise<{ rows: TriageRow[] }> {
    return this.client.get<{ rows: TriageRow[] }>("/triage/");
  }

  /** Scores without storing. The form calls it as the nurse types. */
  previewScore(input: ObservationInput): Promise<Score> {
    return this.client.post<Score>("/triage/score/", input);
  }

  recordObservation(
    encounterId: string,
    input: ObservationInput,
  ): Promise<{ observation: Observation; score: Score }> {
    return this.client.post(`/encounters/${encounterId}/observations/`, input);
  }

  assess(
    encounterId: string,
    input: { category: TriageCode; complaint: string; observation_id?: string },
  ): Promise<TriageAssessment> {
    return this.client.post<TriageAssessment>(`/encounters/${encounterId}/triage/`, input);
  }

  chart(patientId: string): Promise<Observation[]> {
    return this.client.get<Observation[]>(`/patients/${patientId}/chart/`);
  }

  notes(encounterId: string): Promise<NursingNote[]> {
    return this.client.get<NursingNote[]>(`/encounters/${encounterId}/notes/`);
  }

  writeNote(
    encounterId: string,
    input: { body: string; kind?: string; amends_id?: string },
  ): Promise<NursingNote> {
    return this.client.post<NursingNote>(`/encounters/${encounterId}/notes/`, input);
  }
}
