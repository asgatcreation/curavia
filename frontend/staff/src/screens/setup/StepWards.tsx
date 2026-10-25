/**
 * Step 4 — beds, for hospitals that admit.
 *
 * Optional, and the first thing the screen does is check whether the hospital
 * has any wards at all. A clinic that skipped the ward departments in step 3
 * is told so plainly and offered the Skip button, rather than being shown an
 * empty table it cannot do anything with.
 *
 * Beds are created in batches by kind, because that is how a ward is actually
 * described: "eight general beds and two side rooms". Naming them MW-01
 * upwards is the server's job.
 */

import { useState } from "react";
import { cn, inputClass } from "@curavia/shared";

import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

interface Row {
  department_code: string;
  kind: string;
  count: number;
}

export function StepWards({ state, busy, onSubmit, onSkip }: StepProps) {
  const wardTemplates = state.templates.departments.filter((t) => t.kind === "ward");
  const defaults = new Map(state.templates.ward_beds.map((t) => [t.department_code, t.beds]));

  const [branch, setBranch] = useState(
    () => state.branches.find((candidate) => candidate.is_main)?.id ?? state.branches[0]?.id ?? "",
  );
  const [rows, setRows] = useState<Row[]>(() =>
    wardTemplates.flatMap((ward) =>
      (defaults.get(ward.code) ?? [["general", 0]]).map(([kind, count]) => ({
        department_code: ward.code,
        kind: String(kind),
        count: Number(count),
      })),
    ),
  );

  if (wardTemplates.length === 0) {
    return (
      <div>
        <p className="text-sm text-slate-600 dark:text-slate-300">
          Nothing to do here — a practice your size does not usually admit patients. If that
          changes, add a ward under Settings and the bed board appears.
        </p>
        <StepActions busy={busy} onSkip={onSkip} label="Continue" />
      </div>
    );
  }

  function update(index: number, count: number) {
    setRows((current) =>
      current.map((row, position) =>
        position === index ? { ...row, count: Math.max(0, count) } : row,
      ),
    );
  }

  const total = rows.reduce((sum, row) => sum + row.count, 0);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ branch, rows: rows.filter((row) => row.count > 0) });
      }}
    >
      {state.branches.length > 1 && (
        <label className="mb-4 block">
          <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            Which branch?
          </span>
          <select
            value={branch}
            onChange={(event) => setBranch(event.target.value)}
            className="min-h-11 w-full rounded-[var(--radius-control)] bg-white px-3 text-slate-900 ring-1 ring-slate-300 ring-inset sm:max-w-sm dark:bg-slate-800 dark:text-white dark:ring-slate-600"
          >
            {state.branches.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
        </label>
      )}

      <p className="mb-3 text-sm text-slate-600 dark:text-slate-300">
        Only the wards you created in the last step will accept beds. Set a count to zero to
        leave a ward empty for now.
      </p>

      <ul className="space-y-2">
        {rows.map((row, index) => {
          const ward = wardTemplates.find((t) => t.code === row.department_code);
          const kindLabel =
            state.templates.bed_kinds.find((k) => k.value === row.kind)?.label ?? row.kind;
          return (
            <li
              key={`${row.department_code}-${row.kind}`}
              className="flex flex-wrap items-center gap-3 rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-800/60"
            >
              <span className="min-w-0 flex-1 basis-40">
                <span className="block text-sm font-semibold text-slate-900 dark:text-white">
                  {ward?.name ?? row.department_code}
                </span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">
                  {kindLabel}
                </span>
              </span>
              <label className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-600 dark:text-slate-400">
                  Beds
                </span>
                <input
                  type="number"
                  min={0}
                  max={500}
                  value={row.count}
                  onChange={(event) => update(index, Number(event.target.value))}
                  className={cn(inputClass, "numeric min-h-10 w-24 text-right")}
                />
              </label>
            </li>
          );
        })}
      </ul>

      <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
        {total} bed{total === 1 ? "" : "s"} will be created, numbered from the ward's code.
      </p>

      <StepActions busy={busy} onSkip={onSkip} />
    </form>
  );
}
