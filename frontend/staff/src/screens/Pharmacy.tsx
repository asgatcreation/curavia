/**
 * Pharmacy queue.
 *
 * This is the screen that proves the promise: the pharmacist sees the
 * prescription the moment the doctor signs it, with the allergy check and the
 * stock position already done.
 *
 * It is laid out as a board, not a list, because a pharmacy runs as a small
 * production line — new work arrives, somebody picks it up, it gets checked,
 * it gets handed over — and a pharmacist needs to see the whole line at once
 * to know whether to start another one.
 *
 * Two states earn their own treatment:
 *
 *   Blocked — something is wrong with the prescription itself, usually an
 *   allergy. The card says what, and the only action offered is to query the
 *   prescriber. A pharmacist must never be able to dispense past this by
 *   clicking the same button they always click.
 *
 *   Out of stock — the item is not here. The card says where it IS, with the
 *   quantity, because the realistic next action in a group with three
 *   branches is a transfer, not a refusal.
 */

import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionTitle,
  Stat,
  ToneBar,
  cn,
  formatNaira,
  formatWait,
  useNow,
} from "@curavia/shared";
import type { PharmacyJob, PrescriptionLine, StatusTone } from "@curavia/shared";
import { DEMO_PHARMACY } from "@curavia/shared";

import { IconWarning } from "../icons";

const COLUMNS: { state: PharmacyJob["state"]; title: string; hint: string; tone: StatusTone }[] = [
  { state: "New", title: "New", hint: "Straight from the doctor", tone: "info" },
  { state: "Being prepared", title: "Being prepared", hint: "Picked up by a pharmacist", tone: "warning" },
  { state: "Ready", title: "Ready to hand over", hint: "Checked and bagged", tone: "stable" },
  { state: "Blocked", title: "Blocked", hint: "Needs the prescriber", tone: "critical" },
];

export function Pharmacy() {
  const now = useNow();
  const blocked = DEMO_PHARMACY.filter((job) => job.state === "Blocked").length;
  const outOfStock = DEMO_PHARMACY.filter((job) =>
    job.lines.some((line) => line.stock === 0),
  ).length;

  return (
    <div className="mx-auto w-full max-w-[90rem] space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Pharmacy queue</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Ikeja dispensary · live, as doctors sign
          </p>
        </div>
        <Button variant="secondary">Dispensing history</Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="In the queue" value={String(DEMO_PHARMACY.length)} tone="info" />
        <Stat
          label="Blocked"
          value={String(blocked)}
          tone={blocked > 0 ? "critical" : "stable"}
          sub={blocked > 0 ? "Allergy conflict" : "Nothing held up"}
        />
        <Stat
          label="Out of stock"
          value={String(outOfStock)}
          tone={outOfStock > 0 ? "warning" : "stable"}
          sub={outOfStock > 0 ? "Available at another branch" : "All items on hand"}
        />
        <Stat label="Dispensed today" value="112" tone="stable" sub="₦1,284,500 billed" />
      </div>

      {/* One column per state on desktop; on a phone the columns stack, which
          is the right order anyway: new work first, blocked work last. */}
      {/* Four columns only at 2xl. Below that a pharmacy card has to show a
          drug name, a dose and a stock figure, and four columns inside a
          1280px window truncates all three — which is the one thing a
          dispensing screen must never do. */}
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
        {COLUMNS.map((column) => {
          const jobs = DEMO_PHARMACY.filter((job) => job.state === column.state);
          return (
            <section key={column.state} className="flex min-w-0 flex-col gap-3">
              <header className="flex items-baseline justify-between gap-2">
                <h2 className="text-sm font-bold text-slate-900 dark:text-white">
                  {column.title}
                  <span className="numeric ml-2 rounded-full bg-slate-200 px-2 py-0.5 text-xs font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                    {jobs.length}
                  </span>
                </h2>
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{column.hint}</p>
              </header>

              {jobs.length === 0 ? (
                <Card>
                  <EmptyState title="Nothing here" />
                </Card>
              ) : (
                jobs.map((job) => <JobCard key={job.id} job={job} tone={column.tone} now={now} />)
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function JobCard({ job, tone, now }: { job: PharmacyJob; tone: StatusTone; now: Date }) {
  const blocked = job.state === "Blocked";
  const shortOfStock = job.lines.some((line) => line.stock === 0);

  return (
    <Card className="relative overflow-hidden">
      <ToneBar tone={tone} />

      <div className="space-y-3 py-3 pr-3 pl-5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate font-semibold text-slate-900 dark:text-white">{job.patient}</p>
            <p className="numeric truncate text-xs text-slate-500 dark:text-slate-400">{job.mrn}</p>
          </div>
          {job.priority === "Urgent" && <Badge tone="critical">Urgent</Badge>}
        </div>

        <p className="text-xs text-slate-500 dark:text-slate-400">
          {job.prescriber} · {formatWait(job.received_at, now)} ago
        </p>

        <ul className="space-y-2">
          {job.lines.map((line) => (
            <LineRow key={line.name} line={line} />
          ))}
        </ul>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-3 dark:border-slate-800">
          <span className="min-w-0 flex-1 basis-28">
            <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
              {job.payer}
            </span>
            <span className="numeric block text-sm font-bold text-slate-900 dark:text-white">
              {formatNaira(job.total_kobo)}
            </span>
          </span>

          {blocked ? (
            <Button size="sm" variant="danger">
              Query prescriber
            </Button>
          ) : shortOfStock ? (
            <Button size="sm" variant="secondary">
              Request transfer
            </Button>
          ) : job.state === "Ready" ? (
            <Button size="sm" variant="primary">
              Hand over
            </Button>
          ) : (
            <Button size="sm" variant="primary">
              {job.state === "New" ? "Start" : "Mark ready"}
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

function LineRow({ line }: { line: PrescriptionLine }) {
  return (
    <li
      className={cn(
        "rounded-[var(--radius-control)] p-2",
        line.allergy_conflict
          ? "bg-critical-soft dark:bg-critical/15"
          : "bg-slate-50 dark:bg-slate-800/60",
      )}
    >
      <p className="text-sm font-semibold text-slate-900 dark:text-white">{line.name}</p>
      <p className="text-xs text-slate-600 dark:text-slate-300">
        {line.dose} · {line.quantity}
      </p>

      {line.allergy_conflict && (
        <p className="mt-1.5 flex items-start gap-1.5 text-xs font-bold text-critical-ink dark:text-critical">
          <IconWarning className="mt-px size-3.5 shrink-0" />
          {line.allergy_conflict}
        </p>
      )}

      {line.stock === 0 ? (
        <p className="mt-1.5 text-xs font-semibold text-warning-ink dark:text-warning">
          Out of stock here
          {line.stock_elsewhere &&
            ` · ${line.stock_elsewhere.quantity} at ${line.stock_elsewhere.branch}`}
        </p>
      ) : (
        <p className="numeric mt-1.5 text-xs text-slate-500 dark:text-slate-400">
          {line.stock} in stock
        </p>
      )}

      {line.expiring_batch && (
        <p className="mt-1 text-xs text-warning-ink dark:text-warning">{line.expiring_batch}</p>
      )}
    </li>
  );
}
