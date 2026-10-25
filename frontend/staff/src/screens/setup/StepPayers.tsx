/**
 * Step 6 — HMOs and corporate clients.
 *
 * Names only. No tariffs are asked for or offered, and the screen says why:
 * a hospital's rates with an HMO are negotiated per hospital and
 * commercially confidential, so a number shipped by Curavia would imply we
 * know what it should be. What this step saves is the typing.
 *
 * Self-pay is not on the list. Every hospital has exactly one self-pay payer,
 * it is created automatically, and offering it as a choice would invite
 * somebody to untick it and leave the price list with nothing to hang a
 * default on.
 */

import { useState } from "react";
import { Badge, cn } from "@curavia/shared";

import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

const KIND_LABEL: Record<string, string> = {
  hmo: "HMO",
  nhia: "NHIA",
  corporate: "Corporate",
  ngo: "NGO",
};

export function StepPayers({ state, busy, onSubmit, onSkip }: StepProps) {
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(state.templates.payers.filter((t) => t.common).map((t) => t.code)),
  );

  function toggle(code: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ rows: [...chosen].map((code) => ({ code })) });
      }}
    >
      <p className="mb-4 rounded-[var(--radius-control)] bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
        Tick the schemes you already bill. <strong>No rates are set here</strong> — yours are
        negotiated and confidential, so you enter them against your own price list afterwards,
        where you can see your self-pay price beside them.
      </p>

      <div className="grid gap-2 sm:grid-cols-2">
        {state.templates.payers.map((template) => {
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
              <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-900 dark:text-white">
                {template.name}
              </span>
              <Badge tone={template.kind === "nhia" ? "warning" : "info"}>
                {KIND_LABEL[template.kind] ?? template.kind}
              </Badge>
            </label>
          );
        })}
      </div>

      <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
        Missing one? Add it under Settings — this list is only the ones we see most often.
      </p>

      <StepActions busy={busy} onSkip={onSkip} />
    </form>
  );
}
