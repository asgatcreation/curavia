/**
 * The laboratory worklist — live.
 *
 * Four lists, and which one you look at depends on what you do. A nurse
 * works the **collection** list: who needs blood taking, and how many tubes.
 * The laboratory works the **inbox**: tubes that have been drawn and are on
 * their way, which it books in or refuses. Everyone can see what is **on the
 * bench** and the **rejection log**.
 *
 * Three things this screen deliberately does not decide.
 *
 * **How many tubes to draw.** The server groups the tests by specimen and
 * says "two tubes: one EDTA, one plain". A patient should be stuck as few
 * times as the tests allow, and working that out in the browser would drift
 * from the catalogue the moment a supervisor edited it.
 *
 * **What is late.** `overdue` comes from the server, computed against its
 * own clock. A tablet on a ward with the wrong date must not be what decides
 * a result is late.
 *
 * **What a role may do.** The buttons follow `may_collect` and `may_receive`,
 * but the server checks again on every action. The split is the point: a ward
 * may draw and label a sample, and only the laboratory may say it arrived.
 *
 * **On the connection.** A worklist that has silently stopped updating is
 * worse than none, because people believe it — a nurse glances at a quiet
 * screen and concludes there is nothing to draw. So the state is in the
 * header, the age of the data is shown in words, and every nudge from the
 * server makes the screen re-read the whole list rather than patch it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  useNow,
} from "@curavia/shared";
import type {
  LabRequestRow,
  LiveState,
  RejectionReason,
  Sample,
  StatusTone,
  Tube,
  Worklist,
} from "@curavia/shared";
import {
  LabWorklistApi,
  LiveConnection,
  REJECTION_REASONS,
  SERIOUS_REJECTIONS,
  connectLabWorklist,
} from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconTube, IconWarning } from "../icons";

const lab = new LabWorklistApi(api);

/** Data older than this is called stale in the header, loudly. */
const STALE_SECONDS = 60;

const STATE_TONE: Record<LiveState, StatusTone> = {
  live: "stable",
  connecting: "warning",
  offline: "critical",
};

const STATE_LABEL: Record<LiveState, string> = {
  live: "Live",
  connecting: "Connecting…",
  offline: "Not connected",
};

const PRIORITY_TONE: Record<string, StatusTone> = {
  stat: "critical",
  urgent: "urgent",
  routine: "neutral",
};

const PRIORITY_LABEL: Record<string, string> = {
  stat: "Now",
  urgent: "Urgent",
  routine: "Routine",
};

type TabKey = "collect" | "inbox" | "bench" | "rejected";

const TABS: { key: TabKey; label: string; short: string }[] = [
  { key: "collect", label: "To collect", short: "Collect" },
  { key: "inbox", label: "On the way", short: "Inbox" },
  { key: "bench", label: "At the laboratory", short: "Bench" },
  { key: "rejected", label: "Rejected", short: "Rejected" },
];

