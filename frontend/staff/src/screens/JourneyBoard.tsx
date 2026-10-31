/**
 * The patient journey board — live.
 *
 * One row per patient, one column per stage, and at a glance you can see
 * where everybody is and who is stuck. This is the screen a matron leaves
 * open on a wall display, so it is designed to be read from several metres
 * away: large type, heavy weight, nothing that needs a hover.
 *
 * Progress is shown three ways at once — a filled marker, a position in the
 * row and a written stage name — so it survives colour blindness and a cheap
 * projector that crushes the contrast.
 *
 * **The connection is part of the design, not plumbing underneath it.** A
 * board that has silently stopped updating is worse than no board, because
 * people believe it: a nurse glances at a frozen screen and concludes nobody
 * is waiting. So the state is drawn in the header, the age of the data is
 * shown in words, and anything older than a minute says so loudly. When the
 * socket reconnects it asks for a whole fresh snapshot rather than trusting
 * the deltas it may have missed.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  SectionTitle,
  Stat,
  StatusDot,
  cn,
  minutesSince,
  useNow,
  waitTone,
} from "@curavia/shared";
import type { BoardRow, LiveState, Stage } from "@curavia/shared";
import {
  JourneyApi,
  LiveConnection,
  STAGE_LABELS,
  STAGE_ORDER,
  connectJourney,
} from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const journey = new JourneyApi(api);

/** Over this many minutes in one stage and the row is called out. */
const STUCK_MINUTES = 45;

/** Data older than this is called stale in the header, loudly. */
const STALE_SECONDS = 60;

const STATE_TONE: Record<LiveState, "stable" | "warning" | "critical"> = {
  live: "stable",
  connecting: "warning",
  offline: "critical",
};

const STATE_LABEL: Record<LiveState, string> = {
  live: "Live",
  connecting: "Connecting…",
  offline: "Not connected",
};

