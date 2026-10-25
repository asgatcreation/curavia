/**
 * Front desk.
 *
 * The busiest screen in any Nigerian hospital and the one that sets the tone
 * for the rest of the system. Three things decided its layout:
 *
 * 1. The clerk's hands are on the keyboard, not the mouse. Search is focused
 *    on load, and the whole screen is usable from the keyboard.
 * 2. The question being answered is always "who is next, and is anything
 *    stopping them". So the queue is the page, not a panel beside something
 *    else, and anything blocking a patient is on the row rather than behind
 *    a click.
 * 3. Money is visible at the desk. A patient with an outstanding balance has
 *    to be seen as such before they are sent through, because catching it at
 *    the cashier at the end is how hospitals lose the money.
 */

import { useMemo, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  SectionTitle,
  Stat,
  ToneBar,
  TRIAGE,
  cn,
  formatNaira,
  formatWait,
  inputClass,
  minutesSince,
  patientAge,
  useNow,
  waitTone,
} from "@curavia/shared";
import type { WaitingPatient } from "@curavia/shared";
import { DEMO_WAITING } from "@curavia/shared";

import { IconWarning } from "../icons";

const STAGES = ["All", "Waiting for triage", "Waiting for doctor", "Waiting for bill"] as const;
type StageFilter = (typeof STAGES)[number];

export function FrontDesk() {
  const [stage, setStage] = useState<StageFilter>("All");
  const [query, setQuery] = useState("");
  // One clock for the whole screen, so every row on it agrees about the time.
  const now = useNow();

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return DEMO_WAITING.filter((patient) => {
      if (stage !== "All" && patient.stage !== stage) return false;
      if (!needle) return true;
      return (
        patient.full_name.toLowerCase().includes(needle) ||
        patient.mrn.toLowerCase().includes(needle) ||
        patient.token.toLowerCase().includes(needle)
      );
    });
  }, [stage, query]);

  const waitingNow = DEMO_WAITING.filter((p) => p.stage !== "With doctor").length;
  const longest = Math.max(...DEMO_WAITING.map((p) => minutesSince(p.arrived_at, now)));
  const owing = DEMO_WAITING.filter((p) => p.balance_kobo > 0);
  const owed = owing.reduce((total, p) => total + p.balance_kobo, 0);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Front desk</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Ikeja · General Outpatient · today
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary">Check in a booking</Button>
          <Button variant="primary">Register a new patient</Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Waiting now" value={String(waitingNow)} tone="info" sub="Across all stages" />
        <Stat
          label="Longest wait"
          value={`${Math.floor(longest / 60)}h ${longest % 60}m`}
          tone={waitTone(longest)}
          sub="Tunde Afolabi · A-019"
        />
        <Stat label="Seen today" value="38" tone="stable" sub="21 HMO · 17 self-pay" />
        <Stat
          label="Outstanding"
          value={formatNaira(owed)}
          tone={owed > 0 ? "warning" : "stable"}
          sub={`${owing.length} patients at the desk`}
        />
      </div>

      <Card className="overflow-hidden">
        <CardHeader
          title="Queue"
          count={rows.length}
          actions={
            <div className="flex items-center gap-2">
              <label className="w-40 sm:w-56">
                <span className="sr-only">Filter the queue</span>
                <input
                  // The clerk starts typing the moment the screen opens.
                  autoFocus
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Name, MRN or token"
                  className={cn(inputClass, "min-h-9 text-sm")}
                />
              </label>
            </div>
          }
        />

        <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-3 py-2 dark:border-slate-800">
          {STAGES.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setStage(option)}
              aria-pressed={stage === option}
              className={cn(
                "min-h-9 shrink-0 rounded-full px-3 text-xs font-semibold whitespace-nowrap transition-colors",
                stage === option
                  ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700",
              )}
            >
              {option}
            </button>
          ))}
        </div>

        {rows.length === 0 ? (
          <EmptyState
            title="Nobody matches that."
            hint="Clear the filter, or register the patient if this is their first visit."
          />
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {rows.map((patient) => (
              <QueueRow key={patient.id} patient={patient} now={now} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function QueueRow({ patient, now }: { patient: WaitingPatient; now: Date }) {
  const minutes = minutesSince(patient.arrived_at, now);
  const tone = waitTone(minutes);
  const triage = patient.triage ? TRIAGE[patient.triage] : null;

  return (
    <li className="relative">
      <ToneBar tone={triage ? triage.tone : "neutral"} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3 py-3 pr-3 pl-5">
        <span className="numeric w-14 shrink-0 text-sm font-bold text-slate-900 dark:text-white">
          {patient.token}
        </span>

        <div className="min-w-0 flex-1 basis-48">
          <p className="truncate font-semibold text-slate-900 dark:text-white">
            {patient.full_name}
          </p>
          <p className="numeric truncate text-xs text-slate-500 dark:text-slate-400">
            {patient.mrn} · {patientAge(patient.date_of_birth)} ·{" "}
            {patient.sex === "F" ? "Female" : "Male"}
          </p>
        </div>

        {/* Fixed width, not flex-1: the reason is free text and a column that
            resizes itself around it makes every row land differently, which
            is exactly what a queue read at a glance must not do. */}
        <p className="hidden w-44 shrink-0 truncate text-sm text-slate-600 xl:block dark:text-slate-300">
          {patient.reason}
        </p>

        <div className="flex flex-wrap items-center gap-2">
          {triage ? (
            <Badge tone={triage.tone}>
              {triage.label} · {triage.meaning}
            </Badge>
          ) : (
            <Badge tone="neutral">Not triaged</Badge>
          )}
          <Badge tone={patient.payer_kind === "Self-pay" ? "neutral" : "info"}>
            {patient.payer}
          </Badge>
          {patient.balance_kobo > 0 && (
            <Badge tone="warning">
              <IconWarning className="size-3.5" />
              Owes {formatNaira(patient.balance_kobo)}
            </Badge>
          )}
        </div>

        <div className="ml-auto flex items-center gap-3">
          <span className="text-right">
            <span
              className={cn(
                "numeric block text-sm font-bold",
                tone === "critical"
                  ? "text-critical"
                  : tone === "warning"
                    ? "text-warning-ink dark:text-warning"
                    : "text-slate-700 dark:text-slate-200",
              )}
            >
              {formatWait(patient.arrived_at, now)}
            </span>
            <span className="block text-[0.6875rem] text-slate-500 dark:text-slate-400">
              {patient.stage}
            </span>
          </span>
          <Button size="sm" variant={patient.stage === "With doctor" ? "ghost" : "secondary"}>
            Open
          </Button>
        </div>
      </div>
    </li>
  );
}
