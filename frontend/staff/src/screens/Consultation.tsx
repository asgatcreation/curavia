/**
 * The doctor's consultation note.
 *
 * The screen the whole system is judged on. A consultant who finds this
 * slower than paper will not use it, and then nothing downstream works.
 *
 * Four decisions shaped it.
 *
 * **Nothing is ever lost.** The note autosaves as it is typed and the screen
 * says, in words, when the server last stored it — not a spinner, not an
 * optimistic tick. A doctor interrupted mid-sentence by an emergency comes
 * back to the sentence. If a save fails the screen says so and keeps trying,
 * because the one unacceptable outcome is a consultation that looked saved
 * and was not.
 *
 * **Signing is a deliberate, separate act.** It takes two clicks and the
 * second one states what signing means: the wording is closed, and changing
 * it afterwards means an amendment that everybody can see. A one-click
 * signature on the same button as "save" is a signature nobody meant.
 *
 * **A signed note is drawn as a document, not a form.** No textareas, no
 * placeholder grey, and the signature stamp underneath with the reference
 * from its hash. The difference between a draft and a signed record has to
 * be obvious at a glance across a ward.
 *
 * **The template follows the specialty.** A dentist gets a chart of
 * thirty-two teeth, an ophthalmologist gets right and left, a
 * physiotherapist gets measurements. The sections come from the server, so
 * this screen draws whatever the clinical templates say rather than knowing
 * four specialties itself.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  JourneyApi,
  SectionTitle,
  Stat,
  cn,
  formatDate,
  formatDateTime,
  formatNaira,
  inputClass,
  news2Label,
  news2Tone,
} from "@curavia/shared";
import type {
  BoardRow,
  ClinicalNote,
  CodeResult,
  Consent,
  ConsentForm,
  ConsentInput,
  ConsentMethod,
  ConsentOutcome,
  Diagnosis,
  DocumentTemplate,
  IssuedDocument,
  Order,
  OrderInput,
  OrderKind,
  OrderPriority,
  Problem,
  Referral,
  ReferralKind,
  ServiceResult,
  StatusTone,
  NoteContent,
  NoteTemplate,
  Observation,
  TemplateCatalogue,
  TemplateSection,
} from "@curavia/shared";
import {
  ApiRequestError,
  ClinicalApi,
  DiagnosesApi,
  DocumentsApi,
  NotesApi,
  OrdersApi,
} from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const notesApi = new NotesApi(api);
const coding = new DiagnosesApi(api);
const orderApi = new OrdersApi(api);
const paperwork = new DocumentsApi(api);
const clinical = new ClinicalApi(api);
const journey = new JourneyApi(api);

/** The stages where a doctor is the next person to act. */
const MINE = new Set(["waiting_doctor", "with_doctor"]);

/** How long after the last keystroke the draft is sent. */
const AUTOSAVE_AFTER_MS = 1_200;

type SaveState =
  | { kind: "clean" }
  | { kind: "typing" }
  | { kind: "saving" }
  | { kind: "saved"; at: string }
  | { kind: "failed"; message: string };

