/**
 * The ward's task list, and the handover between shifts.
 *
 * Two things the screen must not get wrong.
 *
 * `outstanding` on a handover is a **snapshot**, taken when it was signed.
 * It is not the live list and will drift from it within minutes — that is
 * the point. Render it as what the nurse coming on was told, never as what
 * is outstanding now.
 *
 * A task is closed by an `action`, not by setting a status. "Done" and "not
 * done" are not two values of one field: they lead to different next steps,
 * and only one of them needs a reason.
 */

import type { ApiClient } from "./client";

export type TaskKind =
  | "observation"
  | "medication"
  | "care"
  | "sample"
  | "review"
  | "other";

export type TaskStatus = "pending" | "done" | "not_done" | "cancelled";

export interface NursingTask {
  id: string;
  patient: string;
  patient_name: string;
  encounter: string | null;
  department: string | null;
  kind: TaskKind;
  kind_label: string;
  name: string;
  detail: string;
  /** The order this came from, where it came from one. */
  order: string | null;
  due_at: string | null;
  urgent: boolean;
  status: TaskStatus;
  status_label: string;
  done_at: string | null;
  done_by_name: string;
  done_note: string;
  not_done_reason: string;
  /** How many shifts this has been passed along. Three is worth asking about. */
  handovers: number;
  is_overdue: boolean;
  minutes_late: number;
  created_at: string;
}

export type HandoverState = "draft" | "signed" | "accepted";

/** One line of the frozen outstanding list. */
export interface OutstandingTask {
  task_id: string;
  patient: string;
  patient_id: string;
  kind: string;
  name: string;
  detail: string;
  due_at: string;
  overdue: boolean;
  minutes_late: number;
  urgent: boolean;
  handovers: number;
}

export interface HandoverPatient {
  patient_id?: string;
  name?: string;
  situation?: string;
  background?: string;
  assessment?: string;
  recommendation?: string;
}

export interface HandoverContent {
  patients?: HandoverPatient[];
  ward_note?: string;
}

export interface Handover {
  id: string;
  department: string;
  shift: string;
  from_nurse_name: string;
  from_nurse_role: string;
  to_nurse_name: string;
  status: HandoverState;
  status_label: string;
  content: HandoverContent;
  /** Frozen at signing. Not the live list. */
  outstanding: OutstandingTask[];
  signed_at: string | null;
  accepted_at: string | null;
  accepted_note: string;
  /** The lines that print under the signature. Empty until signed. */
  signature: string[];
  /** True when you are the nurse who gave it, so you are not offered it back. */
  from_me: boolean;
  patient_count: number;
  outstanding_count: number;
  is_signed: boolean;
  created_at: string;
}

export interface WardList {
  tasks: NursingTask[];
  /** The last handover given on this ward, for the nurse coming on. */
  handover: Handover | null;
  /** This nurse's own unfinished handover, if she has one. */
  draft: Handover | null;
}

export interface Ward {
  id: string;
  name: string;
  code: string;
  kind: string;
  kind_label: string;
  open_tasks: number;
  overdue_tasks: number;
}

export interface TaskInput {
  patient_id: string;
  name: string;
  encounter_id?: string;
  department_id?: string;
  kind?: TaskKind;
  detail?: string;
  due_at?: string;
  urgent?: boolean;
}

export type CloseAction = "done" | "not_done" | "cancelled";

export class NursingApi {
  constructor(private readonly client: ApiClient) {}

  /** The wards this nurse can open, busiest first. */
  wards(): Promise<{ wards: Ward[] }> {
    return this.client.get<{ wards: Ward[] }>("/wards/");
  }

  /** The list, the handover to read and the draft being written, in one call. */
  ward(departmentId: string): Promise<WardList> {
    return this.client.get<WardList>(`/wards/${departmentId}/tasks/`);
  }

  forEncounter(encounterId: string): Promise<{ tasks: NursingTask[] }> {
    return this.client.get<{ tasks: NursingTask[] }>(`/encounters/${encounterId}/tasks/`);
  }

  forPatient(patientId: string): Promise<{ tasks: NursingTask[] }> {
    return this.client.get<{ tasks: NursingTask[] }>(`/patients/${patientId}/tasks/`);
  }

  add(input: TaskInput): Promise<NursingTask> {
    return this.client.post<NursingTask>("/nursing-tasks/", input);
  }

  /** `reason` is required for "not_done" and "cancelled", refused without it. */
  close(
    taskId: string,
    action: CloseAction,
    options: { note?: string; reason?: string } = {},
  ): Promise<NursingTask> {
    return this.client.post<NursingTask>(`/nursing-tasks/${taskId}/`, {
      action,
      note: options.note ?? "",
      reason: options.reason ?? "",
    });
  }

  handovers(departmentId: string): Promise<{ handovers: Handover[] }> {
    return this.client.get<{ handovers: Handover[] }>(`/wards/${departmentId}/handovers/`);
  }

  /** Starts one, or returns the draft this nurse already has open. */
  openHandover(departmentId: string, shift = ""): Promise<Handover> {
    return this.client.post<Handover>(`/wards/${departmentId}/handovers/`, { shift });
  }

  saveHandover(handoverId: string, content: HandoverContent): Promise<Handover> {
    return this.client.patch<Handover>(`/handovers/${handoverId}/`, { content });
  }

  /** The outstanding list is built by the server, never sent from here. */
  signHandover(handoverId: string, shift = ""): Promise<Handover> {
    return this.client.post<Handover>(`/handovers/${handoverId}/sign/`, { shift });
  }

  acceptHandover(handoverId: string, note = ""): Promise<Handover> {
    return this.client.post<Handover>(`/handovers/${handoverId}/accept/`, { note });
  }
}
