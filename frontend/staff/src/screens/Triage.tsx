/**
 * Triage.
 *
 * The screen a nurse works from all shift, with forty people waiting. Three
 * things decided its shape:
 *
 * **The queue is the page.** The question is always "who do I see next", and
 * that is a list ordered by urgency, with anyone past their own colour's
 * target called out. A green patient at ninety minutes is fine and a red one
 * at five is a crisis, so the wait is shown against the promise rather than
 * on its own.
 *
 * **The score appears as the numbers are typed.** Not after a save. A nurse
 * entering a pulse of 38 should see the warning while her hands are still on
 * the keyboard, which is why there is a preview endpoint that stores nothing.
 *
 * **A score that must not be acted on is never shown as a number.** NEWS2 is
 * not valid under 16 or in pregnancy, and the server says so rather than
 * sending a misleading 0. This screen prints that sentence where the score
 * would have been.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  SectionTitle,
  Stat,
  StatusDot,
  TRIAGE,
  cn,
  inputClass,
  useNow,
} from "@curavia/shared";
import type {
  Observation,
  ObservationInput,
  Score,
  TriageCode,
  TriageRow,
} from "@curavia/shared";
import { ApiRequestError, ClinicalApi } from "@curavia/shared";

import { api } from "../api";
import { IconWarning } from "../icons";

const clinical = new ClinicalApi(api);

/** The seven NEWS2 parameters, in the order a nurse takes them. */
const VITALS: {
  name: keyof ObservationInput;
  label: string;
  unit: string;
  step?: string;
}[] = [
  { name: "respiration_rate", label: "Breaths / min", unit: "" },
  { name: "spo2", label: "SpO₂", unit: "%" },
  { name: "systolic_bp", label: "BP, upper", unit: "mmHg" },
  { name: "diastolic_bp", label: "BP, lower", unit: "mmHg" },
  { name: "pulse", label: "Pulse", unit: "bpm" },
  { name: "temperature", label: "Temperature", unit: "°C", step: "0.1" },
  { name: "blood_glucose", label: "Glucose", unit: "mg/dL", step: "0.1" },
];

const CONSCIOUSNESS: { value: string; label: string }[] = [
  { value: "A", label: "Alert" },
  { value: "C", label: "New confusion" },
  { value: "V", label: "Responds to voice" },
  { value: "P", label: "Responds to pain" },
  { value: "U", label: "Unresponsive" },
];

const BAND_TONE: Record<string, "stable" | "warning" | "urgent" | "critical" | "neutral"> = {
  routine: "stable",
  low: "warning",
  medium: "urgent",
  high: "critical",
  not_applicable: "neutral",
};

export function Triage() {
  const [rows, setRows] = useState<TriageRow[] | null>(null);
  const [open, setOpen] = useState<TriageRow | null>(null);
  const [denied, setDenied] = useState(false);
  const now = useNow(30_000);

  const load = useCallback(() => {
    void clinical
      .waitingRoom()
      .then((result) => setRows(result.rows))
      .catch(() => setDenied(true));
  }, []);

  useEffect(load, [load]);

  if (denied) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState title="Triage is not shown to your role." />
        </Card>
      </div>
    );
  }

  const overdue = (rows ?? []).filter((row) => row.is_overdue);
  const flagged = (rows ?? []).filter((row) => row.news2_red_flag || (row.news2 ?? 0) >= 5);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Triage</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Waiting to be seen, most urgent first
          </p>
        </div>
        <Button variant="secondary" onClick={load}>
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Waiting" value={String((rows ?? []).length)} tone="info" />
        <Stat
          label="Past their target"
          value={String(overdue.length)}
          tone={overdue.length ? "critical" : "stable"}
          sub="Against their own colour"
        />
        <Stat
          label="Scoring 5 or more"
          value={String(flagged.length)}
          tone={flagged.length ? "urgent" : "stable"}
          sub="Or one reading at 3"
        />
        <Stat
          label="Red and orange"
          value={String(
            (rows ?? []).filter((row) => row.category === "red" || row.category === "orange")
              .length,
          )}
          tone="warning"
        />
      </div>

      {open ? (
        <TriageForm row={open} onDone={() => { setOpen(null); load(); }} onCancel={() => setOpen(null)} />
      ) : (
        <Card className="overflow-hidden">
          <CardHeader title="Waiting" count={rows?.length ?? undefined} />
          {rows === null ? (
            <EmptyState title="Loading…" />
          ) : rows.length === 0 ? (
            <EmptyState
              title="Nobody is waiting for triage."
              hint="Patients appear here once they have been checked in at the front desk."
            />
          ) : (
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {rows.map((row) => (
                <QueueRow key={row.id} row={row} now={now} onOpen={() => setOpen(row)} />
              ))}
            </ul>
          )}
        </Card>
      )}

      {!open && rows !== null && rows.length === 0 && <UntriagedHint />}
    </div>
  );
}

function UntriagedHint() {
  return (
    <Card className="p-4">
      <p className="text-sm text-slate-600 dark:text-slate-300">
        A patient only appears here once somebody has assessed them. To triage a new arrival,
        open them from the journey board and record their observations.
      </p>
    </Card>
  );
}

