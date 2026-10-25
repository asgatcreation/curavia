/**
 * Step 7 — invite the staff.
 *
 * A real invitation form, not a placeholder, but a deliberately small one:
 * email and role, nothing else. Setting up a hospital at the end of a clinic
 * is not the moment to enter forty people's job titles, and the full staff
 * screen does the rest properly.
 *
 * Each invitation is sent as it is added, rather than collected and sent in a
 * batch at the end. Somebody who invites five people and then closes the
 * laptop has sent five invitations, not none.
 */

import { useEffect, useState } from "react";
import { ApiRequestError, Badge, Button, StaffApi, cn, inputClass } from "@curavia/shared";
import type { StaffRole } from "@curavia/shared";

import { api } from "../../api";
import { IconWarning } from "../../icons";
import { StepActions } from "./StepActions";
import type { StepProps } from "./types";

const staffApi = new StaffApi(api);

export function StepStaff({ state, busy, onSubmit, onSkip }: StepProps) {
  const [roles, setRoles] = useState<StaffRole[]>([]);
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState("");
  const [sent, setSent] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    void staffApi
      .roles()
      .then((next) => {
        setRoles(next);
        // Default to a role that is not the owner: inviting a second owner is
        // a real thing a hospital does, and exactly the wrong default.
        setRoleId(next.find((role) => role.slug !== "owner")?.id ?? next[0]?.id ?? "");
      })
      .catch(() => setError("Could not load your roles. You can invite people later."));
  }, []);

  const branchIds = state.branches.map((branch) => branch.id);

  async function invite() {
    const address = email.trim().toLowerCase();
    if (!address || !roleId) return;
    setError(null);
    setSending(true);
    try {
      await staffApi.invite({
        email: address,
        role_ids: [roleId],
        branch_ids: branchIds,
      });
      setSent((current) => [...current, address]);
      setEmail("");
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not send that invitation. Try again.",
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ rows: [] });
      }}
    >
      <p className="mb-4 text-sm text-slate-600 dark:text-slate-300">
        Each person gets a link that asks them to set their own password and enrol in two-factor
        authentication. What they can do comes from the role you choose here.
      </p>

      {error && (
        <p
          role="alert"
          className="mb-3 flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
        >
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-end gap-3 rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-800/60">
        <label className="block min-w-0 flex-1 basis-56">
          <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            Their work email
          </span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={cn(inputClass, "min-h-10")}
            placeholder="nurse@yourhospital.test"
          />
        </label>

        <label className="block min-w-0 basis-44">
          <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            Role
          </span>
          <select
            value={roleId}
            onChange={(event) => setRoleId(event.target.value)}
            className="min-h-10 w-full rounded-[var(--radius-control)] bg-white px-3 text-sm text-slate-900 ring-1 ring-slate-300 ring-inset dark:bg-slate-800 dark:text-white dark:ring-slate-600"
          >
            {roles.map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </select>
        </label>

        <Button
          type="button"
          variant="secondary"
          disabled={sending || !email.trim() || !roleId}
          onClick={() => void invite()}
        >
          {sending ? "Sending…" : "Send invitation"}
        </Button>
      </div>

      {sent.length > 0 && (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
          <Badge tone="stable">
            {sent.length} invitation{sent.length === 1 ? "" : "s"} sent
          </Badge>
          {sent.join(", ")}
        </p>
      )}

      <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
        You do not have to do this now. Staff and roles live under Settings, where you can also
        resend, withdraw and end access.
      </p>

      <StepActions busy={busy} onSkip={onSkip} label="Finish setup" />
    </form>
  );
}
