/**
 * The patient journey board.
 *
 * One row per patient, one column per department, and at a glance you can see
 * where everybody is and who is stuck. This is the screen a matron or a
 * medical director leaves open on a wall display, so it is designed to be
 * read from several metres away: large type, heavy weight, and no information
 * that needs a hover to reveal.
 *
 * Progress is shown three ways at once — a filled marker, a position in the
 * row and a written stage name — so it survives both colour blindness and a
 * cheap projector that crushes the contrast.
 *
 * Anything blocked is pulled out into its own band at the top rather than
 * being left to be spotted in the grid. The board's job is not to show the
 * happy path; it is to show the exceptions.
 */

import {
  Badge,
  Card,
  CardHeader,
  SectionTitle,
  Stat,
  TRIAGE,
  cn,
  formatWait,
  minutesSince,
  useNow,
  waitTone,
} from "@curavia/shared";
import type { JourneyRow, JourneyStage } from "@curavia/shared";
import { DEMO_JOURNEY, JOURNEY_STAGES } from "@curavia/shared";

import { IconWarning } from "../icons";

type CellState = "done" | "current" | "skipped" | "todo";

function cellState(row: JourneyRow, stage: JourneyStage): CellState {
  if (row.current === stage) return "current";
  if (row.done.includes(stage)) return "done";
  if (row.skipped.includes(stage)) return "skipped";
  return "todo";
}