function QueueRow({
  row,
  now,
  onOpen,
}: {
  row: TriageRow;
  now: Date;
  onOpen: () => void;
}) {
  const triage = TRIAGE[row.category];
  // Recomputed here rather than trusting the number that arrived: a screen
  // left open for an hour would otherwise show the wait as it was.
  const minutes =
    row.minutes_waiting +
    Math.max(Math.floor((now.getTime() - Date.parse(row.assessed_at)) / 60000) - row.minutes_waiting, 0);
  const overdue = row.target_minutes !== null && minutes > row.target_minutes;

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
      <Badge tone={triage.tone}>
        {triage.label} · {triage.meaning}
      </Badge>

      <span className="min-w-0 flex-1 basis-48">
        <span className="block font-semibold text-slate-900 dark:text-white">
          {row.patient_name}
        </span>
        <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
          {row.mrn} · {row.complaint}
        </span>
      </span>

      {row.news2 !== null && (
        <Badge tone={row.news2_red_flag || row.news2 >= 5 ? "critical" : row.news2 >= 1 ? "warning" : "stable"}>
          NEWS2 {row.news2}
          {row.news2_red_flag ? " · one reading at 3" : ""}
        </Badge>
      )}

      <span className="ml-auto flex items-center gap-3">
        <span className="text-right">
          <span
            className={cn(
              "numeric block text-sm font-bold",
              overdue ? "text-critical" : "text-slate-700 dark:text-slate-200",
            )}
          >
            {minutes}m
          </span>
          {/* The wait against the promise, not on its own. */}
          <span className="block text-[0.6875rem] text-slate-500 dark:text-slate-400">
            {row.target_minutes === null ? "no target" : `target ${row.target_minutes}m`}
          </span>
        </span>
        {overdue && <IconWarning className="size-4 shrink-0 text-critical" />}
        <Button size="sm" variant="secondary" onClick={onOpen}>
          Observations
        </Button>
      </span>
    </li>
  );
}

/**
 * Record a set of vitals, with the score appearing as they are typed.
 */
