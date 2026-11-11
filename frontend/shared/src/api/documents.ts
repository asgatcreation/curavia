/**
 * Consent forms, and the paper the hospital issues.
 *
 * Two things the screen has to get right, and both are about wording.
 *
 * A consent carries the sentences the patient was actually shown — they
 * arrive on the record itself, not looked up from the catalogue — so the
 * screen must render `statements` and `declaration` from the consent, never
 * from the form it was taken under. The two agree today and will not after
 * the next deployment.
 *
 * A document is a draft until it is issued. A draft has no reference, no
 * signature and no QR code, and the screen must not make one look signed.
 */

import type { ApiClient } from "./client";

export type ConsentMethod = "signed" | "typed" | "thumbprint" | "verbal";
export type ConsentOutcome = "given" | "refused" | "withdrawn";

export interface ConsentForm {
  key: string;
  name: string;
  summary: string;
  statements: string[];
  declaration: string;
  version: number;
  /** True where a second person is expected to sign as well. */
  witness_expected: boolean;
}

export interface Consent {
  id: string;
  form_key: string;
  form_name: string;
  form_version: number;
  /** The sentences this patient was read. Snapshotted, not looked up. */
  statements: string[];
  declaration: string;
  status: ConsentOutcome;
  status_label: string;
  method: ConsentMethod;
  method_label: string;
  subject: string;
  given_by_name: string;
  given_by_relationship: string;
  witness_name: string;
  witness_role: string;
  taken_by_name: string;
  taken_by_role: string;
  given_at: string;
  withdrawn_at: string | null;
  withdrawn_reason: string;
  refused_reason: string;
  extras: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
}

export interface DocumentField {
  key: string;
  label: string;
}

export interface DocumentTemplate {
  key: string;
  name: string;
  summary: string;
  fields: DocumentField[];
  fixed_text: string[];
  version: number;
  /** Must be filled in before it can be issued. */
  required: string[];
}

export type DocumentState = "draft" | "issued" | "revoked";

export interface IssuedDocument {
  id: string;
  template_key: string;
  template_version: number;
  name: string;
  content: Record<string, string>;
  wording: {
    name?: string;
    fields?: DocumentField[];
    fixed_text?: string[];
    required?: string[];
  };
  status: DocumentState;
  status_label: string;
  /** Empty until issued. */
  reference: string;
  issued_at: string | null;
  /** The lines that print under the signature. Empty until issued. */
  signature: string[];
  letterhead: Record<string, string>;
  revoked_at: string | null;
  revoked_reason: string;
  is_issued: boolean;
  /** Issued and not withdrawn. Named around DRF's own `is_valid()`. */
  still_valid: boolean;
  /** Where the QR code points. Empty until issued. */
  verify_url: string;
  verified_count: number;
  created_at: string;
}

export interface ConsentInput {
  form_key: string;
  given_by_name: string;
  encounter_id?: string;
  given_by_relationship?: string;
  method?: ConsentMethod;
  status?: ConsentOutcome;
  subject?: string;
  witness_name?: string;
  witness_role?: string;
  refused_reason?: string;
  extras?: Record<string, unknown>;
}

export class DocumentsApi {
  constructor(private readonly client: ApiClient) {}

  consentForms(): Promise<{ forms: ConsentForm[] }> {
    return this.client.get<{ forms: ConsentForm[] }>("/consent-forms/");
  }

  documentTemplates(): Promise<{ templates: DocumentTemplate[] }> {
    return this.client.get<{ templates: DocumentTemplate[] }>("/document-templates/");
  }

  consentsFor(patientId: string): Promise<{ consents: Consent[] }> {
    return this.client.get<{ consents: Consent[] }>(`/patients/${patientId}/consents/`);
  }

  takeConsent(patientId: string, input: ConsentInput): Promise<Consent> {
    return this.client.post<Consent>(`/patients/${patientId}/consents/`, input);
  }

  withdrawConsent(consentId: string, reason: string): Promise<Consent> {
    return this.client.post<Consent>(`/consents/${consentId}/`, { reason });
  }

  documentsFor(patientId: string): Promise<{ documents: IssuedDocument[] }> {
    return this.client.get<{ documents: IssuedDocument[] }>(`/patients/${patientId}/documents/`);
  }

  draft(
    patientId: string,
    input: { template_key: string; encounter_id?: string; content?: Record<string, string> },
  ): Promise<IssuedDocument> {
    return this.client.post<IssuedDocument>(`/patients/${patientId}/documents/`, input);
  }

  updateDraft(documentId: string, content: Record<string, string>): Promise<IssuedDocument> {
    return this.client.patch<IssuedDocument>(`/documents/${documentId}/`, { content });
  }

  /** Signs it. Refused, with a reason, if a required field is still blank. */
  issue(documentId: string): Promise<IssuedDocument> {
    return this.client.post<IssuedDocument>(`/documents/${documentId}/issue/`, {});
  }

  revoke(documentId: string, reason: string): Promise<IssuedDocument> {
    return this.client.post<IssuedDocument>(`/documents/${documentId}/revoke/`, { reason });
  }

  paperworkFor(
    encounterId: string,
  ): Promise<{ consents: Consent[]; documents: IssuedDocument[] }> {
    return this.client.get(`/encounters/${encounterId}/paperwork/`);
  }
}
