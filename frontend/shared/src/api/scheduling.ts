/**
 * Appointments and the queue.
 *
 * Two things the types say out loud, because both are easy to get wrong in
 * a client:
 *
 * A slot carries `capacity`, `taken` and `free` rather than a boolean. A
 * clinic that books four to the hour is normal here, so "is it free" is a
 * number, and a screen that treats it as a yes/no will tell a clerk a slot
 * is gone when three places are left.
 *
 * Booking can fail with 409 after the diary was drawn — somebody else took
 * the slot in between. That is not a validation error and the screen has to
 * handle it as "the world moved", by reloading availability.
 */

import type { ApiClient } from "./client";

export interface Clinic {
  id: string;
  name: string;
  department: string;
  department_name: string;
  service_name: string;
  default_duration_minutes: number;
  booking_horizon_days: number;
  accepts_walk_ins: boolean;
  is_active: boolean;
}

export interface Slot {
  starts_at: string;
  minutes: number;
  schedule_id: string;
  staff_id: string;
  staff_name: string;
  capacity: number;
  taken: number;
  free: number;
}

export interface DayAvailability {
  day: string;
  /** Why there is nothing, when there is nothing. Never shown as an empty list. */
  closed_because: string;
  free_count: number;
  slots: Slot[];
}

export interface Availability {
  clinic: Clinic;
  days: DayAvailability[];
}

export type AppointmentStatus =
  | "booked"
  | "checked_in"
  | "in_progress"
  | "completed"
  | "cancelled"
  | "no_show";

export interface Appointment {
  id: string;
  patient: string;
  patient_name: string;
  patient_mrn: string;
  clinic: string;
  clinic_name: string;
  staff: string | null;
  staff_name: string;
  starts_at: string;
  duration_minutes: number;
  status: AppointmentStatus;
  source: string;
  kind: "in_person" | "telemedicine";
  reason: string;
  checked_in_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string;
  /** The token they were given at check-in, if they have arrived. */
  ticket_label: string;
}

export type TicketStatus = "waiting" | "called" | "serving" | "done" | "skipped" | "left";

export interface QueueTicket {
  id: string;
  label: string;
  number: number;
  status: TicketStatus;
  /** Lower is seen sooner. Triage sets this. */
  priority: number;
  patient: string;
  patient_name: string;
  patient_mrn: string;
  department: string;
  department_name: string;
  issued_at: string;
  called_at: string | null;
  called_to: string;
  minutes_waiting: number;
}

export interface Schedule {
  id: string;
  clinic: string;
  staff: string;
  staff_name: string;
  weekday: number;
  starts_at: string;
  ends_at: string;
  slot_minutes: number;
  capacity_per_slot: number;
  places_per_session: number;
  valid_from: string;
  valid_until: string | null;
  is_active: boolean;
}

export interface LeavePreview {
  exception: {
    id?: string;
    staff: string | null;
    clinic: string | null;
    kind: string;
    starts_on: string;
    ends_on: string;
    reason: string;
  };
  /** Who this strands. Named, not counted — somebody has to ring them. */
  affected: Appointment[];
  affected_count: number;
}

export class SchedulingApi {
  constructor(private readonly client: ApiClient) {}

  clinics(): Promise<Clinic[]> {
    return this.client.get<Clinic[]>("/clinics/");
  }

  schedules(clinicId?: string): Promise<Schedule[]> {
    const query = clinicId ? `?clinic=${encodeURIComponent(clinicId)}` : "";
    return this.client.get<Schedule[]>(`/schedules/${query}`);
  }

  availability(options: {
    clinicId: string;
    from?: string;
    days?: number;
    staffId?: string;
    includeFull?: boolean;
  }): Promise<Availability> {
    const params = new URLSearchParams({ clinic: options.clinicId });
    if (options.from) params.set("from", options.from);
    if (options.days) params.set("days", String(options.days));
    if (options.staffId) params.set("staff", options.staffId);
    if (options.includeFull) params.set("include_full", "true");
    return this.client.get<Availability>(`/clinics/availability/?${params.toString()}`);
  }

  appointments(options: {
    day?: string;
    clinicId?: string;
    patientId?: string;
    status?: string;
  }): Promise<Appointment[]> {
    const params = new URLSearchParams();
    if (options.day) params.set("day", options.day);
    if (options.clinicId) params.set("clinic", options.clinicId);
    if (options.patientId) params.set("patient", options.patientId);
    if (options.status) params.set("status", options.status);
    return this.client.get<Appointment[]>(`/appointments/?${params.toString()}`);
  }

  book(input: {
    patient_id: string;
    clinic_id: string;
    schedule_id: string;
    starts_at: string;
    reason?: string;
    source?: string;
    kind?: string;
  }): Promise<Appointment> {
    return this.client.post<Appointment>("/appointments/book/", input);
  }

  reschedule(
    id: string,
    input: { schedule_id: string; starts_at: string; reason?: string },
  ): Promise<Appointment> {
    return this.client.post<Appointment>(`/appointments/${id}/reschedule/`, input);
  }

  cancel(id: string, reason: string): Promise<Appointment> {
    return this.client.post<Appointment>(`/appointments/${id}/cancel/`, { reason });
  }

  /** Creating leave never cancels anybody — preview first, then save. */
  leave(
    input: {
      staff?: string | null;
      clinic?: string | null;
      kind: string;
      starts_on: string;
      ends_on: string;
      reason?: string;
    },
    options: { preview?: boolean } = {},
  ): Promise<LeavePreview> {
    const query = options.preview ? "?preview=true" : "";
    return this.client.post<LeavePreview>(`/schedules/exceptions/${query}`, input);
  }

  queue(departmentId?: string): Promise<QueueTicket[]> {
    const query = departmentId ? `?department=${encodeURIComponent(departmentId)}` : "";
    return this.client.get<QueueTicket[]>(`/queue/${query}`);
  }

  checkIn(input: {
    patient_id: string;
    department_id?: string;
    appointment_id?: string;
    priority?: number;
  }): Promise<QueueTicket> {
    return this.client.post<QueueTicket>("/queue/check-in/", input);
  }

  callNext(departmentId: string, calledTo = ""): Promise<QueueTicket | { detail: string }> {
    return this.client.post("/queue/call-next/", {
      department_id: departmentId,
      called_to: calledTo,
    });
  }

  setTicket(id: string, action: "serving" | "done" | "skipped" | "left"): Promise<QueueTicket> {
    return this.client.post<QueueTicket>(`/queue/${id}/${action}/`, {});
  }
}
