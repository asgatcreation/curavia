/**
 * Staff and roles.
 *
 * Two lists on one screen — the people who work here, and the invitations
 * that have not been accepted — because they are two halves of one question
 * a practice manager asks every Monday: who is on the system, and who is
 * supposed to be but is not yet.
 *
 * Two things are deliberately prominent:
 *
 *   **Who has not enrolled in two-factor authentication.** Curavia requires
 *   it, so an unenrolled member of staff is somebody who is going to be
 *   locked out mid-shift. A hospital needs to chase them before that, not
 *   discover it at the ward door.
 *
 *   **Deactivating is not deleting.** The button says "End access", the
 *   confirmation says the notes stay, and the row stays on the list greyed
 *   out. A hospital that could erase who wrote a note could not answer the
 *   question an audit exists to answer.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiRequestError,
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  SectionTitle,
  StaffApi,
  cn,
  formatDate,
  inputClass,
} from "@curavia/shared";
import type { StaffInvitation, StaffMember, StaffRole } from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const staffApi = new StaffApi(api);

export function StaffAndRoles() {
  const { me } = useAuth();
  const canInvite = me?.permissions.includes("staff.invite") ?? false;
  const canDeactivate = me?.permissions.includes("staff.deactivate") ?? false;

  const [members, setMembers] = useState<StaffMember[]>([]);
  const [invitations, setInvitations] = useState<StaffInvitation[]>([]);
  const [roles, setRoles] = useState<StaffRole[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [inviting, setInviting] = useState(false);

  const load = useCallback(async () => {
    const [nextMembers, nextInvitations, nextRoles] = await Promise.all([
      staffApi.members(),
      staffApi.invitations(),
      staffApi.roles(),
    ]);
    setMembers(nextMembers);
    setInvitations(nextInvitations);
    setRoles(nextRoles);
    setLoading(false);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load().catch((caught: unknown) => {
      setError(messageFrom(caught));
      setLoading(false);
    });
  }, [load]);

  async function run(work: () => Promise<unknown>) {
    setError(null);
    try {
      await work();
      await load();
    } catch (caught) {
      setError(messageFrom(caught));
    }
  }

  const open = invitations.filter((invitation) => invitation.is_open);
  const unenrolled = members.filter((member) => member.is_active && !member.has_mfa);

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Staff and roles</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            {me?.organisation?.name ?? "This hospital"}
          </p>
        </div>
        {canInvite && (
          <Button variant="primary" onClick={() => setInviting((current) => !current)}>
            {inviting ? "Cancel" : "Invite somebody"}
          </Button>
        )}
      </div>

      {error && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
        >
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}

      {unenrolled.length > 0 && (
        <Card className="p-4 ring-warning/40">
          <p className="flex items-start gap-2 text-sm text-warning-ink dark:text-warning">
            <IconWarning className="mt-0.5 size-4 shrink-0" />
            <span>
              <strong>
                {unenrolled.length} {unenrolled.length === 1 ? "person has" : "people have"} not
                set up two-factor authentication.
              </strong>{" "}
              Curavia requires it, so they will be asked to before this hospital takes real
              patients. Chasing them now is easier than at a ward door.
            </span>
          </p>
        </Card>
      )}

      {inviting && canInvite && (
        <InviteForm
          roles={roles}
          branches={me?.memberships.flatMap((membership) => membership.branches) ?? []}
          onCancel={() => setInviting(false)}
          onInvited={async () => {
            setInviting(false);
            await load();
          }}
        />
      )}

      <Card className="overflow-hidden">
        <CardHeader title="Invitations not yet accepted" count={open.length} />
        {loading ? (
          <EmptyState title="Loading…" />
        ) : open.length === 0 ? (
          <EmptyState
            title="Nothing outstanding."
            hint="Everyone you have invited has joined."
          />
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {open.map((invitation) => (
              <li key={invitation.id} className="flex flex-wrap items-center gap-3 p-4">
                <span className="min-w-0 flex-1 basis-48">
                  <span className="block truncate font-semibold text-slate-900 dark:text-white">
                    {[invitation.first_name, invitation.last_name].filter(Boolean).join(" ") ||
                      invitation.email}
                  </span>
                  <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                    {invitation.email} · invited by {invitation.invited_by_name || "—"}
                  </span>
                </span>
                <span className="flex flex-wrap gap-1.5">
                  {invitation.roles.map((role) => (
                    <Badge key={role.id} tone="info">
                      {role.name}
                    </Badge>
                  ))}
                </span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  Expires {formatDate(invitation.expires_at)}
                </span>
                {canInvite && (
                  <span className="ml-auto flex gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => void run(() => staffApi.resend(invitation.id))}
                    >
                      Resend
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void run(() => staffApi.revoke(invitation.id))}
                    >
                      Revoke
                    </Button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="overflow-hidden">
        <CardHeader title="Staff" count={members.length} />
        {loading ? (
          <EmptyState title="Loading…" />
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {members.map((member) => (
              <li
                key={member.id}
                className={cn(
                  "flex flex-wrap items-center gap-3 p-4",
                  !member.is_active && "opacity-60",
                )}
              >
                <span className="min-w-0 flex-1 basis-48">
                  <span className="block truncate font-semibold text-slate-900 dark:text-white">
                    {member.full_name}
                  </span>
                  <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                    {member.email}
                    {member.job_title ? ` · ${member.job_title}` : ""}
                  </span>
                </span>

                <span className="flex flex-wrap gap-1.5">
                  {member.roles.map((role) => (
                    <Badge key={role.id} tone="neutral">
                      {role.name}
                    </Badge>
                  ))}
                  {!member.has_mfa && member.is_active && (
                    <Badge tone="warning">No two-factor</Badge>
                  )}
                  {!member.is_active && <Badge tone="critical">Access ended</Badge>}
                </span>

                {canDeactivate && member.is_active && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto"
                    onClick={() => {
                      const sure = window.confirm(
                        `End ${member.full_name}'s access to this hospital?\n\n` +
                          "They are signed out everywhere immediately. Nothing they wrote is " +
                          "deleted — their notes, prescriptions and audit trail stay exactly " +
                          "as they are, attributed to them.",
                      );
                      if (sure) void run(() => staffApi.deactivate(member.id));
                    }}
                  >
                    End access
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------

function InviteForm({
  roles,
  branches,
  onCancel,
  onInvited,
}: {
  roles: StaffRole[];
  branches: { id: string; name: string }[];
  onCancel: () => void;
  onInvited: () => Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [branchIds, setBranchIds] = useState<string[]>(() =>
    branches[0] ? [branches[0].id] : [],
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await staffApi.invite({
        email: email.trim().toLowerCase(),
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        job_title: jobTitle.trim(),
        role_ids: roleIds,
        branch_ids: branchIds,
      });
      await onInvited();
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setBusy(false);
    }
  }

  function toggle(list: string[], set: (next: string[]) => void, id: string) {
    set(list.includes(id) ? list.filter((value) => value !== id) : [...list, id]);
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader title="Invite somebody" />
      <form onSubmit={(event) => void submit(event)} className="space-y-4 p-4">
        {error && (
          <p
            role="alert"
            className="rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
          >
            {error}
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              Work email
            </span>
            <input
              autoFocus
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={inputClass}
            />
            <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
              They set their own password from the link we send, and enrol in two-factor
              authentication before they can see anything.
            </span>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              First name
            </span>
            <input
              value={firstName}
              onChange={(event) => setFirstName(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              Surname
            </span>
            <input
              value={lastName}
              onChange={(event) => setLastName(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              Job title
            </span>
            <input
              value={jobTitle}
              onChange={(event) => setJobTitle(event.target.value)}
              className={inputClass}
              placeholder="Staff nurse"
            />
          </label>
        </div>

        <fieldset>
          <legend className="mb-1.5 text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            Role
          </legend>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {roles.map((role) => (
              <label
                key={role.id}
                className={cn(
                  "flex min-h-11 cursor-pointer items-center gap-2.5 rounded-[var(--radius-control)] px-3 py-2 text-sm ring-1 ring-inset transition-colors",
                  roleIds.includes(role.id)
                    ? "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                    : "bg-white ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:ring-slate-600 dark:hover:bg-slate-700",
                )}
              >
                <input
                  type="checkbox"
                  checked={roleIds.includes(role.id)}
                  onChange={() => toggle(roleIds, setRoleIds, role.id)}
                  className="size-4 shrink-0"
                />
                <span className="min-w-0 flex-1 truncate font-semibold text-slate-900 dark:text-white">
                  {role.name}
                </span>
              </label>
            ))}
          </div>
          <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
            What they can do comes from the role, and from nowhere else. They cannot change it
            when they accept.
          </p>
        </fieldset>

        {branches.length > 1 && (
          <fieldset>
            <legend className="mb-1.5 text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              Branches
            </legend>
            <div className="flex flex-wrap gap-2">
              {branches.map((branch) => (
                <label
                  key={branch.id}
                  className={cn(
                    "flex min-h-10 cursor-pointer items-center gap-2 rounded-full px-3 text-sm ring-1 ring-inset",
                    branchIds.includes(branch.id)
                      ? "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                      : "bg-white ring-slate-300 dark:bg-slate-800 dark:ring-slate-600",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={branchIds.includes(branch.id)}
                    onChange={() => toggle(branchIds, setBranchIds, branch.id)}
                    className="size-4"
                  />
                  {branch.name}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-4 dark:border-slate-800">
          <Button
            type="submit"
            variant="primary"
            size="lg"
            disabled={busy || roleIds.length === 0 || branchIds.length === 0}
          >
            {busy ? "Sending…" : "Send invitation"}
          </Button>
          <Button type="button" variant="ghost" size="lg" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}

function messageFrom(caught: unknown): string {
  if (caught instanceof ApiRequestError) return caught.message;
  return "Could not reach the server. Check your connection and try again.";
}