export function Consultation() {
  const { me } = useAuth();
  const [rows, setRows] = useState<BoardRow[] | null>(null);
  const [open, setOpen] = useState<BoardRow | null>(null);
  const [denied, setDenied] = useState(false);

  const load = useCallback(() => {
    void journey
      .board()
      .then((result) => setRows(result.rows.filter((row) => MINE.has(row.stage))))
      .catch(() => setDenied(true));
  }, []);

  useEffect(load, [load]);

  if (denied) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 sm:p-6">
        <Card>
          <EmptyState title="The consultation screen is not shown to your role." />
        </Card>
      </div>
    );
  }

  if (open) {
    return (
      <ConsultationRoom
        row={open}
        canAmend={me?.permissions.includes("clinical.note.amend") ?? false}
        canOrder={me?.permissions.includes("clinical.order.create") ?? false}
        canRefer={me?.permissions.includes("clinical.referral.create") ?? false}
        canTakeConsent={me?.permissions.includes("consent.take") ?? false}
        canWriteDocument={me?.permissions.includes("document.write") ?? false}
        canIssueDocument={me?.permissions.includes("document.issue") ?? false}
        canWrite={me?.permissions.includes("clinical.note.write") ?? false}
        canSign={me?.permissions.includes("clinical.note.sign") ?? false}
        onClose={() => {
          setOpen(null);
          load();
        }}
      />
    );
  }

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-4 sm:p-6">
      <div>
        <SectionTitle>Consultation</SectionTitle>
        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
          Waiting to be seen by a doctor
        </p>
      </div>

      <Card>
        <CardHeader title="Your list" count={rows?.length} />
        {rows === null ? (
          <p className="p-4 text-sm text-slate-500 dark:text-slate-400">Loading…</p>
        ) : rows.length === 0 ? (
          <EmptyState
            title="Nobody is waiting for a doctor."
            hint="Patients appear here once triage sends them through."
          />
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => setOpen(row)}
                  className="flex w-full flex-wrap items-center gap-3 p-4 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60"
                >
                  <div className="min-w-0 flex-1 basis-48">
                    <p className="truncate font-semibold text-slate-900 dark:text-white">
                      {row.patient_name}
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {row.mrn} · {row.stage_label} · {row.minutes_in_stage}m
                    </p>
                  </div>
                  {row.triage && <Badge tone="neutral">{row.triage}</Badge>}
                  <span className="text-sm font-semibold text-brand-700 dark:text-brand-400">
                    Open
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------------
 * One patient
 * ---------------------------------------------------------------------- */

function ConsultationRoom({
  row,
  canWrite,
  canSign,
  canAmend,
  canOrder,
  canRefer,
  canTakeConsent,
  canWriteDocument,
  canIssueDocument,
  onClose,
}: {
  row: BoardRow;
  canWrite: boolean;
  canSign: boolean;
  canAmend: boolean;
  canOrder: boolean;
  canRefer: boolean;
  canTakeConsent: boolean;
  canWriteDocument: boolean;
  canIssueDocument: boolean;
  onClose: () => void;
}) {
  const [catalogue, setCatalogue] = useState<TemplateCatalogue | null>(null);
  const [notes, setNotes] = useState<ClinicalNote[] | null>(null);
  const [draft, setDraft] = useState<ClinicalNote | null>(null);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [diagnoses, setDiagnoses] = useState<Diagnosis[]>([]);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [basketTotal, setBasketTotal] = useState(0);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [consents, setConsents] = useState<Consent[]>([]);
  const [consentForms, setConsentForms] = useState<ConsentForm[]>([]);
  const [paperDocs, setPaperDocs] = useState<IssuedDocument[]>([]);
  const [docTemplates, setDocTemplates] = useState<DocumentTemplate[]>([]);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    void notesApi.forEncounter(row.id).then((result) => {
      setNotes(result.notes);
      setDraft(result.notes.find((note) => note.may_edit) ?? null);
    });
  }, [row.id]);

  // Coding and the problem list reload together: confirming a chronic
  // diagnosis opens a problem, so showing one without refreshing the other
  // would leave the screen quietly disagreeing with the record.
  const reloadCoding = useCallback(() => {
    void coding.forEncounter(row.id).then((result) => setDiagnoses(result.diagnoses));
    void coding.problems(row.patient_id).then((result) => setProblems(result.problems));
  }, [row.id, row.patient_id]);

  useEffect(reloadCoding, [reloadCoding]);

  const reloadOrders = useCallback(() => {
    void orderApi.forEncounter(row.id).then((result) => {
      setOrders(result.orders);
      setBasketTotal(result.basket_total_kobo);
    });
    void orderApi.referrals(row.id).then((result) => setReferrals(result.referrals));
  }, [row.id]);

  useEffect(reloadOrders, [reloadOrders]);

  // Consents and documents load together because they are one question on
  // the screen — what paper does this visit have — and two spinners in one
  // column read as two things being broken.
  const reloadPaperwork = useCallback(() => {
    void paperwork.paperworkFor(row.id).then((result) => {
      setConsents(result.consents);
      setPaperDocs(result.documents);
    });
  }, [row.id]);

  useEffect(reloadPaperwork, [reloadPaperwork]);

  useEffect(() => {
    // The catalogues never change during a consultation, so they are read
    // once rather than with every reload.
    void paperwork
      .consentForms()
      .then((result) => setConsentForms(result.forms))
      .catch(() => setConsentForms([]));
    void paperwork
      .documentTemplates()
      .then((result) => setDocTemplates(result.templates))
      .catch(() => setDocTemplates([]));
  }, []);

  useEffect(() => {
    void notesApi.templates().then(setCatalogue);
    void clinical
      .chart(row.patient_id)
      .then((rows) => setObservations(rows.filter((item) => item.is_current)))
      // A doctor without the chart permission still gets the note.
      .catch(() => setObservations([]));
    reload();
  }, [reload, row.patient_id]);

  const signed = (notes ?? []).filter((note) => note.is_signed);
  const latest = observations[0];

  async function start(templateKey: string) {
    setError("");
    try {
      const opened = await notesApi.openDraft(row.id, templateKey);
      setDraft(opened);
      setNotes((current) => [opened, ...(current ?? []).filter((n) => n.id !== opened.id)]);
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "The note could not be started.");
    }
  }

  return (
    <div>
      <div className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-3 p-4 sm:px-6">
          <Button size="sm" variant="ghost" onClick={onClose}>
            ← Your list
          </Button>
          <div className="min-w-0 flex-1">
            <p className="truncate font-semibold text-slate-900 dark:text-white">
              {row.patient_name}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {row.mrn} · {row.stage_label}
            </p>
          </div>
          {row.triage && <Badge tone="neutral">{row.triage}</Badge>}
        </div>
      </div>

      <div className="mx-auto grid w-full max-w-7xl gap-5 p-4 sm:p-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          {error && (
            <p
              role="alert"
              className="rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
            >
              {error}
            </p>
          )}

          {draft && catalogue ? (
            <DraftEditor
              note={draft}
              template={templateOf(catalogue, draft.template_key)}
              catalogue={catalogue}
              canSign={canSign}
              onSigned={() => {
                setDraft(null);
                reload();
              }}
              onDiscarded={() => {
                setDraft(null);
                reload();
              }}
            />
          ) : (
            canWrite &&
            catalogue && (
              <TemplatePicker
                catalogue={catalogue}
                // Once something is already written for this visit, the list
                // of six templates is in the way of the thing the doctor
                // came to read. It folds down to one button.
                startFolded={signed.length > 0}
                onStart={(key) => void start(key)}
              />
            )
          )}

          {notes === null ? (
            <Card>
              <p className="p-4 text-sm text-slate-500 dark:text-slate-400">Loading notes…</p>
            </Card>
          ) : (
            signed.map((note) => (
              <SignedNote
                key={note.id}
                note={note}
                template={catalogue ? templateOf(catalogue, note.template_key) : null}
                canAmend={canAmend && note.is_current}
                onAmended={reload}
              />
            ))
          )}

          {notes !== null && signed.length === 0 && !draft && (
            <Card>
              <EmptyState
                title="No note has been written for this visit yet."
                hint={
                  canWrite
                    ? "Choose a template above to start one."
                    : "Only a doctor can write a consultation note."
                }
              />
            </Card>
          )}

          <DiagnosisPanel
            diagnoses={diagnoses}
            canWrite={canWrite}
            onAdd={(code, certainty) =>
              coding.add(row.id, { code, certainty }).then(reloadCoding)
            }
            onChange={(id, action) => coding.change(id, action).then(reloadCoding)}
            onRemove={(id) => coding.remove(id).then(reloadCoding)}
          />

          <OrdersPanel
            orders={orders}
            basketTotal={basketTotal}
            canOrder={canOrder}
            onAdd={(input) => orderApi.add(row.id, input).then(reloadOrders)}
            onSign={(reason) => orderApi.sign(row.id, reason).then(reloadOrders)}
            onCancel={(id, reason) => orderApi.cancel(id, reason).then(reloadOrders)}
          />

          <ReferralsPanel
            referrals={referrals}
            canRefer={canRefer}
            onRefer={(input) => orderApi.refer(row.id, input).then(reloadOrders)}
          />

          <DocumentsPanel
            documents={paperDocs}
            templates={docTemplates}
            canWrite={canWriteDocument}
            canIssue={canIssueDocument}
            onDraft={(templateKey) =>
              paperwork
                .draft(row.patient_id, { template_key: templateKey, encounter_id: row.id })
                .then(reloadPaperwork)
            }
            onUpdate={(id, content) =>
              paperwork.updateDraft(id, content).then(reloadPaperwork)
            }
            onIssue={(id) => paperwork.issue(id).then(reloadPaperwork)}
            onRevoke={(id, reason) => paperwork.revoke(id, reason).then(reloadPaperwork)}
          />
        </div>

        {/* ------------------------------------------------------------- *
         * What the system already knows
         * ------------------------------------------------------------- */}
        <div className="space-y-5">
          <ConsentPanel
            consents={consents}
            forms={consentForms}
            canTake={canTakeConsent}
            patientName={row.patient_name}
            onTake={(input) =>
              paperwork
                .takeConsent(row.patient_id, { ...input, encounter_id: row.id })
                .then(reloadPaperwork)
            }
            onWithdraw={(id, reason) =>
              paperwork.withdrawConsent(id, reason).then(reloadPaperwork)
            }
          />

          <ProblemList
            problems={problems}
            canWrite={canWrite}
            onChange={(id, action) => coding.changeProblem(id, action).then(reloadCoding)}
            onAdd={(code) => coding.addProblem(row.patient_id, { code }).then(reloadCoding)}
          />

          <Card>
            <CardHeader
              title="Latest vitals"
              actions={
                latest ? (
                  <Badge tone="neutral">{formatDateTime(latest.taken_at)}</Badge>
                ) : undefined
              }
            />
            {latest ? (
              <>
                <dl className="grid grid-cols-2 gap-px bg-slate-200 sm:grid-cols-3 xl:grid-cols-2 dark:bg-slate-800">
                  {vitalsOf(latest).map((vital) => (
                    <div key={vital.label} className="bg-white p-3 dark:bg-slate-900">
                      <dt className="text-xs text-slate-500 dark:text-slate-400">{vital.label}</dt>
                      <dd className="numeric mt-0.5 text-lg font-bold text-slate-900 dark:text-white">
                        {vital.value}
                        {vital.unit && (
                          <span className="ml-1 text-xs font-medium text-slate-500 dark:text-slate-400">
                            {vital.unit}
                          </span>
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="border-t border-slate-200 p-4 dark:border-slate-800">
                  {latest.news2_score === null ? (
                    <p className="text-sm text-slate-600 dark:text-slate-300">
                      NEWS2 does not apply to this patient, so no score is shown.
                    </p>
                  ) : (
                    <Stat
                      label="NEWS2 early warning score"
                      value={String(latest.news2_score)}
                      tone={news2Tone(latest.news2_score)}
                      sub={
                        latest.news2_red_flag
                          ? "One reading scores 3 on its own"
                          : news2Label(latest.news2_score)
                      }
                    />
                  )}
                </div>
              </>
            ) : (
              <EmptyState title="No observations have been recorded on this visit." />
            )}
          </Card>

          <Card>
            <CardHeader title="Earlier notes" count={signed.length} />
            {signed.length === 0 ? (
              <p className="p-4 text-sm text-slate-500 dark:text-slate-400">
                Nothing signed on this visit yet.
              </p>
            ) : (
              <ul className="divide-y divide-slate-200 text-sm dark:divide-slate-800">
                {signed.map((note) => (
                  <li key={note.id} className="p-4">
                    <p className="font-semibold text-slate-900 dark:text-white">
                      {note.template_name}
                      {note.version > 1 && ` · version ${note.version}`}
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {note.author_name} ·{" "}
                      {note.signed_at ? formatDateTime(note.signed_at) : "unsigned"}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function templateOf(catalogue: TemplateCatalogue, key: string): NoteTemplate | null {
  return catalogue.templates.find((template) => template.key === key) ?? null;
}

function TemplatePicker({
  catalogue,
  startFolded,
  onStart,
}: {
  catalogue: TemplateCatalogue;
  startFolded: boolean;
  onStart: (key: string) => void;
}) {
  // Derived rather than initialised from `startFolded`: the notes arrive
  // after the first render, so a `useState(startFolded)` would have been
  // decided while the list was still empty and the picker would never fold.
  const [expanded, setExpanded] = useState(false);

  if (startFolded && !expanded) {
    return (
      <Card>
        <div className="p-4">
          <Button size="sm" variant="secondary" onClick={() => setExpanded(true)}>
            Write another note
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title="Start a note" />
      <ul className="divide-y divide-slate-200 dark:divide-slate-800">
        {catalogue.templates.map((template) => (
          <li key={template.key} className="flex flex-wrap items-center gap-3 p-4">
            <div className="min-w-0 flex-1 basis-56">
              <p className="font-semibold text-slate-900 dark:text-white">{template.name}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">{template.summary}</p>
            </div>
            <Badge tone="neutral">{template.discipline}</Badge>
            <Button
              size="sm"
              variant={template.key === catalogue.default ? "primary" : "secondary"}
              onClick={() => onStart(template.key)}
            >
              Write
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/* ------------------------------------------------------------------------
 * The draft
 * ---------------------------------------------------------------------- */

function DraftEditor({
  note,
  template,
  catalogue,
  canSign,
  onSigned,
  onDiscarded,
}: {
  note: ClinicalNote;
  template: NoteTemplate | null;
  catalogue: TemplateCatalogue;
  canSign: boolean;
  onSigned: () => void;
  onDiscarded: () => void;
}) {
  const [content, setContent] = useState<NoteContent>(note.content);
  const [sensitive, setSensitive] = useState(note.is_sensitive);
  const [save, setSave] = useState<SaveState>({ kind: "clean" });
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");

  // The last content actually sent, so the timer does not re-send an
  // unchanged note every time React re-renders.
  const sent = useRef(JSON.stringify(note.content));
  const timer = useRef<number | undefined>(undefined);

  const flush = useCallback(
    async (next: NoteContent, isSensitive: boolean) => {
      const serialised = JSON.stringify(next);
      setSave({ kind: "saving" });
      try {
        const saved = await notesApi.autosave(note.id, {
          content: next,
          is_sensitive: isSensitive,
        });
        sent.current = serialised;
        setSave({ kind: "saved", at: saved.last_saved_at });
      } catch (exc) {
        setSave({
          kind: "failed",
          message:
            exc instanceof ApiRequestError
              ? exc.message
              : "The last change has not reached the server.",
        });
      }
    },
    [note.id],
  );

  useEffect(() => {
    if (JSON.stringify(content) === sent.current && sensitive === note.is_sensitive) return;
    setSave((current) => (current.kind === "saving" ? current : { kind: "typing" }));
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(content, sensitive), AUTOSAVE_AFTER_MS);
    return () => window.clearTimeout(timer.current);
  }, [content, sensitive, flush, note.is_sensitive]);

  // A doctor who closes the tab mid-sentence should not lose it. Not a
  // promise — the browser may kill the request — which is why the timer
  // above is short enough that there is rarely much to lose.
  useEffect(() => {
    function onHide() {
      if (JSON.stringify(content) !== sent.current) void flush(content, sensitive);
    }
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [content, sensitive, flush]);

  const empty = useMemo(
    () => Object.values(content).every((value) => !value || Object.keys(value).length === 0),
    [content],
  );

  async function sign() {
    setError("");
    // Anything still in the box goes first, so a signature can never cover
    // less than what is on the screen.
    if (JSON.stringify(content) !== sent.current) await flush(content, sensitive);
    try {
      await notesApi.sign(note.id);
      onSigned();
    } catch (exc) {
      setConfirming(false);
      setError(exc instanceof ApiRequestError ? exc.message : "The note could not be signed.");
    }
  }

  async function discard() {
    try {
      await notesApi.discard(note.id);
      onDiscarded();
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "The draft could not be discarded.");
    }
  }

  return (
    <Card>
      <CardHeader
        title={template?.name ?? note.template_name}
        actions={
          <div className="flex items-center gap-2">
            {note.version > 1 && <Badge tone="warning">Amendment · version {note.version}</Badge>}
            <Badge tone="info">Draft</Badge>
          </div>
        }
      />

      {note.version > 1 && note.amendment_reason && (
        <p className="border-b border-slate-200 bg-warning-soft px-4 py-2.5 text-sm text-warning-ink dark:border-slate-800 dark:bg-warning/15 dark:text-warning">
          Amending the signed note: {note.amendment_reason}
        </p>
      )}

      <div className="space-y-4 p-4">
        {(template?.sections ?? []).map((section) => (
          <SectionInput
            key={section.key}
            section={section}
            catalogue={catalogue}
            value={content[section.key]}
            onChange={(value) =>
              setContent((current) => ({ ...current, [section.key]: value }))
            }
          />
        ))}

        <label className="flex items-start gap-2.5 rounded-[var(--radius-control)] bg-slate-50 p-3 text-sm dark:bg-slate-950/40">
          <input
            type="checkbox"
            checked={sensitive}
            onChange={(event) => setSensitive(event.target.checked)}
            className="mt-0.5 size-4"
          />
          <span className="text-slate-700 dark:text-slate-200">
            This note is sensitive
            <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
              Mark it when the note names an HIV result, a psychiatric history or anything else
              that only a clinician should read. Everyone else sees that the note exists, and not
              what it says.
            </span>
          </span>
        </label>
      </div>

      <div className="space-y-3 border-t border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/40">
        <SaveLine state={save} onRetry={() => void flush(content, sensitive)} />

        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
          >
            <IconWarning className="mt-0.5 size-4 shrink-0" />
            {error}
          </p>
        )}

        {confirming ? (
          <div className="space-y-3 rounded-[var(--radius-control)] border border-slate-300 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
            <p className="text-sm text-slate-700 dark:text-slate-200">
              Signing closes this note. Your name, qualifications and registration number are
              stamped on it, and the wording cannot be changed afterwards — a correction becomes
              an amendment that anybody reading the record can see.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="md" variant="primary" onClick={() => void sign()}>
                Yes, sign it
              </Button>
              <Button size="md" variant="ghost" onClick={() => setConfirming(false)}>
                Not yet
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button
              size="lg"
              variant="primary"
              disabled={!canSign || empty}
              onClick={() => setConfirming(true)}
              className="flex-1 sm:flex-none"
            >
              Sign the note
            </Button>
            <Button size="lg" variant="ghost" onClick={() => void discard()}>
              Discard draft
            </Button>
          </div>
        )}

        {!canSign && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Your role can write a note but not sign one.
          </p>
        )}
        {canSign && empty && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            There is nothing in the note yet.
          </p>
        )}
      </div>
    </Card>
  );
}

/**
 * What the server has, in words.
 *
 * "Saved at 14:32" rather than a tick, because the question a doctor
 * actually has is "if this machine dies now, what survives" — and the
 * answer is a time, not a symbol.
 */
function SaveLine({ state, onRetry }: { state: SaveState; onRetry: () => void }) {
  if (state.kind === "failed") {
    return (
      <p
        role="status"
        className="flex flex-wrap items-center gap-2 text-sm font-medium text-critical-ink dark:text-critical"
      >
        <IconWarning className="size-4 shrink-0" />
        Not saved. {state.message}
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </p>
    );
  }

  const words =
    state.kind === "saving"
      ? "Saving…"
      : state.kind === "typing"
        ? "Not saved yet"
        : state.kind === "saved"
          ? `Saved at ${new Date(state.at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}`
          : "Autosaves as you type";

  return (
    <p role="status" className="text-sm text-slate-500 dark:text-slate-400">
      {words}
    </p>
  );
}

/* ------------------------------------------------------------------------
 * One section, in whichever shape its template asks for
 * ---------------------------------------------------------------------- */

function SectionInput({
  section,
  catalogue,
  value,
  onChange,
}: {
  section: TemplateSection;
  catalogue: TemplateCatalogue;
  value: string | Record<string, string> | undefined;
  onChange: (value: string | Record<string, string>) => void;
}) {
  const label = (
    <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
      {section.label}
    </span>
  );

  if (section.kind === "teeth") {
    const marked = (typeof value === "object" ? value : {}) ?? {};
    return (
      <div>
        {label}
        {section.hint && (
          <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">{section.hint}</p>
        )}
        <DentalChart
          teeth={catalogue.teeth}
          findings={catalogue.tooth_findings}
          marked={marked}
          onChange={onChange}
        />
      </div>
    );
  }

  if (section.kind === "fields") {
    const current = (typeof value === "object" ? value : {}) ?? {};
    return (
      <fieldset>
        <legend className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
          {section.label}
        </legend>
        {section.hint && (
          <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">{section.hint}</p>
        )}
        <div className="grid gap-3 sm:grid-cols-3">
          {section.fields.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                {field.label}
              </span>
              <input
                type="text"
                value={current[field.key] ?? ""}
                onChange={(event) =>
                  onChange({ ...current, [field.key]: event.target.value })
                }
                className={inputClass}
              />
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  const text = typeof value === "string" ? value : "";
  return (
    <div>
      <label className="block">
        {label}
        {section.hint && (
          <span className="mb-1.5 block text-xs text-slate-500 dark:text-slate-400">
            {section.hint}
          </span>
        )}
        <textarea
          value={text}
          rows={section.rows}
          onChange={(event) => onChange(event.target.value)}
          className={cn(inputClass, "resize-y py-2 leading-relaxed")}
        />
      </label>
      {section.phrases.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {section.phrases.map((phrase) => (
            <Button
              key={phrase}
              size="sm"
              variant="ghost"
              // Appended, never substituted: a suggestion that replaces what
              // somebody typed is a suggestion that loses a finding.
              onClick={() => onChange(text ? `${text.trimEnd()} ${phrase}` : phrase)}
            >
              + {phrase}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The dental chart.
 *
 * Only the teeth with something wrong are stored, so a healthy mouth stores
 * nothing rather than thirty-two copies of the word "normal". Each tooth
 * cycles through the findings on click, with the number always visible —
 * a dentist reads FDI numbers, and hiding them behind a colour would make
 * the chart unreadable to the person who needs it.
 */
function DentalChart({
  teeth,
  findings,
  marked,
  onChange,
}: {
  teeth: string[];
  findings: string[];
  marked: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
}) {
  function cycle(tooth: string) {
    const current = marked[tooth];
    const index = current ? findings.indexOf(current) : -1;
    const next = findings[index + 1];
    const updated = { ...marked };
    if (next) updated[tooth] = next;
    else delete updated[tooth];
    onChange(updated);
  }

  const quadrants = [teeth.slice(0, 8), teeth.slice(8, 16), teeth.slice(16, 24), teeth.slice(24, 32)];

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        {quadrants.map((quadrant, index) => (
          <div key={index} className="grid grid-cols-8 gap-1">
            {quadrant.map((tooth) => {
              const finding = marked[tooth];
              return (
                <button
                  key={tooth}
                  type="button"
                  onClick={() => cycle(tooth)}
                  aria-label={`Tooth ${tooth}${finding ? `, ${finding}` : ", no finding"}`}
                  className={cn(
                    "numeric min-h-9 rounded-[var(--radius-control)] border text-xs font-semibold",
                    finding
                      ? "border-warning bg-warning-soft text-warning-ink dark:bg-warning/20 dark:text-warning"
                      : "border-slate-300 bg-white text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300",
                  )}
                >
                  {tooth}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {Object.keys(marked).length > 0 ? (
        <ul className="flex flex-wrap gap-1.5">
          {Object.entries(marked).map(([tooth, finding]) => (
            <li key={tooth}>
              <Badge tone="warning">
                {tooth} · {finding}
              </Badge>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Tap a tooth to cycle through {findings.slice(0, 3).join(", ")} and the rest.
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------
 * A signed note
 * ---------------------------------------------------------------------- */

function SignedNote({
  note,
  template,
  canAmend,
  onAmended,
}: {
  note: ClinicalNote;
  template: NoteTemplate | null;
  canAmend: boolean;
  onAmended: () => void;
}) {
  const [versions, setVersions] = useState<ClinicalNote[] | null>(null);
  const [amending, setAmending] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  const redacted = "_redacted" in note.content;

  async function amend() {
    setError("");
    try {
      await notesApi.amend(note.id, reason);
      setAmending(false);
      setReason("");
      onAmended();
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "The amendment could not be started.");
    }
  }

  async function showHistory() {
    const result = await notesApi.read(note.id);
    setVersions(result.versions);
  }

  return (
    <Card>
      <CardHeader
        title={template?.name ?? note.template_name}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {note.version > 1 && <Badge tone="warning">Version {note.version}</Badge>}
            {note.is_sensitive && <Badge tone="urgent">Sensitive</Badge>}
            <Badge tone="stable">Signed</Badge>
          </div>
        }
      />

      <div className="space-y-4 p-4">
        {redacted ? (
          <p className="rounded-[var(--radius-control)] bg-slate-100 p-3 text-sm text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {redactionNotice(note)}
          </p>
        ) : (
          (template?.sections ?? []).map((section) => {
            const value = note.content[section.key];
            if (!value || (typeof value === "object" && Object.keys(value).length === 0)) {
              return null;
            }
            return (
              <div key={section.key}>
                <h3 className="text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                  {section.label}
                </h3>
                {typeof value === "string" ? (
                  <p className="mt-1 text-sm leading-relaxed whitespace-pre-wrap text-slate-900 dark:text-slate-100">
                    {value}
                  </p>
                ) : (
                  <ul className="mt-1 flex flex-wrap gap-1.5">
                    {Object.entries(value).map(([key, item]) => (
                      <li key={key}>
                        <Badge tone="neutral">
                          {labelFor(section, key)} · {item}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })
        )}

        {note.amendment_reason && (
          <p className="rounded-[var(--radius-control)] bg-warning-soft p-3 text-sm text-warning-ink dark:bg-warning/15 dark:text-warning">
            Amended: {note.amendment_reason}
          </p>
        )}
      </div>

      {/* The stamp. Drawn as a signature block rather than a line of
       * metadata, because that is what it is. */}
      <div className="border-t border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/40">
        <div className="text-sm">
          {note.signature.map((line, index) => (
            <p
              key={line}
              className={cn(
                index === 0
                  ? "font-semibold text-slate-900 dark:text-white"
                  : "text-slate-600 dark:text-slate-300",
                index === note.signature.length - 1 && "numeric text-xs",
              )}
            >
              {line}
            </p>
          ))}
        </div>

        {error && (
          <p
            role="alert"
            className="mt-3 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
          >
            {error}
          </p>
        )}

        {amending ? (
          <div className="mt-3 space-y-2">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                Why is this note being amended?
              </span>
              <input
                type="text"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="The dose in the plan was the wrong one."
                className={inputClass}
              />
            </label>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              The note above keeps its signature and stays in the record. Your amendment becomes
              version {note.version + 1}, and the reason is shown with it.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="primary" disabled={!reason.trim()} onClick={() => void amend()}>
                Start the amendment
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setAmending(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            {canAmend && (
              <Button size="sm" variant="secondary" onClick={() => setAmending(true)}>
                Amend
              </Button>
            )}
            {note.version > 1 && versions === null && (
              <Button size="sm" variant="ghost" onClick={() => void showHistory()}>
                Show every version
              </Button>
            )}
          </div>
        )}

        {versions && (
          <ol className="mt-3 space-y-1.5 text-xs text-slate-600 dark:text-slate-300">
            {versions.map((version) => (
              <li key={version.id}>
                <span className="font-semibold">Version {version.version}</span> ·{" "}
                {version.author_name} ·{" "}
                {version.signed_at ? formatDateTime(version.signed_at) : "unsigned draft"}
                {version.amendment_reason && ` · ${version.amendment_reason}`}
              </li>
            ))}
          </ol>
        )}
      </div>
    </Card>
  );
}

/** The sentence the server sends in place of a note this reader may not see. */
function redactionNotice(note: ClinicalNote): string {
  const value = note.content["_redacted"];
  return typeof value === "string" ? value : "This note is not shown to your role.";
}

function labelFor(section: TemplateSection, key: string): string {
  return section.fields.find((field) => field.key === key)?.label ?? key;
}

/* ------------------------------------------------------------------------
 * Vitals
 * ---------------------------------------------------------------------- */

function vitalsOf(observation: Observation): { label: string; value: string; unit: string }[] {
  const rows: { label: string; value: string; unit: string }[] = [];
  const push = (label: string, value: string | number | null, unit = "") => {
    if (value !== null && value !== "") rows.push({ label, value: String(value), unit });
  };
  push("Blood pressure", observation.blood_pressure, "mmHg");
  push("Pulse", observation.pulse, "bpm");
  push("Temperature", observation.temperature, "°C");
  push("SpO₂", observation.spo2, "%");
  push("Breaths", observation.respiration_rate, "/min");
  push("Weight", observation.weight_kg, "kg");
  return rows;
}

/* ------------------------------------------------------------------------
 * Coding the visit
 * ---------------------------------------------------------------------- */

/**
 * Diagnoses for this visit.
 *
 * The search is the whole feature. A doctor types what they would say —
 * "BP", "sugar", "runs" — and the right code has to be the first thing in
 * the list, because a code list that needs reading is a code list that gets
 * ignored, and then every patient in the hospital is recorded under "fever,
 * unspecified".
 *
 * Certainty is chosen when the code is added, not afterwards, and the two
 * buttons say what they mean: Suspected for a working diagnosis and
 * Confirmed for one that has been established. Confirming a long-term
 * condition is what puts it on the patient's problem list, so the list says
 * so beside the code rather than letting it happen invisibly.
 */
function DiagnosisPanel({
  diagnoses,
  canWrite,
  onAdd,
  onChange,
  onRemove,
}: {
  diagnoses: Diagnosis[];
  canWrite: boolean;
  onAdd: (code: string, certainty: "suspected" | "confirmed") => Promise<unknown>;
  onChange: (id: string, action: "primary" | "rule_out") => Promise<unknown>;
  onRemove: (id: string) => Promise<unknown>;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CodeResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const text = query.trim();
    // The clearing happens inside the timer rather than here: a state
    // change written straight into an effect body runs before the browser
    // paints and makes the list flicker as somebody types.
    const timer = window.setTimeout(() => {
      if (text.length < 2) {
        setResults([]);
        return;
      }
      void coding
        .search(text)
        .then((result) => setResults(result.results))
        .catch(() => setResults([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function run(work: Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work;
      setQuery("");
      setResults([]);
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "That could not be recorded.");
    } finally {
      setBusy(false);
    }
  }

  const active = diagnoses.filter((item) => item.certainty !== "ruled_out");
  const excluded = diagnoses.filter((item) => item.certainty === "ruled_out");

  return (
    <Card>
      <CardHeader title="Diagnoses" count={active.length} />

      {canWrite && (
        <div className="border-b border-slate-200 p-4 dark:border-slate-800">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              Add a diagnosis
            </span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Type what you would say — malaria, BP, sugar, UTI"
              className={inputClass}
            />
          </label>

          {results.length > 0 && (
            <ul className="mt-2 divide-y divide-slate-200 rounded-[var(--radius-control)] border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
              {results.map((result) => (
                <li key={result.code} className="flex flex-wrap items-center gap-2 p-2.5">
                  <div className="min-w-0 flex-1 basis-48">
                    <p className="text-sm font-semibold text-slate-900 dark:text-white">
                      {result.label}
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {result.chapter}
                      {result.chronic && " · long-term condition"}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void run(onAdd(result.code, "suspected"))}
                  >
                    Suspected
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => void run(onAdd(result.code, "confirmed"))}
                  >
                    Confirmed
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {query.trim().length >= 2 && results.length === 0 && (
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              Nothing matches that yet. Try the condition rather than the symptom, or a shorter
              word.
            </p>
          )}

          {error && (
            <p
              role="alert"
              className="mt-2 rounded-[var(--radius-control)] bg-critical-soft p-2.5 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
            >
              {error}
            </p>
          )}
        </div>
      )}

      {diagnoses.length === 0 ? (
        <EmptyState
          title="This visit has not been coded yet."
          hint={
            canWrite
              ? "What you choose here is what the hospital reports, and what the next doctor reads first."
              : undefined
          }
        />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {[...active, ...excluded].map((diagnosis) => (
            <li
              key={diagnosis.id}
              className={cn("p-4", diagnosis.certainty === "ruled_out" && "opacity-70")}
            >
              <div className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1 basis-48">
                  <p className="font-semibold text-slate-900 dark:text-white">{diagnosis.label}</p>
                  {/* The certainty is the badge on the right. Printing it
                   * here as well read as "Confirmed · Confirmed · the
                   * doctor's name" on the first patient this was tried on. */}
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {diagnosis.diagnosed_by_name || "Recorded"} ·{" "}
                    {formatDateTime(diagnosis.diagnosed_at)}
                  </p>
                  {diagnosis.note && (
                    <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                      {diagnosis.note}
                    </p>
                  )}
                </div>
                {diagnosis.is_primary && <Badge tone="info">Main</Badge>}
                <Badge
                  tone={
                    diagnosis.certainty === "confirmed"
                      ? "stable"
                      : diagnosis.certainty === "ruled_out"
                        ? "neutral"
                        : "warning"
                  }
                >
                  {diagnosis.certainty_label}
                </Badge>
              </div>

              {canWrite && diagnosis.certainty !== "ruled_out" && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {!diagnosis.is_primary && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void run(onChange(diagnosis.id, "primary"))}
                    >
                      Make it the main one
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void run(onChange(diagnosis.id, "rule_out"))}
                  >
                    Rule out
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void run(onRemove(diagnosis.id))}
                  >
                    Entered in error
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/**
 * The problem list.
 *
 * First card in the column, above the vitals, because it is what a doctor
 * reads in the fifteen seconds before the patient sits down. Active
 * conditions are never mixed with old ones: the history matters, and it
 * belongs underneath.
 */
function ProblemList({
  problems,
  canWrite,
  onChange,
  onAdd,
}: {
  problems: Problem[];
  canWrite: boolean;
  onChange: (id: string, action: "resolve" | "reopen") => Promise<unknown>;
  onAdd: (code: string) => Promise<unknown>;
}) {
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CodeResult[]>([]);

  useEffect(() => {
    const text = query.trim();
    const timer = window.setTimeout(() => {
      if (text.length < 2) {
        setResults([]);
        return;
      }
      void coding
        .search(text)
        .then((result) => setResults(result.results))
        .catch(() => setResults([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const active = problems.filter((problem) => problem.is_active);
  const closed = problems.filter((problem) => !problem.is_active);

  return (
    <Card>
      <CardHeader
        title="Problem list"
        count={active.length}
        actions={
          canWrite ? (
            <Button size="sm" variant="ghost" onClick={() => setAdding(!adding)}>
              {adding ? "Cancel" : "+ Add"}
            </Button>
          ) : undefined
        }
      />

      {adding && (
        <div className="border-b border-slate-200 p-4 dark:border-slate-800">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="A condition they already have"
            className={inputClass}
          />
          <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
            For something diagnosed elsewhere. A condition confirmed here reaches this list on its
            own.
          </p>
          {results.length > 0 && (
            <ul className="mt-2 divide-y divide-slate-200 rounded-[var(--radius-control)] border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
              {results.slice(0, 6).map((result) => (
                <li key={result.code}>
                  <button
                    type="button"
                    className="w-full p-2.5 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800/60"
                    onClick={() =>
                      void onAdd(result.code).then(() => {
                        setQuery("");
                        setResults([]);
                        setAdding(false);
                      })
                    }
                  >
                    {result.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {problems.length === 0 ? (
        <EmptyState title="Nothing on this patient's problem list." />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {[...active, ...closed].map((problem) => (
            <li key={problem.id} className={cn("p-4", !problem.is_active && "opacity-70")}>
              <div className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1 basis-40">
                  <p className="text-sm font-semibold text-slate-900 dark:text-white">
                    {problem.label}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {problem.is_active
                      ? problem.onset_on
                        ? `Since ${formatDate(problem.onset_on)}`
                        : "Active"
                      : problem.status === "resolved"
                        ? `Resolved ${problem.resolved_on ? formatDate(problem.resolved_on) : ""}`
                        : "No longer thought to be present"}
                  </p>
                  {problem.note && (
                    <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                      {problem.note}
                    </p>
                  )}
                </div>
                {problem.is_active && <Badge tone="warning">Active</Badge>}
              </div>

              {canWrite && (
                <div className="mt-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      void onChange(problem.id, problem.is_active ? "resolve" : "reopen")
                    }
                  >
                    {problem.is_active ? "Mark resolved" : "It has come back"}
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------------
 * Orders
 * ---------------------------------------------------------------------- */

/** The kinds a doctor picks between, in the order they are usually needed. */
const ORDER_KINDS: { value: OrderKind; label: string }[] = [
  { value: "laboratory", label: "Laboratory" },
  { value: "radiology", label: "Imaging" },
  { value: "medication", label: "Medicine" },
  { value: "procedure", label: "Procedure" },
  { value: "nursing", label: "Nursing" },
];

/**
 * The basket of orders.
 *
 * Everything is drafted and signed together, because a doctor thinks in a
 * plan — bloods, an antibiotic and something for the fever — and signing
 * each separately is three times the clicks for one intention.
 *
 * The allergy warning is the point of the screen. It is attached to the
 * order line from the moment the drug is added, with the reason written
 * out, and the sign button says plainly what is blocking it. A warning
 * behind a dialog is a warning that gets dismissed by reflex.
 */
function OrdersPanel({
  orders,
  basketTotal,
  canOrder,
  onAdd,
  onSign,
  onCancel,
}: {
  orders: Order[];
  basketTotal: number;
  canOrder: boolean;
  onAdd: (input: OrderInput) => Promise<unknown>;
  onSign: (overrideReason: string) => Promise<unknown>;
  onCancel: (id: string, reason: string) => Promise<unknown>;
}) {
  const [kind, setKind] = useState<OrderKind>("laboratory");
  const [name, setName] = useState("");
  const [dose, setDose] = useState("");
  const [frequency, setFrequency] = useState("");
  const [duration, setDuration] = useState("");
  const [priority, setPriority] = useState<OrderPriority>("routine");
  const [results, setResults] = useState<ServiceResult[]>([]);
  const [override, setOverride] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const text = name.trim();
    const timer = window.setTimeout(() => {
      if (text.length < 2) {
        setResults([]);
        return;
      }
      void orderApi
        .services(text, kind)
        .then((result) => setResults(result.results))
        .catch(() => setResults([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [name, kind]);

  const basket = orders.filter((order) => order.status === "draft");
  const sent = orders.filter((order) => order.status !== "draft");
  const blocking = basket.filter((order) =>
    order.warnings.some((warning) => warning.tone === "critical"),
  );

  function reset() {
    setName("");
    setDose("");
    setFrequency("");
    setDuration("");
    setResults([]);
  }

  async function run(work: Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work;
      reset();
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "That could not be done.");
    } finally {
      setBusy(false);
    }
  }

  function add(serviceId?: string) {
    const input: OrderInput = { kind, priority };
    if (serviceId) input.service_id = serviceId;
    else input.name = name.trim();
    if (kind === "medication") {
      input.dose = dose.trim();
      input.frequency = frequency.trim();
      input.duration = duration.trim();
      if (serviceId) input.name = name.trim();
    }
    void run(onAdd(input));
  }

  return (
    <Card>
      {/* What this card is showing, as everywhere else on the screen. The
          basket is counted on the sign button, where it is the thing being
          acted on; here it would read "Orders 0" over a list of orders. */}
      <CardHeader title="Orders" count={orders.length} />

      {canOrder && (
        <div className="space-y-3 border-b border-slate-200 p-4 dark:border-slate-800">
          <div className="flex flex-wrap gap-1.5">
            {ORDER_KINDS.map((option) => (
              <Button
                key={option.value}
                size="sm"
                variant={option.value === kind ? "secondary" : "ghost"}
                onClick={() => {
                  setKind(option.value);
                  setResults([]);
                }}
              >
                {option.label}
              </Button>
            ))}
          </div>

          <label className="block">
            <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
              What are you ordering?
            </span>
            <input
              type="search"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={
                kind === "medication"
                  ? "Artemether-lumefantrine, paracetamol…"
                  : "Full blood count, chest X-ray…"
              }
              className={inputClass}
            />
          </label>

          {results.length > 0 && (
            <ul className="divide-y divide-slate-200 rounded-[var(--radius-control)] border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
              {results.map((result) => (
                <li key={result.id} className="flex flex-wrap items-center gap-2 p-2.5">
                  <div className="min-w-0 flex-1 basis-40">
                    <p className="text-sm font-semibold text-slate-900 dark:text-white">
                      {result.name}
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {result.code} ·{" "}
                      {result.price_kobo === null
                        ? "no price set"
                        : formatNaira(result.price_kobo)}
                    </p>
                  </div>
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => add(result.id)}>
                    Add
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {kind === "medication" && (
            <div className="grid gap-2 sm:grid-cols-3">
              <label className="block">
                <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Dose</span>
                <input
                  type="text"
                  value={dose}
                  onChange={(event) => setDose(event.target.value)}
                  placeholder="500 mg"
                  className={inputClass}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                  How often
                </span>
                <input
                  type="text"
                  value={frequency}
                  onChange={(event) => setFrequency(event.target.value)}
                  placeholder="Three times a day"
                  className={inputClass}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                  For how long
                </span>
                <input
                  type="text"
                  value={duration}
                  onChange={(event) => setDuration(event.target.value)}
                  placeholder="5 days"
                  className={inputClass}
                />
              </label>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <select
              value={priority}
              onChange={(event) => setPriority(event.target.value as OrderPriority)}
              className={cn(inputClass, "w-auto")}
              aria-label="How urgent is this order?"
            >
              <option value="routine">Routine</option>
              <option value="urgent">Urgent</option>
              <option value="stat">Immediately (stat)</option>
            </select>
            <Button
              size="md"
              variant="secondary"
              disabled={busy || name.trim().length < 2}
              onClick={() => add()}
            >
              Add to the basket
            </Button>
          </div>

          {error && (
            <p
              role="alert"
              className="rounded-[var(--radius-control)] bg-critical-soft p-2.5 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
            >
              {error}
            </p>
          )}
        </div>
      )}

      {basket.length === 0 && sent.length === 0 ? (
        <EmptyState
          title="Nothing has been ordered on this visit."
          hint={canOrder ? undefined : "Only a doctor can order tests and medicines."}
        />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {[...basket, ...sent].map((order) => (
            <OrderRow
              key={order.id}
              order={order}
              canOrder={canOrder}
              onCancel={(reason) => {
                void run(onCancel(order.id, reason));
              }}
            />
          ))}
        </ul>
      )}

      {basket.length > 0 && canOrder && (
        <div className="space-y-3 border-t border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/40">
          <dl className="flex flex-wrap justify-between gap-2 text-sm">
            <dt className="text-slate-600 dark:text-slate-300">Estimated cost</dt>
            <dd className="numeric font-bold text-slate-900 dark:text-white">
              {formatNaira(basketTotal)}
            </dd>
          </dl>

          {blocking.length > 0 && (
            <>
              <p
                role="alert"
                className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
              >
                <IconWarning className="mt-0.5 size-4 shrink-0" />
                <span>
                  {blocking.length === 1
                    ? `${blocking[0]?.name} conflicts with this patient's record.`
                    : `${blocking.length} orders conflict with this patient's record.`}{" "}
                  Remove it, change the medicine, or write down why you are going ahead.
                </span>
              </p>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                  Why are you going ahead?
                </span>
                <input
                  type="text"
                  value={override}
                  onChange={(event) => setOverride(event.target.value)}
                  placeholder="Childhood rash only; discussed with the patient."
                  className={inputClass}
                />
              </label>
            </>
          )}

          <Button
            size="lg"
            variant={blocking.length > 0 ? "danger" : "primary"}
            disabled={busy || (blocking.length > 0 && override.trim().length < 5)}
            onClick={() => void run(onSign(override))}
          >
            {blocking.length > 0
              ? "Sign anyway, with the reason above"
              : `Sign and send ${basket.length} order${basket.length === 1 ? "" : "s"}`}
          </Button>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Signing sends the prescription to the pharmacy queue and the laboratory orders to the
            bench, and the charges follow. Everything here is recorded under your name.
          </p>
        </div>
      )}
    </Card>
  );
}

function OrderRow({
  order,
  canOrder,
  onCancel,
}: {
  order: Order;
  canOrder: boolean;
  onCancel: (reason: string) => void;
}) {
  const [stopping, setStopping] = useState(false);
  const [reason, setReason] = useState("");
  const critical = order.warnings.some((warning) => warning.tone === "critical");
  const stoppable = order.status === "draft" || order.status === "ordered";

  return (
    <li className={cn("p-4", critical && "bg-critical-soft/60 dark:bg-critical/10")}>
      <div className="flex flex-wrap items-start gap-2">
        <Badge tone="neutral">{order.kind_label}</Badge>
        <div className="min-w-0 flex-1 basis-48">
          <p className="font-semibold text-slate-900 dark:text-white">{order.summary}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {order.status_label}
            {order.priority !== "routine" && ` · ${order.priority_label}`}
            {order.ordered_by_name && ` · ${order.ordered_by_name}`}
            {order.estimate_kobo !== null && ` · ${formatNaira(order.estimate_kobo)}`}
            {order.estimate_note && ` · ${order.estimate_note}`}
          </p>
          {order.cancel_reason && (
            <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
              Stopped: {order.cancel_reason}
            </p>
          )}
          {order.override_reason && (
            <p className="mt-1 text-xs text-warning-ink dark:text-warning">
              Signed despite the warning: {order.override_reason}
            </p>
          )}
        </div>
        {order.status === "draft" && <Badge tone="info">Not sent yet</Badge>}
        {order.status === "cancelled" && <Badge tone="neutral">Stopped</Badge>}
      </div>

      {order.warnings.map((warning) => (
        <p
          key={warning.message}
          className={cn(
            "mt-2 flex items-start gap-2 rounded-[var(--radius-control)] p-2.5 text-sm font-medium",
            warning.tone === "critical"
              ? "bg-critical text-white"
              : "bg-warning-soft text-warning-ink dark:bg-warning/15 dark:text-warning",
          )}
        >
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          {warning.message}
        </p>
      ))}

      {canOrder && stoppable && (
        <div className="mt-2">
          {stopping ? (
            <div className="flex flex-wrap items-end gap-2">
              <label className="block flex-1 basis-48">
                <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                  Why is it being stopped?
                </span>
                <input
                  type="text"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  className={inputClass}
                />
              </label>
              <Button
                size="sm"
                variant="danger"
                disabled={!reason.trim()}
                onClick={() => {
                  onCancel(reason);
                  setStopping(false);
                  setReason("");
                }}
              >
                Stop it
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setStopping(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            // A draft has not been sent anywhere, so taking it off is just
            // changing your mind and comes off in one click. Asking why
            // would only teach people to type a full stop, and the question
            // then means nothing on the orders that have really gone out.
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                if (order.status === "draft") {
                  onCancel("");
                } else {
                  setStopping(true);
                }
              }}
            >
              {order.status === "draft" ? "Take it off" : "Stop this order"}
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------------
 * Referrals
 * ---------------------------------------------------------------------- */

/**
 * Sending the patient to somebody else.
 *
 * Sent immediately rather than saved as a draft: a referral the patient
 * leaves without is a referral that did not happen, and leaving the
 * building with them is the one thing it has to do.
 */
function ReferralsPanel({
  referrals,
  canRefer,
  onRefer,
}: {
  referrals: Referral[];
  canRefer: boolean;
  onRefer: (input: {
    kind: ReferralKind;
    reason: string;
    to_facility?: string;
    speciality?: string;
    urgency?: Referral["urgency"];
  }) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [facility, setFacility] = useState("");
  const [speciality, setSpeciality] = useState("");
  const [reason, setReason] = useState("");
  const [urgency, setUrgency] = useState<Referral["urgency"]>("routine");
  const [error, setError] = useState("");

  async function send() {
    setError("");
    try {
      await onRefer({
        kind: "external",
        reason,
        to_facility: facility,
        speciality,
        urgency,
      });
      setOpen(false);
      setFacility("");
      setSpeciality("");
      setReason("");
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "The referral could not be sent.");
    }
  }

  return (
    <Card>
      <CardHeader
        title="Referrals"
        count={referrals.length}
        actions={
          canRefer ? (
            <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>
              {open ? "Cancel" : "+ Refer"}
            </Button>
          ) : undefined
        }
      />

      {open && (
        <div className="space-y-3 border-b border-slate-200 p-4 dark:border-slate-800">
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                Which hospital or clinic?
              </span>
              <input
                type="text"
                value={facility}
                onChange={(event) => setFacility(event.target.value)}
                placeholder="Lagos University Teaching Hospital"
                className={inputClass}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
                Speciality
              </span>
              <input
                type="text"
                value={speciality}
                onChange={(event) => setSpeciality(event.target.value)}
                placeholder="Cardiology"
                className={inputClass}
              />
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
              What are you asking them to do?
            </span>
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Murmur, for an echocardiogram."
              className={inputClass}
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={urgency}
              onChange={(event) => setUrgency(event.target.value as Referral["urgency"])}
              className={cn(inputClass, "w-auto")}
              aria-label="How urgent is this referral?"
            >
              <option value="routine">Routine</option>
              <option value="soon">Soon</option>
              <option value="urgent">Urgent</option>
              <option value="emergency">Emergency</option>
            </select>
            <Button
              size="md"
              variant="primary"
              disabled={!reason.trim() || !facility.trim()}
              onClick={() => void send()}
            >
              Send the referral
            </Button>
          </div>
          {error && (
            <p
              role="alert"
              className="rounded-[var(--radius-control)] bg-critical-soft p-2.5 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
            >
              {error}
            </p>
          )}
        </div>
      )}

      {referrals.length === 0 ? (
        <EmptyState title="Nobody has been referred on this visit." />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {referrals.map((referral) => (
            <li key={referral.id} className="p-4">
              <div className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1 basis-48">
                  <p className="font-semibold text-slate-900 dark:text-white">
                    {referral.destination}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {referral.kind_label} · {referral.status_label}
                    {referral.speciality && ` · ${referral.speciality}`}
                    {referral.referred_at && ` · ${formatDateTime(referral.referred_at)}`}
                  </p>
                  <p className="mt-1 text-sm text-slate-700 dark:text-slate-200">
                    {referral.reason}
                  </p>
                  {referral.response_note && (
                    <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                      {referral.response_note}
                    </p>
                  )}
                </div>
                {referral.urgency !== "routine" && (
                  <Badge tone={referral.urgency === "emergency" ? "critical" : "warning"}>
                    {referral.urgency_label}
                  </Badge>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------------
 * Consent
 * ---------------------------------------------------------------------- */

/**
 * What the patient has agreed to.
 *
 * The statements are rendered from the consent record, never from the
 * catalogue. They are the same text today; the whole point of snapshotting
 * them is that after the next deployment they will not be, and the record
 * has to keep showing what this patient was actually read.
 *
 * Refusal is a first-class outcome here, not a missing row. "Offered an HIV
 * test and declined, wants to talk to his wife first" is a clinical fact
 * the next clinician needs, and a screen that only recorded agreement would
 * make it indistinguishable from nobody having asked.
 */
function ConsentPanel({
  consents,
  forms,
  canTake,
  patientName,
  onTake,
  onWithdraw,
}: {
  consents: Consent[];
  forms: ConsentForm[];
  canTake: boolean;
  patientName: string;
  onTake: (input: ConsentInput) => Promise<unknown>;
  onWithdraw: (id: string, reason: string) => Promise<unknown>;
}) {
  const [picked, setPicked] = useState<ConsentForm | null>(null);
  const [error, setError] = useState("");

  const active = consents.filter((row) => row.is_active);

  return (
    <Card>
      <CardHeader
        title="Consent"
        count={consents.length}
        actions={
          canTake && !picked ? (
            <Button size="sm" variant="ghost" onClick={() => setPicked(forms[0] ?? null)}>
              + Take consent
            </Button>
          ) : undefined
        }
      />

      {picked && canTake && (
        <TakeConsent
          forms={forms}
          form={picked}
          patientName={patientName}
          onPick={setPicked}
          onCancel={() => {
            setPicked(null);
            setError("");
          }}
          onDone={async (input) => {
            setError("");
            try {
              await onTake(input);
              setPicked(null);
            } catch (exc) {
              setError(
                exc instanceof ApiRequestError ? exc.message : "That could not be recorded.",
              );
            }
          }}
        />
      )}

      {error && (
        <p className="border-b border-slate-200 px-4 pb-3 text-sm font-medium text-critical dark:border-slate-800">
          {error}
        </p>
      )}

      {consents.length === 0 ? (
        <EmptyState
          title="Nothing has been asked yet."
          hint={canTake ? undefined : "Your role cannot take consent."}
        />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {consents.map((row) => (
            <ConsentRow
              key={row.id}
              consent={row}
              canWithdraw={canTake}
              onWithdraw={(reason) => {
                void onWithdraw(row.id, reason);
              }}
            />
          ))}
        </ul>
      )}

      {active.length > 0 && (
        <p className="border-t border-slate-200 px-4 py-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          {active.length === 1 ? "One consent stands" : `${active.length} consents stand`} on this
          record. Each shows the words the patient was given at the time.
        </p>
      )}
    </Card>
  );
}

function ConsentRow({
  consent,
  canWithdraw,
  onWithdraw,
}: {
  consent: Consent;
  canWithdraw: boolean;
  onWithdraw: (reason: string) => void;
}) {
  const [showWords, setShowWords] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const [reason, setReason] = useState("");

  const tone: StatusTone =
    consent.status === "given" ? "stable" : consent.status === "refused" ? "warning" : "neutral";

  return (
    <li className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-slate-900 dark:text-white">
            {consent.form_name}
            {consent.subject && ` — ${consent.subject}`}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {consent.given_by_name}
            {consent.given_by_relationship && ` (${consent.given_by_relationship})`} ·{" "}
            {consent.method_label} · {formatDateTime(consent.given_at)}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Taken by {consent.taken_by_name}
            {consent.witness_name && `, witnessed by ${consent.witness_name}`}
          </p>
        </div>
        <Badge tone={tone}>{consent.status_label}</Badge>
      </div>

      {consent.refused_reason && (
        <p className="mt-2 text-sm text-warning-ink dark:text-warning">
          Refused: {consent.refused_reason}
        </p>
      )}
      {consent.withdrawn_reason && (
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          Withdrawn: {consent.withdrawn_reason}
        </p>
      )}

      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" variant="ghost" onClick={() => setShowWords((shown) => !shown)}>
          {showWords ? "Hide the wording" : "Show the wording"}
        </Button>
        {canWithdraw && consent.is_active && !withdrawing && (
          <Button size="sm" variant="ghost" onClick={() => setWithdrawing(true)}>
            Withdraw it
          </Button>
        )}
      </div>

      {withdrawing && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="block flex-1 basis-48">
            <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
              What did the patient say?
            </span>
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className={inputClass}
            />
          </label>
          <Button
            size="sm"
            variant="danger"
            disabled={!reason.trim()}
            onClick={() => {
              onWithdraw(reason);
              setWithdrawing(false);
              setReason("");
            }}
          >
            Withdraw
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setWithdrawing(false)}>
            Cancel
          </Button>
        </div>
      )}

      {showWords && (
        <div className="mt-3 rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-950/40">
          <ul className="space-y-1.5">
            {consent.statements.map((line) => (
              <li key={line} className="text-sm text-slate-700 dark:text-slate-200">
                {line}
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-slate-200 pt-3 text-sm font-semibold text-slate-900 dark:border-slate-800 dark:text-white">
            {consent.declaration}
          </p>
        </div>
      )}
    </li>
  );
}

/**
 * Taking it.
 *
 * The statements are shown in full before anything can be recorded. A
 * consent form that is collapsed behind "view details" is a consent form
 * that was not read, and the record would then claim an understanding that
 * did not happen.
 */
function TakeConsent({
  forms,
  form,
  patientName,
  onPick,
  onCancel,
  onDone,
}: {
  forms: ConsentForm[];
  form: ConsentForm;
  patientName: string;
  onPick: (form: ConsentForm) => void;
  onCancel: () => void;
  onDone: (input: ConsentInput) => Promise<unknown>;
}) {
  const [givenBy, setGivenBy] = useState(patientName);
  const [relationship, setRelationship] = useState("");
  const [method, setMethod] = useState<ConsentMethod>("typed");
  const [witness, setWitness] = useState("");
  const [witnessRole, setWitnessRole] = useState("");
  const [subject, setSubject] = useState("");
  const [refusedReason, setRefusedReason] = useState("");
  const [busy, setBusy] = useState(false);

  const needsWitness = method === "thumbprint" || method === "verbal";

  async function record(outcome: ConsentOutcome) {
    setBusy(true);
    const input: ConsentInput = {
      form_key: form.key,
      given_by_name: givenBy.trim(),
      method,
      status: outcome,
    };
    if (relationship.trim()) input.given_by_relationship = relationship.trim();
    if (witness.trim()) input.witness_name = witness.trim();
    if (witnessRole.trim()) input.witness_role = witnessRole.trim();
    if (subject.trim()) input.subject = subject.trim();
    if (outcome === "refused") input.refused_reason = refusedReason.trim();
    try {
      await onDone(input);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 border-b border-slate-200 p-4 dark:border-slate-800">
      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          What is being agreed to?
        </span>
        <select
          value={form.key}
          onChange={(event) => {
            const next = forms.find((item) => item.key === event.target.value);
            if (next) onPick(next);
          }}
          className={inputClass}
        >
          {forms.map((item) => (
            <option key={item.key} value={item.key}>
              {item.name}
            </option>
          ))}
        </select>
      </label>

      {/* Read in full, never collapsed. A form behind "view details" is a
          form that was not read. */}
      <div className="rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-950/40">
        <p className="mb-2 text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Read this to the patient
        </p>
        <ul className="space-y-1.5">
          {form.statements.map((line) => (
            <li key={line} className="text-sm text-slate-700 dark:text-slate-200">
              {line}
            </li>
          ))}
        </ul>
        <p className="mt-3 border-t border-slate-200 pt-3 text-sm font-semibold text-slate-900 dark:border-slate-800 dark:text-white">
          {form.declaration}
        </p>
      </div>

      {form.key === "procedure" && (
        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Which operation or procedure?
          </span>
          <input
            type="text"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            placeholder="Open reduction, left forearm"
            className={inputClass}
          />
        </label>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Who is agreeing?
          </span>
          <input
            type="text"
            value={givenBy}
            onChange={(event) => setGivenBy(event.target.value)}
            className={inputClass}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
            If not the patient, who are they?
          </span>
          <input
            type="text"
            value={relationship}
            onChange={(event) => setRelationship(event.target.value)}
            placeholder="Mother"
            className={inputClass}
          />
        </label>
      </div>

      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          How was it given?
        </span>
        <select
          value={method}
          onChange={(event) => setMethod(event.target.value as ConsentMethod)}
          className={inputClass}
        >
          <option value="typed">Name typed by the patient</option>
          <option value="signed">Signed on screen</option>
          <option value="thumbprint">Thumbprint, with a witness</option>
          <option value="verbal">Given verbally, with a witness</option>
        </select>
      </label>

      {(needsWitness || form.witness_expected) && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Witness{needsWitness ? "" : " (expected for this form)"}
            </span>
            <input
              type="text"
              value={witness}
              onChange={(event) => setWitness(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Their job
            </span>
            <input
              type="text"
              value={witnessRole}
              onChange={(event) => setWitnessRole(event.target.value)}
              placeholder="Staff nurse"
              className={inputClass}
            />
          </label>
        </div>
      )}

      <label className="block">
        <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          If they refuse, what did they say?
        </span>
        <input
          type="text"
          value={refusedReason}
          onChange={(event) => setRefusedReason(event.target.value)}
          placeholder="Wants to discuss it with his wife first."
          className={inputClass}
        />
      </label>

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={busy || !givenBy.trim() || (needsWitness && !witness.trim())}
          onClick={() => void record("given")}
        >
          They agree
        </Button>
        {/* Refusal is recorded, not left blank: "asked and declined" is
            what the next clinician needs to see. */}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy || !givenBy.trim() || !refusedReason.trim()}
          onClick={() => void record("refused")}
        >
          They refuse
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------
 * Documents
 * ---------------------------------------------------------------------- */

/**
 * Sick notes, reports and certificates.
 *
 * A draft shows no reference and no signature, because a screenshot of a
 * draft that looked signed is exactly the forgery this feature exists to
 * make harder. Once issued, the reference and the link to the printable
 * page appear, and the wording is fixed for good.
 */
function DocumentsPanel({
  documents,
  templates,
  canWrite,
  canIssue,
  onDraft,
  onUpdate,
  onIssue,
  onRevoke,
}: {
  documents: IssuedDocument[];
  templates: DocumentTemplate[];
  canWrite: boolean;
  canIssue: boolean;
  onDraft: (templateKey: string) => Promise<unknown>;
  onUpdate: (id: string, content: Record<string, string>) => Promise<unknown>;
  onIssue: (id: string) => Promise<unknown>;
  onRevoke: (id: string, reason: string) => Promise<unknown>;
}) {
  const [choosing, setChoosing] = useState(false);
  const [error, setError] = useState("");

  return (
    <Card>
      <CardHeader
        title="Documents"
        count={documents.length}
        actions={
          canWrite && !choosing ? (
            <Button size="sm" variant="ghost" onClick={() => setChoosing(true)}>
              + Write one
            </Button>
          ) : undefined
        }
      />

      {choosing && canWrite && (
        <div className="space-y-2 border-b border-slate-200 p-4 dark:border-slate-800">
          <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
            What are you writing?
          </p>
          <div className="grid gap-2">
            {templates.map((template) => (
              <button
                key={template.key}
                type="button"
                onClick={() => {
                  setError("");
                  void onDraft(template.key)
                    .then(() => setChoosing(false))
                    .catch((exc: unknown) =>
                      setError(
                        exc instanceof ApiRequestError
                          ? exc.message
                          : "That could not be started.",
                      ),
                    );
                }}
                className="rounded-[var(--radius-control)] border border-slate-200 p-3 text-left hover:border-accent dark:border-slate-800"
              >
                <span className="block font-semibold text-slate-900 dark:text-white">
                  {template.name}
                </span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">
                  {template.summary}
                </span>
              </button>
            ))}
          </div>
          <Button size="sm" variant="ghost" onClick={() => setChoosing(false)}>
            Cancel
          </Button>
        </div>
      )}

      {error && (
        <p className="border-b border-slate-200 px-4 pb-3 text-sm font-medium text-critical dark:border-slate-800">
          {error}
        </p>
      )}

      {documents.length === 0 ? (
        <EmptyState
          title="Nothing has been written on this visit."
          hint={canWrite ? undefined : "Only a doctor can write a certificate."}
        />
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {documents.map((row) => (
            <DocumentRow
              key={row.id}
              document={row}
              canWrite={canWrite}
              canIssue={canIssue}
              onUpdate={(content) => onUpdate(row.id, content)}
              onIssue={() => onIssue(row.id)}
              onRevoke={(reason) => onRevoke(row.id, reason)}
            />
          ))}
        </ul>
      )}
    </Card>
  );
}

function DocumentRow({
  document,
  canWrite,
  canIssue,
  onUpdate,
  onIssue,
  onRevoke,
}: {
  document: IssuedDocument;
  canWrite: boolean;
  canIssue: boolean;
  onUpdate: (content: Record<string, string>) => Promise<unknown>;
  onIssue: () => Promise<unknown>;
  onRevoke: (reason: string) => Promise<unknown>;
}) {
  const fields = document.wording.fields ?? [];
  const required = document.wording.required ?? [];
  const [content, setContent] = useState<Record<string, string>>(document.content);
  const [revoking, setRevoking] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const missing = required.filter((key) => !(content[key] ?? "").trim());
  const editable = canWrite && !document.is_issued;

  async function run(work: Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work;
    } catch (exc) {
      setError(exc instanceof ApiRequestError ? exc.message : "That could not be done.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-slate-900 dark:text-white">{document.name}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {document.status_label}
            {document.reference && ` · ${document.reference}`}
            {document.issued_at && ` · ${formatDateTime(document.issued_at)}`}
          </p>
        </div>
        {/* A draft says so. A screenshot of one must never read as signed. */}
        {!document.is_issued && <Badge tone="info">Not issued yet</Badge>}
        {document.revoked_at && <Badge tone="critical">Withdrawn</Badge>}
      </div>

      {document.revoked_reason && (
        <p className="mt-2 text-sm text-critical">Withdrawn: {document.revoked_reason}</p>
      )}

      {editable ? (
        <div className="mt-3 space-y-3">
          {fields.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
                {field.label}
                {required.includes(field.key) && " *"}
              </span>
              <textarea
                rows={field.key === "advice" || field.key.length > 8 ? 2 : 1}
                value={content[field.key] ?? ""}
                onChange={(event) =>
                  setContent((current) => ({ ...current, [field.key]: event.target.value }))
                }
                className={cn(inputClass, "resize-y")}
              />
            </label>
          ))}

          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => void run(onUpdate(content))}
            >
              Save the draft
            </Button>
            {canIssue && (
              <Button
                size="sm"
                disabled={busy || missing.length > 0}
                onClick={() => void run(onUpdate(content).then(onIssue))}
              >
                Sign and issue
              </Button>
            )}
          </div>

          {missing.length > 0 && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Still needed before it can be issued:{" "}
              {missing
                .map((key) => fields.find((field) => field.key === key)?.label ?? key)
                .join(", ")}
              .
            </p>
          )}
        </div>
      ) : (
        <dl className="mt-3 space-y-2">
          {fields
            .filter((field) => (document.content[field.key] ?? "").trim())
            .map((field) => (
              <div key={field.key}>
                <dt className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  {field.label}
                </dt>
                <dd className="whitespace-pre-wrap text-sm text-slate-800 dark:text-slate-100">
                  {document.content[field.key]}
                </dd>
              </div>
            ))}
        </dl>
      )}

      {document.is_issued && (
        <div className="mt-3 rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-950/40">
          {document.signature.map((line) => (
            <p key={line} className="text-sm text-slate-700 dark:text-slate-200">
              {line}
            </p>
          ))}
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            Anyone holding this document can check it is genuine by scanning the code printed on
            it. The check never shows a diagnosis.
          </p>
        </div>
      )}

      {error && <p className="mt-2 text-sm font-medium text-critical">{error}</p>}

      <div className="mt-2 flex flex-wrap gap-2">
        {document.verify_url && (
          <a
            href={`${document.verify_url}print/`}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-semibold text-accent hover:underline"
          >
            Print it
          </a>
        )}
        {canIssue && document.still_valid && !revoking && (
          <Button size="sm" variant="ghost" onClick={() => setRevoking(true)}>
            Withdraw it
          </Button>
        )}
      </div>

      {revoking && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="block flex-1 basis-48">
            <span className="mb-1 block text-xs text-slate-500 dark:text-slate-400">
              Why is it being withdrawn?
            </span>
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className={inputClass}
            />
          </label>
          <Button
            size="sm"
            variant="danger"
            disabled={!reason.trim() || busy}
            onClick={() => {
              void run(onRevoke(reason));
              setRevoking(false);
              setReason("");
            }}
          >
            Withdraw
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setRevoking(false)}>
            Cancel
          </Button>
        </div>
      )}
    </li>
  );
}
