/**
 * The ward: what is still to do, and what the next shift is told.
 *
 * Three things decided its shape.
 *
 * **The list is the page.** A nurse opens this at the start of a shift and
 * comes back to it between jobs. So the overdue work is at the top with how
 * late it is spelled out in minutes, and closing something takes one tap
 * when it was done and two when it was not — because only one of those
 * needs a reason, and asking for one when a task was simply done is how a
 * ward learns to type a full stop into every box.
 *
 * **Not done is not the same as done, and neither is cancelled.** Three
 * buttons, three meanings: she had it; she did not and still needs it; she
 * never needed it. A screen that offered one "close" button would lose the
 * difference, and the difference is the entire value of the list at four
 * o'clock in the morning.
 *
 * **The handover writes its own outstanding list.** The nurse writes the
 * judgement — what she is worried about, what the night should watch for —
 * and the system supplies what is still open. A handover sheet typed from
 * memory omits precisely the thing nobody remembered, which is what
 * "forgot" means.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiRequestError,
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  NursingApi,
  SectionTitle,
  Stat,
  cn,
  formatDateTime,
  inputClass,
  useNow,
} from "@curavia/shared";
import type {
  Handover,
  NursingTask,
  TaskInput,
  OutstandingTask,
  StatusTone,
  Ward as WardRow,
} from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const nursing = new NursingApi(api);

/** How late is late enough to shout about. */
const VERY_LATE_MINUTES = 60;

function lateness(task: NursingTask): { tone: StatusTone; label: string } | null {
  if (!task.is_overdue) return null;
  const minutes = task.minutes_late;
  const label =
    minutes < 60
      ? `${minutes} min late`
      : `${Math.floor(minutes / 60)}h ${minutes % 60}m late`;
  return { tone: minutes >= VERY_LATE_MINUTES ? "critical" : "warning", label };
}

export function Ward() {
  const { me } = useAuth();
  const [wards, setWards] = useState<WardRow[] | null>(null);
  const [ward, setWard] = useState<WardRow | null>(null);
  const [error, setError] = useState("");

  const canWork = me?.permissions.includes("nursing.task.manage") ?? false;
  const canHandOver = me?.permissions.includes("nursing.handover.write") ?? false;
  const denied = me != null && !me.permissions.includes("nursing.task.view");

  const loadWards = useCallback(() => {
    void nursing
      .wards()
      .then((result) => {
        setWards(result.wards);
        // Straight into the busiest one. A nurse with a single ward should
        // never have to pick it, and one with four wants the loud one.
        setWard((current) => current ?? result.wards[0] ?? null);
      })
      .catch((exc: unknown) =>
        setError(exc instanceof ApiRequestError ? exc.message : "The wards could not be read."),
      );
  }, []);

  useEffect(loadWards, [loadWards]);

  if (denied) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState title="The ward list is not shown to your role." />
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 p-4 sm:p-6">
      <div>
        <SectionTitle>Ward</SectionTitle>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
          What is still to do, and what the next shift needs to know.
        </p>
      </div>

      {error && <p className="text-sm font-medium text-critical">{error}</p>}

      {wards && wards.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {wards.map((option) => (
            <Button
              key={option.id}
              size="sm"
              variant={option.id === ward?.id ? "primary" : "ghost"}
              onClick={() => setWard(option)}
            >
              {option.name}
              {option.open_tasks > 0 && ` · ${option.open_tasks}`}
            </Button>
          ))}
        </div>
      )}

      {ward ? (
        <WardRoom
          ward={ward}
          canWork={canWork}
          canHandOver={canHandOver}
          onChanged={loadWards}
        />
      ) : (
        wards !== null && (
          <Card>
            <EmptyState title="No ward or clinic is set up yet." />
          </Card>
        )
      )}
    </div>
  );
}

