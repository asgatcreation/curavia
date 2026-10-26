/**
 * The primitives every Curavia screen is built from.
 *
 * Small on purpose. A hospital system is mostly dense lists, one banner and a
 * lot of buttons that commit something irreversible, so the kit optimises for
 * those three rather than trying to cover everything a general UI library
 * would.
 */

import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";
import type { StatusTone } from "./status";

// --------------------------------------------------------------------------
// Tone maps
//
// Written out in full rather than composed from template strings: Tailwind
// scans source text, so a class built at runtime is a class that never makes
// it into the stylesheet.
// --------------------------------------------------------------------------

const TONE_BADGE: Record<StatusTone, string> = {
  critical:
    "bg-critical-soft text-critical-ink ring-critical/30 dark:bg-critical/15 dark:text-critical dark:ring-critical/40",
  urgent:
    "bg-urgent-soft text-urgent-ink ring-urgent/30 dark:bg-urgent/15 dark:text-urgent dark:ring-urgent/40",
  warning:
    "bg-warning-soft text-warning-ink ring-warning/30 dark:bg-warning/15 dark:text-warning dark:ring-warning/40",
  stable:
    "bg-stable-soft text-stable-ink ring-stable/30 dark:bg-stable/15 dark:text-stable dark:ring-stable/40",
  info: "bg-info-soft text-info-ink ring-info/30 dark:bg-info/15 dark:text-info dark:ring-info/40",
  neutral:
    "bg-slate-100 text-slate-700 ring-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-600",
};

const TONE_DOT: Record<StatusTone, string> = {
  critical: "bg-critical",
  urgent: "bg-urgent",
  warning: "bg-warning",
  stable: "bg-stable",
  info: "bg-info",
  neutral: "bg-slate-400",
};

const TONE_BAR: Record<StatusTone, string> = {
  critical: "bg-critical",
  urgent: "bg-urgent",
  warning: "bg-warning",
  stable: "bg-stable",
  info: "bg-info",
  neutral: "bg-slate-300 dark:bg-slate-600",
};

// --------------------------------------------------------------------------
// Badge
// --------------------------------------------------------------------------

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: StatusTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5",
        "text-xs font-semibold whitespace-nowrap ring-1 ring-inset",
        TONE_BADGE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * A status dot that is never the only signal.
 *
 * `label` is required, not optional — the component will not let a caller
 * render colour on its own.
 */
export function StatusDot({ tone, label }: { tone: StatusTone; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden="true"
        className={cn("size-2.5 shrink-0 rounded-full ring-2 ring-white dark:ring-slate-900", TONE_DOT[tone])}
      />
      <span className="text-sm font-medium">{label}</span>
    </span>
  );
}

/** The coloured edge down the left of a queue card. Decorative; paired with a badge. */
export function ToneBar({ tone }: { tone: StatusTone }) {
  return <span aria-hidden="true" className={cn("absolute inset-y-0 left-0 w-1.5", TONE_BAR[tone])} />;
}

// --------------------------------------------------------------------------
// Button
// --------------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-brand-600 text-white hover:bg-brand-700 active:bg-brand-800 dark:bg-brand-500 dark:hover:bg-brand-400 dark:text-slate-950",
  secondary:
    "bg-white text-slate-800 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-600 dark:hover:bg-slate-700",
  ghost:
    "text-slate-600 hover:bg-slate-200/70 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
  danger: "bg-critical text-white hover:brightness-110 active:brightness-95",
};

const BUTTON_SIZE: Record<ButtonSize, string> = {
  // min-h on every size: the floor is the gloved finger, not the font.
  sm: "min-h-9 px-3 text-sm gap-1.5",
  md: "min-h-11 px-4 text-sm gap-2",
  lg: "min-h-12 px-5 text-base gap-2",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center rounded-[var(--radius-control)]",
        "font-semibold transition-colors",
        "disabled:pointer-events-none disabled:opacity-50",
        BUTTON_VARIANT[variant],
        BUTTON_SIZE[size],
        className,
      )}
      {...rest}
    />
  );
}

// --------------------------------------------------------------------------
// Card and section furniture
// --------------------------------------------------------------------------

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-[var(--radius-card)] bg-white ring-1 ring-slate-200",
        "dark:bg-slate-900 dark:ring-slate-800",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({
  title,
  count,
  actions,
}: {
  title: string;
  count?: number | undefined;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
      <h2 className="flex items-center gap-2 text-sm font-semibold tracking-wide text-slate-700 uppercase dark:text-slate-300">
        {title}
        {count !== undefined && (
          <span className="numeric rounded-full bg-slate-200 px-2 py-0.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
            {count}
          </span>
        )}
      </h2>
      {actions}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">{children}</h2>
  );
}

export function EmptyState({
  title,
  hint,
}: {
  title: string;
  // `| undefined` as well as `?`: exactOptionalPropertyTypes is on, so a
  // caller passing a computed value that may be undefined is rejected
  // without it, and every such caller would have to build its props object
  // conditionally.
  hint?: string | undefined;
}) {
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-sm font-medium text-slate-600 dark:text-slate-300">{title}</p>
      {hint && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
    </div>
  );
}

// --------------------------------------------------------------------------
// Stat — the dashboard tile
// --------------------------------------------------------------------------

export function Stat({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: StatusTone;
}) {
  return (
    <Card className="relative overflow-hidden p-4">
      <ToneBar tone={tone} />
      <p className="pl-2 text-xs font-medium tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {label}
      </p>
      {/* The type size follows the length of the value.
       *
       * These tiles carry counts ("14") beside amounts of money
       * ("₦6,106,600.00"), and a group's daily takings are four times as wide
       * as a queue length. Wrapping was worse than small: it split a number
       * across two lines mid-digit, which reads as two numbers. Truncating
       * would be worse still. So the figure shrinks to fit, and the tile
       * always shows the whole of it. */}
      <p
        className={cn(
          "numeric mt-1 pl-2 font-bold whitespace-nowrap text-slate-900 dark:text-white",
          value.length > 10 ? "text-lg" : value.length > 7 ? "text-xl" : "text-2xl",
        )}
      >
        {value}
      </p>
      {sub && <p className="mt-1 pl-2 text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
    </Card>
  );
}

// --------------------------------------------------------------------------
// Avatar — initials only
//
// No photographs anywhere in Curavia's staff UI. A face on a queue board is a
// patient identifier visible to everyone who walks past the screen.
// --------------------------------------------------------------------------

export function Avatar({ name, className }: { name: string; className?: string }) {
  const initials = name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0] ?? "")
    .join("")
    .toUpperCase();

  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex size-9 shrink-0 items-center justify-center rounded-full",
        "bg-brand-100 text-sm font-bold text-brand-800",
        "dark:bg-brand-900 dark:text-brand-100",
        className,
      )}
    >
      {initials}
    </span>
  );
}

// --------------------------------------------------------------------------
// Field
// --------------------------------------------------------------------------

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
        {label}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">{hint}</span>}
    </label>
  );
}

export const inputClass = cn(
  "w-full min-h-11 rounded-[var(--radius-control)] px-3",
  "bg-white text-slate-900 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400",
  "dark:bg-slate-800 dark:text-white dark:ring-slate-600 dark:placeholder:text-slate-500",
);
