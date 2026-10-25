/**
 * Group dashboard — the owner's screen.
 *
 * A hospital group's owner is not a clinician and is usually not on site.
 * The question is always the same: is each branch coping, and is the money
 * coming in.
 *
 * **It shows only what the system can actually measure, and says so about
 * the rest.** Patients waiting, bed occupancy and money arrive at milestones
 * 5, 8 and 10, so those tiles are drawn as "waiting for milestone N" rather
 * than filled with a plausible figure. An owner decides whether to put a
 * second doctor on a Tuesday from this screen; a number that looks real and
 * is not would be worse than no screen, and once one tile is invented none
 * of the others can be trusted.
 *
 * What it deliberately does NOT show: any patient's name. The owner role can
 * see counts, beds, staff and alerts across branches without opening a
 * single record — and the endpoint behind this has nothing to leak.
 */

import { useEffect, useState } from "react";
import {
  ApiRequestError,
  Badge,
  Card,
  CardHeader,
  ReportingApi,
  SectionTitle,
  Stat,
  cn,
} from "@curavia/shared";
import type { GroupAlert, GroupSummary, PendingMeasure } from "@curavia/shared";

import { api } from "../api";
import { IconWarning } from "../icons";

const reporting = new ReportingApi(api);

export function GroupDashboard() {
  const [data, setData] = useState<GroupSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void reporting
      .group()
      .then(setData)
      .catch((caught: unknown) =>
        setError(
          caught instanceof ApiRequestError
            ? caught.message
            : "Could not load the dashboard. Check your connection.",
        ),
      );
  }, []);

  if (error) {
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

  if (!data) {
    return (
      <Shell>
        <p className="text-slate-500 dark:text-slate-400">Loading…</p>
      </Shell>
    );
  }

  const { totals } = data;

  return (
    <Shell
      title={data.organisation.name}
      subtitle={`${totals.branches} branch${totals.branches === 1 ? "" : "es"} · as at ${new Date(
        data.as_of,
      ).toLocaleTimeString("en-NG", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Africa/Lagos",
      })} WAT`}
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Staff on the system"
          value={String(totals.staff_active)}
          tone="info"
          sub={
            totals.invitations_open > 0
              ? `${totals.invitations_open} invitation${totals.invitations_open === 1 ? "" : "s"} outstanding`
              : "Everyone invited has joined"
          }
        />
        <Stat
          label="Without two-factor"
          value={String(totals.staff_without_mfa)}
          tone={totals.staff_without_mfa > 0 ? "warning" : "stable"}
          sub={totals.staff_without_mfa > 0 ? "Required before real patients" : "All enrolled"}
        />
        <Stat
          label="Services priced"
          value={String(totals.services_priced)}
          tone={totals.services_priced > 0 ? "stable" : "critical"}
          sub={`${totals.payers} payer${totals.payers === 1 ? "" : "s"} besides self-pay`}
        />
        <Stat
          label="Beds"
          value={String(totals.beds)}
          tone="neutral"
          sub={`${totals.departments} department${totals.departments === 1 ? "" : "s"}`}
        />
      </div>

      {data.alerts.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader title="Needs attention" count={data.alerts.length} />
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {data.alerts.map((alert, index) => (
              <AlertRow key={`${alert.message}-${index}`} alert={alert} />
            ))}
          </ul>
        </Card>
      )}

      <Card className="overflow-hidden">
        <CardHeader title="Branches" count={data.branches.length} />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs tracking-wide text-slate-500 uppercase dark:border-slate-800 dark:text-slate-400">
                <th scope="col" className="px-4 py-2 text-left font-bold">
                  Branch
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Departments
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Wards
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Beds
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Staff
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {data.branches.map((branch) => (
                <tr key={branch.id}>
                  <th scope="row" className="px-4 py-3 text-left">
                    <span className="font-semibold text-slate-900 dark:text-white">
                      {branch.name}
                    </span>
                    {branch.is_main && (
                      <Badge tone="info" className="ml-2">
                        Main
                      </Badge>
                    )}
                    {branch.state && (
                      <span className="block text-xs text-slate-500 dark:text-slate-400">
                        {branch.state}
                      </span>
                    )}
                  </th>
                  <td
                    className={cn(
                      "numeric px-4 py-3 text-right",
                      branch.departments === 0
                        ? "font-bold text-critical"
                        : "text-slate-800 dark:text-slate-100",
                    )}
                  >
                    {branch.departments}
                  </td>
                  <td className="numeric px-4 py-3 text-right text-slate-800 dark:text-slate-100">
                    {branch.wards}
                  </td>
                  <td className="numeric px-4 py-3 text-right text-slate-800 dark:text-slate-100">
                    {branch.beds}
                  </td>
                  <td className="numeric px-4 py-3 text-right text-slate-800 dark:text-slate-100">
                    {branch.staff_active}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader
          title="Not measured yet"
          count={data.pending.length}
          actions={<Badge tone="neutral">No invented figures</Badge>}
        />
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.pending.map((measure) => (
            <PendingTile key={measure.key} measure={measure} />
          ))}
        </div>
        <p className="border-t border-slate-200 px-4 py-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          These are the tiles this screen is built for. Each needs the module that measures it,
          and each appears here with a real number the week that module lands — rather than a
          plausible one now.
        </p>
      </Card>
    </Shell>
  );
}

function Shell({
  title = "Group",
  subtitle,
  children,
}: {
  title?: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-[90rem] space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>{title}</SectionTitle>
          {subtitle && (
            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>
          )}
        </div>
        <Badge tone="neutral">No patient records on this screen</Badge>
      </div>
      {children}
    </div>
  );
}

function AlertRow({ alert }: { alert: GroupAlert }) {
  const colour =
    alert.tone === "critical"
      ? "text-critical"
      : alert.tone === "warning"
        ? "text-warning"
        : "text-info";

  return (
    <li className="flex flex-wrap items-center gap-3 p-4">
      <IconWarning className={cn("size-5 shrink-0", colour)} />
      <span className="min-w-0 flex-1 text-sm text-slate-800 dark:text-slate-100">
        {alert.message}
      </span>
      {alert.branch && (
        <Badge tone="neutral" className="ml-auto">
          {alert.branch}
        </Badge>
      )}
    </li>
  );
}

function PendingTile({ measure }: { measure: PendingMeasure }) {
  return (
    <div className="rounded-[var(--radius-card)] border border-dashed border-slate-300 p-4 dark:border-slate-700">
      <p className="text-xs font-medium tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {measure.label}
      </p>
      <p className="mt-1 text-2xl font-bold text-slate-300 dark:text-slate-600">—</p>
      <p className="mt-1 text-xs font-semibold text-brand-700 dark:text-brand-300">
        {measure.milestone}
      </p>
      {measure.note && (
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{measure.note}</p>
      )}
    </div>
  );
}
