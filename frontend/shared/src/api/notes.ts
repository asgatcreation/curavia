/**
 * Consultation notes: drafting, signing, amending.
 *
 * Two fields decide what the screen is allowed to do, and neither is
 * guessed on the client. `may_edit` is the server's answer to "can THIS
 * person type into THIS note" — a draft, and theirs — because the rule
 * involves the author, the status and the signature, and a client that
 * reimplemented it would eventually disagree with the server and show a
 * doctor a form that silently fails to save.
 *
 * `signature` is a list of lines rather than a formatted string: it is
 * printed as a stamp on screen and in a PDF, and the server already decided
 * which lines it has. An empty list means the note is not signed, so a draft
 * can never be made to look like a signed document.
 */

import type { ApiClient } from "./client";

export type NoteStatus = "draft" | "signed";

/** What the screen draws for one section. See note_templates.py. */
export type SectionKind = "text" | "fields" | "teeth";

export interface TemplateField {
  key: string;
  label: string;
}

export interface TemplateSection {
  key: string;
  label: string;
  kind: SectionKind;
  hint: string;
  rows: number;
  fields: TemplateField[];
  /** Offered as buttons. Never filled in automatically. */
  phrases: string[];
}

export interface NoteTemplate {
  key: string;
  name: string;
  discipline: string;
  summary: string;
  sections: TemplateSection[];
}

export interface TemplateCatalogue {
  templates: NoteTemplate[];
  default: string;
  /** FDI tooth numbers, for the dental chart. */
  teeth: string[];
  tooth_findings: string[];
}

/**
 * A section's value: a string for `text`, a map for `fields` and `teeth`.
 *
 * A note the reader may not see carries `_redacted` with the sentence to
 * show in place of the words — the note's existence is never hidden, only
 * its contents.
 */
export type NoteContent = Record<string, string | Record<string, string>>;

export interface ClinicalNote {
  id: string;
  encounter: string;
  patient: string;
  template_key: string;
  template_name: string;
  content: NoteContent;
  status: NoteStatus;
  status_label: string;
  is_signed: boolean;
  thread: string;
  version: number;
  supersedes: string | null;
  is_current: boolean;
  amendment_reason: string;
  author_name: string;
  signed_at: string | null;
  /** The stamp, line by line. Empty on a draft. */
  signature: string[];
  content_hash: string;
  is_sensitive: boolean;
  last_saved_at: string;
  /** Whether this reader can type into it. The server's answer, not a guess. */
  may_edit: boolean;
}

export class NotesApi {
  constructor(private readonly client: ApiClient) {}

  templates(): Promise<TemplateCatalogue> {
    return this.client.get<TemplateCatalogue>("/note-templates/");
  }

  forEncounter(encounterId: string): Promise<{ notes: ClinicalNote[] }> {
    return this.client.get<{ notes: ClinicalNote[] }>(`/encounters/${encounterId}/consultation/`);
  }

  /** Idempotent: two clicks give one draft back, not two half-notes. */
  openDraft(encounterId: string, templateKey: string): Promise<ClinicalNote> {
    return this.client.post<ClinicalNote>(`/encounters/${encounterId}/consultation/`, {
      template_key: templateKey,
    });
  }

  /** Called as the clinician types. The response carries the stored time. */
  autosave(
    noteId: string,
    input: { content?: NoteContent; is_sensitive?: boolean },
  ): Promise<ClinicalNote> {
    return this.client.patch<ClinicalNote>(`/notes/${noteId}/`, input);
  }

  read(noteId: string): Promise<{ note: ClinicalNote; versions: ClinicalNote[] }> {
    return this.client.get(`/notes/${noteId}/`);
  }

  sign(noteId: string): Promise<ClinicalNote> {
    return this.client.post<ClinicalNote>(`/notes/${noteId}/sign/`, {});
  }

  amend(noteId: string, reason: string): Promise<ClinicalNote> {
    return this.client.post<ClinicalNote>(`/notes/${noteId}/amend/`, { reason });
  }

  discard(noteId: string): Promise<void> {
    return this.client.delete<void>(`/notes/${noteId}/`);
  }

  forPatient(patientId: string): Promise<{ notes: ClinicalNote[] }> {
    return this.client.get<{ notes: ClinicalNote[] }>(`/patients/${patientId}/notes/`);
  }
}
