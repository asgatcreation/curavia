/**
 * Step 7 — invite the staff.
 *
 * The invitation flow itself is the next slice of milestone 4. Rather than
 * show a form that does not work, this step says plainly where it is and what
 * will happen, and lets the hospital finish setup without it — which is
 * honest and is also correct, because a hospital can be set up by its owner
 * on a Sunday and staffed on the Monday.
 */

import { Badge } from "@curavia/shared";

import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

export function StepStaff({ busy, onSubmit, onSkip }: StepProps) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ rows: [] });
      }}
    >
      <Badge tone="info">Coming in the next update</Badge>

      <div className="mt-3 space-y-3 text-sm text-slate-600 dark:text-slate-300">
        <p>
          Staff invitations are being finished now. When they land, you will enter a name, an
          email and a role, and each person gets a link that asks them to set their own password
          and enrol in two-factor authentication before they can see anything.
        </p>
        <p>
          You do not need them to finish setting up. Carry on, and invite people when you are
          ready.
        </p>
      </div>

      <StepActions busy={busy} onSkip={onSkip} label="Finish setup" />
    </form>
  );
}