export function JourneyBoard() {
  const { me } = useAuth();
  const [rows, setRows] = useState<BoardRow[] | null>(null);
  const [state, setState] = useState<LiveState>("connecting");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [denied, setDenied] = useState(false);
  const connection = useRef<LiveConnection | null>(null);
  const now = useNow(10_000);

  const mayMove = me?.permissions.includes("queue.manage") ?? false;

  /**
   * Fetch the whole board over HTTP.
   *
   * Called on mount and whenever the socket has been away, so the screen is
   * never left showing deltas it applied to a picture that was already out
   * of date.
   */
  const reload = useCallback(() => {
    void journey
      .board()
      .then((result) => {
        setRows(result.rows);
        setUpdatedAt(new Date());
      })
      .catch(() => setDenied(true));
  }, []);

  useEffect(() => {
    reload();

    const live = connectJourney(api, {
      onState: (next) => {
        setState(next);
        // Coming back from a drop: the snapshot the server sends on connect
        // replaces everything, so nothing has to be reconciled by hand.
        if (next === "live") reload();
      },
      onMessage: (message) => {
        if (message["type"] === "snapshot") {
          setRows((message["rows"] as BoardRow[]) ?? []);
          setUpdatedAt(new Date());
          return;
        }
        if (message["event"] === "journey") {
          const row = message["row"] as BoardRow | null;
          setUpdatedAt(new Date());
          if (!row) return;
          setRows((current) => {
            const existing = current ?? [];
            const without = existing.filter((entry) => entry.id !== row.id);
            // A row that has left the building is removed rather than
            // redrawn greyed out: the board is who is here now.
            if (row.stage === "done" || row.stage === "left") return without;
            return [...without, row].sort(
              (a, b) => Date.parse(a.stage_since) - Date.parse(b.stage_since),
            );
          });
        }
      },
    });
    connection.current = live;
    return () => {
      live.close();
      connection.current = null;
    };
  }, [reload]);

  // Clamped at zero: `useNow` ticks on an interval, so its clock can be a
  // few seconds behind the moment data actually arrived — and "last updated
  // -1 seconds ago" on a clinical screen reads as a broken system.
  const staleSeconds = updatedAt
    ? Math.max(Math.floor((now.getTime() - updatedAt.getTime()) / 1000), 0)
    : null;
  const isStale = state !== "live" || (staleSeconds !== null && staleSeconds > STALE_SECONDS);

  const waiting = (rows ?? []).filter((row) => row.is_waiting);
  const stuck = (rows ?? []).filter((row) => row.minutes_in_stage >= STUCK_MINUTES);
  const longest = waiting.reduce((most, row) => Math.max(most, row.minutes_in_stage), 0);

  const byStage = useMemo(() => {
    const map = new Map<Stage, BoardRow[]>();
    for (const row of rows ?? []) {
      map.set(row.stage, [...(map.get(row.stage) ?? []), row]);
    }
    return map;
  }, [rows]);

  if (denied) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState
            title="The journey board is not shown to your role."
            hint="Ask whoever administers Curavia here if you need it."
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Journey board</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Everybody in the building, right now
          </p>
        </div>

        {/* The connection, in words and in colour, never colour alone. */}
        <div className="flex flex-wrap items-center gap-3">
          <StatusDot tone={STATE_TONE[state]} label={STATE_LABEL[state]} />
          <span
            className={cn(
              "text-xs",
              isStale
                ? "font-bold text-critical"
                : "text-slate-500 dark:text-slate-400",
            )}
          >
            {staleSeconds === null
              ? "No data yet"
              : isStale
                ? `Last updated ${describeAge(staleSeconds)} ago — this may be out of date`
                : "Up to date"}
          </span>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              reload();
              connection.current?.refresh();
            }}
          >
            Refresh
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="In the building" value={String((rows ?? []).length)} tone="info" />
        <Stat
          label="Waiting now"
          value={String(waiting.length)}
          tone={waiting.length ? "warning" : "stable"}
          sub="Not counting anyone being seen"
        />
        <Stat
          label="Longest wait"
          /* A dash only when nobody is waiting. Somebody who has just
             arrived has waited zero minutes, which is an answer — showing
             a dash there reads as "no data" on a screen whose whole job is
             to be trusted. */
          value={waiting.length === 0 ? "—" : describeMinutes(longest)}
          tone={waiting.length === 0 ? "stable" : waitTone(longest)}
        />
        <Stat
          label="Waiting over 45 min"
          value={String(stuck.length)}
          tone={stuck.length ? "critical" : "stable"}
          sub="In one stage"
        />
      </div>

      {/* The exceptions band. The board's job is not to show the happy path. */}
      {stuck.length > 0 && (
        <Card className="overflow-hidden ring-critical/40">
          <CardHeader title="Waiting too long" count={stuck.length} />
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {stuck.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-3 p-3">
                <IconWarning className="size-4 shrink-0 text-critical" />
                <span className="min-w-0 flex-1 basis-48">
                  <span className="block font-semibold text-slate-900 dark:text-white">
                    {row.patient_name}
                  </span>
                  <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                    {row.token || row.mrn} · {row.stage_label}
                    {row.department ? ` · ${row.department}` : ""}
                  </span>
                </span>
                <Badge tone="critical">{describeMinutes(row.minutes_in_stage)}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {rows === null ? (
        <Card>
          <EmptyState title="Loading the board…" />
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            title="Nobody is in the building."
            hint="Patients appear here the moment they are checked in at the front desk."
          />
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {STAGE_ORDER.filter((stage) => (byStage.get(stage) ?? []).length > 0).map((stage) => (
            <StageColumn
              key={stage}
              stage={stage}
              rows={byStage.get(stage) ?? []}
              mayMove={mayMove}
              now={now}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function StageColumn({
  stage,
  rows,
  mayMove,
  now,
}: {
  stage: Stage;
  rows: BoardRow[];
  mayMove: boolean;
  now: Date;
}) {
  return (
    <Card className="overflow-hidden">
      <CardHeader title={STAGE_LABELS[stage]} count={rows.length} />
      <ul className="divide-y divide-slate-200 dark:divide-slate-800">
        {rows.map((row) => (
          <PatientCard key={row.id} row={row} mayMove={mayMove} now={now} />
        ))}
      </ul>
    </Card>
  );
}

function PatientCard({
  row,
  mayMove,
  now,
}: {
  row: BoardRow;
  mayMove: boolean;
  now: Date;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [open, setOpen] = useState(false);

  // Counted here rather than trusting the number that arrived with the row:
  // a board left open for an hour would otherwise show the wait as it was
  // when the message came, which is the frozen-clock failure again.
  const minutes = minutesSince(row.stage_since, now);
  const tone = waitTone(minutes);

  function move(stage: Stage) {
    setBusy(true);
    setProblem("");
    void journey
      .move(row.id, stage)
      .then(() => setOpen(false))
      .catch(() => setProblem("That did not go through. Try again."))
      .finally(() => setBusy(false));
  }

  return (
    <li className="p-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {row.token && (
          <span className="numeric text-sm font-bold text-slate-900 dark:text-white">
            {row.token}
          </span>
        )}
        <span className="min-w-0 flex-1 basis-32 truncate font-semibold text-slate-900 dark:text-white">
          {row.patient_name}
        </span>
        <span
          className={cn(
            "numeric text-sm font-bold",
            tone === "critical"
              ? "text-critical"
              : tone === "warning"
                ? "text-warning-ink dark:text-warning"
                : "text-slate-600 dark:text-slate-300",
          )}
        >
          {describeMinutes(minutes)}
        </span>
      </div>

      <p className="numeric mt-0.5 text-xs text-slate-500 dark:text-slate-400">
        {row.mrn}
        {row.department ? ` · ${row.department}` : ""}
        {row.attending ? ` · ${row.attending}` : ""}
      </p>

      {mayMove && (
        <div className="mt-2">
          <Button size="sm" variant="ghost" onClick={() => setOpen((shown) => !shown)}>
            {open ? "Cancel" : "Send on"}
          </Button>
          {open && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {STAGE_ORDER.filter((stage) => stage !== row.stage).map((stage) => (
                <button
                  key={stage}
                  type="button"
                  disabled={busy}
                  onClick={() => move(stage)}
                  className="min-h-9 rounded-full bg-slate-100 px-3 text-xs font-semibold text-slate-700 hover:bg-slate-200 disabled:opacity-50 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                >
                  {STAGE_LABELS[stage]}
                </button>
              ))}
              <button
                type="button"
                disabled={busy}
                onClick={() => move("done")}
                className="min-h-9 rounded-full bg-stable-soft px-3 text-xs font-semibold text-stable-ink hover:brightness-95 disabled:opacity-50 dark:bg-stable/15 dark:text-stable"
              >
                Finished
              </button>
            </div>
          )}
          {problem && <p className="mt-1 text-xs font-semibold text-critical">{problem}</p>}
        </div>
      )}
    </li>
  );
}

function describeMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function describeAge(seconds: number): string {
  if (seconds < 90) return `${seconds} seconds`;
  return `${Math.floor(seconds / 60)} minutes`;
}
