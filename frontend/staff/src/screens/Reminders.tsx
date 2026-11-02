/**
 * Reminders, and what they cost.
 *
 * The number at the top of this screen is the point of it. Text messages are
 * a few naira each and email is free, so "shall we switch on SMS reminders?"
 * is a question about money, and a hospital owner can only answer it with a
 * figure in front of them — including how many patients have no email address
 * and would therefore have to be texted.
 *
 * The second thing this screen exists for is to be honest when nothing is
 * switched on. A list of queued reminders that never send, with no
 * explanation, looks like a broken feature. So the state of each channel is
 * said in words at the top, with what to do about it.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  SectionTitle,
  Stat,
  StatusDot,
  formatNaira,
} from "@curavia/shared";
import type { NoShow, Reminder, ReminderCost, ReminderList } from "@curavia/shared";
import { ApiRequestError, RemindersApi } from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const reminders = new RemindersApi(api);

const STATUS_TONE: Record<Reminder["status"], "info" | "stable" | "critical" | "neutral"> = {
  queued: "info",
  sent: "stable",
  failed: "critical",
  skipped: "neutral",
  cancelled: "neutral",
};

const STATUS_LABEL: Record<Reminder["status"], string> = {
  queued: "Waiting to go",
  sent: "Sent",
  failed: "Failed",
  skipped: "Not sent",
  cancelled: "Cancelled",
};

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-NG", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function Reminders() {
  const { me } = useAuth();
  const [list, setList] = useState<ReminderList | null>(null);
  const [cost, setCost] = useState<ReminderCost | null>(null);
  const [noShows, setNoShows] = useState<NoShow[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [denied, setDenied] = useState(false);

  const mayForce = me?.permissions.includes("schedule.manage") ?? false;

  const load = useCallback(() => {
    void reminders
      .list()
      .then(setList)
      .catch(() => setDenied(true));
    void reminders.cost(30).then(setCost).catch(() => setCost(null));
    void reminders.noShows().then(setNoShows).catch(() => setNoShows([]));
  }, []);

  useEffect(load, [load]);

  function sendNow() {
    setBusy(true);
    setNotice("");
    void reminders
      .sendNow()
      .then((result) => {
        setNotice(
          result.sent === 0 && result.failed === 0
            ? "Nothing was due."
            : `${result.sent} sent, ${result.failed} failed, ${result.skipped} not sent.`,
        );
        load();
      })
      .catch((caught: unknown) => {
        setNotice(
          caught instanceof ApiRequestError ? caught.message : "That did not go through.",
        );
      })
      .finally(() => setBusy(false));
  }

  if (denied) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState title="Reminders are not shown to your role." />
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <SectionTitle>Reminders</SectionTitle>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Sent the day before, and a couple of hours before
          </p>
        </div>
        {mayForce && (
          <Button variant="secondary" disabled={busy} onClick={sendNow}>
            {busy ? "Sending…" : "Send what is due now"}
          </Button>
        )}
      </div>

      {/* What is switched on, in words. An empty list with no explanation
          looks like a bug. */}
      <Card className="space-y-3 p-4">
        <StatusDot
          tone={list?.email_enabled ? "stable" : "warning"}
          label={
            list?.email_enabled
              ? "Email reminders are on"
              : "Email reminders are not set up yet"
          }
        />
        <StatusDot
          tone={list?.sms_enabled ? "stable" : "neutral"}
          label={
            list?.sms_enabled
              ? "Text message reminders are on"
              : "Text messages are off — email is used where there is an address"
          }
        />
        {!list?.email_enabled && (
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Email is free and sends up to 300 a day. Until it is set up, reminders are queued
            here and nothing leaves the building.
          </p>
        )}
      </Card>

      {notice && (
        <Card className="p-4">
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{notice}</p>
        </Card>
      )}

      {/* The number the decision gets made with. */}
      {cost && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="By email · next 30 days"
            value={String(cost.by_email)}
            tone="stable"
            sub="Free"
          />
          <Stat
            label="Would need a text"
            value={String(cost.by_sms)}
            tone={cost.by_sms ? "warning" : "stable"}
            sub="Patients with no email address"
          />
          <Stat
            label="Text messages would cost"
            value={formatNaira(cost.sms_cost_kobo)}
            tone={cost.sms_cost_kobo ? "warning" : "stable"}
            sub="Estimated, over 30 days"
          />
          <Stat
            label="Cannot be reached"
            value={String(cost.no_contact)}
            tone={cost.no_contact ? "critical" : "stable"}
            sub="No phone and no email on file"
          />
        </div>
      )}

      {list && list.spent_kobo > 0 && (
        <Card className="p-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Spent on text messages so far:{" "}
            <span className="numeric font-bold text-slate-900 dark:text-white">
              {formatNaira(list.spent_kobo)}
            </span>
          </p>
        </Card>
      )}

      <Card className="overflow-hidden">
        <CardHeader
          title="Reminders"
          count={list?.reminders.length ?? undefined}
          actions={
            list ? (
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {list.counts.sent} sent · {list.counts.queued} waiting ·{" "}
                {list.counts.failed} failed
              </span>
            ) : undefined
          }
        />
        {!list ? (
          <EmptyState title="Loading…" />
        ) : list.reminders.length === 0 ? (
          <EmptyState
            title="No reminders yet."
            hint="They are queued automatically when an appointment is booked."
          />
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {list.reminders.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
                <span className="min-w-0 flex-1 basis-48">
                  <span className="block font-semibold text-slate-900 dark:text-white">
                    {row.patient_name}
                  </span>
                  <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                    {row.patient_mrn} · {row.kind_label} · appointment {when(row.appointment_at)}
                  </span>
                </span>
                <Badge tone={STATUS_TONE[row.status]}>{STATUS_LABEL[row.status]}</Badge>
                {row.channel && <Badge tone="neutral">{row.channel}</Badge>}
                <span className="numeric ml-auto text-xs text-slate-500 dark:text-slate-400">
                  {row.sent_at ? when(row.sent_at) : when(row.send_at)}
                </span>
                {row.failure_reason && (
                  <p className="w-full text-xs text-slate-600 dark:text-slate-300">
                    {row.failure_reason}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="overflow-hidden">
        <CardHeader title="Did not attend" count={noShows.length} />
        {noShows.length === 0 ? (
          <EmptyState title="Nobody has missed an appointment." />
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {noShows.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-3 p-4">
                <span className="min-w-0 flex-1 basis-48">
                  <span className="block font-semibold text-slate-900 dark:text-white">
                    {row.patient_name}
                  </span>
                  <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                    {row.patient_mrn} · {when(row.missed_at)}
                  </span>
                </span>
                {/* Said plainly, because it changes whose problem the missed
                    appointment is. */}
                {row.was_reminded ? (
                  <Badge tone="neutral">Was reminded</Badge>
                ) : (
                  <Badge tone="warning">
                    <IconWarning className="size-3.5" />
                    Never reminded
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
