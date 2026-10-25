/**
 * A screen that is on the roadmap but not built.
 *
 * This exists because the server decides where each role lands, and it knows
 * about roles whose screens arrive at later milestones. A nurse signing in
 * today is sent to triage, and triage is milestone 6.
 *
 * The honest answer is to say so. The alternatives are both worse: a 404
 * makes a working system look broken, and quietly redirecting her to the
 * front desk makes her think triage does not exist — and she would be right
 * to stop looking for it.
 */

import { Badge, Button, Card, SectionTitle } from "@curavia/shared";

import { useAuth } from "../auth/useAuth";

import { PLANNED } from "./planned";

export function NotBuiltYet({ path }: { path: string }) {
  const { me } = useAuth();
  const planned = PLANNED[path];

  return (
    <div className="mx-auto w-full max-w-2xl p-4 sm:p-6">
      <SectionTitle>{planned?.title ?? "Not built yet"}</SectionTitle>

      <Card className="mt-4 space-y-4 p-5">
        <Badge tone="info">Arriving in {planned?.milestone ?? "a later milestone"}</Badge>

        <p className="text-sm text-slate-600 dark:text-slate-300">
          {planned?.blurb ??
            `The screen at ${path} has not been built yet. It is on the plan, and you were sent here because it is where your role will work from.`}
        </p>

        <p className="text-sm text-slate-600 dark:text-slate-300">
          Your account is set up correctly — this is where {me?.full_name ?? "you"} will land
          once it is ready. Nothing is wrong with your access.
        </p>

        <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-4 dark:border-slate-800">
          <Button variant="secondary" onClick={() => window.location.assign("/")}>
            Go to the front desk
          </Button>
        </div>
      </Card>
    </div>
  );
}
