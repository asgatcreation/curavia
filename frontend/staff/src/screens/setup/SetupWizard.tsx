/**
 * The setup wizard.
 *
 * Seven steps, and the person doing them is usually a medical director at the
 * end of a clinic who has about fifteen minutes. Three things follow from
 * that:
 *
 * 1. **Every step can be left and come back to.** Progress is on the server,
 *    not in this component's state, so closing the laptop loses nothing.
 * 2. **Every step has a sensible default already filled in.** Accepting what
 *    is on screen and pressing Continue produces a working hospital. The
 *    lists are a starting point, not a questionnaire.
 * 3. **The optional steps say they are optional** and have a Skip beside
 *    Continue, with the skip recorded as a skip — so nobody is later told the
 *    wards were set up when they were not.
 *
 * The rail shows "Step 4 of 7" because a wizard with no visible end is one
 * people abandon.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiRequestError, Button, Card, SectionTitle, SetupApi, cn } from "@curavia/shared";
import type { SetupState } from "@curavia/shared";

import { api } from "../../api";
import { useAuth } from "../../auth/useAuth";
import { IconWarning } from "../../icons";
import { StepBranches } from "./StepBranches";
import { StepDepartments } from "./StepDepartments";
import { StepPayers } from "./StepPayers";
import { StepProfile } from "./StepProfile";
import { StepServices } from "./StepServices";
import { StepStaff } from "./StepStaff";
import { StepWards } from "./StepWards";
import type { StepProps } from "./types";

const setupApi = new SetupApi(api);

const STEP_COMPONENTS: Record<string, (props: StepProps) => React.JSX.Element> = {
  profile: StepProfile,
  branches: StepBranches,
  departments: StepDepartments,
  wards: StepWards,
  services: StepServices,
  payers: StepPayers,
  staff: StepStaff,
};

export function SetupWizard() {
  const { reload } = useAuth();
  const navigate = useNavigate();

  const [state, setState] = useState<SetupState | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const next = await setupApi.state();
    setState(next);
    // Open on the first unfinished step, unless the person has navigated to
    // another one themselves. `??=` rather than an overwrite: re-loading must
    // not yank somebody off a step they deliberately went back to.
    setActive((current) => current ?? next.next_step ?? next.steps[0]?.key ?? null);
  }, []);

  useEffect(() => {
    // The rule disabled here objects because it can see setState inside
    // `load`. Nothing is set synchronously: `load` awaits a request first.
    // This is the "fetch what the screen needs on mount" case.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load().catch((caught: unknown) => {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not load the setup wizard. Check your connection.",
      );
    });
  }, [load]);

  if (error && !state) {
    return (
      <Shell>
        <Card className="p-5">
          <p className="flex items-start gap-2 text-sm text-critical-ink dark:text-critical">
            <IconWarning className="mt-0.5 size-4 shrink-0" />
            {error}
          </p>
        </Card>
      </Shell>
    );
  }

  if (!state) {
    return (
      <Shell>
        <p className="text-slate-500 dark:text-slate-400">Loading…</p>
      </Shell>
    );
  }

  // Every step done or skipped. Not a redirect: somebody who has just spent
  // ten minutes on this deserves to be told it worked, and to be able to go
  // back into a step they rushed.
  if (!active) {
    return (
      <Shell>
        <Rail steps={state.steps} active="" onPick={(key) => setActive(key)} />
        <Card className="mt-5 p-6">
          <h2 className="text-lg font-bold text-slate-900 dark:text-white">
            {state.organisation.name} is ready.
          </h2>
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Count label="Branches" value={state.counts["branches"] ?? 0} />
            <Count label="Departments" value={state.counts["departments"] ?? 0} />
            <Count label="Services priced" value={state.counts["services"] ?? 0} />
            <Count label="Beds" value={state.counts["beds"] ?? 0} />
          </dl>
          <p className="mt-4 text-sm text-slate-600 dark:text-slate-300">
            You can start seeing patients. Anything above can be changed under Settings, and
            any step you skipped is still there if you want to come back to it.
          </p>
          <Button
            variant="primary"
            size="lg"
            className="mt-5"
            onClick={() => void navigate({ to: "/" })}
          >
            Go to my hospital
          </Button>
        </Card>
      </Shell>
    );
  }

  const index = state.steps.findIndex((step) => step.key === active);
  const step = state.steps[index];
  const Step = STEP_COMPONENTS[active];
  if (!step || !Step) return <Shell>Unknown step.</Shell>;

  async function run(work: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await work();
      const refreshed = await setupApi.state();
      setState(refreshed);

      // Move to the next unfinished step rather than simply the next one in
      // the list: somebody who jumped back to fix the profile should be
      // returned to where they actually were. `null` means every step is
      // done or skipped, and the wizard shows its finished panel.
      setActive(refreshed.next_step);

      // The moment the REQUIRED steps are done, `/me` reports the hospital as
      // set up and the gate that forced this wizard stops forcing it. If the
      // wizard is only on screen because of that gate, it would vanish
      // mid-flow and take the two optional steps with it. Moving to its own
      // route first keeps it on screen until the person decides to leave.
      if (refreshed.complete && window.location.pathname !== "/setup") {
        await navigate({ to: "/setup" });
      }
      await reload();
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not save that. Check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <Rail
        steps={state.steps}
        active={active}
        onPick={(key) => {
          setError(null);
          setActive(key);
        }}
      />

      <Card className="mt-5 overflow-hidden">
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <p className="text-xs font-bold tracking-wide text-brand-700 uppercase dark:text-brand-300">
            Step {index + 1} of {state.steps.length}
            {!step.required && " · optional"}
          </p>
          <h2 className="mt-1 text-lg font-bold text-slate-900 dark:text-white">{step.title}</h2>
          <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{step.blurb}</p>
        </div>

        <div className="p-5">
          {error && (
            <p
              role="alert"
              className="mb-4 flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
            >
              <IconWarning className="mt-0.5 size-4 shrink-0" />
              {error}
            </p>
          )}

          <Step
            state={state}
            busy={busy}
            onSubmit={(payload) => void run(() => setupApi.apply(step.key, payload))}
            onSkip={step.required ? undefined : () => void run(() => setupApi.skip(step.key))}
          />
        </div>
      </Card>

      <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
        Everything here can be changed later under Settings. Nothing is locked in by finishing
        the wizard.
      </p>
    </Shell>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-800/60">
      <dt className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {label}
      </dt>
      <dd className="numeric mt-0.5 text-2xl font-bold text-slate-900 dark:text-white">{value}</dd>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-4xl p-4 sm:p-6">
      <SectionTitle>Set up your hospital</SectionTitle>
      <p className="mt-0.5 mb-5 text-sm text-slate-500 dark:text-slate-400">
        You can stop at any point and pick up where you left off.
      </p>
      {children}
    </div>
  );
}

/**
 * The progress rail.
 *
 * Every step is clickable, including ones not reached yet. A wizard that
 * refuses to let you look ahead is one you cannot plan around, and these
 * steps have no real ordering constraint beyond departments needing a branch
 * — which the department step checks for itself.
 */
