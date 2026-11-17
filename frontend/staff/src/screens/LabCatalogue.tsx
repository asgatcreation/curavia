/**
 * The laboratory catalogue: the tests, what they measure, and what normal is.
 *
 * Three things decided its shape.
 *
 * **The bands are a list, and the answer is not.** An analyte can carry six
 * reference ranges, and which one a patient is judged against depends on
 * their age and sex. A supervisor reading six rows cannot work out what a
 * six-year-old girl will actually be compared with — so the screen does not
 * ask them to. The "Try a patient" box sends an age and a sex to the server
 * and shows the band it picked and the flag a value would get. That is the
 * only honest way to show it: the rules live on the server, and a second
 * copy of them in here would drift.
 *
 * **Overlap is the error worth designing for.** Two bands that both match
 * one patient would give a result two answers, so the server refuses them.
 * The message names the band it clashed with, and the screen shows it
 * against the form rather than as a toast that disappears — a supervisor
 * needs to read which existing row is in the way.
 *
 * **"From Curavia" is a prompt, not a warning.** The shipped ranges are
 * ordinary intervals. Only this hospital's laboratory knows what its own
 * analyser reports, so unreviewed rows are marked quietly and the count sits
 * at the top. Saving a row clears the mark, because touching it is the
 * review.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiRequestError,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  LaboratoryApi,
  SectionTitle,
  Stat,
  cn,
  inputClass,
} from "@curavia/shared";
import type {
  Analyte,
  LabPanel,
  Level,
  PanelSummary,
  Preview,
  RangeInput,
  RangeSex,
  ReferenceRange,
  StatusTone,
} from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";

const lab = new LaboratoryApi(api);

const DAYS_PER_YEAR = 365.25;

/** How a flag reads and prints. Mirrors MARKERS in apps/laboratory/ranges.py. */
const LEVELS: Record<Level, { label: string; tone: StatusTone }> = {
  normal: { label: "Normal", tone: "stable" },
  low: { label: "Low", tone: "warning" },
  high: { label: "High", tone: "warning" },
  critical_low: { label: "Critically low", tone: "critical" },
  critical_high: { label: "Critically high", tone: "critical" },
  abnormal: { label: "Abnormal", tone: "warning" },
  critical: { label: "Critical", tone: "critical" },
  unknown: { label: "No band applies", tone: "neutral" },
};

const SEXES: { value: RangeSex; label: string }[] = [
  { value: "any", label: "Any" },
  { value: "F", label: "Female" },
  { value: "M", label: "Male" },
];

function message(exc: unknown, fallback: string): string {
  return exc instanceof ApiRequestError ? exc.message : fallback;
}