export function JourneyBoard() {
  // This board is meant to be left open on a wall display, so the clock has
  // to tick: times read from Date.now() during render would freeze at the
  // moment the page was loaded.
  const now = useNow();
  const blocked = DEMO_JOURNEY.filter((row) => row.blocked_by);
  const longest = Math.max(...DEMO_JOURNEY.map((row) => minutesSince(row.since, now)));

  return (
    <div className="mx-auto w-full max-w-[90rem] space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Patient journey board</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Ikeja · live · updates as each department hands over
          </p>
        </div>
        <Badge tone="stable">● Live</Badge>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="In the building" value={String(DEMO_JOURNEY.length)} tone="info" />
        <Stat
          label="Blocked"
          value={String(blocked.length)}
          tone={blocked.length > 0 ? "critical" : "stable"}
          sub={blocked.length > 0 ? "Someone must act" : "Nothing held up"}
        />
        <Stat
          label="Longest in one stage"
          value={`${longest}m`}
          tone={waitTone(longest)}
          sub="Tunde Afolabi · cashier"
        />
        <Stat label="Completed today" value="38" tone="stable" sub="Average 1h 12m door to door" />
      </div>

      {blocked.length > 0 && (
        <Card className="overflow-hidden ring-critical/40">
          <CardHeader title="Blocked — needs someone" count={blocked.length} />
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {blocked.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-3 p-4">
                <IconWarning className="size-5 shrink-0 text-critical" />
                <span className="numeric font-bold text-slate-900 dark:text-white">{row.token}</span>
                <span className="min-w-0 flex-1 basis-40 truncate font-semibold text-slate-900 dark:text-white">
                  {row.patient}
                </span>
                <Badge tone="neutral">{row.current}</Badge>
                <span className="min-w-0 flex-1 basis-full text-sm text-critical-ink sm:basis-auto dark:text-critical">
                  {row.blocked_by}
                </span>
                <span className="numeric ml-auto text-sm font-bold text-slate-700 dark:text-slate-200">
                  {formatWait(row.since, now)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card className="overflow-hidden">
        <CardHeader title="Everyone in the building" count={DEMO_JOURNEY.length} />

        {/* The grid scrolls sideways on a phone rather than reflowing: the
            columns ARE the information, and a stacked version of this board
            stops being a board. */}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[46rem] border-collapse">
            <thead>
              <tr className="border-b border-slate-200 dark:border-slate-800">
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-bold tracking-wide text-slate-500 uppercase dark:text-slate-400"
                >
                  Patient
                </th>
                {JOURNEY_STAGES.map((stage) => (
                  <th
                    key={stage}
                    scope="col"
                    className="px-2 py-2 text-center text-xs font-bold tracking-wide text-slate-500 uppercase dark:text-slate-400"
                  >
                    {stage}
                  </th>
                ))}
                <th
                  scope="col"
                  className="px-4 py-2 text-right text-xs font-bold tracking-wide text-slate-500 uppercase dark:text-slate-400"
                >
                  In stage
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {DEMO_JOURNEY.map((row) => {
                const triage = row.triage ? TRIAGE[row.triage] : null;
                const minutes = minutesSince(row.since, now);
                return (
                  <tr
                    key={row.id}
                    className={cn(row.blocked_by && "bg-critical-soft/50 dark:bg-critical/10")}
                  >
                    <th scope="row" className="px-4 py-3 text-left">
                      <span className="flex items-center gap-2">
                        <span className="numeric text-sm font-bold text-slate-900 dark:text-white">
                          {row.token}
                        </span>
                        {triage && <Badge tone={triage.tone}>{triage.label}</Badge>}
                      </span>
                      <span className="mt-0.5 block truncate text-sm font-semibold text-slate-800 dark:text-slate-100">
                        {row.patient}
                      </span>
                    </th>

                    {JOURNEY_STAGES.map((stage) => (
                      <td key={stage} className="px-2 py-3 text-center">
                        <Cell state={cellState(row, stage)} stage={stage} patient={row.patient} />
                      </td>
                    ))}

                    <td className="px-4 py-3 text-right">
                      <span
                        className={cn(
                          "numeric text-sm font-bold",
                          minutes >= 60
                            ? "text-critical"
                            : minutes >= 30
                              ? "text-warning-ink dark:text-warning"
                              : "text-slate-700 dark:text-slate-200",
                        )}
                      >
                        {formatWait(row.since, now)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Legend />
    </div>
  );
}

function Cell({
  state,
  stage,
  patient,
}: {
  state: CellState;
  stage: JourneyStage;
  patient: string;
}) {
  const description = {
    done: `${patient}: ${stage} finished`,
    current: `${patient}: now at ${stage}`,
    skipped: `${patient}: ${stage} not needed`,
    todo: `${patient}: ${stage} still to come`,
  }[state];

  return (
    <span className="inline-flex flex-col items-center gap-1">
      <span className="sr-only">{description}</span>
      {state === "done" && (
        <span
          aria-hidden="true"
          className="flex size-6 items-center justify-center rounded-full bg-stable text-xs font-black text-white"
        >
          ✓
        </span>
      )}
      {state === "current" && (
        <span
          aria-hidden="true"
          className="flex size-6 animate-pulse items-center justify-center rounded-full bg-brand-600 ring-4 ring-brand-200 dark:bg-brand-400 dark:ring-brand-900"
        />
      )}
      {state === "skipped" && (
        <span aria-hidden="true" className="text-sm font-bold text-slate-300 dark:text-slate-600">
          —
        </span>
      )}
      {state === "todo" && (
        <span
          aria-hidden="true"
          className="size-6 rounded-full border-2 border-dashed border-slate-300 dark:border-slate-700"
        />
      )}
    </span>
  );
}

function Legend() {
  return (
    <p className="flex flex-wrap items-center gap-x-5 gap-y-2 px-1 text-xs text-slate-500 dark:text-slate-400">
      <span className="flex items-center gap-2">
        <span className="flex size-5 items-center justify-center rounded-full bg-stable text-[0.625rem] font-black text-white">
          ✓
        </span>
        Finished
      </span>
      <span className="flex items-center gap-2">
        <span className="size-5 rounded-full bg-brand-600 ring-2 ring-brand-200 dark:bg-brand-400 dark:ring-brand-900" />
        Here now
      </span>
      <span className="flex items-center gap-2">
        <span className="size-5 rounded-full border-2 border-dashed border-slate-300 dark:border-slate-700" />
        Still to come
      </span>
      <span className="flex items-center gap-2">
        <span className="font-bold text-slate-400">—</span>
        Not needed for this patient
      </span>
    </p>
  );
}
