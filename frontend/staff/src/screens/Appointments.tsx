/**
 * The diary, and booking into it.
 *
 * Laid out around how a Nigerian front desk actually works rather than
 * around a calendar grid:
 *
 * **Today's list is the page.** The question at the desk is "who is coming
 * and have they arrived", and that is a list with a check-in button on each
 * row, not a week view somebody has to scan.
 *
 * **Booking is a side panel, not another screen.** The clerk has the patient
 * in front of them and the list behind them, and losing the list to book is
 * how somebody gets booked into the wrong day.
 *
 * **A full slot is shown as full.** Hiding it makes a busy ten o'clock look
 * like a clinic that has no ten o'clock, and the clerk needs to be able to
 * tell the patient the difference.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  SectionTitle,
  Stat,
  cn,
  inputClass,
} from "@curavia/shared";
import type {
  Appointment,
  Clinic,
  DayAvailability,
  PatientSummary,
  Slot,
} from "@curavia/shared";
import { ApiRequestError, PatientsApi, SchedulingApi } from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const scheduling = new SchedulingApi(api);
const patients = new PatientsApi(api);

/** How many days of availability the booking panel asks for at once. */
const WINDOW_DAYS = 14;

const STATUS_TONE: Record<Appointment["status"], "neutral" | "info" | "stable" | "warning"> = {
  booked: "info",
  checked_in: "stable",
  in_progress: "stable",
  completed: "neutral",
  cancelled: "neutral",
  no_show: "warning",
};

const STATUS_LABEL: Record<Appointment["status"], string> = {
  booked: "Booked",
  checked_in: "Arrived",
  in_progress: "With the clinician",
  completed: "Seen",
  cancelled: "Cancelled",
  no_show: "Did not attend",
};

