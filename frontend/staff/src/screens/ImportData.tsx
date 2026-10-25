/**
 * Importing a spreadsheet.
 *
 * Three steps, and the middle one is the point: **nothing is written until
 * the person has seen what will happen.** The preview shows, row by row, the
 * sentence that describes the outcome — "Add FBC — Full blood count at
 * ₦6,500 (laboratory)" — not a payload. Somebody approving an import should
 * be approving an outcome they can read.
 *
 * The errors are shown in full, with the row number Excel uses, so the
 * person can fix the file in one pass rather than discovering the next bad
 * row after each attempt. A hospital's price list has been through four
 * people and two versions of Excel; there are always bad rows.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiRequestError,
  Badge,
  Button,
  Card,
  CardHeader,
  ImportApi,
  SectionTitle,
  cn,
} from "@curavia/shared";
import type { AnalyseResult, CommitResult, ImportKind, PreviewResult } from "@curavia/shared";

import { api } from "../api";
import { IconWarning } from "../icons";

const importApi = new ImportApi(api);

type Stage = "choose" | "map" | "done";

export function ImportData() {
  const [kinds, setKinds] = useState<ImportKind[]>([]);
  const [kind, setKind] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [analysis, setAnalysis] = useState<AnalyseResult | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [result, setResult] = useState<CommitResult | null>(null);
  const [stage, setStage] = useState<Stage>("choose");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void importApi
      .kinds()
      .then((next) => {
        setKinds(next);
        setKind((current) => current || next[0]?.kind || "");
      })
      .catch(() => setError("Could not load the import options."));
  }, []);

  const runPreview = useCallback(
    async (nextMapping: Record<string, string>) => {
      if (!file || !kind) return;
      setPreview(await importApi.preview(kind, file, nextMapping));
    },
    [file, kind],
  );

  async function chooseFile(chosen: File) {
    setError(null);
    setBusy(true);
    setFile(chosen);
    try {
      const next = await importApi.analyse(kind, chosen);
      setAnalysis(next);
      setMapping(next.suggested_mapping);
      setStage("map");
      setPreview(await importApi.preview(kind, chosen, next.suggested_mapping));
    } catch (caught) {
      setError(messageFrom(caught));
      setFile(null);
    } finally {
      setBusy(false);
    }
  }

  async function remap(key: string, header: string) {
    const next = { ...mapping, [key]: header };
    if (!header) delete next[key];
    setMapping(next);
    setBusy(true);
    try {
      await runPreview(next);
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!file || !kind) return;
    setError(null);
    setBusy(true);
    try {
      setResult(await importApi.commit(kind, file, mapping));
      setStage("done");
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setBusy(false);
    }
  }

  function startOver() {
    setFile(null);
    setAnalysis(null);
    setMapping({});
    setPreview(null);
    setResult(null);
    setStage("choose");
    setError(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  const spec = kinds.find((entry) => entry.kind === kind);

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-4 sm:p-6">
      <div>
        <SectionTitle>Import from a spreadsheet</SectionTitle>
        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
          Nothing is saved until you have seen exactly what will happen.
        </p>
      </div>

      {error && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
        >
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}

      {kinds.length === 0 && !error && (
        <Card className="p-5">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Your role does not allow any of the imports. Price lists need permission to manage
            prices; staff lists need permission to invite staff.
          </p>
        </Card>
      )}

      {stage === "choose" && kinds.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader title="What are you importing?" />
          <div className="space-y-4 p-4">
            <div className="grid gap-2 sm:grid-cols-3">
              {kinds.map((entry) => (
                <label
                  key={entry.kind}
                  className={cn(
                    "cursor-pointer rounded-[var(--radius-control)] p-3 ring-1 ring-inset transition-colors",
                    kind === entry.kind
                      ? "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                      : "bg-white ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:ring-slate-600 dark:hover:bg-slate-700",
                  )}
                >
                  <input
                    type="radio"
                    name="kind"
                    className="sr-only"
                    checked={kind === entry.kind}
                    onChange={() => setKind(entry.kind)}
                  />
                  <span className="block text-sm font-semibold text-slate-900 dark:text-white">
                    {entry.title}
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                    {entry.blurb}
                  </span>
                </label>
              ))}
            </div>

            {spec && (
              <div className="rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-800/60">
                <p className="mb-2 text-xs font-bold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                  Columns we look for
                </p>
                <ul className="space-y-1 text-xs text-slate-600 dark:text-slate-300">
                  {spec.columns.map((column) => (
                    <li key={column.key}>
                      <strong className="text-slate-900 dark:text-white">{column.label}</strong>
                      {column.required ? "" : " (optional)"}
                      {column.example ? ` — e.g. ${column.example}` : ""}
                      {column.help_text ? ` · ${column.help_text}` : ""}
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                  Your column names do not have to match — you will match them up on the next
                  screen. .csv or .xlsx, up to 5 MB.
                </p>
              </div>
            )}

            <label className="block">
              <span className="sr-only">Choose a file</span>
              <input
                ref={fileInput}
                type="file"
                accept=".csv,.xlsx,.xlsm,.txt"
                disabled={busy || !kind}
                onChange={(event) => {
                  const chosen = event.target.files?.[0];
                  if (chosen) void chooseFile(chosen);
                }}
                className="block w-full text-sm text-slate-600 file:mr-3 file:min-h-11 file:cursor-pointer file:rounded-[var(--radius-control)] file:border-0 file:bg-brand-600 file:px-4 file:font-semibold file:text-white hover:file:bg-brand-700 dark:text-slate-300 dark:file:bg-brand-500 dark:file:text-slate-950"
              />
            </label>
            {busy && <p className="text-sm text-slate-500 dark:text-slate-400">Reading…</p>}
          </div>
        </Card>
      )}

      {stage === "map" && analysis && (
        <>
          <Card className="overflow-hidden">
            <CardHeader
              title="Match your columns"
              actions={
                <Badge tone="neutral">
                  {analysis.row_count} row{analysis.row_count === 1 ? "" : "s"}
                </Badge>
              }
            />
            <div className="space-y-3 p-4">
              {analysis.truncated && (
                <p className="rounded-[var(--radius-control)] bg-warning-soft p-3 text-sm text-warning-ink dark:bg-warning/15 dark:text-warning">
                  That file is longer than we import in one go. Only the first{" "}
                  {analysis.row_count} rows are included — split the file and run it twice.
                </p>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                {analysis.columns.map((column) => (
                  <label key={column.key} className="block">
                    <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                      {column.label}
                      {!column.required && (
                        <span className="ml-1 font-normal normal-case">(optional)</span>
                      )}
                    </span>
                    <select
                      value={mapping[column.key] ?? ""}
                      disabled={busy}
                      onChange={(event) => void remap(column.key, event.target.value)}
                      className="min-h-11 w-full rounded-[var(--radius-control)] bg-white px-3 text-sm text-slate-900 ring-1 ring-slate-300 ring-inset disabled:opacity-60 dark:bg-slate-800 dark:text-white dark:ring-slate-600"
                    >
                      <option value="">— not in my file —</option>
                      {analysis.headers.map((header) => (
                        <option key={header} value={header}>
                          {header}
                        </option>
                      ))}
                    </select>
                    {column.help_text && (
                      <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
                        {column.help_text}
                      </span>
                    )}
                  </label>
                ))}
              </div>
            </div>
          </Card>

          {preview && (
            <Card className="overflow-hidden">
              <CardHeader
                title="What will happen"
                actions={
                  <span className="flex gap-2">
                    <Badge tone={preview.ok > 0 ? "stable" : "neutral"}>
                      {preview.ok} ready
                    </Badge>
                    {preview.failed > 0 && (
                      <Badge tone="critical">{preview.failed} with problems</Badge>
                    )}
                  </span>
                }
              />

              {preview.errors.length > 0 && (
                <div className="border-b border-slate-200 bg-critical-soft/50 p-4 dark:border-slate-800 dark:bg-critical/10">
                  <p className="mb-2 text-sm font-semibold text-critical-ink dark:text-critical">
                    These rows will be skipped. Fix them in your file and upload it again, or
                    import the rest now and add them by hand.
                  </p>
                  <ul className="max-h-56 space-y-1 overflow-y-auto text-sm text-critical-ink dark:text-critical">
                    {preview.errors.map((item, index) => (
                      <li key={`${item.row_number}-${index}`}>
                        {item.row_number > 0 && (
                          <strong className="numeric">Row {item.row_number}</strong>
                        )}
                        {item.column ? ` · ${item.column}` : ""}
                        {item.row_number > 0 ? " — " : ""}
                        {item.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {preview.rows.length > 0 && (
                <ul className="max-h-72 divide-y divide-slate-200 overflow-y-auto dark:divide-slate-800">
                  {preview.rows.map((row) => (
                    <li
                      key={row.row_number}
                      className="flex gap-3 px-4 py-2 text-sm text-slate-700 dark:text-slate-200"
                    >
                      <span className="numeric w-12 shrink-0 text-slate-400">
                        {row.row_number}
                      </span>
                      <span className="min-w-0 flex-1">{row.summary}</span>
                    </li>
                  ))}
                  {preview.ok > preview.rows.length && (
                    <li className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                      …and {preview.ok - preview.rows.length} more.
                    </li>
                  )}
                </ul>
              )}

              <div className="flex flex-wrap gap-2 border-t border-slate-200 p-4 dark:border-slate-800">
                <Button
                  variant="primary"
                  size="lg"
                  disabled={busy || preview.ok === 0}
                  onClick={() => void commit()}
                >
                  {busy
                    ? "Importing…"
                    : `Import ${preview.ok} row${preview.ok === 1 ? "" : "s"}`}
                </Button>
                <Button variant="ghost" size="lg" onClick={startOver}>
                  Choose a different file
                </Button>
              </div>
            </Card>
          )}
        </>
      )}

      {stage === "done" && result && (
        <Card className="p-5">
          <h2 className="text-lg font-bold text-slate-900 dark:text-white">Imported.</h2>
          <dl className="mt-4 grid grid-cols-3 gap-3">
            <Count label="Added" value={result.created} />
            <Count label="Updated" value={result.updated} />
            <Count label="Skipped" value={result.skipped} />
          </dl>

          {result.errors.length > 0 && (
            <div className="mt-4 rounded-[var(--radius-control)] bg-warning-soft p-3 dark:bg-warning/15">
              <p className="mb-1 text-sm font-semibold text-warning-ink dark:text-warning">
                These rows were skipped:
              </p>
              <ul className="max-h-56 space-y-1 overflow-y-auto text-sm text-warning-ink dark:text-warning">
                {result.errors.map((item, index) => (
                  <li key={`${item.row_number}-${index}`}>
                    <strong className="numeric">Row {item.row_number}</strong>
                    {item.column ? ` · ${item.column}` : ""} — {item.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Button variant="secondary" className="mt-5" onClick={startOver}>
            Import something else
          </Button>
        </Card>
      )}
    </div>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[var(--radius-control)] bg-slate-50 p-3 dark:bg-slate-800/60">
      <dt className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {label}
      </dt>
      <dd className="numeric mt-0.5 text-2xl font-bold text-slate-900 dark:text-white">
        {value}
      </dd>
    </div>
  );
}

function messageFrom(caught: unknown): string {
  if (caught instanceof ApiRequestError) return caught.message;
  return "Could not reach the server. Check your connection and try again.";
}
