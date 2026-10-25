/**
 * Step 2 — the hospital's locations.
 *
 * An editable list rather than a one-at-a-time form, because a group setting
 * up has three or four and typing them into the same form four times, with a
 * page transition between each, is the part of setup people give up on.
 *
 * Nothing can be deleted here, only added or edited. A branch with a patient
 * in it must not disappear because somebody tidied a list during setup; the
 * foreign keys are PROTECT for the same reason. Closing a branch is a real
 * operation with consequences, and it lives in Settings.
 */

import { useState } from "react";
import { Badge, Button, cn, inputClass } from "@curavia/shared";
import type { SetupBranch } from "@curavia/shared";

import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

type Row = Pick<SetupBranch, "name" | "code" | "state" | "lga" | "phone"> & {
  id?: string;
  is_main?: boolean;
};

export function StepBranches({ state, busy, onSubmit, onSkip }: StepProps) {
  const [rows, setRows] = useState<Row[]>(() =>
    state.branches.length > 0
      ? state.branches.map((branch) => ({
          id: branch.id,
          name: branch.name,
          code: branch.code,
          state: branch.state,
          lga: branch.lga,
          phone: branch.phone,
          is_main: branch.is_main,
        }))
      : [{ name: "Main Branch", code: "MAIN", state: "", lga: "", phone: "" }],
  );

  function update(index: number, key: keyof Row, value: string) {
    setRows((current) =>
      current.map((row, position) => (position === index ? { ...row, [key]: value } : row)),
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ rows });
      }}
    >
      <div className="space-y-3">
        {rows.map((row, index) => (
          <div
            key={row.id ?? `new-${index}`}
            className="rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-800/60"
          >
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs font-bold tracking-wide text-slate-500 uppercase dark:text-slate-400">
                Branch {index + 1}
              </span>
              {row.is_main && <Badge tone="info">Main</Badge>}
            </div>

            <div className="grid gap-3 sm:grid-cols-6">
              <label className="block sm:col-span-3">
                <span className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">
                  Name
                </span>
                <input
                  value={row.name}
                  onChange={(event) => update(index, "name", event.target.value)}
                  className={cn(inputClass, "min-h-10")}
                  placeholder="Ikeja"
                />
              </label>
              <label className="block sm:col-span-1">
                <span className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">
                  Code
                </span>
                <input
                  value={row.code}
                  onChange={(event) => update(index, "code", event.target.value.toUpperCase())}
                  className={cn(inputClass, "numeric min-h-10 uppercase")}
                  placeholder="IKJ"
                  maxLength={16}
                />
              </label>
              <label className="block sm:col-span-1">
                <span className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">
                  State
                </span>
                <input
                  value={row.state}
                  onChange={(event) => update(index, "state", event.target.value)}
                  className={cn(inputClass, "min-h-10")}
                  placeholder="Lagos"
                />
              </label>
              <label className="block sm:col-span-1">
                <span className="mb-1 block text-xs font-semibold text-slate-600 dark:text-slate-400">
                  LGA
                </span>
                <input
                  value={row.lga}
                  onChange={(event) => update(index, "lga", event.target.value)}
                  className={cn(inputClass, "min-h-10")}
                />
              </label>
            </div>

            <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
              The code appears in every medical record number issued here, so keep it short and
              do not change it once patients exist.
            </p>
          </div>
        ))}
      </div>

      <Button
        type="button"
        variant="secondary"
        className="mt-3"
        onClick={() =>
          setRows((current) => [...current, { name: "", code: "", state: "", lga: "", phone: "" }])
        }
      >
        + Add another branch
      </Button>

      <StepActions busy={busy} onSkip={onSkip} />
    </form>
  );
}
