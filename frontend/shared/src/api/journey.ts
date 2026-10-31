/**
 * The journey: who is in the building, and where.
 *
 * `BoardRow` is what both the HTTP snapshot and the live socket deliver —
 * deliberately the same shape, so a screen applies a delta and a refetch
 * through one code path. Two shapes would mean two ways to be wrong, and
 * only one of them would be exercised in a test.
 */

import type { ApiClient } from "./client";

export type Stage =
  | "arrived"
  | "triage"
  | "in_triage"
  | "waiting_doctor"
  | "with_doctor"
  | "lab"
  | "radiology"
  | "pharmacy"
  | "cashier"
  | "ward"
  | "done"
  | "left";

export interface BoardRow {
  id: string;
  patient_id: string;
  patient_name: string;
  mrn: string;
  /** The queue token, when they were given one at the desk. */
  token: string;
  stage: Stage;
  stage_label: string;
  department: string;
  triage: string;
  kind: string;
  attending: string;
  minutes_in_stage: number;
  minutes_in_hospital: number;
  /** False while they are being seen — that time is not waiting. */
  is_waiting: boolean;
  started_at: string;
  stage_since: string;
}

export interface JourneyEvent {
  id: string;
  at: string;
  from_stage: string;
  to_stage: Stage;
  to_stage_label: string;
  department_name: string;
  actor_name: string;
  minutes_in_previous_stage: number;
  previous_was_waiting: boolean;
  note: string;
}

export interface DisplayPayload {
  screen: string;
  footer: string;
  now_serving: {
    label: string;
    department: string;
    called_to: string;
    called_at: string | null;
  }[];
  waiting: { department: string; count: number }[];
  at: string;
}

export interface DisplayScreen {
  id: string;
  name: string;
  footer: string;
  is_active: boolean;
  last_seen_at: string | null;
  department_names: string[];
  /** Empty except when deliberately revealed — it is a password-free URL. */
  url: string;
}

/** The order the board draws its columns in. */
export const STAGE_ORDER: Stage[] = [
  "arrived",
  "triage",
  "in_triage",
  "waiting_doctor",
  "with_doctor",
  "lab",
  "radiology",
  "pharmacy",
  "cashier",
  "ward",
];

export const STAGE_LABELS: Record<Stage, string> = {
  arrived: "Arrived",
  triage: "Waiting for triage",
  in_triage: "In triage",
  waiting_doctor: "Waiting for the doctor",
  with_doctor: "With the doctor",
  lab: "Laboratory",
  radiology: "Radiology",
  pharmacy: "Pharmacy",
  cashier: "Cashier",
  ward: "Ward",
  done: "Finished",
  left: "Left",
};

export class JourneyApi {
  constructor(private readonly client: ApiClient) {}

  board(): Promise<{ branch: string; rows: BoardRow[] }> {
    return this.client.get<{ branch: string; rows: BoardRow[] }>("/journey/");
  }

  move(
    id: string,
    stage: Stage,
    options: { department_id?: string; note?: string } = {},
  ): Promise<BoardRow> {
    return this.client.post<BoardRow>(`/journey/${id}/move/`, { stage, ...options });
  }

  events(
    id: string,
  ): Promise<{ encounter: BoardRow; events: JourneyEvent[]; minutes_waiting: number }> {
    return this.client.get(`/journey/${id}/events/`);
  }

  start(input: {
    patient_id: string;
    department_id?: string;
    kind?: string;
    reason?: string;
  }): Promise<{ encounter: BoardRow }> {
    return this.client.post("/journey/start/", input);
  }

  screens(): Promise<DisplayScreen[]> {
    return this.client.get<DisplayScreen[]>("/displays/");
  }

  createScreen(input: {
    name: string;
    footer?: string;
    department_ids?: string[];
  }): Promise<DisplayScreen> {
    return this.client.post<DisplayScreen>("/displays/", input);
  }

  screenToken(id: string, action: "reveal" | "rotate"): Promise<DisplayScreen> {
    return this.client.post<DisplayScreen>(`/displays/${id}/${action}/`, {});
  }

  /** What a television shows, for a client with no socket. Needs no sign-in. */
  display(token: string): Promise<DisplayPayload> {
    return this.client.get<DisplayPayload>(
      `/displays/public/${encodeURIComponent(token)}/`,
    );
  }
}
