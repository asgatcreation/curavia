/**
 * Spreadsheet import.
 *
 * The file is sent with each of the three calls. That is the server's
 * decision, not an accident — see apps/onboarding/api/import_views.py — and
 * it means this client holds the File in memory between steps rather than a
 * token standing in for it.
 */

import type { ApiClient } from "./client";

export interface ImportColumn {
  key: string;
  label: string;
  required: boolean;
  help_text: string;
  example: string;
}

export interface ImportKind {
  kind: string;
  title: string;
  blurb: string;
  columns: ImportColumn[];
}

export interface AnalyseResult {
  headers: string[];
  row_count: number;
  truncated: boolean;
  suggested_mapping: Record<string, string>;
  sample: Record<string, string>[];
  columns: ImportColumn[];
}

export interface ImportRowError {
  row_number: number;
  column: string;
  message: string;
}

export interface PreviewResult {
  ok: number;
  failed: number;
  truncated: boolean;
  rows: { row_number: number; summary: string }[];
  errors: ImportRowError[];
}

export interface CommitResult {
  created: number;
  updated: number;
  skipped: number;
  errors: ImportRowError[];
}

export class ImportApi {
  constructor(private readonly client: ApiClient) {}

  kinds(): Promise<ImportKind[]> {
    return this.client.get<ImportKind[]>("/onboarding/import/");
  }

  analyse(kind: string, file: File): Promise<AnalyseResult> {
    return this.send<AnalyseResult>(`/onboarding/import/${kind}/analyse/`, file);
  }

  preview(kind: string, file: File, mapping: Record<string, string>): Promise<PreviewResult> {
    return this.send<PreviewResult>(`/onboarding/import/${kind}/preview/`, file, mapping);
  }

  commit(kind: string, file: File, mapping: Record<string, string>): Promise<CommitResult> {
    return this.send<CommitResult>(`/onboarding/import/${kind}/commit/`, file, mapping);
  }

  /**
   * Multipart, with the mapping as a JSON string beside the file.
   *
   * No Content-Type header is set: the browser has to add its own multipart
   * boundary, and setting it by hand produces a body the server cannot
   * parse. `ApiClient.request` only defaults the header when one is absent,
   * so a FormData body is left alone — which is why this can go through the
   * ordinary client rather than around it.
   */
  private send<T>(path: string, file: File, mapping?: Record<string, string>): Promise<T> {
    const form = new FormData();
    form.append("file", file);
    if (mapping) form.append("mapping", JSON.stringify(mapping));
    return this.client.request<T>(path, { method: "POST", body: form });
  }
}
