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
import { Button, cn, useTheme } from "@curavia/shared";
import type { Branch, Me, ThemeChoice } from "@curavia/shared";

import { useAuth } from "./auth/useAuth";

import { api } from "./api";
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
  IconWarning,
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
        <MfaBanner />
        {/* pb-20 clears the bottom bar on phone and tablet. */}
        <main className="flex-1 pb-20 lg:pb-0">{children}</main>
      </div>

      <BottomBar path={path} />
    </div>
  );
}

// --------------------------------------------------------------------------

/**
 * Two-factor authentication is mandatory for staff, and this is what says so.
 *
 * It is a banner rather than a hard block, deliberately. A hospital that
 * cannot see any screen until every member of staff has enrolled is a
 * hospital that will share one account — which is worse than the thing the
 * rule protects against, because an audit log full of one name answers no
 * questions at all.
 *
 * The enrolment screen itself arrives with the rest of the account settings.
 * Until it does, this states the obligation rather than pretending it does
 * not exist.
 */
function MfaBanner() {
  const { me } = useAuth();
  if (!me || me.has_mfa) return null;

  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-warning/40 bg-warning-soft px-4 py-2 text-sm text-warning-ink dark:bg-warning/15 dark:text-warning"
    >
      <IconWarning className="size-4 shrink-0" />
      <span className="font-semibold">Two-factor authentication is not set up.</span>
      <span>
        Curavia requires it for every member of staff. You will be asked to enrol before this
        hospital can take real patients.
      </span>
    </div>
  );
}

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
  const { me } = useAuth();

  return (
    <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/95 px-4 py-2.5 backdrop-blur dark:border-slate-800 dark:bg-slate-900/95">
      <span className="lg:hidden">
        <Wordmark nameFrom="sm" />
      </span>

      <BranchSwitcher me={me} />

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

      <UserMenu me={me} />
    </header>
  );
}

function UserMenu({ me }: { me: Me | null }) {
  const { signOut } = useAuth();
  const name = me?.full_name ?? "";
  const jobTitle = me?.memberships[0]?.job_title ?? "";

  return (
    <span className="flex items-center gap-2 border-l border-slate-200 pl-3 dark:border-slate-700">
      <span className="hidden max-w-40 text-right leading-tight sm:block">
        <span className="block truncate text-xs font-semibold text-slate-900 dark:text-white">
          {name}
        </span>
        <span className="block truncate text-[0.6875rem] text-slate-500 dark:text-slate-400">
          {jobTitle}
        </span>
      </span>
      <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-brand-100 text-sm font-bold text-brand-800 dark:bg-brand-900 dark:text-brand-100">
        {initials(name)}
      </span>
      <Button
        size="sm"
        variant="ghost"
        className="whitespace-nowrap"
        onClick={() => void signOut()}
      >
        Sign out
      </Button>
    </span>
  );
}

function initials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0] ?? "")
    .join("")
    .toUpperCase();
}

/**
 * Which branch this person is working in.
 *
 * The list comes from their own membership, not from the hospital: somebody
 * who works at Ikeja only must not be able to pick Lekki from a dropdown and
 * discover that the server says no. Switching is a server call — it moves the
 * scope every later request is made under — so it reloads the profile rather
 * than just changing a label.
 */
function BranchSwitcher({ me }: { me: Me | null }) {
  const { reload } = useAuth();
  const [busy, setBusy] = useState(false);

  const branches: Branch[] = me?.memberships.flatMap((membership) => membership.branches) ?? [];
  const current = me?.active_branch?.id ?? "";
  const organisation = me?.organisation?.name ?? "";

  if (branches.length === 0) return null;

  async function choose(branchId: string) {
    if (!branchId || branchId === current) return;
    setBusy(true);
    try {
      await api.switchBranch(branchId);
      await reload();
    } finally {
      setBusy(false);
    }
  }

  // One branch is not a choice. Shown as a label so the person can still see
  // where they are, which is the half of this control that always matters.
  if (branches.length === 1) {
    return (
      // shrink-0: without it the search field beside this one, which grows,
      // squeezes the hospital's name down to three characters. The search box
      // can give up space; knowing which building you are in cannot.
      <span className="max-w-[40vw] shrink-0 truncate rounded-full bg-brand-50 px-3 py-2 text-sm font-semibold text-brand-900 ring-1 ring-brand-200 ring-inset sm:max-w-xs dark:bg-brand-900/40 dark:text-brand-100 dark:ring-brand-700">
        {organisation} · {branches[0]?.name}
      </span>
    );
  }

  return (
    // min-w-0 and a max width: the option text is the hospital name plus the
    // branch name, which is wider than a phone, and a select sizes itself to
    // its widest option. Without a cap it pushed the theme toggle and the
    // avatar off the right-hand edge of the screen.
    <label className="relative max-w-[40vw] shrink-0 sm:max-w-xs">
      <span className="sr-only">Branch</span>
      <select
        value={current}
        disabled={busy}
        onChange={(event) => void choose(event.target.value)}
        className="min-h-10 w-full appearance-none truncate rounded-full bg-brand-50 py-1.5 pr-8 pl-3 text-sm font-semibold text-brand-900 ring-1 ring-brand-200 ring-inset disabled:opacity-60 dark:bg-brand-900/40 dark:text-brand-100 dark:ring-brand-700"
      >
        {branches.map((branch) => (
          <option key={branch.id} value={branch.id}>
            {organisation} · {branch.name}
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
