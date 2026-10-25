import { Button } from "@curavia/shared";

/**
 * The Continue / Skip pair, identical on every step.
 *
 * Skip is a quiet ghost button rather than a second primary: it is a real
 * choice, not an equal one. And it says "Skip for now", because a hospital
 * that skips wards today may well admit patients next month — the step is
 * recorded as skipped, not as impossible.
 */
export function StepActions({
  busy,
  onSkip,
  label = "Save and continue",
}: {
  busy: boolean;
  onSkip?: (() => void) | undefined;
  label?: string;
}) {
  return (
    <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4 dark:border-slate-800">
      <Button type="submit" variant="primary" size="lg" disabled={busy}>
        {busy ? "Saving…" : label}
      </Button>
      {onSkip && (
        <Button type="button" variant="ghost" size="lg" disabled={busy} onClick={onSkip}>
          Skip for now
        </Button>
      )}
    </div>
  );
}
