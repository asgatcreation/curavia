/**
 * Doctor's consultation.
 *
 * The screen the whole system is judged on. A consultant who finds this
 * slower than paper will not use it, and then nothing downstream works.
 *
 * The layout is two columns on desktop and one on tablet and phone, in that
 * order: the note is written on the left, and what the system already knows
 * sits on the right where it can be read without leaving the note. Orders
 * are drafted and signed as one basket, not one at a time, because a doctor
 * thinks in a plan — "bloods, an antibiotic and something for the fever" —
 * and signing each separately is three times the clicks for the same intent.
 *
 * The important behaviour is the conflict. The allergy warning is not a
 * dialog that appears after signing: it is attached to the order line from
 * the moment the drug is added, with the reason written out, and the sign
 * button states plainly what is blocking it. An alert that can be dismissed
 * by reflex is an alert that will be dismissed by reflex.
 */

import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  PatientBanner,
  Stat,
  cn,
  formatDateTime,
  formatNaira,
  inputClass,
  news2Label,
  news2Tone,
} from "@curavia/shared";
import type { OrderDraft } from "@curavia/shared";
import {
  DEMO_CONSULTATION_PATIENT,
  DEMO_NEWS2,
  DEMO_ORDER_DRAFT,
  DEMO_TIMELINE,
  DEMO_VITALS,
} from "@curavia/shared";

import { IconWarning } from "../icons";

