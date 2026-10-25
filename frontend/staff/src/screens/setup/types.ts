import type { SetupState } from "@curavia/shared";

/**
 * What every wizard step is given.
 *
 * `onSkip` is present only on optional steps, so a step component cannot
 * offer a Skip button it is not entitled to — the wizard decides, from what
 * the server said, rather than each step remembering.
 */
export interface StepProps {
  state: SetupState;
  busy: boolean;
  onSubmit: (payload: unknown) => void;
  onSkip?: (() => void) | undefined;
}
