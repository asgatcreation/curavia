/**
 * Diagnoses and the problem list.
 *
 * Two different things, deliberately kept apart in the types as well as on
 * the screen. A `Diagnosis` is what was decided on one visit; a `Problem` is
 * what the patient has, across all of them. The second is maintained from
 * the first — confirming a chronic condition opens a problem — but a screen
 * that drew them as one list would be answering neither question.
 */

import type { ApiClient } from "./client";

export type Certainty = "suspected" | "confirmed" | "ruled_out";
export type ProblemStatus = "active" | "resolved" | "inactive";

export interface CodeResult {
  code: string;
  title: string;
  /** "E11 · Type 2 diabetes mellitus" — what the list draws. */
  label: string;
  chapter: string;
  /** Confirming one of these opens an entry on the problem list. */
  chronic: boolean;
}

export interface Diagnosis {
  id: string;
  code: string;
  title: string;
  label: string;
  chapter: string;
  certainty: Certainty;
  certainty_label: string;
  /** The reason they came. Exactly one per visit; the server enforces it. */
  is_primary: boolean;
  is_chronic: boolean;
  note: string;
  diagnosed_at: string;
  diagnosed_by_name: string;
}

export interface Problem {
  id: string;
  code: string;
  title: string;
  label: string;
  chapter: string;
  status: ProblemStatus;
  status_label: string;
  is_active: boolean;
  onset_on: string | null;
  resolved_on: string | null;
  note: string;
  last_noted_at: string;
}

export class DiagnosesApi {
  constructor(private readonly client: ApiClient) {}

  /** Writes nothing and touches no patient, so it runs as the doctor types. */
  search(query: string): Promise<{
    results: CodeResult[];
    chapters: string[];
    total_in_catalogue: number;
  }> {
    return this.client.get(`/icd/?q=${encodeURIComponent(query)}`);
  }

  forEncounter(encounterId: string): Promise<{ diagnoses: Diagnosis[] }> {
    return this.client.get<{ diagnoses: Diagnosis[] }>(`/encounters/${encounterId}/diagnoses/`);
  }

  /** Adding a code already on the visit updates it rather than duplicating. */
  add(
    encounterId: string,
    input: { code: string; certainty?: Certainty; is_primary?: boolean; note?: string },
  ): Promise<Diagnosis> {
    return this.client.post<Diagnosis>(`/encounters/${encounterId}/diagnoses/`, input);
  }

  change(
    diagnosisId: string,
    action: "primary" | "rule_out",
    note = "",
  ): Promise<Diagnosis> {
    return this.client.post<Diagnosis>(`/diagnoses/${diagnosisId}/`, { action, note });
  }

  /** For a code entered in error. Something genuinely excluded is ruled out. */
  remove(diagnosisId: string): Promise<void> {
    return this.client.delete<void>(`/diagnoses/${diagnosisId}/`);
  }

  problems(patientId: string): Promise<{ problems: Problem[] }> {
    return this.client.get<{ problems: Problem[] }>(`/patients/${patientId}/problems/`);
  }

  addProblem(
    patientId: string,
    input: { code: string; note?: string; onset_on?: string },
  ): Promise<Problem> {
    return this.client.post<Problem>(`/patients/${patientId}/problems/`, input);
  }

  changeProblem(
    problemId: string,
    action: "resolve" | "reopen",
    note = "",
  ): Promise<Problem> {
    return this.client.post<Problem>(`/problems/${problemId}/`, { action, note });
  }
}
