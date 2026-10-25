/**
 * Step 3 — departments and clinics, per branch.
 *
 * Pre-ticked with the common ones for this hospital's size, so accepting the
 * screen produces a working hospital. The uncommon ones are there, unticked,
 * rather than hidden behind "show more": a dental clinic knowing its own
 * department is on the list matters more than the list being short.
 *
 * Grouped by kind because that is how somebody checks the list — clinics
 * first, then wards, then the service departments — rather than alphabetically
 * across all three.
 */

import { useState } from "react";
import { Badge, cn } from "@curavia/shared";

import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

const KIND_ORDER = ["clinic", "ward", "laboratory", "pharmacy", "radiology", "theatre", "store", "admin"];

const KIND_LABEL: Record<string, string> = {
  clinic: "Clinics",
  ward: "Wards",
  laboratory: "Laboratory",
  pharmacy: "Pharmacy",
  radiology: "Radiology",
  theatre: "Theatre",
  store: "Stores",
  admin: "Administration",
};

export function StepDepartments({ state, busy, onSubmit, onSkip }: StepProps) {
  const [branch, setBranch] = useState(
    () => state.branches.find((candidate) => candidate.is_main)?.id ?? state.branches[0]?.id ?? "",
  );
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(state.templates.departments.filter((t) => t.common).map((t) => t.code)),
  );

  function toggle(code: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  const groups = KIND_ORDER.map((kind) => ({
    kind,
    entries: state.templates.departments.filter((template) => template.kind === kind),
  })).filter((group) => group.entries.length > 0);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ branch, codes: [...chosen] });
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
          <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
            Run this step once per branch. They do not have to be the same.
          </span>
        </label>
      )}

      <div className="space-y-5">
        {groups.map((group) => (
          <section key={group.kind}>
            <h3 className="mb-2 text-xs font-bold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              {KIND_LABEL[group.kind] ?? group.kind}
            </h3>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {group.entries.map((template) => {
                const ticked = chosen.has(template.code);
                return (
                  <label
                    key={template.code}
                    className={cn(
                      "flex min-h-11 cursor-pointer items-center gap-3 rounded-[var(--radius-control)] px-3 py-2 ring-1 ring-inset transition-colors",
                      ticked
                        ? "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                        : "bg-white ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:ring-slate-600 dark:hover:bg-slate-700",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={ticked}
                      onChange={() => toggle(template.code)}
                      className="size-4 shrink-0"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-slate-900 dark:text-white">
                        {template.name}
                      </span>
                      <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                        {template.code}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      <p className="mt-4 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
        <Badge tone="neutral">{chosen.size} selected</Badge>
        You can rename any of these later, and add your own.
      </p>

      <StepActions busy={busy} onSkip={onSkip} />
    </form>
  );
}
