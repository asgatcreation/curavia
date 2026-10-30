/**
 * One patient's record.
 *
 * Four things on one screen, in this order, because that is the order the
 * questions get asked: who is this, what has happened to them, print their
 * card, and — occasionally — this is the same person as that other record.
 *
 * The clinical fields may not be in the response at all. The server removes
 * blood group, genotype and the allergy list for a caller without
 * `clinical.note.read` rather than sending nulls, so a cashier's copy of this
 * screen has nothing to leak in the network tab. Everything here that reads
 * one of those fields checks first, and says "you cannot see this" rather
 * than drawing an empty box — a blank where an allergy list should be is the
 * single most dangerous thing this screen could show.
 */

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  SectionTitle,
  Stat,
  StatusDot,
  cn,
  inputClass,
} from "@curavia/shared";
import type {
  MergePlan,
  MergeRecord,
  PatientCard as PatientCardData,
  PatientDetail,
  PatientSummary,
  Timeline,
  TimelineEntry,
} from "@curavia/shared";
import { ApiRequestError, PatientsApi } from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const patients = new PatientsApi(api);

/** The merge reason the server will accept. Mirrored so the button can wait. */
const MIN_REASON_LENGTH = 8;

/**
 * "1 allergy", "2 allergies".
 *
 * The keys come from the API as plural nouns, so the singular is made by
 * hand for the three that are not formed by dropping an s. Hospital software
 * that says "1 allergies" reads as unfinished, and this screen is asking
 * somebody to approve something irreversible.
 */
const SINGULAR: Record<string, string> = {
  allergies: "allergy",
  identifiers: "identifier",
  next_of_kin: "next of kin",
};

function countOf(count: number, plural: string): string {
  const noun = count === 1 ? (SINGULAR[plural] ?? plural) : plural;
  return `${count} ${noun.replace(/_/g, " ")}`;
}

function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function PatientRecord({ patientId }: { patientId: string }) {
  const { me } = useAuth();
  const navigate = useNavigate();

  const [patient, setPatient] = useState<PatientDetail | null>(null);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<"timeline" | "card" | "merge">("timeline");
  // Bumped after a merge or a reversal. A merge carries allergies and an
  // allergy history across, so leaving the strip above saying "nobody has
  // asked" would be showing a prescriber the opposite of what the record
  // now holds — which is the one thing this screen must never do.
  const [version, setVersion] = useState(0);

  const mayReadClinical = me?.permissions.includes("clinical.note.read") ?? false;
  const mayMerge = me?.permissions.includes("patients.merge") ?? false;

  useEffect(() => {
    let live = true;
    // Clearing on an id change is the effect synchronising React with a
    // different patient. Leaving the old record on screen while the new one
    // loads is exactly the wrong-patient failure the banner exists to
    // prevent, so this one is deliberate.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPatient(null);
    setError("");
    void patients
      .get(patientId)
      .then((found) => {
        if (live) setPatient(found);
      })
      .catch((caught: unknown) => {
        if (live) {
          setError(
            caught instanceof ApiRequestError
              ? caught.message
              : "That record could not be opened.",
          );
        }
      });
    return () => {
      live = false;
    };
  }, [patientId, version]);

  if (error) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState title={error} hint="Check the number, or search for them by name." />
          <div className="px-4 pb-4">
            <Button onClick={() => void navigate({ to: "/" })}>Back to the front desk</Button>
          </div>
        </Card>
      </div>
    );
  }

  if (!patient) {
    return (
      <div className="mx-auto w-full max-w-5xl p-4 sm:p-6">
        <Card>
          <EmptyState title="Opening the record…" />
        </Card>
      </div>
    );
  }

  const allergies = patient.allergies ?? [];
  const critical = allergies.filter(
    (allergy) => allergy.is_active && ["severe", "anaphylaxis"].includes(allergy.severity),
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-4 sm:p-6">
      {/* The identity block. Never truncated, never behind a click: a
          wrong-patient error happens when the identifier moves. */}
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <SectionTitle>{patient.full_name}</SectionTitle>
          <p className="numeric mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            {patient.mrn}
            {patient.age ? ` · ${patient.age}` : ""} ·{" "}
            {patient.sex === "F" ? "Female" : patient.sex === "M" ? "Male" : "Sex not recorded"}
            {patient.date_of_birth_is_estimated && " · age estimated"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {patient.is_provisional && <Badge tone="warning">Not yet identified</Badge>}
          {patient.is_deceased && <Badge tone="critical">Deceased</Badge>}
        </div>
      </header>

      <AllergyStrip
        mayReadClinical={mayReadClinical}
        known={patient.allergies_known ?? false}
        allergies={allergies.map((allergy) => allergy.substance)}
        criticalCount={critical.length}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label="Registered"
          value={new Date(patient.registered_at).toLocaleDateString("en-NG", {
            day: "numeric",
            month: "short",
            year: "numeric",
          })}
          tone="neutral"
          sub={patient.home_branch_name || undefined}
        />
        <Stat label="Payer" value={patient.default_payer_name || "Self-pay"} tone="info" />
        <Stat
          label="Phone"
          value={patient.phone || "Not given"}
          tone={patient.phone ? "neutral" : "warning"}
          sub={patient.alternate_phone || undefined}
        />
        <Stat
          label="Next of kin"
          value={String(patient.next_of_kin.length)}
          tone={patient.next_of_kin.length ? "stable" : "warning"}
          sub={patient.next_of_kin[0]?.name}
        />
      </div>

      <nav className="flex gap-1 overflow-x-auto" aria-label="Sections of this record">
        {([
          ["timeline", "History"],
          ["card", "Patient card"],
          ...(mayMerge ? ([["merge", "Duplicates"]] as const) : []),
        ] as const).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setPanel(key)}
            aria-pressed={panel === key}
            className={cn(
              "min-h-11 shrink-0 rounded-full px-4 text-sm font-semibold transition-colors",
              panel === key
                ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700",
            )}
          >
            {label}
          </button>
        ))}
      </nav>

      {panel === "timeline" && (
        <TimelinePanel
          patientId={patientId}
          mayReadClinical={mayReadClinical}
          version={version}
        />
      )}
      {panel === "card" && <CardPanel patientId={patientId} />}
      {panel === "merge" && (
        <MergePanel patient={patient} onChanged={() => setVersion((n) => n + 1)} />
      )}
    </div>
  );
}

