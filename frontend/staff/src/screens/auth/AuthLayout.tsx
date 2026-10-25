/**
 * The frame every signed-out screen sits in.
 *
 * One column, centred, nothing else on the page. Sign-in is the one screen
 * where a person has exactly one thing to do, and anything else on it is a
 * way to fail to do it.
 *
 * The strip on the right only appears from `lg` up. It is there for the
 * owner signing up on a laptop, who is being asked to trust an unfamiliar
 * product with their hospital's records; on a phone, where screen space is
 * the scarce thing, it is not shown at all.
 */

import type { ReactNode } from "react";
import { Card } from "@curavia/shared";

export function AuthLayout({
  title,
  blurb,
  children,
  footer,
}: {
  title: string;
  blurb?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="grid min-h-full lg:grid-cols-[minmax(0,1fr)_28rem]">
      <main className="flex items-center justify-center px-4 py-10 sm:px-6">
        <div className="w-full max-w-md">
          <span className="mb-8 flex items-center gap-2">
            <span className="flex size-9 items-center justify-center rounded-lg bg-brand-600 text-lg font-black text-white dark:bg-brand-500 dark:text-slate-950">
              C
            </span>
            <span className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">
              Curavia
            </span>
          </span>

          <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl dark:text-white">
            {title}
          </h1>
          {blurb && <p className="mt-2 text-slate-600 dark:text-slate-300">{blurb}</p>}

          <div className="mt-8">{children}</div>

          {footer && (
            <div className="mt-6 text-sm text-slate-600 dark:text-slate-400">{footer}</div>
          )}
        </div>
      </main>

      <aside className="hidden flex-col justify-center gap-5 bg-slate-900 px-10 py-12 lg:flex">
        <p className="text-sm font-bold tracking-wide text-brand-300 uppercase">
          One system for your whole hospital
        </p>
        <p className="text-2xl leading-snug font-semibold text-white">
          Front desk, triage, consultation, laboratory, pharmacy, cashier and wards — handing
          patients to each other as it happens.
        </p>
        <ul className="space-y-3 text-sm text-slate-300">
          <Point>Your records are unreachable from any other hospital.</Point>
          <Point>Every view of a patient record is written to an audit log you can read.</Point>
          <Point>Two-factor authentication for every member of staff.</Point>
        </ul>
      </aside>
    </div>
  );
}

function Point({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-brand-400" />
      <span>{children}</span>
    </li>
  );
}

/** A form-wide error. Field errors are rendered beside their field. */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
    >
      {message}
    </p>
  );
}

/** A quiet panel for "we have sent you an email" style outcomes. */
export function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
      <div className="mt-2 space-y-3 text-sm text-slate-600 dark:text-slate-300">{children}</div>
    </Card>
  );
}
