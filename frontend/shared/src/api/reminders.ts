/**
 * Appointment reminders.
 *
 * `sms_enabled` and `email_enabled` are on every response on purpose. A list
 * of queued reminders that never send, with no explanation, looks like a
 * broken feature; "text messages are not switched on yet" is an answer, and
 * it is the one that tells a hospital what to do next.
 *
 * Costs are integer kobo throughout, like every other money value in Curavia.
 */

import type { ApiClient } from "./client";

export type ReminderStatus = "queued" | "sent" | "failed" | "skipped" | "cancelled";

export interface Reminder {
  id: string;
  patient_name: string;
  patient_mrn: string;
  kind: "day_before" | "same_day";
  kind_label: string;
  status: ReminderStatus;
  /** Blank until it is sent — the channel is decided at send time. */
  channel: string;
  send_at: string;
  sent_at: string | null;
  attempts: number;
  cost_kobo: number;
  failure_reason: string;
  appointment_at: string;
}

export interface ReminderList {
  reminders: Reminder[];
  counts: Record<ReminderStatus, number>;
  spent_kobo: number;
  sms_enabled: boolean;
  email_enabled: boolean;
}

export interface ReminderCost {
  days: number;
  by_email: number;
  by_sms: number;
  /** Reminders for patients with neither a phone number nor an email. */
  no_contact: number;
  sms_cost_kobo: number;
  sms_enabled: boolean;
  email_enabled: boolean;
}

export interface NoShow {
  id: string;
  patient_name: string;
  patient_mrn: string;
  missed_at: string;
  /** False means the hospital never told them, which is a different problem. */
  was_reminded: boolean;
  note: string;
}

export class RemindersApi {
  constructor(private readonly client: ApiClient) {}

  list(status?: ReminderStatus): Promise<ReminderList> {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    return this.client.get<ReminderList>(`/reminders/${query}`);
  }

  cost(days = 30): Promise<ReminderCost> {
    return this.client.get<ReminderCost>(`/reminders/cost/?days=${days}`);
  }

  sendNow(): Promise<{ sent: number; failed: number; skipped: number; cost_kobo: number }> {
    return this.client.post("/reminders/send/", {});
  }

  noShows(): Promise<NoShow[]> {
    return this.client.get<NoShow[]>("/no-shows/");
  }

  setPreference(
    patientId: string,
    optedOut: boolean,
  ): Promise<{ id: string; reminders_opted_out: boolean }> {
    return this.client.post(`/patients/${patientId}/reminders/`, { opted_out: optedOut });
  }
}
