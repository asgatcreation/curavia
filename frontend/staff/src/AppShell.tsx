/**
 * The frame every staff screen sits in.
 *
 * Three shapes, one component:
 *
 *   phone   — content fills the screen, navigation is a bottom bar under the
 *             thumb. Hospital staff on a phone are walking; a hamburger menu
 *             two taps deep is a menu nobody opens.
 *   tablet  — the same bottom bar, wider content. This is the ward trolley
 *             and the triage desk, and it is the width most of this system
 *             will actually be used at.
 *   desktop — a fixed sidebar, because the front desk and the cashier sit at
 *             a real machine all day and a label beside the icon is faster
 *             than a tooltip.
 *
 * The branch switcher is in the top bar on every width, never hidden in a
 * menu. A clerk who thinks they are booking at Ikeja while the system is set
 * to Lekki creates a record in the wrong place, and that is expensive to undo.
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { cn, useTheme } from "@curavia/shared";
import type { ThemeChoice } from "@curavia/shared";
import { DEMO_ORGANISATION, DEMO_USER } from "@curavia/shared";

import {
  IconBoard,
  IconChart,
  IconChevron,
  IconDesk,
  IconDisplay,
  IconMoon,
  IconPill,
  IconSearch,
  IconStethoscope,
  IconSun,
} from "./icons";

interface NavItem {
  to: string;
  label: string;
  short: string;
  icon: (props: { className?: string }) => ReactNode;
}

const NAV: NavItem[] = [
  { to: "/", label: "Front desk", short: "Desk", icon: IconDesk },
  { to: "/consultation", label: "Consultation", short: "Consult", icon: IconStethoscope },
  { to: "/pharmacy", label: "Pharmacy", short: "Pharmacy", icon: IconPill },
  { to: "/journey", label: "Journey board", short: "Journey", icon: IconBoard },
  { to: "/group", label: "Group", short: "Group", icon: IconChart },
];

export function AppShell({ children }: { children: ReactNode }) {
  const path = useRouterState({ select: (state) => state.location.pathname });

  return (
    <div className="flex min-h-full flex-col lg:flex-row">
      <DesktopSidebar path={path} />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        {/* pb-20 clears the bottom bar on phone and tablet. */}
        <main className="flex-1 pb-20 lg:pb-0">{children}</main>
      </div>

      <BottomBar path={path} />
    </div>
  );
}

// --------------------------------------------------------------------------

/**
 * `nameFrom` is the width at which the word "Curavia" joins the mark. In the
 * top bar on a phone the mark alone has to earn its place against the branch
 * switcher, which is the control that actually matters there.
 */
function Wordmark({ nameFrom = "" }: { nameFrom?: string }) {
  return (
    <span className="flex shrink-0 items-center gap-2">
      <span className="flex size-8 items-center justify-center rounded-lg bg-brand-600 text-base font-black text-white dark:bg-brand-500 dark:text-slate-950">
        C
      </span>
      <span
        className={cn(
          "text-lg font-bold tracking-tight text-slate-900 dark:text-white",
          nameFrom === "sm" ? "hidden sm:inline" : "",
        )}
      >
        Curavia
      </span>
    </span>
  );
}

function isActive(path: string, to: string): boolean {
  return to === "/" ? path === "/" : path.startsWith(to);
}

function DesktopSidebar({ path }: { path: string }) {
  return (
    <nav
      aria-label="Departments"
      className="hidden w-60 shrink-0 flex-col gap-1 border-r border-slate-200 bg-white px-3 py-4 lg:flex dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="px-2 pb-4">
        <Wordmark />
      </div>

      {NAV.map((item) => {
        const active = isActive(path, item.to);
        return (
          <Link
            key={item.to}
            to={item.to}
            className={cn(
              "flex min-h-11 items-center gap-3 rounded-[var(--radius-control)] px-3 text-sm font-semibold transition-colors",
              active
                ? "bg-brand-50 text-brand-800 dark:bg-brand-900/50 dark:text-brand-100"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white",
            )}
            aria-current={active ? "page" : undefined}
          >
            <item.icon />
            {item.label}
          </Link>
        );
      })}

      <p className="mt-auto px-3 text-xs leading-relaxed text-slate-400 dark:text-slate-500">
        Milestone 3 · visual direction. These screens run on synthetic data.
      </p>
    </nav>
  );
}