function TriageForm({
  row,
  onDone,
  onCancel,
}: {
  row: TriageRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [onOxygen, setOnOxygen] = useState<boolean | null>(null);
  const [consciousness, setConsciousness] = useState("");
  const [scale2, setScale2] = useState(false);
  const [score, setScore] = useState<Score | null>(null);
  const [category, setCategory] = useState<TriageCode>(row.category);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [saved, setSaved] = useState<Observation | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function build(): ObservationInput {
    const input: ObservationInput = {};
    for (const [key, raw] of Object.entries(values)) {
      if (raw.trim() === "") continue;
      input[key as keyof ObservationInput] = (
        key === "temperature" || key === "blood_glucose" ? raw : Number(raw)
      ) as never;
    }
    if (onOxygen !== null) input.on_oxygen = onOxygen;
    if (consciousness) input.consciousness = consciousness;
    if (scale2) input.oxygen_scale = 2;
    return input;
  }

  // Debounced preview: the score appears while the nurse is still typing,
  // which is the whole point of it being a separate endpoint that writes
  // nothing.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void clinical
        .previewScore(build())
        .then(setScore)
        .catch(() => setScore(null));
    }, 300);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values, onOxygen, consciousness, scale2]);

  function save() {
    setBusy(true);
    setProblem("");
    // The nurse's category is written FIRST, then the observations.
    //
    // The other way round, found by saving a pulse of 38: recording the
    // vitals raises the patient to orange, and the assessment that followed
    // put the nurse's original yellow straight back over it. The server only
    // ever escalates upwards, so letting the score go last means the
    // automatic raise is what survives — and a nurse who chose red still
    // keeps red, because the score never lowers anybody.
    void clinical
      .assess(row.encounter_id, { category, complaint: row.complaint })
      .then(() => clinical.recordObservation(row.encounter_id, build()))
      .then((result) => {
        setSaved(result.observation);
        setScore(result.score);
        onDone();
      })
      .catch((caught: unknown) => {
        setProblem(
          caught instanceof ApiRequestError ? caught.message : "That did not save.",
        );
      })
      .finally(() => setBusy(false));
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card className="overflow-hidden">
        <CardHeader
          title={`${row.patient_name} · ${row.mrn}`}
          actions={
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Back to the queue
            </Button>
          }
        />
        <div className="space-y-4 p-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">{row.complaint}</p>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {VITALS.map((vital) => (
              <Field key={vital.name} label={`${vital.label}${vital.unit ? ` (${vital.unit})` : ""}`}>
                <input
                  type="number"
                  inputMode="decimal"
                  step={vital.step ?? "1"}
                  value={values[vital.name] ?? ""}
                  onChange={(event) =>
                    setValues((current) => ({ ...current, [vital.name]: event.target.value }))
                  }
                  className={cn(inputClass, "numeric")}
                />
              </Field>
            ))}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Air or oxygen">
              <div className="flex gap-2">
                {[
                  { value: false, label: "Air" },
                  { value: true, label: "Oxygen" },
                ].map((option) => (
                  <button
                    key={String(option.value)}
                    type="button"
                    aria-pressed={onOxygen === option.value}
                    onClick={() => setOnOxygen(option.value)}
                    className={cn(
                      "min-h-11 flex-1 rounded-[var(--radius-control)] text-sm font-semibold",
                      onOxygen === option.value
                        ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                        : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
                    )}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Level of consciousness">
              <select
                value={consciousness}
                onChange={(event) => setConsciousness(event.target.value)}
                className={inputClass}
              >
                <option value="">Not recorded</option>
                {CONSCIOUSNESS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          {/* Scale 2 has to be a clinician's choice, once, so it is a
              deliberate switch rather than something inferred. */}
          <label className="flex items-start gap-3 text-sm text-slate-700 dark:text-slate-200">
            <input
              type="checkbox"
              checked={scale2}
              onChange={(event) => setScale2(event.target.checked)}
              className="mt-1 size-4"
            />
            <span>
              Use the COPD oxygen scale (target 88–92%).
              <span className="block text-xs text-slate-500 dark:text-slate-400">
                Only when a doctor has said so. It changes how the saturation is scored.
              </span>
            </span>
          </label>

          <Field label="How urgent are they?">
            <div className="flex flex-wrap gap-2">
              {(Object.keys(TRIAGE) as TriageCode[]).map((code) => (
                <button
                  key={code}
                  type="button"
                  aria-pressed={category === code}
                  onClick={() => setCategory(code)}
                  className={cn(
                    "min-h-11 rounded-full px-4 text-sm font-semibold ring-1 ring-inset",
                    category === code
                      ? "bg-slate-900 text-white ring-slate-900 dark:bg-white dark:text-slate-900 dark:ring-white"
                      : "bg-white text-slate-700 ring-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-600",
                  )}
                >
                  {TRIAGE[code].label} · {TRIAGE[code].meaning}
                </button>
              ))}
            </div>
          </Field>

          {problem && <p className="text-sm font-semibold text-critical">{problem}</p>}

          <Button variant="primary" disabled={busy} onClick={save}>
            {busy ? "Saving…" : "Save and send to the doctor"}
          </Button>
        </div>
      </Card>

      <ScorePanel score={score} saved={saved} />
    </div>
  );
}

function ScorePanel({ score, saved }: { score: Score | null; saved: Observation | null }) {
  if (!score) {
    return (
      <Card>
        <EmptyState title="NEWS2" hint="The score appears as the readings are entered." />
      </Card>
    );
  }

  // A score that must not be acted on is never shown as a number.
  if (!score.usable) {
    return (
      <Card className="p-4 ring-warning/40">
        <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
          NEWS2
        </p>
        <p className="mt-2 flex items-start gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
          <IconWarning className="mt-0.5 size-4 shrink-0 text-warning" />
          {score.reason}
        </p>
      </Card>
    );
  }

  return (
    <Card className="space-y-4 p-4">
      <div>
        <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
          NEWS2
        </p>
        <p className="numeric mt-1 text-5xl font-black text-slate-900 dark:text-white">
          {score.total}
        </p>
        <div className="mt-2">
          <StatusDot tone={BAND_TONE[score.band] ?? "neutral"} label={score.response} />
        </div>
      </div>

      {/* A single reading at 3 escalates whatever the total says, so it is
          called out rather than left to be worked out from the list. */}
      {score.red_flag && (
        <p className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-semibold text-critical-ink dark:bg-critical/15 dark:text-critical">
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          One reading scores 3 on its own. This needs an urgent review even though the total
          is low.
        </p>
      )}

      {!score.is_complete && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Not yet measured: {score.missing.join(", ")}. The score is incomplete until they are.
        </p>
      )}

      <ul className="space-y-1">
        {score.parameters.map((parameter) => (
          <li
            key={parameter.name}
            className="flex items-baseline justify-between gap-3 text-sm"
          >
            <span className="text-slate-600 dark:text-slate-300">{parameter.label}</span>
            <span className="flex items-baseline gap-2">
              <span className="numeric text-slate-900 dark:text-white">{parameter.value}</span>
              <span
                className={cn(
                  "numeric w-5 rounded text-center text-xs font-bold",
                  parameter.score >= 3
                    ? "bg-critical text-white"
                    : parameter.score > 0
                      ? "bg-warning-soft text-warning-ink dark:bg-warning/20 dark:text-warning"
                      : "text-slate-400",
                )}
              >
                {parameter.score}
              </span>
            </span>
          </li>
        ))}
      </ul>

      {saved?.bmi && (
        <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
            BMI
          </p>
          <p className="numeric text-lg font-bold text-slate-900 dark:text-white">
            {saved.bmi.value} {saved.bmi.category && <span className="text-sm font-medium">· {saved.bmi.category}</span>}
          </p>
          {saved.bmi.caveat && (
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{saved.bmi.caveat}</p>
          )}
        </div>
      )}
    </Card>
  );
}