/**
 * The allergy line.
 *
 * Three states, not two. "Nobody has asked" is drawn as a warning rather
 * than as an empty list, because the empty list is what a prescriber reads
 * as "safe".
 */
function AllergyStrip({
  mayReadClinical,
  known,
  allergies,
  criticalCount,
}: {
  mayReadClinical: boolean;
  known: boolean;
  allergies: string[];
  criticalCount: number;
}) {
  if (!mayReadClinical) {
    return (
      <Card className="p-4">
        <StatusDot tone="neutral" label="Allergies are not shown to your role." />
      </Card>
    );
  }
  if (!known) {
    return (
      <Card className="flex flex-wrap items-center gap-3 p-4 ring-warning/40">
        <Badge tone="warning">
          <IconWarning className="size-3.5" />
          Nobody has asked about allergies
        </Badge>
        <span className="text-sm text-slate-600 dark:text-slate-300">
          This is not the same as none. Take a history before prescribing.
        </span>
      </Card>
    );
  }
  if (allergies.length === 0) {
    return (
      <Card className="p-4">
        <StatusDot tone="stable" label="Allergy history taken — none known" />
      </Card>
    );
  }
  return (
    <Card className="flex flex-wrap items-center gap-2 p-4">
      <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        Allergies
      </span>
      {allergies.map((substance) => (
        <Badge key={substance} tone={criticalCount > 0 ? "critical" : "warning"}>
          {substance}
        </Badge>
      ))}
    </Card>
  );
}

// --------------------------------------------------------------------------
// History
// --------------------------------------------------------------------------

const TONE_RAIL: Record<TimelineEntry["tone"], string> = {
  critical: "bg-critical",
  urgent: "bg-urgent",
  warning: "bg-warning",
  stable: "bg-stable",
  info: "bg-info",
  neutral: "bg-slate-300 dark:bg-slate-600",
};

