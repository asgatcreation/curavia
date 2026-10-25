/**
 * Step 5 — services and what they cost.
 *
 * The prices arrive filled in, and the screen says plainly that they are
 * indicative rather than recommended. Curavia does not know what any hospital
 * should charge; what it can do is stop a hospital setting up at 9pm from
 * having to invent forty numbers before it can see a patient.
 *
 * Prices are typed in naira and sent in kobo. The conversion happens once,
 * here, at the edge — never in the middle of the system, and never as a
 * float. ₦6,500 is 650000 kobo and nothing in between is allowed to round it.
 */

import { useMemo, useState } from "react";
import { Badge, cn, formatNaira, inputClass } from "@curavia/shared";

import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

const CATEGORY_LABEL: Record<string, string> = {
  consultation: "Consultations",
  laboratory: "Laboratory",
  radiology: "Imaging",
  nursing: "Nursing",
  procedure: "Procedures",
  maternity: "Maternity",
  theatre: "Theatre",
  bed: "Beds",
  vaccine: "Immunisation",
  pharmacy: "Pharmacy",
  admin: "Administrative",
  other: "Other",
};

/** "6500" or "6,500.50" in naira -> whole kobo. Never a float in the middle. */
function nairaToKobo(input: string): number {
  const cleaned = input.replace(/[^0-9.]/g, "");
  if (!cleaned) return 0;
  const [whole = "0", fraction = ""] = cleaned.split(".");
  const kobo = `${fraction}00`.slice(0, 2);
  return Number(whole) * 100 + Number(kobo);
}

function koboToNairaInput(kobo: number): string {
  const naira = Math.floor(kobo / 100);
  const remainder = kobo % 100;
  return remainder === 0 ? String(naira) : `${naira}.${String(remainder).padStart(2, "0")}`;
}

export function StepServices({ state, busy, onSubmit, onSkip }: StepProps) {
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(state.templates.services.filter((t) => t.common).map((t) => t.code)),
  );
  const [prices, setPrices] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      state.templates.services.map((t) => [t.code, koboToNairaInput(t.price_kobo)]),
    ),
  );

  const services = state.templates.services;
  const groups = useMemo(() => {
    const byCategory = new Map<string, typeof services>();
    for (const template of services) {
      const list = byCategory.get(template.category) ?? [];
      list.push(template);
      byCategory.set(template.category, list);
    }
    return [...byCategory.entries()];
  }, [services]);

  const total = [...chosen].reduce(
    (sum, code) => sum + nairaToKobo(prices[code] ?? "0"),
    0,
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
        onSubmit({
          rows: [...chosen].map((code) => ({
            code,
            price_kobo: nairaToKobo(prices[code] ?? "0"),
          })),
        });
      }}
    >
      <p className="mb-4 rounded-[var(--radius-control)] bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
        These prices are a <strong>starting point</strong>, not a recommendation — mid-range
        private figures so you can begin today. Change any of them now, or later under the price
        list. Each one is the self-pay price; HMO rates are entered against it afterwards.
      </p>

      <div className="space-y-5">
        {groups.map(([category, entries]) => (
          <section key={category}>
            <h3 className="mb-2 text-xs font-bold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              {CATEGORY_LABEL[category] ?? category}
            </h3>
            <ul className="space-y-2">
              {entries.map((template) => {
                const ticked = chosen.has(template.code);
                return (
                  <li
                    key={template.code}
                    className={cn(
                      "flex flex-wrap items-center gap-3 rounded-[var(--radius-control)] px-3 py-2 ring-1 ring-inset transition-colors",
                      ticked
                        ? "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                        : "bg-white ring-slate-300 dark:bg-slate-800 dark:ring-slate-600",
                    )}
                  >
                    <label className="flex min-w-0 flex-1 basis-48 cursor-pointer items-center gap-3">
                      <input
                        type="checkbox"
                        checked={ticked}
                        onChange={() => toggle(template.code)}
                        className="size-4 shrink-0"
                      />
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold text-slate-900 dark:text-white">
                          {template.name}
                        </span>
                        <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                          {template.code}
                        </span>
                      </span>
                    </label>

                    <label className="flex items-center gap-1.5">
                      <span className="text-sm font-semibold text-slate-500 dark:text-slate-400">
                        ₦
                      </span>
                      <input
                        inputMode="decimal"
                        value={prices[template.code] ?? ""}
                        disabled={!ticked}
                        onChange={(event) =>
                          setPrices((current) => ({
                            ...current,
                            [template.code]: event.target.value,
                          }))
                        }
                        className={cn(inputClass, "numeric min-h-10 w-28 text-right")}
                      />
                    </label>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>

      <p className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
        <Badge tone="neutral">{chosen.size} services</Badge>
        <span>
          A patient needing every one of them would be billed {formatNaira(total)} — a sanity
          check on the figures above, not a real bill.
        </span>
      </p>

      <StepActions busy={busy} onSkip={onSkip} />
    </form>
  );
}
