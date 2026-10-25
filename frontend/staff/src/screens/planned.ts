/**
 * The screens that are on the roadmap but not built.
 *
 * Its own module because it is data, not a component: Fast Refresh cannot
 * hot-reload a file that exports both, and it stops reloading silently
 * rather than saying so.
 *
 * Keyed by the same paths as apps/core/landing.py, which is what decides
 * where each role lands.
 */

export interface PlannedScreen {
  title: string;
  /** What it will do, in the words of somebody who would use it. */
  blurb: string;
  milestone: string;
}

/**
 * Every route the server may send somebody to that is not built yet.
 *
 * Keyed by the same paths as apps/core/landing.py, and a test on the server
 * side keeps that file's paths honest. If a role lands somewhere missing from
 * both this map and the real screens, the router's own Not Found would show —
 * which is why the fallback below names the path.
 */
export const PLANNED: Record<string, PlannedScreen> = {
  "/triage": {
    title: "Triage",
    blurb:
      "Vitals, NEWS2 early warning scores, nursing notes and the nurse task list — the screen a triage nurse works from all shift.",
    milestone: "milestone 6",
  },
  "/laboratory": {
    title: "Laboratory",
    blurb:
      "Live orders, barcode sample labels, reference ranges by age and sex, critical flags that alert the ordering doctor, and supervisor verification.",
    milestone: "milestone 7",
  },
  "/radiology": {
    title: "Radiology",
    blurb: "Imaging orders, scheduling, reports and private file uploads.",
    milestone: "milestone 7",
  },
  "/inventory": {
    title: "Inventory and stores",
    blurb:
      "Batches, NAFDAC numbers, expiry dates, suppliers, purchase orders, transfers and wastage — all on the stock ledger.",
    milestone: "milestone 7",
  },
  "/billing": {
    title: "Billing and cashier",
    blurb:
      "Bills from orders and dispenses, part payments, Paystack, receipts, refunds, waivers and end-of-day reconciliation.",
    milestone: "milestone 8",
  },
  "/accounts": {
    title: "Accounts",
    blurb:
      "Revenue by department and payer, expenses, payables and consultant fee shares.",
    milestone: "milestone 11",
  },
  "/audit": {
    title: "Audit log",
    blurb:
      "Every view and every change of a patient record, in an append-only hash-chained log you can search and verify.",
    milestone: "milestone 12",
  },
};