function today(): string {
  // Local date, not toISOString(): the latter is UTC, which in Lagos turns
  // anything before 1am into yesterday.
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate(),
  ).padStart(2, "0")}`;
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-NG", { hour: "2-digit", minute: "2-digit" });
}

export function Appointments() {
  const { me } = useAuth();
  const [day, setDay] = useState(today());
  const [clinics, setClinics] = useState<Clinic[]>([]);
  const [rows, setRows] = useState<Appointment[] | null>(null);
  const [booking, setBooking] = useState(false);
  const [notice, setNotice] = useState("");

  const mayBook = me?.permissions.includes("appointment.book") ?? false;
  const mayQueue = me?.permissions.includes("queue.manage") ?? false;

  const load = useCallback(() => {
    void scheduling
      .appointments({ day })
      .then(setRows)
      .catch(() => setRows([]));
  }, [day]);

  useEffect(load, [load]);

  useEffect(() => {
    void scheduling.clinics().then(setClinics).catch(() => setClinics([]));
  }, []);

  const live = (rows ?? []).filter((row) => row.status !== "cancelled");
  const arrived = live.filter((row) => row.checked_in_at).length;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Appointments</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            {new Date(`${day}T00:00:00`).toLocaleDateString("en-NG", {
              weekday: "long",
              day: "numeric",
              month: "long",
              year: "numeric",
            })}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label>
            <span className="sr-only">Which day</span>
            <input
              type="date"
              value={day}
              onChange={(event) => setDay(event.target.value)}
              className={cn(inputClass, "min-h-11 w-44 text-sm")}
            />
          </label>
          {mayBook && (
            <Button variant="primary" onClick={() => setBooking((open) => !open)}>
              {booking ? "Close" : "Book an appointment"}
            </Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Booked" value={String(live.length)} tone="info" sub="Not counting cancelled" />
        <Stat
          label="Arrived"
          value={String(arrived)}
          tone={arrived ? "stable" : "neutral"}
          sub={`${Math.max(live.length - arrived, 0)} still to come`}
        />
        <Stat
          label="Did not attend"
          value={String((rows ?? []).filter((row) => row.status === "no_show").length)}
          tone="warning"
          sub="Marked an hour after the slot"
        />
        <Stat label="Clinics running" value={String(clinics.length)} tone="neutral" />
      </div>

      {notice && (
        <Card className="p-4 ring-stable/40">
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{notice}</p>
        </Card>
      )}

      {booking && mayBook && (
        <BookingPanel
          clinics={clinics}
          onBooked={(appointment) => {
            setNotice(
              `${appointment.patient_name} is booked for ${new Date(
                appointment.starts_at,
              ).toLocaleDateString("en-NG", {
                weekday: "long",
                day: "numeric",
                month: "long",
              })} at ${time(appointment.starts_at)}.`,
            );
            setBooking(false);
            setDay(appointment.starts_at.slice(0, 10));
            load();
          }}
        />
      )}

      <Card className="overflow-hidden">
        <CardHeader title="The day" count={rows?.length ?? undefined} />
        {rows === null ? (
          <EmptyState title="Loading the diary…" />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Nothing is booked for this day."
            hint={
              mayBook
                ? "Walk-ins do not need an appointment — check them in from the front desk."
                : undefined
            }
          />
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {rows.map((row) => (
              <AppointmentRow
                key={row.id}
                appointment={row}
                mayQueue={mayQueue}
                onChanged={load}
              />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function AppointmentRow({
  appointment,
  mayQueue,
  onChanged,
}: {
  appointment: Appointment;
  mayQueue: boolean;
  onChanged: () => void;
}) {
  const { me } = useAuth();
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState("");

  const mayCancel = me?.permissions.includes("appointment.cancel") ?? false;
  const open = appointment.status === "booked";

  function checkIn() {
    setBusy(true);
    setProblem("");
    void scheduling
      .checkIn({ patient_id: appointment.patient, appointment_id: appointment.id })
      .then(onChanged)
      .catch((caught: unknown) => {
        setProblem(
          caught instanceof ApiRequestError ? caught.message : "They could not be checked in.",
        );
      })
      .finally(() => setBusy(false));
  }

  function cancel() {
    setBusy(true);
    void scheduling
      .cancel(appointment.id, reason.trim())
      .then(() => {
        setCancelling(false);
        setReason("");
        onChanged();
      })
      .catch((caught: unknown) => {
        setProblem(
          caught instanceof ApiRequestError ? caught.message : "It could not be cancelled.",
        );
      })
      .finally(() => setBusy(false));
  }

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-3 p-4">
      <span className="numeric w-16 shrink-0 text-sm font-bold text-slate-900 dark:text-white">
        {time(appointment.starts_at)}
      </span>

      <span className="min-w-0 flex-1 basis-48">
        <span className="block font-semibold text-slate-900 dark:text-white">
          {appointment.patient_name}
        </span>
        <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
          {appointment.patient_mrn}
          {appointment.staff_name ? ` · ${appointment.staff_name}` : ""}
        </span>
      </span>

      <span className="hidden min-w-0 basis-40 truncate text-sm text-slate-600 xl:block dark:text-slate-300">
        {appointment.reason || appointment.clinic_name}
      </span>

      <span className="flex flex-wrap items-center gap-2">
        <Badge tone={STATUS_TONE[appointment.status]}>{STATUS_LABEL[appointment.status]}</Badge>
        {appointment.ticket_label && <Badge tone="neutral">{appointment.ticket_label}</Badge>}
        {appointment.kind === "telemedicine" && <Badge tone="info">Video</Badge>}
      </span>

      <span className="ml-auto flex flex-wrap items-center gap-2">
        {open && mayQueue && (
          <Button size="sm" variant="primary" disabled={busy} onClick={checkIn}>
            {busy ? "…" : "Check in"}
          </Button>
        )}
        {open && mayCancel && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setCancelling((shown) => !shown);
              setReason("");
            }}
          >
            {cancelling ? "Keep it" : "Cancel"}
          </Button>
        )}
      </span>

      {problem && (
        <p className="w-full text-sm font-semibold text-critical">{problem}</p>
      )}

      {cancelling && (
        <div className="w-full space-y-3 border-t border-slate-200 pt-3 dark:border-slate-800">
          <Field
            label="Why is it being cancelled?"
            hint="Somebody may have to ring this patient. Write what they would need to hear."
          >
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Doctor called to theatre — offered Thursday instead"
              className={inputClass}
            />
          </Field>
          <Button variant="danger" disabled={busy || reason.trim().length < 4} onClick={cancel}>
            Cancel this appointment
          </Button>
        </div>
      )}
    </li>
  );
}

/**
 * Find the patient, pick a clinic, pick a slot, book.
 *
 * In that order and on one panel, because that is the order the conversation
 * at the desk happens in and the clerk should never be typing into a screen
 * that has forgotten who they are talking about.
 */
function BookingPanel({
  clinics,
  onBooked,
}: {
  clinics: Clinic[];
  onBooked: (appointment: Appointment) => void;
}) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<PatientSummary[]>([]);
  const [patient, setPatient] = useState<PatientSummary | null>(null);
  const [clinicId, setClinicId] = useState("");
  const [days, setDays] = useState<DayAvailability[] | null>(null);
  const [chosen, setChosen] = useState<Slot | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const clinic = useMemo(
    () => clinics.find((row) => row.id === clinicId) ?? null,
    [clinics, clinicId],
  );

  useEffect(() => {
    if (!clinicId && clinics.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setClinicId(clinics[0]!.id);
    }
  }, [clinics, clinicId]);

  useEffect(() => {
    const text = query.trim();
    if (text.length < 2) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFound([]);
      return;
    }
    const timer = setTimeout(() => {
      void patients.search(text, 6).then(setFound).catch(() => setFound([]));
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  const loadAvailability = useCallback(() => {
    if (!clinicId) return;
    void scheduling
      .availability({ clinicId, from: today(), days: WINDOW_DAYS, includeFull: true })
      .then((result) => setDays(result.days))
      .catch(() => setDays([]));
  }, [clinicId]);

  useEffect(() => {
    // Clearing the chosen slot when the clinic changes is the effect
    // synchronising React with a different clinic: a 10:15 picked at the
    // morning GOPD must not stay selected when the clerk switches to
    // Antenatal, where it may not exist at all.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setChosen(null);
    loadAvailability();
  }, [loadAvailability]);

  function book() {
    if (!patient || !clinic || !chosen) return;
    setBusy(true);
    setProblem("");
    void scheduling
      .book({
        patient_id: patient.id,
        clinic_id: clinic.id,
        schedule_id: chosen.schedule_id,
        starts_at: chosen.starts_at,
        reason: reason.trim(),
      })
      .then(onBooked)
      .catch((caught: unknown) => {
        setProblem(
          caught instanceof ApiRequestError ? caught.message : "That booking could not be made.",
        );
        // 409 means somebody else took it while this panel was open, so the
        // slots on screen are stale. Reload rather than leaving the clerk
        // clicking a slot that no longer exists.
        if (caught instanceof ApiRequestError && caught.status === 409) loadAvailability();
      })
      .finally(() => setBusy(false));
  }

  const openDays = (days ?? []).filter((entry) => entry.slots.length > 0 || entry.closed_because);

  return (
    <Card className="overflow-hidden">
      <CardHeader title="Book an appointment" />
      <div className="space-y-4 p-4">
        {/* 1. Who */}
        {patient ? (
          <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-control)] bg-slate-100 p-3 dark:bg-slate-800">
            <span className="min-w-0 flex-1">
              <span className="block font-semibold text-slate-900 dark:text-white">
                {patient.full_name}
              </span>
              <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                {patient.mrn}
                {patient.age ? ` · ${patient.age}` : ""}
              </span>
            </span>
            <Button size="sm" variant="ghost" onClick={() => setPatient(null)}>
              Somebody else
            </Button>
          </div>
        ) : (
          <>
            <Field label="Who is it for?">
              <input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Name, MRN or phone number"
                className={inputClass}
              />
            </Field>
            {found.length > 0 && (
              <ul className="divide-y divide-slate-200 rounded-[var(--radius-control)] ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
                {found.map((row) => (
                  <li key={row.id} className="flex flex-wrap items-center gap-3 p-3">
                    <span className="min-w-0 flex-1 basis-40">
                      <span className="block font-semibold text-slate-900 dark:text-white">
                        {row.full_name}
                      </span>
                      <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                        {row.mrn}
                        {row.phone ? ` · ${row.phone}` : ""}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      className="ml-auto"
                      onClick={() => {
                        setPatient(row);
                        setQuery("");
                      }}
                    >
                      Choose
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        {/* 2. Which clinic */}
        {patient && (
          <Field label="Which clinic?">
            <select
              value={clinicId}
              onChange={(event) => setClinicId(event.target.value)}
              className={inputClass}
            >
              {clinics.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name} — {row.department_name}
                </option>
              ))}
            </select>
          </Field>
        )}

        {/* 3. Which slot */}
        {patient && clinic && (
          <div className="space-y-3">
            <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              Next {WINDOW_DAYS} days
            </p>
            {days === null ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">Looking…</p>
            ) : openDays.length === 0 ? (
              <p className="text-sm text-slate-600 dark:text-slate-300">
                This clinic has no sessions in the next {WINDOW_DAYS} days.
              </p>
            ) : (
              <ul className="space-y-3">
                {openDays.map((entry) => (
                  <li key={entry.day}>
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                      {new Date(`${entry.day}T00:00:00`).toLocaleDateString("en-NG", {
                        weekday: "long",
                        day: "numeric",
                        month: "short",
                      })}
                    </p>
                    {entry.slots.length === 0 ? (
                      /* Never an empty row. "Dr Eze is on leave" and "no
                         clinic on Wednesdays" mean different things to a
                         patient deciding whether to wait. */
                      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                        {entry.closed_because}
                      </p>
                    ) : (
                      <div className="mt-1 flex flex-wrap gap-2">
                        {entry.slots.map((slot) => {
                          const full = slot.free <= 0;
                          const picked = chosen?.starts_at === slot.starts_at;
                          return (
                            <button
                              key={`${slot.schedule_id}-${slot.starts_at}`}
                              type="button"
                              disabled={full}
                              aria-pressed={picked}
                              /* The label leads with the time.
                               *
                               * A `title` alone becomes the accessible name
                               * and replaces the button's text, so a screen
                               * reader announced "1 of 1 left with Folake
                               * Ilesanmi" and never said which slot it was —
                               * which is the only thing that matters here. */
                              aria-label={
                                full
                                  ? `${time(slot.starts_at)} with ${slot.staff_name} — full, ${slot.capacity} already booked`
                                  : `${time(slot.starts_at)} with ${slot.staff_name} — ${slot.free} of ${slot.capacity} left`
                              }
                              onClick={() => setChosen(slot)}
                              className={cn(
                                "numeric min-h-11 rounded-[var(--radius-control)] px-3 text-sm font-semibold transition-colors",
                                full
                                  ? "cursor-not-allowed bg-slate-100 text-slate-400 line-through dark:bg-slate-800 dark:text-slate-600"
                                  : picked
                                    ? "bg-brand-600 text-white dark:bg-brand-500 dark:text-slate-950"
                                    : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700",
                              )}
                            >
                              {time(slot.starts_at)}
                              {slot.capacity > 1 && !full && (
                                <span className="ml-1 text-[0.6875rem] font-normal opacity-70">
                                  {slot.free} left
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* 4. Book */}
        {patient && chosen && (
          <div className="space-y-3 border-t border-slate-200 pt-4 dark:border-slate-800">
            <Field label="What is it for?" hint="Optional. It shows on the clinic list.">
              <input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Review after malaria treatment"
                className={inputClass}
              />
            </Field>
            {problem && (
              <p className="flex items-center gap-2 text-sm font-semibold text-critical">
                <IconWarning className="size-4 shrink-0" />
                {problem}
              </p>
            )}
            <Button variant="primary" disabled={busy} onClick={book}>
              {busy
                ? "Booking…"
                : `Book ${patient.full_name} for ${time(chosen.starts_at)} with ${chosen.staff_name}`}
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
