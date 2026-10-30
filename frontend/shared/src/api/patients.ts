/**
 * Patients.
 *
 * Note what is NOT here: the clinical fields are optional on
 * `PatientDetail`, because the server removes them entirely for a caller
 * without `clinical.note.read` rather than sending nulls. A cashier's
 * response has no `genotype` key at all, and the type says so — so a screen
 * that reads one has to admit it might not be there.
 */

import type { ApiClient } from "./client";

export type Sex = "F" | "M" | "U";

export interface PatientAllergy {
  id: string;
  substance: string;
  kind: string;
  reaction: string;
  severity: "mild" | "moderate" | "severe" | "anaphylaxis";
  noted_on: string;
  is_active: boolean;
}

export interface PatientSummary {
  id: string;
  mrn: string;
  full_name: string;
  first_name: string;
  last_name: string;
  other_names: string;
  sex: Sex;
  date_of_birth: string | null;
  date_of_birth_is_estimated: boolean;
  /** Already formatted by the server: "34y", "7mo", "newborn". */
  age: string;
  phone: string;
  is_provisional: boolean;
  is_deceased: boolean;
}

export interface PatientDetail extends PatientSummary {
  address: string;
  state: string;
  lga: string;
  email: string;
  alternate_phone: string;
  marital_status: string;
  occupation: string;
  home_branch_name: string;
  default_payer_name: string;
  registered_at: string;
  identifiers: { id: string; kind: string; value: string; is_verified: boolean }[];
  next_of_kin: {
    id: string;
    name: string;
    relationship: string;
    phone: string;
    is_primary: boolean;
  }[];

  // Removed entirely for a caller without clinical.note.read.
  blood_group?: string;
  genotype?: string;
  allergies?: PatientAllergy[];
  allergies_checked_at?: string | null;
  allergies_known?: boolean;
}

export interface DuplicateMatch {
  patient: PatientSummary;
  score: number;
  reasons: string[];
  is_strong: boolean;
}

export interface RegisterPatientInput {
  first_name: string;
  last_name: string;
  other_names?: string;
  sex?: Sex;
  date_of_birth?: string | null;
  date_of_birth_is_estimated?: boolean;
  phone?: string;
  email?: string;
  address?: string;
  state?: string;
  lga?: string;
  blood_group?: string;
  genotype?: string;
  occupation?: string;
  identifiers?: { kind: string; value: string }[];
  next_of_kin?: { name: string; relationship: string; phone: string }[];
  allergies?: { substance: string; reaction?: string; severity?: string }[];
  allergies_checked?: boolean;
  /** Sent after the clerk has looked at the possible duplicates and said no. */
  confirmed_not_duplicate?: boolean;
}

export interface TimelineEntry {
  at: string;
  /** Stable key the screen switches an icon on. */
  kind: string;
  title: string;
  detail: string;
  actor: string;
  tone: "neutral" | "info" | "stable" | "warning" | "urgent" | "critical";
  is_pinned: boolean;
}

export interface Timeline {
  entries: TimelineEntry[];
  /** What this timeline will carry later, and from which milestone. */
  pending: { key: string; label: string; milestone: string }[];
}

export interface PatientCard {
  patient: PatientSummary;
  /** Surname first, in capitals — how a clerk reads a card at a counter. */
  printed_name: string;
  hospital_name: string;
  branch_name: string;
  /** `CURAVIA:<hospital>:<MRN>`. Deliberately not a URL, and never a name. */
  payload: string;
  /** An inline SVG fragment, sized in millimetres for printing. */
  qr_svg: string;
  support_phone: string;
}

export interface MergePlan {
  winner: PatientSummary;
  loser: PatientSummary;
  fills: { field: string; label: string; value: string }[];
  moves: { identifiers: number; next_of_kin: number; allergies: number };
  severity_changes: { substance: string; was: string; becomes: string }[];
  allergies_already_present: string[];
  warnings: string[];
  is_safe: boolean;
  suggested_winner_id: string;
}

export interface MergeRecord {
  id: string;
  at: string;
  by: string;
  reason: string;
  winner: { id: string; mrn: string };
  loser: { id: string; mrn: string };
  is_reversed: boolean;
  reversed_at: string | null;
  reversed_by: string;
  reversal_reason: string;
  can_reverse: boolean;
}

export class PatientsApi {
  constructor(private readonly client: ApiClient) {}

  search(query: string, limit = 25): Promise<PatientSummary[]> {
    return this.client.get<PatientSummary[]>(
      `/patients/?q=${encodeURIComponent(query)}&limit=${limit}`,
    );
  }

  get(id: string): Promise<PatientDetail> {
    return this.client.get<PatientDetail>(`/patients/${id}/`);
  }

  byMrn(mrn: string): Promise<PatientSummary & { resolved_from_merge: boolean }> {
    return this.client.get(`/patients/by-mrn/${encodeURIComponent(mrn)}/`);
  }

  checkDuplicates(input: {
    first_name: string;
    last_name: string;
    phone?: string;
    date_of_birth?: string | null;
    nin?: string;
  }): Promise<DuplicateMatch[]> {
    return this.client.post<DuplicateMatch[]>("/patients/check-duplicates/", input);
  }

  register(input: RegisterPatientInput): Promise<PatientDetail> {
    return this.client.post<PatientDetail>("/patients/register/", input);
  }

  quickRegister(input: {
    sex?: Sex;
    estimated_age?: number | null;
    note?: string;
  }): Promise<PatientDetail> {
    return this.client.post<PatientDetail>("/patients/register/quick/", input);
  }

  timeline(id: string): Promise<Timeline> {
    return this.client.get<Timeline>(`/patients/${id}/timeline/`);
  }

  card(id: string): Promise<PatientCard> {
    return this.client.get<PatientCard>(`/patients/${id}/card/`);
  }

  merges(id: string): Promise<MergeRecord[]> {
    return this.client.get<MergeRecord[]>(`/patients/${id}/merges/`);
  }

  /** What a merge would do. The screen calls this before offering the button. */
  previewMerge(winnerId: string, loserId: string): Promise<MergePlan> {
    return this.client.post<MergePlan>("/patients/merge/preview/", {
      winner_id: winnerId,
      loser_id: loserId,
    });
  }

  merge(winnerId: string, loserId: string, reason: string): Promise<{ id: string }> {
    return this.client.post<{ id: string }>("/patients/merge/", {
      winner_id: winnerId,
      loser_id: loserId,
      reason,
    });
  }

  reverseMerge(mergeId: string, reason: string): Promise<{ id: string }> {
    return this.client.post<{ id: string }>(`/patients/merge/${mergeId}/reverse/`, { reason });
  }

  /** Whatever the scanner read, or an MRN typed by hand. */
  scan(code: string): Promise<PatientSummary & { resolved_from_merge: boolean }> {
    return this.client.post("/patients/scan/", { code });
  }
}