function Rail({
  steps,
  active,
  onPick,
}: {
  steps: SetupState["steps"];
  active: string;
  onPick: (key: string) => void;
}) {
  return (
    <ol className="flex gap-1.5 overflow-x-auto pb-1">
      {steps.map((step, index) => {
        const isActive = step.key === active;
        return (
          <li key={step.key} className="min-w-0 flex-1">
            <button
              type="button"
              onClick={() => onPick(step.key)}
              aria-current={isActive ? "step" : undefined}
              className={cn(
                "flex w-full min-h-11 flex-col justify-center rounded-[var(--radius-control)] px-2.5 py-2 text-left transition-colors",
                isActive
                  ? "bg-brand-600 text-white dark:bg-brand-500 dark:text-slate-950"
                  : step.done
                    ? "bg-stable-soft text-stable-ink hover:brightness-95 dark:bg-stable/15 dark:text-stable"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700",
              )}
            >
              {/* On a phone the titles truncate to two letters, which tells
                  nobody anything. The number and the tick do, and the step
                  the person is actually on has its full title in the card
                  header below. */}
              <span className="numeric text-xs font-bold opacity-80 sm:text-[0.6875rem]">
                {step.done ? "✓" : index + 1}
                <span className="hidden sm:inline">{step.done ? " done" : ""}</span>
              </span>
              <span className="hidden truncate text-xs font-semibold sm:block">{step.title}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
