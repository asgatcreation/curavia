/**
 * Group dashboard — the owner's screen.
 *
 * A hospital group's owner is not a clinician and is usually not on site.
 * The question is always the same: is each branch coping, and is the money
 * coming in. So the page answers those two in that order and refuses to
 * become a reporting suite.
 *
 * What it deliberately does NOT show: any patient's name. The owner role can
 * see counts, money and alerts across branches without opening a single
 * record, and the permission matrix enforces that separately. A dashboard
 * that leaks a patient name into a tooltip would make that boundary a lie.
 *
 * The chart is drawn as inline SVG rather than with a charting library. It is
 * one series of twelve bars; a dependency would be 60 kB to draw twelve
 * rectangles, and this version is readable by a screen reader because the
 * same numbers are in a table underneath.
 */

import {
  Badge,
  Card,
  CardHeader,
  SectionTitle,
  Stat,
  cn,
  formatNaira,
  waitTone,
} from "@curavia/shared";
import type { BranchRow } from "@curavia/shared";
import {
  DEMO_BRANCH_ROWS,
  DEMO_ORGANISATION,
  DEMO_PAYER_MIX,
  DEMO_REVENUE_BY_HOUR,
} from "@curavia/shared";

import { IconWarning } from "../icons";

export function GroupDashboard() {
  const revenue = DEMO_BRANCH_ROWS.reduce((sum, row) => sum + row.revenue_today_kobo, 0);
  const outstanding = DEMO_BRANCH_ROWS.reduce((sum, row) => sum + row.outstanding_kobo, 0);
  const waiting = DEMO_BRANCH_ROWS.reduce((sum, row) => sum + row.waiting, 0);
  const beds = DEMO_BRANCH_ROWS.reduce(
    (totals, row) => ({
      occupied: totals.occupied + row.occupied_beds,
      total: totals.total + row.total_beds,
    }),
    { occupied: 0, total: 0 },
  );
  const alerts = DEMO_BRANCH_ROWS.flatMap((row) =>
    row.alerts.map((alert) => ({ branch: row.name, alert })),
  );

  return (
    <div className="mx-auto w-full max-w-[90rem] space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>{DEMO_ORGANISATION.group}</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Three branches · today, to {new Date().toLocaleTimeString("en-NG", {
              hour: "2-digit",
              minute: "2-digit",
              timeZone: "Africa/Lagos",
            })}{" "}
            WAT
          </p>
        </div>
        <Badge tone="neutral">No patient records on this screen</Badge>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Billed today"
          value={formatNaira(revenue)}
          tone="stable"
          sub="Across all branches"
        />
        <Stat
          label="Outstanding"
          value={formatNaira(outstanding)}
          tone="warning"
          sub="Unpaid bills and HMO claims"
        />
        <Stat label="Waiting now" value={String(waiting)} tone="info" sub="Patients in queues" />
        <Stat
          label="Bed occupancy"
          value={`${Math.round((beds.occupied / beds.total) * 100)}%`}
          tone={beds.occupied / beds.total > 0.9 ? "critical" : "stable"}
          sub={`${beds.occupied} of ${beds.total} beds`}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card className="overflow-hidden">
          <CardHeader title="Billed by hour" actions={<Badge tone="info">All branches</Badge>} />
          <div className="p-4">
            <RevenueChart />
          </div>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader title="Who is paying" />
          <ul className="space-y-3 p-4">
            {DEMO_PAYER_MIX.map((slice) => (
              <li key={slice.payer}>
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="font-semibold text-slate-800 dark:text-slate-100">
                    {slice.payer}
                  </span>
                  <span className="numeric font-bold text-slate-900 dark:text-white">
                    {Math.round(slice.share * 100)}%
                  </span>
                </div>
                <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
                  <div
                    className={cn(
                      "h-full rounded-full",
                      slice.tone === "info"
                        ? "bg-info"
                        : slice.tone === "stable"
                          ? "bg-stable"
                          : slice.tone === "warning"
                            ? "bg-warning"
                            : "bg-slate-400",
                    )}
                    style={{ width: `${slice.share * 100}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
          <p className="px-4 pb-4 text-xs text-slate-500 dark:text-slate-400">
            HMO and NHIA money arrives weeks after the visit. The outstanding figure above is
            mostly these two.
          </p>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader title="Branches" count={DEMO_BRANCH_ROWS.length} />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[52rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs tracking-wide text-slate-500 uppercase dark:border-slate-800 dark:text-slate-400">
                <th scope="col" className="px-4 py-2 text-left font-bold">
                  Branch
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Waiting
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Longest wait
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Beds
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  On duty
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Billed today
                </th>
                <th scope="col" className="px-4 py-2 text-right font-bold">
                  Outstanding
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {DEMO_BRANCH_ROWS.map((row) => (
                <BranchTableRow key={row.id} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader title="Needs attention" count={alerts.length} />
        {alerts.length === 0 ? (
          <p className="p-4 text-sm text-slate-500 dark:text-slate-400">Nothing outstanding.</p>
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {alerts.map((item) => (
              <li key={`${item.branch}-${item.alert}`} className="flex items-center gap-3 p-4">
                <IconWarning className="size-5 shrink-0 text-warning" />
                <span className="text-sm text-slate-800 dark:text-slate-100">{item.alert}</span>
                <Badge tone="neutral" className="ml-auto">
                  {item.branch}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function BranchTableRow({ row }: { row: BranchRow }) {
  const occupancy = row.occupied_beds / row.total_beds;
  const waitBand = waitTone(row.longest_wait_minutes);

  return (
    <tr>
      <th scope="row" className="px-4 py-3 text-left font-semibold text-slate-900 dark:text-white">
        {row.name}
      </th>
      <td className="numeric px-4 py-3 text-right text-slate-800 dark:text-slate-100">
        {row.waiting}
      </td>
      <td className="px-4 py-3 text-right">
        <span
          className={cn(
            "numeric font-semibold",
            waitBand === "critical"
              ? "text-critical"
              : waitBand === "warning"
                ? "text-warning-ink dark:text-warning"
                : "text-slate-800 dark:text-slate-100",
          )}
        >
          {Math.floor(row.longest_wait_minutes / 60)}h {row.longest_wait_minutes % 60}m
        </span>
      </td>
      <td className="px-4 py-3 text-right">
        <span
          className={cn(
            "numeric font-semibold",
            occupancy > 0.9 ? "text-critical" : "text-slate-800 dark:text-slate-100",
          )}
        >
          {row.occupied_beds}/{row.total_beds}
        </span>
      </td>
      <td className="numeric px-4 py-3 text-right text-slate-800 dark:text-slate-100">
        {row.staff_on_duty}
      </td>
      <td className="numeric px-4 py-3 text-right font-semibold text-slate-900 dark:text-white">
        {formatNaira(row.revenue_today_kobo)}
      </td>
      <td className="numeric px-4 py-3 text-right text-warning-ink dark:text-warning">
        {formatNaira(row.outstanding_kobo)}
      </td>
    </tr>
  );
}

/**
 * Twelve bars, drawn to a viewBox so they scale to any width without
 * JavaScript measuring anything. The table underneath is not a fallback —
 * it is the accessible version, always rendered, visually hidden.
 */
function RevenueChart() {
  const peak = Math.max(...DEMO_REVENUE_BY_HOUR.map((point) => point.kobo));
  const width = 100;
  const height = 38;
  const gap = 1.2;
  const barWidth = (width - gap * (DEMO_REVENUE_BY_HOUR.length - 1)) / DEMO_REVENUE_BY_HOUR.length;

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Amount billed each hour from 07:00 to 18:00, peaking at ₦754,200 at 10:00."
        className="h-40 w-full sm:h-48"
      >
        {DEMO_REVENUE_BY_HOUR.map((point, index) => {
          const barHeight = (point.kobo / peak) * height;
          return (
            <rect
              key={point.hour}
              x={index * (barWidth + gap)}
              y={height - barHeight}
              width={barWidth}
              height={barHeight}
              rx={0.8}
              className="fill-brand-500 dark:fill-brand-400"
            />
          );
        })}
      </svg>

      <div className="mt-1.5 flex justify-between text-xs text-slate-500 dark:text-slate-400">
        {DEMO_REVENUE_BY_HOUR.map((point) => (
          <span key={point.hour} className="numeric">
            {point.hour}
          </span>
        ))}
      </div>

      <figcaption className="sr-only">
        <table>
          <caption>Amount billed by hour</caption>
          <tbody>
            {DEMO_REVENUE_BY_HOUR.map((point) => (
              <tr key={point.hour}>
                <th scope="row">{point.hour}:00</th>
                <td>{formatNaira(point.kobo)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </figcaption>
    </figure>
  );
}