function WardRoom({
  ward,
  canWork,
  canHandOver,
  onChanged,
}: {
  ward: WardRow;
  canWork: boolean;
  canHandOver: boolean;
  /** Keeps the ward tabs' counts honest: a badge reading 3 beside a list of
      four is the screen telling the nurse it cannot be trusted. */
  onChanged: () => void;
}) {
  const [tasks, setTasks] = useState<NursingTask[]>([]);
  const [handover, setHandover] = useState<Handover | null>(null);
  const [draft, setDraft] = useState<Handover | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  // Re-renders once a minute so "40 min late" does not sit there reading 40
  // for an hour. The ward's clock is the thing the nurse is checking.
  useNow(60_000);

  const reload = useCallback(() => {
    void nursing
      .ward(ward.id)
      .then((result) => {
        setTasks(result.tasks);
        setHandover(result.handover);
        setDraft(result.draft);
        onChanged();
      })
      .catch((exc: unknown) =>
        setError(exc instanceof ApiRequestError ? exc.message : "The list could not be read."),
      );
  }, [ward.id, onChanged]);

  useEffect(reload, [reload]);

  const overdue = tasks.filter((task) => task.is_overdue);
  const urgent = tasks.filter((task) => task.urgent && !task.is_overdue);

  // Who the ward already has work for. Enough to hang a hand-written task
  // on without building a second patient search here — "ring the lab about
  // bed 3" is always about somebody already on the list.
  const patientsOnWard = Array.from(
    new Map(
      tasks.map((task) => [
        task.patient,
        { id: task.patient, name: task.patient_name, encounter: task.encounter },
      ]),
    ).values(),
  );

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="space-y-5">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="Still to do" value={String(tasks.length)} />
          {overdue.length > 0 ? (
            <Stat label="Overdue" value={String(overdue.length)} tone="critical" />
          ) : (
            <Stat label="Overdue" value="0" />
          )}
          {urgent.length > 0 ? (
            <Stat label="Urgent" value={String(urgent.length)} tone="warning" />
          ) : (
            <Stat label="Urgent" value="0" />
          )}
        </div>

        {error && <p className="text-sm font-medium text-critical">{error}</p>}

        <Card>
          <CardHeader
            title="To do"
            count={tasks.length}
            actions={
              canWork && !adding ? (
                <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
                  + Add a task
                </Button>
              ) : undefined
            }
          />

          {adding && canWork && (
            <AddTask
              ward={ward}
              patients={patientsOnWard}
              onCancel={() => setAdding(false)}
              onDone={() => {
                setAdding(false);
                reload();
              }}
            />
          )}

          {tasks.length === 0 && !adding ? (
            <EmptyState
              title="Nothing is outstanding on this ward."
              hint={
                canWork
                  ? "Tasks appear here as doctors sign orders and observations fall due."
                  : "Your role can read this list but not work it."
              }
            />
          ) : (
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {tasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  canWork={canWork}
                  onClose={(action, options) =>
                    nursing.close(task.id, action, options).then(reload)
                  }
                />
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="space-y-5">
        {handover && <HandoverCard handover={handover} canAccept={canHandOver} onDone={reload} />}
        {canHandOver && (
          <WriteHandover
            key={draft?.id ?? "new"}
            ward={ward}
            draft={draft}
            openTasks={tasks.length}
            onDone={reload}
          />
        )}
      </div>
    </div>
  );
}

function TaskRow({
  task,
  canWork,
  onClose,
}: {
  task: NursingTask;
  canWork: boolean;
  onClose: (
    action: "done" | "not_done" | "cancelled",
    options: { note?: string; reason?: string },
  ) => Promise<unknown>;
}) {
  const [closing, setClosing] = useState<"not_done" | "cancelled" | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const late = lateness(task);

  async function run(work: Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work;
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "That could not be done.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-slate-900 dark:text-white">{task.name}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {task.patient_name} · {task.kind_label}
            {task.due_at && ` · due ${formatDateTime(task.due_at)}`}
          </p>
          {task.detail && (
            <p className="mt-1 text-sm text-slate-700 dark:text-slate-200">{task.detail}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {task.urgent && <Badge tone="urgent">Urgent</Badge>}
          {late && <Badge tone={late.tone}>{late.label}</Badge>}
        </div>
      </div>

      {/* A task on its third shift is one the ward should be asked about,
          so it says so rather than relying on anybody noticing. */}
      {task.handovers >= 2 && (
        <p className="mt-2 flex items-start gap-2 rounded-[var(--radius-control)] bg-warning-soft p-2.5 text-sm font-medium text-warning-ink dark:bg-warning/15 dark:text-warning">
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          Still here after {task.handovers} handovers. Should it still be on the list?
        </p>
      )}

      {error && <p className="mt-2 text-sm font-medium text-critical">{error}</p>}

      {canWork && (
        <div className="mt-2">
          {closing ? (
            <div className="flex flex-wrap items-end gap-2">
              <label className="block flex-1 basis-48">
                <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                  {closing === "not_done"
                    ? "Why was it not done?"
                    : "Why is it being taken off?"}
                </span>
                <input
                  type="text"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder={
                    closing === "not_done" ? "Patient was in theatre." : "Ordered in error."
                  }
                  className={inputClass}
                />
              </label>
              <Button
                size="sm"
                variant="danger"
                disabled={busy || !reason.trim()}
                onClick={() => {
                  void run(onClose(closing, { reason }));
                  setClosing(null);
                  setReason("");
                }}
              >
                {closing === "not_done" ? "Not done" : "Take it off"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setClosing(null)}>
                Cancel
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {/* One tap when it was done. Asking "why" at this moment is
                  how a ward learns to type a full stop into every box. */}
              <Button size="sm" disabled={busy} onClick={() => void run(onClose("done", {}))}>
                Done
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setClosing("not_done")}>
                Not done
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setClosing("cancelled")}>
                Take it off
              </Button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * What the last shift left.
 *
 * `outstanding` is rendered as what that nurse was told, not as what is
 * outstanding now — the live list is the card on the left, and conflating
 * the two would make a frozen snapshot look like a stale bug.
 */
function HandoverCard({
  handover,
  canAccept,
  onDone,
}: {
  handover: Handover;
  canAccept: boolean;
  onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showOutstanding, setShowOutstanding] = useState(false);

  const patients = handover.content.patients ?? [];

  return (
    <Card>
      <CardHeader
        title="Handover received"
        actions={
          <Badge tone={handover.status === "accepted" ? "stable" : "info"}>
            {handover.status_label}
          </Badge>
        }
      />
      <div className="space-y-3 p-4">
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {handover.shift ? `${handover.shift} · ` : ""}
          from {handover.from_nurse_name}
          {handover.signed_at && ` · ${formatDateTime(handover.signed_at)}`}
        </p>

        {handover.content.ward_note && (
          <p className="whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-100">
            {handover.content.ward_note}
          </p>
        )}

        {patients.map((patient, index) => (
          <div
            key={patient.patient_id ?? `${patient.name ?? ""}-${String(index)}`}
            className="rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-950/40"
          >
            <p className="font-semibold text-slate-900 dark:text-white">{patient.name}</p>
            {(
              [
                ["Situation", patient.situation],
                ["Background", patient.background],
                ["Assessment", patient.assessment],
                ["Recommendation", patient.recommendation],
              ] as const
            )
              .filter(([, value]) => (value ?? "").trim())
              .map(([label, value]) => (
                <p key={label} className="mt-1 text-sm text-slate-700 dark:text-slate-200">
                  <span className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    {label}:{" "}
                  </span>
                  {value}
                </p>
              ))}
          </div>
        ))}

        {handover.outstanding_count > 0 && (
          <div>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowOutstanding((shown) => !shown)}
            >
              {showOutstanding ? "Hide" : "Show"} what was outstanding (
              {handover.outstanding_count})
            </Button>
            {showOutstanding && (
              <ul className="mt-2 space-y-1.5">
                {handover.outstanding.map((row: OutstandingTask) => (
                  <li key={row.task_id} className="text-sm text-slate-700 dark:text-slate-200">
                    {row.name} — {row.patient}
                    {row.overdue && (
                      <span className="text-critical"> · was {row.minutes_late} min late</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              As it stood when the shift handed over. The live list is on the left.
            </p>
          </div>
        )}

        {error && <p className="text-sm font-medium text-critical">{error}</p>}

        {canAccept && handover.status === "signed" && !handover.from_me && (
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
            <label className="block">
              <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                Anything to add? (optional)
              </span>
              <input
                type="text"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                className={inputClass}
              />
            </label>
            <Button
              size="sm"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setError("");
                nursing
                  .acceptHandover(handover.id, note)
                  .then(onDone)
                  .catch((exc: unknown) =>
                    setError(
                      exc instanceof ApiRequestError
                        ? exc.message
                        : "That could not be accepted.",
                    ),
                  )
                  .finally(() => setBusy(false));
              }}
            >
              I have taken the ward
            </Button>
          </div>
        )}

        {handover.from_me && handover.status === "signed" && (
          <p className="border-t border-slate-200 pt-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
            Waiting for the nurse coming on to take the ward. She accepts it in her own name.
          </p>
        )}

        {handover.signature.length > 0 && (
          <div className="border-t border-slate-200 pt-3 dark:border-slate-800">
            {handover.signature.map((line) => (
              <p key={line} className="text-xs text-slate-500 dark:text-slate-400">
                {line}
              </p>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

/**
 * Writing one.
 *
 * The nurse writes the judgement. The count of what is still open is shown
 * beside the sign button so she knows what she is passing on, but she does
 * not type it and cannot edit it — the server builds that list at the
 * moment of signing.
 */
function WriteHandover({
  ward,
  draft,
  openTasks,
  onDone,
}: {
  ward: WardRow;
  draft: Handover | null;
  openTasks: number;
  onDone: () => void;
}) {
  // Seeded from the draft once, not synced to it. The parent reloads this
  // card whenever a task is closed, and an effect copying the server's copy
  // back over the box would wipe a half-typed handover mid-sentence. The
  // call site keys this component on the draft's id, so starting a new one
  // remounts it with the new values.
  const [shift, setShift] = useState(draft?.shift ?? "");
  const [wardNote, setWardNote] = useState(draft?.content.ward_note ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function run(work: Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work;
      onDone();
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "That could not be done.");
    } finally {
      setBusy(false);
    }
  }

  if (!draft) {
    return (
      <Card>
        <CardHeader title="Hand over" />
        <div className="space-y-3 p-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Start the sheet for the shift coming on. What is still outstanding is added for you
            when you hand over.
          </p>
          <Button
            size="sm"
            disabled={busy}
            onClick={() => void run(nursing.openHandover(ward.id))}
          >
            Start the handover
          </Button>
          {error && <p className="text-sm font-medium text-critical">{error}</p>}
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title="Hand over" actions={<Badge tone="info">Being written</Badge>} />
      <div className="space-y-3 p-4">
        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Which shift?
          </span>
          <input
            type="text"
            value={shift}
            onChange={(event) => setShift(event.target.value)}
            placeholder="Morning to afternoon"
            className={inputClass}
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
            What does the next shift need to know?
          </span>
          <textarea
            rows={5}
            value={wardNote}
            onChange={(event) => setWardNote(event.target.value)}
            placeholder="Bed 3 is for theatre at eight and is nil by mouth. Bed 7's family are expected."
            className={cn(inputClass, "resize-y")}
          />
        </label>

        <p className="rounded-[var(--radius-control)] bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-950/40 dark:text-slate-300">
          {openTasks === 0
            ? "Nothing is outstanding. The sheet will say so."
            : `${String(openTasks)} outstanding ${openTasks === 1 ? "task" : "tasks"} will be added to this sheet when you hand over. You do not need to list them.`}
        </p>

        {error && <p className="text-sm font-medium text-critical">{error}</p>}

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() =>
              void run(
                nursing.saveHandover(draft.id, { ward_note: wardNote }),
              )
            }
          >
            Save it for now
          </Button>
          {/* Two steps. Signing makes the ward somebody else's
              responsibility, and it cannot be taken back. */}
          {confirming ? (
            <Button
              size="sm"
              variant="danger"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                void run(
                  nursing
                    .saveHandover(draft.id, { ward_note: wardNote })
                    .then(() => nursing.signHandover(draft.id, shift)),
                );
              }}
            >
              Yes, hand over the ward
            </Button>
          ) : (
            <Button size="sm" disabled={busy} onClick={() => setConfirming(true)}>
              Hand over
            </Button>
          )}
          {confirming && (
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Not yet
            </Button>
          )}
        </div>

        {confirming && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Once handed over, this sheet cannot be rewritten. The nurse coming on has to accept
            it in her own name.
          </p>
        )}
      </div>
    </Card>
  );
}

/**
 * A task somebody adds by hand.
 *
 * Deliberately limited to a patient the ward already has work for. Wards
 * are not fully describable and a nurse must be able to write "ring the lab
 * about bed 3" — but that is always about somebody already on the list, and
 * a second patient search on this screen would be a second place for a
 * nurse to pick the wrong Adebayo.
 */
function AddTask({
  ward,
  patients,
  onCancel,
  onDone,
}: {
  ward: WardRow;
  patients: { id: string; name: string; encounter: string | null }[];
  onCancel: () => void;
  onDone: () => void;
}) {
  const [patientId, setPatientId] = useState(patients[0]?.id ?? "");
  const [name, setName] = useState("");
  const [detail, setDetail] = useState("");
  const [urgent, setUrgent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (patients.length === 0) {
    return (
      <div className="space-y-2 border-b border-slate-200 p-4 dark:border-slate-800">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          This ward has no patients with work outstanding yet. Tasks appear here as doctors sign
          orders and observations fall due; once a patient is on the list you can add to it.
        </p>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Close
        </Button>
      </div>
    );
  }

  const chosen = patients.find((patient) => patient.id === patientId);

  return (
    <div className="space-y-3 border-b border-slate-200 p-4 dark:border-slate-800">
      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          For which patient?
        </span>
        <select
          value={patientId}
          onChange={(event) => setPatientId(event.target.value)}
          className={inputClass}
        >
          {patients.map((patient) => (
            <option key={patient.id} value={patient.id}>
              {patient.name}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          What has to be done?
        </span>
        <input
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Ring the laboratory about the culture"
          className={inputClass}
        />
      </label>

      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Anything else the next nurse needs?
        </span>
        <input
          type="text"
          value={detail}
          onChange={(event) => setDetail(event.target.value)}
          className={inputClass}
        />
      </label>

      <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
        <input
          type="checkbox"
          checked={urgent}
          onChange={(event) => setUrgent(event.target.checked)}
        />
        This one cannot wait
      </label>

      {error && <p className="text-sm font-medium text-critical">{error}</p>}

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={busy || !name.trim() || !patientId}
          onClick={() => {
            setBusy(true);
            setError("");
            const input: TaskInput = {
              patient_id: patientId,
              name: name.trim(),
              department_id: ward.id,
              urgent,
            };
            if (detail.trim()) input.detail = detail.trim();
            if (chosen?.encounter) input.encounter_id = chosen.encounter;
            nursing
              .add(input)
              .then(onDone)
              .catch((exc: unknown) =>
                setError(
                  exc instanceof ApiRequestError ? exc.message : "That could not be added.",
                ),
              )
              .finally(() => setBusy(false));
          }}
        >
          Add it
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
