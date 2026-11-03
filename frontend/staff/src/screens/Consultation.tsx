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
  inputClass,
  news2Label,
  news2Tone,
} from "@curavia/shared";
import type {
  BoardRow,
  ClinicalNote,
  CodeResult,
  Diagnosis,
  Problem,
  NoteContent,
  NoteTemplate,
  Observation,
  TemplateCatalogue,
  TemplateSection,
} from "@curavia/shared";
import { ApiRequestError, ClinicalApi, DiagnosesApi, NotesApi } from "@curavia/shared";

import { api } from "../api";
import { useAuth } from "../auth/useAuth";
import { IconWarning } from "../icons";

const notesApi = new NotesApi(api);
const coding = new DiagnosesApi(api);
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
  onClose,
}: {
  row: BoardRow;
  canWrite: boolean;
  canSign: boolean;
  canAmend: boolean;
  onClose: () => void;
}) {
  const [catalogue, setCatalogue] = useState<TemplateCatalogue | null>(null);
  const [notes, setNotes] = useState<ClinicalNote[] | null>(null);
  const [draft, setDraft] = useState<ClinicalNote | null>(null);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [diagnoses, setDiagnoses] = useState<Diagnosis[]>([]);
  const [problems, setProblems] = useState<Problem[]>([]);
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

          <Card>
            <CardHeader title="Orders" />
            <div className="p-4 text-sm text-slate-600 dark:text-slate-300">
              Prescriptions, laboratory and radiology orders are signed from here. They arrive
              with the next milestone, together with the allergy and interaction checks that have
              to run before anything can be sent to the pharmacy.
            </div>
          </Card>
        </div>

        {/* ------------------------------------------------------------- *
         * What the system already knows
         * ------------------------------------------------------------- */}
        <div className="space-y-5">
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