export function Consultation() {
  const [orders, setOrders] = useState<OrderDraft[]>(DEMO_ORDER_DRAFT);
  const [note, setNote] = useState(
    "34-year-old woman, three days of frontal headache, worse in the mornings. " +
      "No visual disturbance, no neck stiffness, no vomiting. Known hypertensive, " +
      "not currently on treatment. Last BP reading at booking was 150/96.",
  );

  const blocking = orders.filter((order) => order.conflict?.tone === "critical");
  const total = orders.reduce((sum, order) => sum + order.price_kobo, 0);
  const payable = orders
    .filter((order) => !order.covered_by_payer)
    .reduce((sum, order) => sum + order.price_kobo, 0);

  function removeOrder(id: string) {
    setOrders((current) => current.filter((order) => order.id !== id));
  }

  return (
    <div>
      <PatientBanner patient={DEMO_CONSULTATION_PATIENT} />

      <div className="mx-auto grid w-full max-w-7xl gap-5 p-4 sm:p-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* ---------------------------------------------------------------
         * Left: the note and the orders being drafted
         * ------------------------------------------------------------- */}
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Consultation note"
              actions={<Badge tone="info">SOAP · autosaved</Badge>}
            />
            <div className="space-y-4 p-4">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                  Subjective
                </span>
                <textarea
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  rows={5}
                  className={cn(inputClass, "resize-y py-2 leading-relaxed")}
                />
              </label>

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                    Examination
                  </span>
                  <textarea
                    rows={3}
                    defaultValue="Alert, oriented. No focal neurological deficit. Fundi normal. Chest clear."
                    className={cn(inputClass, "resize-y py-2 leading-relaxed")}
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                    Assessment
                  </span>
                  <textarea
                    rows={3}
                    defaultValue="Tension-type headache on a background of untreated hypertension."
                    className={cn(inputClass, "resize-y py-2 leading-relaxed")}
                  />
                </label>
              </div>

              <div>
                <span className="mb-1.5 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                  Diagnosis (ICD-10)
                </span>
                <div className="flex flex-wrap gap-2">
                  <Badge tone="info">G44.2 · Tension-type headache</Badge>
                  <Badge tone="info">I10 · Essential hypertension</Badge>
                  <Button size="sm" variant="ghost">
                    + Add
                  </Button>
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Orders to sign"
              count={orders.length}
              actions={
                <Button size="sm" variant="secondary">
                  + Add an order
                </Button>
              }
            />
            <ul className="divide-y divide-slate-200 dark:divide-slate-800">
              {orders.map((order) => (
                <OrderRow key={order.id} order={order} onRemove={() => removeOrder(order.id)} />
              ))}
            </ul>

            <div className="space-y-3 border-t border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/40">
              <dl className="flex flex-wrap justify-between gap-2 text-sm">
                <dt className="text-slate-600 dark:text-slate-300">Order total</dt>
                <dd className="numeric font-bold text-slate-900 dark:text-white">
                  {formatNaira(total)}
                </dd>
              </dl>
              <dl className="flex flex-wrap justify-between gap-2 text-sm">
                <dt className="text-slate-600 dark:text-slate-300">Patient pays today</dt>
                <dd className="numeric font-bold text-slate-900 dark:text-white">
                  {formatNaira(payable)}
                </dd>
              </dl>

              {blocking.length > 0 && (
                <p
                  role="alert"
                  className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
                >
                  <IconWarning className="mt-0.5 size-4 shrink-0" />
                  <span>
                    {blocking.length === 1
                      ? "One order conflicts with this patient's record."
                      : `${blocking.length} orders conflict with this patient's record.`}{" "}
                    Remove it, or change the drug, before signing.
                  </span>
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                <Button
                  // Red rather than a dimmed brand button: a disabled primary
                  // reads as "nearly ready", and this one is a stop.
                  variant={blocking.length > 0 ? "danger" : "primary"}
                  size="lg"
                  disabled={blocking.length > 0}
                  className="flex-1 sm:flex-none"
                >
                  {blocking.length > 0 ? "Blocked — resolve the conflict" : "Sign and send"}
                </Button>
                <Button variant="ghost" size="lg">
                  Save draft
                </Button>
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Signing sends the prescription to the pharmacy queue and the laboratory orders to
                the bench, and writes the bill. Everything here is recorded in the audit log under
                your name.
              </p>
            </div>
          </Card>
        </div>

        {/* ---------------------------------------------------------------
         * Right: what the system already knows
         * ------------------------------------------------------------- */}
        <div className="space-y-5">
          <Card>
            <CardHeader title="Vitals" actions={<Badge tone="neutral">64 minutes ago</Badge>} />
            <dl className="grid grid-cols-2 gap-px bg-slate-200 sm:grid-cols-3 xl:grid-cols-2 dark:bg-slate-800">
              {DEMO_VITALS.map((vital) => (
                <div key={vital.label} className="bg-white p-3 dark:bg-slate-900">
                  <dt className="text-xs text-slate-500 dark:text-slate-400">{vital.label}</dt>
                  <dd
                    className={cn(
                      "numeric mt-0.5 text-lg font-bold",
                      vital.tone === "warning"
                        ? "text-warning-ink dark:text-warning"
                        : vital.tone === "critical"
                          ? "text-critical"
                          : "text-slate-900 dark:text-white",
                    )}
                  >
                    {vital.value}
                    <span className="ml-1 text-xs font-medium text-slate-500 dark:text-slate-400">
                      {vital.unit}
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
            <div className="border-t border-slate-200 p-4 dark:border-slate-800">
              <Stat
                label="NEWS2 early warning score"
                value={String(DEMO_NEWS2)}
                tone={news2Tone(DEMO_NEWS2)}
                sub={news2Label(DEMO_NEWS2)}
              />
            </div>
          </Card>

          <Card>
            <CardHeader title="This visit" />
            <ol className="space-y-0 p-4">
              {[...DEMO_TIMELINE]
                .sort((a, b) => a.at.localeCompare(b.at))
                .map((entry, index, all) => (
                  <li key={entry.at} className="relative flex gap-3 pb-4 last:pb-0">
                    <span className="flex flex-col items-center">
                      <span className="mt-1 size-2.5 shrink-0 rounded-full bg-brand-500" />
                      {index < all.length - 1 && (
                        <span className="w-px flex-1 bg-slate-200 dark:bg-slate-700" />
                      )}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-slate-900 dark:text-white">
                        {entry.what}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {entry.who} · {formatDateTime(entry.at)}
                      </p>
                      {entry.detail && (
                        <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">
                          {entry.detail}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}

function OrderRow({ order, onRemove }: { order: OrderDraft; onRemove: () => void }) {
  const critical = order.conflict?.tone === "critical";

  return (
    <li className={cn("p-4", critical && "bg-critical-soft/60 dark:bg-critical/10")}>
      <div className="flex flex-wrap items-start gap-3">
        <Badge tone="neutral">{order.kind}</Badge>
        <div className="min-w-0 flex-1 basis-56">
          <p className="font-semibold text-slate-900 dark:text-white">{order.name}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{order.detail}</p>
        </div>
        <span className="numeric text-sm font-semibold text-slate-900 dark:text-white">
          {formatNaira(order.price_kobo)}
        </span>
        <Button size="sm" variant="ghost" onClick={onRemove}>
          Remove
        </Button>
      </div>

      {order.conflict && (
        <p
          className={cn(
            "mt-2 flex items-start gap-2 rounded-[var(--radius-control)] p-2.5 text-sm font-medium",
            critical
              ? "bg-critical text-white"
              : "bg-warning-soft text-warning-ink dark:bg-warning/15 dark:text-warning",
          )}
        >
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          {order.conflict.message}
        </p>
      )}

      {!order.covered_by_payer && !order.conflict && (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
          Not covered by this payer — the patient pays for it.
        </p>
      )}
    </li>
  );
}
