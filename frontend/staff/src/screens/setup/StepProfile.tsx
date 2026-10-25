/**
 * Step 1 — the hospital's own details.
 *
 * The three registration numbers are asked for here but **checked by a human
 * later**, at the platform level, before the hospital goes live with real
 * patients. The form says so rather than implying a tick means verified:
 * a regular expression that accepts "RC-1234567" has checked nothing, and a
 * green tick beside it would be a lie a hospital might rely on.
 */

import { Field, inputClass } from "@curavia/shared";
import { useState } from "react";

import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

export function StepProfile({ state, busy, onSubmit, onSkip }: StepProps) {
  const [form, setForm] = useState({ ...state.organisation });

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(form);
      }}
    >
      <div className="space-y-4">
        <Field label="Hospital name">
          <input
            value={form.name}
            onChange={(event) => set("name", event.target.value)}
            className={inputClass}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Contact email">
            <input
              type="email"
              value={form.contact_email}
              onChange={(event) => set("contact_email", event.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Contact phone">
            <input
              value={form.contact_phone}
              onChange={(event) => set("contact_phone", event.target.value)}
              className={inputClass}
              placeholder="08031234567"
            />
          </Field>
        </div>

        <fieldset className="rounded-[var(--radius-control)] bg-slate-50 p-4 dark:bg-slate-800/60">
          <legend className="px-1 text-xs font-bold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            Registration
          </legend>
          <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
            Stored now, checked by us before your hospital goes live with real patients. You can
            fill these in later.
          </p>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="CAC number">
              <input
                value={form.cac_number}
                onChange={(event) => set("cac_number", event.target.value)}
                className={inputClass}
                placeholder="RC-1234567"
              />
            </Field>
            <Field label="MoH facility licence">
              <input
                value={form.moh_licence_number}
                onChange={(event) => set("moh_licence_number", event.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Medical director MDCN">
              <input
                value={form.medical_director_mdcn}
                onChange={(event) => set("medical_director_mdcn", event.target.value)}
                className={inputClass}
              />
            </Field>
          </div>
        </fieldset>

        <label className="flex cursor-pointer items-start gap-3 rounded-[var(--radius-control)] bg-slate-50 p-4 dark:bg-slate-800/60">
          <input
            type="checkbox"
            checked={form.share_records_across_branches}
            onChange={(event) => set("share_records_across_branches", event.target.checked)}
            className="mt-1 size-4 shrink-0"
          />
          <span>
            <span className="block text-sm font-semibold text-slate-900 dark:text-white">
              Share patient records between branches
            </span>
            <span className="block text-xs text-slate-500 dark:text-slate-400">
              A patient registered at one branch can be seen at another without re-registering.
              Every cross-branch read is written to your audit log. Leave this off if your
              branches are run as separate practices.
            </span>
          </span>
        </label>
      </div>

      <StepActions busy={busy} onSkip={onSkip} />
    </form>
  );
}