function TimelinePanel({
  patientId,
  mayReadClinical,
  version,
}: {
  patientId: string;
  mayReadClinical: boolean;
  version: number;
}) {
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    let live = true;
    void patients
      .timeline(patientId)
      .then((found) => {
        if (live) setTimeline(found);
      })
      .catch(() => {
        if (live) setDenied(true);
      });
    return () => {
      live = false;
    };
  }, [patientId, version]);

  if (denied || !mayReadClinical) {
    return (
      <Card>
        <EmptyState
          title="The clinical history is not shown to your role."
          hint="You can see who this patient is, and print their card."
        />
      </Card>
    );
  }
  if (!timeline) {
    return (
      <Card>
        <EmptyState title="Loading the history…" />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <CardHeader title="History" count={timeline.entries.length} />
        <ol className="divide-y divide-slate-200 dark:divide-slate-800">
          {timeline.entries.map((entry, index) => (
            <li key={`${entry.at}-${entry.kind}-${index}`} className="relative py-3 pr-4 pl-5">
              <span
                aria-hidden="true"
                className={cn("absolute inset-y-0 left-0 w-1.5", TONE_RAIL[entry.tone])}
              />
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <p className="min-w-0 flex-1 basis-56 font-semibold text-slate-900 dark:text-white">
                  {entry.title}
                </p>
                <time
                  dateTime={entry.at}
                  className="numeric shrink-0 text-xs text-slate-500 dark:text-slate-400"
                >
                  {new Date(entry.at).toLocaleDateString("en-NG", {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                  })}
                </time>
                {entry.is_pinned && <Badge tone="neutral">Keep in view</Badge>}
              </div>
              {entry.detail && (
                <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{entry.detail}</p>
              )}
              {entry.actor && (
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{entry.actor}</p>
              )}
            </li>
          ))}
        </ol>
      </Card>

      {/* What is not here yet, said out loud. A history showing only a
          registration would otherwise read as "nothing has happened to this
          patient", which on a clinical screen is a wrong answer rather than
          an empty state. */}
      {timeline.pending.length > 0 && (
        <Card className="p-4">
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
            Not in this history yet
          </p>
          <ul className="mt-2 space-y-1">
            {timeline.pending.map((row) => (
              <li key={row.key} className="text-sm text-slate-600 dark:text-slate-300">
                {row.label}{" "}
                <span className="text-slate-400 dark:text-slate-500">— {row.milestone}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// The card
// --------------------------------------------------------------------------

function CardPanel({ patientId }: { patientId: string }) {
  const [card, setCard] = useState<PatientCardData | null>(null);
  const [refused, setRefused] = useState("");

  useEffect(() => {
    let live = true;
    void patients
      .card(patientId)
      .then((found) => {
        if (live) setCard(found);
      })
      .catch((caught: unknown) => {
        if (live) {
          setRefused(
            caught instanceof ApiRequestError ? caught.message : "The card could not be made.",
          );
        }
      });
    return () => {
      live = false;
    };
  }, [patientId]);

  if (refused) {
    return (
      <Card>
        <EmptyState title={refused} />
      </Card>
    );
  }
  if (!card) {
    return (
      <Card>
        <EmptyState title="Making the card…" />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <CardHeader
          title="Patient card"
          actions={
            <Button size="sm" variant="primary" onClick={() => window.print()}>
              Print
            </Button>
          }
        />
        <div className="p-4">
          {/* Fixed at real card dimensions — 85.6 × 54 mm — so what is on
              screen is what comes out of the printer. The print stylesheet
              in index.css hides everything else on the page. */}
          <div
            className="curavia-card mx-auto flex w-full max-w-[85.6mm] items-start justify-between gap-3 rounded-xl bg-white p-3 text-slate-900 ring-1 ring-slate-300"
            style={{ minHeight: "54mm" }}
          >
            <div className="flex min-w-0 flex-col justify-between self-stretch">
              <div className="min-w-0">
                <p className="truncate text-[0.625rem] font-bold tracking-wide uppercase">
                  {card.hospital_name}
                </p>
                {card.branch_name && (
                  <p className="truncate text-[0.5625rem] text-slate-500">{card.branch_name}</p>
                )}
                <p className="mt-3 text-sm leading-tight font-bold break-words">
                  {card.printed_name}
                </p>
                <p className="numeric mt-1 text-xs font-semibold">{card.patient.mrn}</p>
              </div>
              {/* The bottom line is always printed, with or without a phone
                  number. Not decoration: "bring this card" is the whole
                  reason the card exists — a returning patient who produces
                  it is a patient nobody has to find by name, which is where
                  duplicate records come from. */}
              <p className="text-[0.5625rem] text-slate-500">
                Please bring this card to every visit.
                {card.support_phone && (
                  <span className="numeric block">{card.support_phone}</span>
                )}
              </p>
            </div>
            {/* The SVG comes from the server. It is generated by our own
                code from a payload we built — no user-supplied text reaches
                it — and it is the only place in Curavia that sets markup
                directly. */}
            <div className="shrink-0" dangerouslySetInnerHTML={{ __html: card.qr_svg }} />
          </div>
        </div>
      </Card>

      <Card className="p-4">
        <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
          What the code contains
        </p>
        <p className="numeric mt-1 text-sm break-all text-slate-700 dark:text-slate-200">
          {card.payload}
        </p>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          The hospital and the record number, and nothing else — no name, no date of birth, no
          link. A card found on the street tells whoever found it nothing about its owner, and
          scanning it opens this record only for signed-in staff.
        </p>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------
// Duplicates
// --------------------------------------------------------------------------

/**
 * Find the other record, see what would happen, then do it.
 *
 * Three steps and no shortcut past the middle one. The clerk approves an
 * outcome — "this address moves across, these two records disagree about
 * sex" — rather than an intention, and they have to write why.
 */
function MergePanel({
  patient,
  onChanged,
}: {
  patient: PatientDetail;
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<PatientSummary[]>([]);
  const [other, setOther] = useState<PatientSummary | null>(null);
  const [plan, setPlan] = useState<MergePlan | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [history, setHistory] = useState<MergeRecord[]>([]);
  const [done, setDone] = useState(false);
  // Reversing is asked for inline, with the same reason field as the merge
  // itself. A native `prompt()` would be unstyled, unvalidated and gone the
  // moment somebody hit Escape — and undoing a merge deserves the same
  // sentence the merge did.
  const [undoing, setUndoing] = useState<string | null>(null);
  const [undoReason, setUndoReason] = useState("");

  const loadHistory = useMemo(
    () => () => {
      void patients.merges(patient.id).then(setHistory).catch(() => setHistory([]));
    },
    [patient.id],
  );

  useEffect(loadHistory, [loadHistory]);

  useEffect(() => {
    const text = query.trim();
    if (text.length < 2) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFound([]);
      return;
    }
    const timer = setTimeout(() => {
      void patients
        .search(text, 8)
        .then((rows) => setFound(rows.filter((row) => row.id !== patient.id)))
        .catch(() => setFound([]));
    }, 250);
    return () => clearTimeout(timer);
  }, [query, patient.id]);

  function choose(candidate: PatientSummary) {
    setOther(candidate);
    setPlan(null);
    setProblem("");
    void patients
      .previewMerge(patient.id, candidate.id)
      .then(setPlan)
      .catch((caught: unknown) => {
        setProblem(
          caught instanceof ApiRequestError ? caught.message : "That pair cannot be merged.",
        );
      });
  }

  function doMerge() {
    if (!other) return;
    setBusy(true);
    setProblem("");
    void patients
      .merge(patient.id, other.id, reason)
      .then(() => {
        setDone(true);
        setOther(null);
        setPlan(null);
        setReason("");
        setQuery("");
        loadHistory();
        onChanged();
      })
      .catch((caught: unknown) => {
        setProblem(
          caught instanceof ApiRequestError ? caught.message : "The merge could not be done.",
        );
      })
      .finally(() => setBusy(false));
  }

  function undo(record: MergeRecord) {
    void patients
      .reverseMerge(record.id, undoReason.trim())
      .then(() => {
        setUndoing(null);
        setUndoReason("");
        loadHistory();
        onChanged();
      })
      .catch((caught: unknown) => {
        setProblem(
          caught instanceof ApiRequestError ? caught.message : "That merge could not be reversed.",
        );
      });
  }

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <CardHeader title="Is this the same person as another record?" />
        <div className="space-y-3 p-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            The record you are on now — <span className="numeric">{patient.mrn}</span> — is the one
            that will be kept. Find the duplicate to fold into it.
          </p>
          <Field label="Find the other record">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Name, MRN or phone number"
              className={inputClass}
            />
          </Field>
          {found.length > 0 && (
            <ul className="divide-y divide-slate-200 rounded-[var(--radius-control)] ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
              {found.map((row) => (
                <li key={row.id} className="flex flex-wrap items-center gap-3 p-3">
                  <span className="min-w-0 flex-1 basis-40">
                    <span className="block font-semibold text-slate-900 dark:text-white">
                      {row.full_name}
                    </span>
                    <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                      {row.mrn}
                      {row.age ? ` · ${row.age}` : ""}
                      {row.phone ? ` · ${row.phone}` : ""}
                    </span>
                  </span>
                  <Button size="sm" className="ml-auto" onClick={() => choose(row)}>
                    See what would happen
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      {done && (
        <Card className="p-4">
          <StatusDot tone="stable" label="Merged. The old number still opens this record." />
        </Card>
      )}

      {problem && (
        <Card className="p-4 ring-critical/40">
          <Badge tone="critical">
            <IconWarning className="size-3.5" />
            {problem}
          </Badge>
        </Card>
      )}

      {plan && other && (
        <Card className="overflow-hidden ring-warning/40">
          <CardHeader title={`Merging ${plan.loser.mrn} into ${plan.winner.mrn}`} />
          <div className="space-y-4 p-4">
            {plan.suggested_winner_id !== plan.winner.id && (
              <p className="text-sm font-semibold text-warning-ink dark:text-warning">
                The other record looks like the better one to keep — it is older, or this one has
                no confirmed identity. Open that record and merge from there instead.
              </p>
            )}

            {plan.warnings.length > 0 && (
              <ul className="space-y-2">
                {plan.warnings.map((warning) => (
                  <li key={warning} className="flex gap-2 text-sm text-slate-700 dark:text-slate-200">
                    <IconWarning className="mt-0.5 size-4 shrink-0 text-warning" />
                    {warning}
                  </li>
                ))}
              </ul>
            )}

            <div>
              <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
                What moves across
              </p>
              <ul className="mt-1 space-y-0.5 text-sm text-slate-700 dark:text-slate-200">
                {Object.entries(plan.moves)
                  .filter(([, count]) => count > 0)
                  .map(([what, count]) => (
                    <li key={what}>{countOf(count, what)}</li>
                  ))}
                {plan.fills.map((fill) => (
                  <li key={fill.field}>
                    {sentenceCase(fill.label)}: {fill.value}
                  </li>
                ))}
                {plan.moves.identifiers +
                  plan.moves.next_of_kin +
                  plan.moves.allergies +
                  plan.fills.length ===
                  0 && <li className="text-slate-500 dark:text-slate-400">Nothing new to add.</li>}
              </ul>
            </div>

            {plan.severity_changes.length > 0 && (
              <div>
                <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
                  Allergy warnings that get stronger
                </p>
                <ul className="mt-1 space-y-0.5 text-sm text-slate-700 dark:text-slate-200">
                  {plan.severity_changes.map((change) => (
                    <li key={change.substance}>
                      {change.substance}: {change.was} → {change.becomes}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-sm text-slate-600 dark:text-slate-300">
              Nothing is deleted and nothing is overwritten. {plan.loser.mrn} stays in the register
              and still opens this record, and the merge can be reversed.
            </p>

            <Field
              label="Why are these the same person?"
              hint="Whoever reads the audit log in two years needs this sentence."
            >
              <input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Same patient — registered twice, surname spelled differently"
                className={inputClass}
              />
            </Field>

            <div className="flex flex-wrap gap-2">
              <Button
                variant="danger"
                disabled={busy || reason.trim().length < MIN_REASON_LENGTH}
                onClick={doMerge}
              >
                {busy ? "Merging…" : `Merge ${plan.loser.mrn} into ${plan.winner.mrn}`}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setOther(null);
                  setPlan(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        </Card>
      )}

      {history.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader title="Merges on this record" count={history.length} />
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {history.map((record) => (
              <li key={record.id} className="flex flex-wrap items-center gap-3 p-4">
                <span className="min-w-0 flex-1 basis-56">
                  <span className="numeric block text-sm font-semibold text-slate-900 dark:text-white">
                    {record.loser.mrn} → {record.winner.mrn}
                  </span>
                  <span className="block text-xs text-slate-500 dark:text-slate-400">
                    {new Date(record.at).toLocaleDateString("en-NG")}
                    {record.by ? ` · ${record.by}` : ""}
                    {record.reason ? ` · ${record.reason}` : ""}
                  </span>
                </span>
                {record.is_reversed ? (
                  <Badge tone="neutral">Reversed</Badge>
                ) : (
                  record.can_reverse && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setUndoing(undoing === record.id ? null : record.id);
                        setUndoReason("");
                      }}
                    >
                      {undoing === record.id ? "Cancel" : "Reverse"}
                    </Button>
                  )
                )}

                {undoing === record.id && (
                  <div className="w-full space-y-3 border-t border-slate-200 pt-3 dark:border-slate-800">
                    <p className="text-sm text-slate-600 dark:text-slate-300">
                      Both records go back to how they were. Anything typed into{" "}
                      <span className="numeric">{record.winner.mrn}</span> since the merge is
                      left alone.
                    </p>
                    <Field label="Why is this being reversed?">
                      <input
                        value={undoReason}
                        onChange={(event) => setUndoReason(event.target.value)}
                        placeholder="These are two different people — the phone number is shared."
                        className={inputClass}
                      />
                    </Field>
                    <Button
                      variant="danger"
                      disabled={undoReason.trim().length < MIN_REASON_LENGTH}
                      onClick={() => undo(record)}
                    >
                      Reverse this merge
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
