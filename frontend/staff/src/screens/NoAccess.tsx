/**
 * Where somebody lands when their role grants nothing yet.
 *
 * Not an error page. A hospital can legitimately create a role, assign it,
 * and not get round to granting it anything until the end of the week — and
 * in the meantime that person signs in and sees a working application with
 * nothing in it, which looks like a broken system rather than an incomplete
 * setup.
 *
 * So it says what has happened, who can fix it, and nothing else.
 */

import { Card, SectionTitle } from "@curavia/shared";

import { useAuth } from "../auth/useAuth";

export function NoAccess() {
  const { me } = useAuth();
  const membership = me?.memberships[0];

  return (
    <div className="mx-auto w-full max-w-2xl p-4 sm:p-6">
      <SectionTitle>Nothing has been assigned to you yet</SectionTitle>

      <Card className="mt-4 space-y-4 p-5 text-sm text-slate-600 dark:text-slate-300">
        <p>
          Your account at <strong>{membership?.organisation_name ?? "this hospital"}</strong> is
          active, but the role you hold
          {membership?.roles.length ? ` — ${membership.roles.join(", ")} — ` : " "}
          does not grant access to any screen yet.
        </p>
        <p>
          This is a settings question rather than a fault. Whoever administers Curavia here —
          usually the medical director or the facility administrator — can grant your role what
          it needs under <strong>Staff and roles</strong>.
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Signed in as {me?.email}. If you think you are at the wrong hospital, check the branch
          selector at the top of the screen.
        </p>
      </Card>
    </div>
  );
}