export function LabWorklist() {
  const { me } = useAuth();
  const [data, setData] = useState<Worklist | null>(null);
  const [state, setState] = useState<LiveState>("connecting");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [denied, setDenied] = useState(false);
  const [tab, setTab] = useState<TabKey>("collect");
  const [error, setError] = useState("");
  /** The patient whose collection drawer is open. */
  const [drawing, setDrawing] = useState<string | null>(null);
  /** The sample being rejected. */
  const [rejecting, setRejecting] = useState<Sample | null>(null);
  const connection = useRef<LiveConnection | null>(null);
  const now = useNow(15_000);

  const reload = useCallback(() => {
    void lab
      .worklist()
      .then((result) => {
        setData(result);
        setUpdatedAt(new Date());
      })
      .catch(() => setDenied(true));
  }, []);

  useEffect(() => {
    reload();

    const live = connectLabWorklist(api, {
      onState: (next) => {
        setState(next);
        // Coming back from a drop: re-read rather than trust what is drawn.
        if (next === "live") reload();
      },
      // Every message is a nudge. See the consumer for why.
      onMessage: (message) => {
        if (message["type"] === "changed") reload();
      },
    });
    connection.current = live;
    return () => {
      live.close();
      connection.current = null;
    };
  }, [reload]);

  const staleSeconds = updatedAt
    ? Math.max(Math.floor((now.getTime() - updatedAt.getTime()) / 1000), 0)
    : null;
  const isStale = state !== "live" || (staleSeconds !== null && staleSeconds > STALE_SECONDS);

  /**
   * The collection list, one entry per patient rather than per test.
   *
   * A nurse walks to a person, not to a test. Three tests on one patient is
   * one trip with two tubes, and a list that showed three rows would be
   * three trips as far as anybody reading it could tell.
   */
  const byPatient = useMemo(() => {
    const map = new Map<string, LabRequestRow[]>();
    for (const row of data?.awaiting_collection ?? []) {
      map.set(row.patient, [...(map.get(row.patient) ?? []), row]);
    }
    return [...map.entries()];
  }, [data]);

  if (denied) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState
            title="The laboratory worklist is not shown to your role."
            hint="Ask whoever administers Curavia here if you need it."
          />
        </Card>
      </div>
    );
  }

  const counts = data?.counts;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Laboratory worklist</SectionTitle>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {me?.organisation?.name ?? "This hospital"} — what has to be drawn, what is on its
            way, and what the bench is working on.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={STATE_TONE[state]}>{STATE_LABEL[state]}</Badge>
          {staleSeconds !== null && (
            <span
              className={cn(
                "text-xs",
                isStale
                  ? "font-semibold text-critical"
                  : "text-slate-500 dark:text-slate-400",
              )}
            >
              {isStale ? "Last updated " : "Updated "}
              {staleSeconds < 60
                ? `${staleSeconds}s ago`
                : `${Math.floor(staleSeconds / 60)} min ago`}
            </span>
          )}
          <Button size="sm" onClick={reload}>
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <Card className="border-l-4 border-critical p-3">
          <p className="flex items-start gap-2 text-sm text-critical-ink dark:text-critical">
            <IconWarning className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </p>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {/* The tile counts tests and the list below counts people, so the
         * tile says which is which. Without it "5" above a list of four
         * names reads as a bug. */}
        <Stat
          label="To collect"
          value={String(counts?.awaiting_collection ?? 0)}
          sub={collectSub(counts?.awaiting_collection ?? 0, byPatient.length, counts?.redraws ?? 0)}
          tone={counts?.awaiting_collection ? "warning" : "stable"}
        />
        <Stat label="On the way" value={String(counts?.in_transit ?? 0)} tone="info" />
        <Stat label="At the laboratory" value={String(counts?.in_laboratory ?? 0)} tone="info" />
        <Stat
          label="Past due"
          value={String(counts?.overdue ?? 0)}
          tone={counts?.overdue ? "critical" : "stable"}
        />
      </div>

      <div
        role="tablist"
        aria-label="Worklist sections"
        className="flex gap-1 overflow-x-auto rounded-[var(--radius-card)] bg-slate-100 p-1 dark:bg-slate-900"
      >
        {TABS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            role="tab"
            aria-selected={tab === entry.key}
            onClick={() => setTab(entry.key)}
            className={cn(
              "min-h-10 flex-1 rounded-[var(--radius-control)] px-3 text-sm font-semibold whitespace-nowrap",
              tab === entry.key
                ? "bg-white text-slate-900 shadow-sm dark:bg-slate-800 dark:text-white"
                : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white",
            )}
          >
            <span className="hidden sm:inline">{entry.label}</span>
            <span className="sm:hidden">{entry.short}</span>
          </button>
        ))}
      </div>

      {tab === "collect" && (
        <Card>
          <CardHeader title="Waiting for a sample" count={byPatient.length} />
          {byPatient.length === 0 ? (
            <EmptyState
              title="Nothing to draw."
              hint="Tests appear here the moment a doctor signs the order."
            />
          ) : (
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {byPatient.map(([patientId, rows]) => (
                <CollectionRow
                  key={patientId}
                  rows={rows}
                  mayCollect={data?.may_collect ?? false}
                  open={drawing === patientId}
                  onToggle={() => setDrawing(drawing === patientId ? null : patientId)}
                  onDone={() => {
                    setDrawing(null);
                    setError("");
                    reload();
                  }}
                  onError={setError}
                />
              ))}
            </ul>
          )}
        </Card>
      )}

      {tab === "inbox" && (
        <Card>
          <CardHeader title="Drawn, not yet received" count={data?.in_transit.length} />
          {(data?.in_transit.length ?? 0) === 0 ? (
            <EmptyState
              title="No tubes on the way."
              hint="A sample appears here as soon as it is collected on a ward."
            />
          ) : (
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {data?.in_transit.map((sample) => (
                <InboxRow
                  key={sample.id}
                  sample={sample}
                  mayReceive={data.may_receive}
                  mayPrint={data.may_collect}
                  onReceive={() => {
                    setError("");
                    void lab
                      .receive(sample.id)
                      .then(reload)
                      .catch((exc: unknown) => setError(messageFrom(exc)));
                  }}
                  onReject={() => setRejecting(sample)}
                  onError={setError}
                />
              ))}
            </ul>
          )}
        </Card>
      )}

      {tab === "bench" && (
        <Card>
          <CardHeader title="At the laboratory" count={data?.in_laboratory.length} />
          {(data?.in_laboratory.length ?? 0) === 0 ? (
            <EmptyState
              title="Nothing on the bench."
              hint="Tests appear here once their tube has been booked in."
            />
          ) : (
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {data?.in_laboratory.map((row) => (
                <BenchRow key={row.id} row={row} />
              ))}
            </ul>
          )}
        </Card>
      )}

      {tab === "rejected" && (
        <Card>
          <CardHeader title="Rejection log" count={data?.rejections.length} />
          {(data?.rejections.length ?? 0) === 0 ? (
            <EmptyState
              title="No rejections."
              hint="The pattern over a month is what this list is for."
            />
          ) : (
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {data?.rejections.map((sample) => (
                <RejectionRow key={sample.id} sample={sample} />
              ))}
            </ul>
          )}
        </Card>
      )}

      {rejecting && (
        <RejectDialog
          sample={rejecting}
          onClose={() => setRejecting(null)}
          onDone={() => {
            setRejecting(null);
            setError("");
            reload();
          }}
          onError={setError}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// The collection list
// --------------------------------------------------------------------------

function CollectionRow({
  rows,
  mayCollect,
  open,
  onToggle,
  onDone,
  onError,
}: {
  rows: LabRequestRow[];
  mayCollect: boolean;
  open: boolean;
  onToggle: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  // The answer is stored with the patient it is about, rather than cleared
  // and refilled. Two people are opened one after the other on a slow phone
  // and the first reply can land second; tying the tubes to a patient means
  // a late answer for somebody else is ignored instead of being drawn under
  // the wrong name.
  const [loaded, setLoaded] = useState<{ patient: string; tubes: Tube[] } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const first = rows[0];
  const patientId = first?.patient ?? "";

  // Every hook before the early return below: React matches hooks up by the
  // order they are called in, so a render that skipped one would shift the
  // state of every hook after it.
  useEffect(() => {
    if (!open || !patientId) return;
    void lab
      .tubesFor(patientId)
      .then((rows_) => setLoaded({ patient: patientId, tubes: rows_ }))
      .catch(() => setLoaded({ patient: patientId, tubes: [] }));
  }, [open, patientId]);

  if (!first) return null;

  const tubes = loaded && loaded.patient === patientId ? loaded.tubes : null;

  const redraws = rows.filter((row) => row.rejection_count > 0);
  const late = rows.filter((row) => row.overdue);

  const collect = () => {
    setBusy(true);
    void lab
      .collect({ requests: rows.map((row) => row.id), note })
      .then(async ({ samples }) => {
        // Straight to the labels: the tube has to be labelled at the
        // bedside, before the nurse walks away from the patient.
        await openLabels(samples.map((sample) => sample.accession));
        onDone();
      })
      .catch((exc: unknown) => onError(messageFrom(exc)))
      .finally(() => setBusy(false));
  };

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-semibold text-slate-900 dark:text-white">
            {first.patient_name}
            <span className="numeric text-xs font-normal text-slate-500 dark:text-slate-400">
              {first.mrn}
            </span>
            {redraws.length > 0 && <Badge tone="warning">Redraw</Badge>}
            {late.length > 0 && <Badge tone="critical">Past due</Badge>}
          </p>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {rows.map((row) => row.code).join(", ")}
            {" — "}
            {tubeCount(rows)}
          </p>
        </div>
        <Button size="sm" variant={open ? "secondary" : "primary"} onClick={onToggle}>
          {open ? "Close" : "Collect"}
        </Button>
      </div>

      {open && (
        <div className="mt-3 space-y-3 rounded-[var(--radius-card)] bg-slate-50 p-3 dark:bg-slate-950/40">
          {tubes === null ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">Working out the tubes…</p>
          ) : (
            <ol className="space-y-2">
              {tubes.map((tube, index) => (
                <li
                  key={tube.specimen}
                  className="flex items-start gap-3 rounded-[var(--radius-control)] bg-white p-3 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800"
                >
                  <span className="numeric mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-bold text-brand-800 dark:bg-brand-900 dark:text-brand-100">
                    {index + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-semibold text-slate-900 dark:text-white">
                      {tube.specimen_label}
                    </span>
                    {tube.container && (
                      <span className="block text-sm text-slate-600 dark:text-slate-400">
                        {tube.container}
                      </span>
                    )}
                    <span className="block text-xs text-slate-500 dark:text-slate-400">
                      {tube.panels.map((panel) => panel.code).join(", ")}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}

          {rows.some((row) => row.is_send_out) && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              One of these is sent away to another laboratory.
            </p>
          )}

          <Field label="Note for the laboratory" hint="Optional. Difficult draw, on a drip, fasting.">
            <input
              className={inputClass}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={300}
            />
          </Field>

          {mayCollect ? (
            <Button variant="primary" onClick={collect} disabled={busy || tubes === null}>
              <IconTube className="size-4" />
              {busy ? "Recording…" : `Collected — print ${tubes?.length ?? 0} label${(tubes?.length ?? 0) === 1 ? "" : "s"}`}
            </Button>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Your role does not collect samples.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

// --------------------------------------------------------------------------
// The laboratory's inbox
// --------------------------------------------------------------------------

function InboxRow({
  sample,
  mayReceive,
  mayPrint,
  onReceive,
  onReject,
  onError,
}: {
  sample: Sample;
  mayReceive: boolean;
  mayPrint: boolean;
  onReceive: () => void;
  onReject: () => void;
  onError: (message: string) => void;
}) {
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="numeric font-bold text-slate-900 dark:text-white">
              {sample.accession}
            </span>
            {sample.replaces && <Badge tone="warning">Replaces {sample.replaces}</Badge>}
          </p>
          <p className="mt-0.5 text-sm font-semibold text-slate-800 dark:text-slate-100">
            {sample.patient_name}{" "}
            <span className="numeric text-xs font-normal text-slate-500 dark:text-slate-400">
              {sample.mrn}
            </span>
          </p>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {sample.specimen_label}
            {sample.container ? ` — ${sample.container}` : ""} · {sample.panel_names}
          </p>
          {sample.collection_note && (
            <p className="mt-1 text-xs text-slate-500 italic dark:text-slate-400">
              “{sample.collection_note}”
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {mayPrint && (
            <Button
              size="sm"
              onClick={() => {
                void openLabels([sample.accession]).catch((exc: unknown) =>
                  onError(messageFrom(exc)),
                );
              }}
            >
              Label
            </Button>
          )}
          {mayReceive ? (
            <>
              <Button size="sm" variant="primary" onClick={onReceive}>
                Receive
              </Button>
              <Button size="sm" variant="danger" onClick={onReject}>
                Reject
              </Button>
            </>
          ) : (
            <span className="self-center text-xs text-slate-500 dark:text-slate-400">
              The laboratory receives this
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

// --------------------------------------------------------------------------
// The bench, and the log
// --------------------------------------------------------------------------

function BenchRow({ row }: { row: LabRequestRow }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 font-semibold text-slate-900 dark:text-white">
          {row.code}
          <span className="font-normal text-slate-600 dark:text-slate-400">{row.name}</span>
          {row.priority !== "routine" && (
            <Badge tone={PRIORITY_TONE[row.priority] ?? "neutral"}>
              {PRIORITY_LABEL[row.priority] ?? row.priority}
            </Badge>
          )}
          {row.overdue && <Badge tone="critical">{lateLabel(row.minutes_late)}</Badge>}
        </p>
        <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
          {row.patient_name}{" "}
          <span className="numeric text-xs">{row.mrn}</span>
          {row.accession && <span className="numeric"> · {row.accession}</span>}
        </p>
      </div>
      <Badge tone={row.status === "in_progress" ? "info" : "neutral"}>{row.status_label}</Badge>
    </li>
  );
}

function RejectionRow({ sample }: { sample: Sample }) {
  const serious = SERIOUS_REJECTIONS.includes(sample.rejection_reason as RejectionReason);
  return (
    <li className="px-4 py-3">
      <p className="flex flex-wrap items-center gap-2">
        <span className="numeric font-bold text-slate-900 dark:text-white">{sample.accession}</span>
        <Badge tone={serious ? "critical" : "warning"}>{sample.rejection}</Badge>
      </p>
      <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
        {sample.patient_name} · {sample.panel_names} · {sample.specimen_label}
      </p>
      {serious && (
        <p className="mt-1 flex items-start gap-1.5 text-xs font-semibold text-critical-ink dark:text-critical">
          <IconWarning className="mt-0.5 size-3.5 shrink-0" />
          A mis-identified sample. The ward has to be told, not just the tube redrawn.
        </p>
      )}
    </li>
  );
}

// --------------------------------------------------------------------------
// Rejecting
// --------------------------------------------------------------------------

function RejectDialog({
  sample,
  onClose,
  onDone,
  onError,
}: {
  sample: Sample;
  onClose: () => void;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  const [reason, setReason] = useState<RejectionReason | "">("");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);

  const serious = reason !== "" && SERIOUS_REJECTIONS.includes(reason);
  const needsNote = reason === "other";
  const blocked =
    reason === "" || (needsNote && !note.trim()) || (serious && !confirmed) || busy;

  const submit = () => {
    if (reason === "") return;
    setBusy(true);
    void lab
      .reject(sample.id, reason, note)
      .then(onDone)
      .catch((exc: unknown) => onError(messageFrom(exc)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/50 p-0 sm:items-center sm:p-6">
      <Card className="max-h-[90vh] w-full max-w-lg overflow-y-auto">
        <CardHeader title={`Reject ${sample.accession}`} />
        <div className="space-y-4 p-4">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {sample.patient_name} · {sample.panel_names}. The tests go back on the collection
            list and the ward will see that this is a second attempt.
          </p>

          <Field label="Why" hint="Chosen from this list so the pattern over a month can be read.">
            <select
              className={inputClass}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value as RejectionReason | "");
                setConfirmed(false);
              }}
            >
              <option value="">Choose a reason…</option>
              {REJECTION_REASONS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="Note"
            hint={needsNote ? "Required: say what was wrong with it." : "Optional."}
          >
            <input
              className={inputClass}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={300}
            />
          </Field>

          {serious && (
            <label className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm dark:bg-critical/15">
              <input
                type="checkbox"
                className="mt-0.5 size-4"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span className="text-critical-ink dark:text-critical">
                This is a patient-identification error, not a spoiled tube. I will tell the ward
                that drew it.
              </span>
            </label>
          )}

          <div className="flex flex-wrap justify-end gap-2">
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="danger" onClick={submit} disabled={blocked}>
              {busy ? "Rejecting…" : "Reject the sample"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

/**
 * Open the labels in a new tab, so the print dialogue is the browser's own.
 *
 * Fetched as a blob rather than linked, because the endpoint is behind the
 * bearer token and a plain navigation does not carry it. The object URL is
 * revoked on a timer: revoking it immediately cancels the load in the tab
 * that was just opened, and holding it forever leaks the PDF — which carries
 * a patient's name — into the tab's memory for as long as the app is open.
 */
async function openLabels(accessions: string[]): Promise<void> {
  const blob = await lab.labels(accessions);
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** "5 tests · 4 patients · 1 to redraw", or as much of it as applies. */
function collectSub(tests: number, patients: number, redraws: number): string | undefined {
  if (tests === 0) return undefined;
  const parts = [`${patients} patient${patients === 1 ? "" : "s"}`];
  if (redraws) parts.push(`${redraws} to redraw`);
  return parts.join(" · ");
}

/** "Two tubes: EDTA, plain" is the server's job; this is just the count. */
function tubeCount(rows: LabRequestRow[]): string {
  const specimens = new Set(rows.map((row) => row.specimen));
  const n = specimens.size;
  return `${n} tube${n === 1 ? "" : "s"}`;
}

function lateLabel(minutes: number): string {
  if (minutes < 60) return `${minutes} min late`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h late`;
}

function messageFrom(error: unknown): string {
  if (error && typeof error === "object" && "body" in error) {
    const body = (error as { body?: { detail?: string } }).body;
    if (body?.detail) return body.detail;
  }
  return "That did not work. Try again.";
}