function BottomBar({ path }: { path: string }) {
  return (
    <nav
      aria-label="Departments"
      className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-slate-200 bg-white/95 backdrop-blur lg:hidden dark:border-slate-800 dark:bg-slate-900/95"
    >
      {NAV.map((item) => {
        const active = isActive(path, item.to);
        return (
          <Link
            key={item.to}
            to={item.to}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex min-h-[var(--size-touch)] flex-col items-center justify-center gap-0.5 py-2 text-[0.6875rem] font-semibold",
              active
                ? "text-brand-700 dark:text-brand-300"
                : "text-slate-500 dark:text-slate-400",
            )}
          >
            <item.icon />
            {item.short}
          </Link>
        );
      })}
    </nav>
  );
}

function TopBar() {
  const [branch, setBranch] = useState<string>(DEMO_ORGANISATION.branches[0].name);

  return (
    <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/95 px-4 py-2.5 backdrop-blur dark:border-slate-800 dark:bg-slate-900/95">
      <span className="lg:hidden">
        <Wordmark nameFrom="sm" />
      </span>

      <BranchSwitcher value={branch} onChange={setBranch} />

      {/* Search is the front desk's primary tool, so it gets real width on
          desktop and collapses to an icon-sized field on a phone. */}
      <label className="relative ml-auto hidden min-w-0 flex-1 sm:block sm:max-w-xs">
        <span className="sr-only">Search patients</span>
        <IconSearch className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" />
        <input
          type="search"
          placeholder="Search patients"
          className="min-h-10 w-full rounded-full bg-slate-100 pr-3 pl-9 text-sm text-slate-900 ring-1 ring-transparent ring-inset placeholder:text-slate-400 focus:bg-white focus:ring-brand-500 dark:bg-slate-800 dark:text-white dark:placeholder:text-slate-500 dark:focus:bg-slate-800"
        />
      </label>

      <ThemeToggle />

      <span className="flex items-center gap-2 border-l border-slate-200 pl-3 dark:border-slate-700">
        <span className="hidden text-right leading-tight sm:block">
          <span className="block text-xs font-semibold text-slate-900 dark:text-white">
            {DEMO_USER.full_name}
          </span>
          <span className="block text-[0.6875rem] text-slate-500 dark:text-slate-400">
            {DEMO_USER.job_title}
          </span>
        </span>
        <span className="flex size-9 items-center justify-center rounded-full bg-brand-100 text-sm font-bold text-brand-800 dark:bg-brand-900 dark:text-brand-100">
          {DEMO_USER.initials}
        </span>
      </span>
    </header>
  );
}

function BranchSwitcher({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  return (
    // min-w-0 and a max width: the option text is "Oakfield Medical Centre ·
    // Ikeja", which is wider than a phone, and a select sizes itself to its
    // widest option. Without a cap it pushed the theme toggle and the avatar
    // off the right-hand edge of the screen.
    <label className="relative min-w-0 max-w-[48vw] sm:max-w-none">
      <span className="sr-only">Branch</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-10 w-full appearance-none truncate rounded-full bg-brand-50 py-1.5 pr-8 pl-3 text-sm font-semibold text-brand-900 ring-1 ring-brand-200 ring-inset dark:bg-brand-900/40 dark:text-brand-100 dark:ring-brand-700"
      >
        {DEMO_ORGANISATION.branches.map((option) => (
          <option key={option.id} value={option.name}>
            {DEMO_ORGANISATION.name} · {option.name}
          </option>
        ))}
      </select>
      <IconChevron className="pointer-events-none absolute top-1/2 right-2 size-4 -translate-y-1/2 text-brand-700 dark:text-brand-300" />
    </label>
  );
}

const THEMES: { value: ThemeChoice; label: string; icon: (p: { className?: string }) => ReactNode }[] =
  [
    { value: "light", label: "Light", icon: IconSun },
    { value: "dark", label: "Dark", icon: IconMoon },
    { value: "system", label: "System", icon: IconDisplay },
  ];

function ThemeToggle() {
  const [choice, setChoice] = useTheme();

  return (
    <div
      role="group"
      aria-label="Colour theme"
      // ml-auto below sm, where the search field it normally sits beside is
      // hidden and there is nothing else to push it right.
      className="ml-auto flex shrink-0 items-center gap-0.5 rounded-full bg-slate-100 p-0.5 sm:ml-0 dark:bg-slate-800"
    >
      {THEMES.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => setChoice(option.value)}
          aria-pressed={choice === option.value}
          title={option.label}
          className={cn(
            "flex size-8 items-center justify-center rounded-full transition-colors",
            choice === option.value
              ? "bg-white text-brand-700 shadow-sm dark:bg-slate-700 dark:text-brand-300"
              : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-white",
          )}
        >
          <option.icon className="size-4" />
          <span className="sr-only">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