/** Minutes as a laboratory would promise them: "2 hours", "1 hour", "30 min". */
function turnaround(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = minutes / 60;
  if (!Number.isInteger(hours)) return `${hours.toFixed(1)} hours`;
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

export function LabCatalogue() {
  const { me } = useAuth();
  const [panels, setPanels] = useState<PanelSummary[] | null>(null);
  const [unreviewed, setUnreviewed] = useState(0);
  const [mayEdit, setMayEdit] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const denied = me != null && !me.permissions.includes("lab.order.view");

  const load = useCallback(() => {
    void lab
      .panels()
      .then((result) => {
        setPanels(result.panels);
        setUnreviewed(result.unreviewed_ranges);
        setMayEdit(result.may_edit);
      })
      .catch((exc: unknown) => setError(message(exc, "The catalogue could not be read.")));
  }, []);

  useEffect(load, [load]);

  if (denied) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState title="The laboratory catalogue is not shown to your role." />
        </Card>
      </div>
    );
  }

  const active = panels?.filter((panel) => panel.is_active) ?? [];

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 p-4 sm:p-6">
      <div>
        <SectionTitle>Laboratory catalogue</SectionTitle>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
          What each test measures, and what counts as normal for whom.
        </p>
      </div>

      {error && <p className="text-sm font-medium text-critical">{error}</p>}

      {panels !== null && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="Tests set up" value={String(active.length)} />
          <Stat
            label="Analytes"
            value={String(active.reduce((total, panel) => total + panel.analyte_count, 0))}
          />
          <Stat
            label="Bands not yet reviewed"
            value={String(unreviewed)}
            sub={unreviewed > 0 ? "Curavia's starting values" : "All reviewed here"}
            tone={unreviewed > 0 ? "info" : "stable"}
          />
        </div>
      )}

      {unreviewed > 0 && (
        <Card className="border-l-4 border-l-info">
          <p className="text-sm text-slate-700 dark:text-slate-200">
            Curavia ships ordinary reference intervals so the laboratory can report from day
            one. They are a starting point: your own analyser and method decide the real
            figures, so a supervisor should read through them. Changing a band marks it as
            reviewed.
          </p>
        </Card>
      )}

      {panels === null ? null : panels.length === 0 ? (
        <Card>
          <EmptyState
            title="No laboratory test is set up yet."
            hint="Tests appear here once the laboratory services are added to the price list."
          />
        </Card>
      ) : (
        <div className="space-y-3">
          {panels.map((panel) => (
            <PanelCard
              key={panel.id}
              panel={panel}
              open={openId === panel.id}
              mayEdit={mayEdit}
              onToggle={() => setOpenId((current) => (current === panel.id ? null : panel.id))}
              onChanged={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function PanelCard({
  panel,
  open,
  mayEdit,
  onToggle,
  onChanged,
}: {
  panel: PanelSummary;
  open: boolean;
  mayEdit: boolean;
  onToggle: () => void;
  /** Keeps the counts at the top honest after an edit further down. */
  onChanged: () => void;
}) {
  const [full, setFull] = useState<LabPanel | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    void lab
      .panel(panel.id)
      .then(setFull)
      .catch((exc: unknown) => setError(message(exc, "That test could not be read.")));
  }, [panel.id]);

  useEffect(() => {
    if (open && full === null) reload();
  }, [open, full, reload]);

  return (
    <Card className={cn(!panel.is_active && "opacity-60")}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-start gap-3 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-slate-900 dark:text-slate-50">{panel.name}</span>
            <Badge tone="neutral">{panel.code}</Badge>
            {panel.fasting_required && <Badge tone="warning">Fasting</Badge>}
            {panel.is_send_out && <Badge tone="info">Sent away</Badge>}
          </div>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
            {panel.specimen_label}
            {panel.container && ` · ${panel.container}`}
            {` · ${panel.analyte_count} measured`}
            {` · ${turnaround(panel.turnaround_minutes)}`}
          </p>
        </div>
        <span aria-hidden="true" className="pt-1 text-slate-400">
          {open ? "–" : "+"}
        </span>
      </button>

      {open && (
        <div className="mt-4 space-y-4 border-t border-slate-200 pt-4 dark:border-slate-700">
          {error && <p className="text-sm font-medium text-critical">{error}</p>}
          {full?.handling && (
            <p className="text-sm text-slate-600 dark:text-slate-300">
              <span className="font-medium">Handling:</span> {full.handling}
            </p>
          )}
          {full?.preparation && (
            <p className="text-sm text-slate-600 dark:text-slate-300">
              <span className="font-medium">Tell the patient:</span> {full.preparation}
            </p>
          )}
          {full === null
            ? null
            : full.analytes.length === 0
              ? <EmptyState title="Nothing is measured on this test yet." />
              : full.analytes.map((analyte) => (
                  <AnalyteRow
                    key={analyte.id}
                    analyte={analyte}
                    mayEdit={mayEdit}
                    onChanged={() => {
                      reload();
                      onChanged();
                    }}
                  />
                ))}
        </div>
      )}
    </Card>
  );
}

function AnalyteRow({
  analyte,
  mayEdit,
  onChanged,
}: {
  analyte: Analyte;
  mayEdit: boolean;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [trying, setTrying] = useState(false);

  return (
    <div className="rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-800/50">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-slate-900 dark:text-slate-50">{analyte.name}</span>
        <Badge tone="neutral">{analyte.code}</Badge>
        {analyte.unit && (
          <span className="text-sm text-slate-500 dark:text-slate-400">{analyte.unit}</span>
        )}
        {analyte.value_type !== "numeric" && (
          <Badge tone="info">{analyte.value_type_label}</Badge>
        )}
        {analyte.from_template && <Badge tone="neutral">From Curavia</Badge>}

        <div className="ml-auto flex gap-1.5">
          {analyte.value_type === "numeric" && (
            <Button size="sm" variant="ghost" onClick={() => setTrying((open) => !open)}>
              Try a patient
            </Button>
          )}
          {mayEdit && analyte.value_type === "numeric" && (
            <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
              Add a band
            </Button>
          )}
        </div>
      </div>

      {analyte.value_type === "coded" ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {analyte.options.map((option) => (
            <Badge
              key={option.label}
              tone={
                option.flag === "critical"
                  ? "critical"
                  : option.flag === "abnormal"
                    ? "warning"
                    : "stable"
              }
            >
              {option.label}
            </Badge>
          ))}
        </div>
      ) : analyte.value_type === "text" ? (
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
          Written as a sentence by the scientist, and never flagged by the system.
        </p>
      ) : analyte.reference_ranges.length === 0 ? (
        <p className="mt-2 text-sm text-warning">
          No reference band, so a result here will be reported without a flag.
        </p>
      ) : (
        <ul className="mt-2 space-y-1">
          {analyte.reference_ranges.map((band) => (
            <BandLine
              key={band.id}
              band={band}
              unit={analyte.unit}
              mayEdit={mayEdit}
              onChanged={onChanged}
            />
          ))}
        </ul>
      )}

      {trying && <TryPatient analyte={analyte} />}
      {adding && (
        <BandForm
          analyteId={analyte.id}
          onDone={() => {
            setAdding(false);
            onChanged();
          }}
          onCancel={() => setAdding(false)}
        />
      )}
    </div>
  );
}

function BandLine({
  band,
  unit,
  mayEdit,
  onChanged,
}: {
  band: ReferenceRange;
  unit: string;
  mayEdit: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <li>
        <BandForm
          analyteId={band.analyte}
          band={band}
          onDone={() => {
            setEditing(false);
            onChanged();
          }}
          onCancel={() => setEditing(false)}
        />
      </li>
    );
  }

  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
      <span className="min-w-36 font-medium text-slate-700 dark:text-slate-200">{band.band}</span>
      <span className="text-slate-900 dark:text-slate-50">
        {band.normal || "no normal band"}
        {unit && band.normal && ` ${unit}`}
      </span>
      {band.note && <span className="text-slate-500 dark:text-slate-400">({band.note})</span>}
      {band.critical && <Badge tone="critical">Telephone {band.critical}</Badge>}
      {band.from_template && (
        <span className="text-xs text-slate-400 dark:text-slate-500">not yet reviewed</span>
      )}
      {mayEdit && (
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={() => setEditing(true)}
        >
          Change
        </Button>
      )}
    </li>
  );
}

/** Years in the form, days in the database. Nobody says "4383 days old". */
function yearsToDays(years: string): number | null {
  const value = Number(years);
  if (years.trim() === "" || Number.isNaN(value) || value < 0) return null;
  return Math.round(value * DAYS_PER_YEAR);
}

function daysToYears(days: number | null): string {
  if (days === null) return "";
  return String(Math.round((days / DAYS_PER_YEAR) * 10) / 10);
}

function BandForm({
  analyteId,
  band,
  onDone,
  onCancel,
}: {
  analyteId: string;
  band?: ReferenceRange;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [sex, setSex] = useState<RangeSex>(band?.sex ?? "any");
  const [fromYears, setFromYears] = useState(daysToYears(band?.age_min_days ?? 0));
  const [toYears, setToYears] = useState(daysToYears(band?.age_max_days ?? null));
  const [low, setLow] = useState(band?.low ?? "");
  const [high, setHigh] = useState(band?.high ?? "");
  const [criticalLow, setCriticalLow] = useState(band?.critical_low ?? "");
  const [criticalHigh, setCriticalHigh] = useState(band?.critical_high ?? "");
  const [note, setNote] = useState(band?.note ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = () => {
    const input: RangeInput = {
      sex,
      age_min_days: yearsToDays(fromYears) ?? 0,
      age_max_days: yearsToDays(toYears),
      low: low.trim() || null,
      high: high.trim() || null,
      critical_low: criticalLow.trim() || null,
      critical_high: criticalHigh.trim() || null,
      note: note.trim(),
    };
    setBusy(true);
    setError("");
    const saving = band ? lab.saveRange(band.id, input) : lab.addRange(analyteId, input);
    void saving
      .then(onDone)
      .catch((exc: unknown) => setError(message(exc, "That band could not be saved.")))
      .finally(() => setBusy(false));
  };

  const remove = () => {
    if (!band) return;
    setBusy(true);
    void lab
      .removeRange(band.id)
      .then(onDone)
      .catch((exc: unknown) => setError(message(exc, "That band could not be removed.")))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mt-3 space-y-3 rounded-[var(--radius-control)] bg-white p-3 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Applies to">
          <select
            className={inputClass}
            value={sex}
            onChange={(event) => setSex(event.target.value as RangeSex)}
          >
            {SEXES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="From age (years)" hint="0 for from birth">
          <input
            className={inputClass}
            inputMode="decimal"
            value={fromYears}
            onChange={(event) => setFromYears(event.target.value)}
          />
        </Field>
        <Field label="Up to age (years)" hint="Leave empty for no limit">
          <input
            className={inputClass}
            inputMode="decimal"
            value={toYears}
            onChange={(event) => setToYears(event.target.value)}
          />
        </Field>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Normal from">
          <input
            className={inputClass}
            inputMode="decimal"
            value={low}
            onChange={(event) => setLow(event.target.value)}
          />
        </Field>
        <Field label="Normal to">
          <input
            className={inputClass}
            inputMode="decimal"
            value={high}
            onChange={(event) => setHigh(event.target.value)}
          />
        </Field>
        <Field label="Telephone below" hint="Critically low">
          <input
            className={inputClass}
            inputMode="decimal"
            value={criticalLow}
            onChange={(event) => setCriticalLow(event.target.value)}
          />
        </Field>
        <Field label="Telephone above" hint="Critically high">
          <input
            className={inputClass}
            inputMode="decimal"
            value={criticalHigh}
            onChange={(event) => setCriticalHigh(event.target.value)}
          />
        </Field>
      </div>

      <Field label="Note" hint="Printed with the band, e.g. fasting">
        <input
          className={inputClass}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </Field>

      {/* Shown against the form, not as a toast: the message names the band
          it clashed with, and that has to stay on screen to be read. */}
      {error && <p className="text-sm font-medium text-critical">{error}</p>}

      <div className="flex flex-wrap gap-2">
        <Button variant="primary" size="sm" onClick={submit} disabled={busy}>
          {band ? "Save the band" : "Add the band"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        {band && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto text-critical"
            onClick={remove}
            disabled={busy}
          >
            Remove
          </Button>
        )}
      </div>
    </div>
  );
}

function TryPatient({ analyte }: { analyte: Analyte }) {
  const [years, setYears] = useState("30");
  const [sex, setSex] = useState<RangeSex>("F");
  const [value, setValue] = useState("");
  const [result, setResult] = useState<Preview | null>(null);
  const [error, setError] = useState("");

  // Asks the server on every change, because the age and sex rules live
  // there. Re-implementing them here is how the screen would start
  // disagreeing with the flag on a real result.
  useEffect(() => {
    const days = yearsToDays(years);
    let current = true;
    void lab
      .preview(analyte.id, { age_days: days, sex, value })
      .then((answer) => {
        if (current) {
          setResult(answer);
          setError("");
        }
      })
      .catch((exc: unknown) => {
        if (current) setError(message(exc, "That could not be worked out."));
      });
    return () => {
      current = false;
    };
  }, [analyte.id, years, sex, value]);

  const level = result?.level ? LEVELS[result.level] : null;

  return (
    <div className="mt-3 space-y-3 rounded-[var(--radius-control)] bg-white p-3 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
      <p className="text-sm text-slate-600 dark:text-slate-300">
        Which band a patient would be judged against, and what a result would be flagged as.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Age (years)">
          <input
            className={inputClass}
            inputMode="decimal"
            value={years}
            onChange={(event) => setYears(event.target.value)}
          />
        </Field>
        <Field label="Sex">
          <select
            className={inputClass}
            value={sex}
            onChange={(event) => setSex(event.target.value as RangeSex)}
          >
            <option value="F">Female</option>
            <option value="M">Male</option>
            <option value="any">Not recorded</option>
          </select>
        </Field>
        <Field label={`Result${analyte.unit ? ` (${analyte.unit})` : ""}`}>
          <input
            className={inputClass}
            inputMode="decimal"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </Field>
      </div>

      {error && <p className="text-sm font-medium text-critical">{error}</p>}

      {result && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {result.applies ? (
            <>
              <span className="font-medium text-slate-700 dark:text-slate-200">
                {result.applies.band}
              </span>
              <span className="text-slate-900 dark:text-slate-50">{result.describes}</span>
            </>
          ) : (
            <span className="text-warning">
              No band covers that patient, so a result would print with no flag.
            </span>
          )}
          {level && value.trim() !== "" && (
            <Badge tone={level.tone}>
              {level.label}
              {result.marker && ` (${result.marker})`}
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
