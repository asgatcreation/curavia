/**
 * The laboratory worklist: work arriving, blood being drawn, tubes booked in.
 *
 * Three things this side must not decide for itself.
 *
 * **How many tubes.** Ask `tubesFor(patient)`. Whether a full blood count and
 * a packed cell volume share one EDTA tube depends on the specimen recorded
 * on each panel, and a second implementation here would drift from the one
 * on the server the first time a supervisor edited the catalogue.
 *
 * **What is overdue.** `overdue` and `minutes_late` come from the server,
 * computed against its clock. A tablet on a ward with the wrong date must not
 * be the thing that decides a result is late.
 *
 * **Who may do what.** `may_collect` and `may_receive` are there to decide
 * what to draw, not what to allow. The server checks again on every action,
 * and the split matters: a ward may draw and label a sample, but only the
 * laboratory may say it has arrived or that it is unusable.
 */

import type { ApiClient } from "./client";
import type { Specimen } from "./laboratory";

export type SampleStatus = "collected" | "received" | "rejected";

export type RequestStatus =
  | "awaiting"
  | "collected"
  | "received"
  | "in_progress"
  | "cancelled";

/** Why a sample was refused. The server requires one from this list. */
export type RejectionReason =
  | "haemolysed"
  | "clotted"
  | "insufficient"
  | "wrong_container"
  | "unlabelled"
  | "mislabelled"
  | "leaked"
  | "too_old"
  | "contaminated"
  | "wrong_patient"
  | "other";

export interface Sample {
  id: string;
  /** The number on the label and in the barcode: "L26-000147". */
  accession: string;
  patient: string;
  patient_name: string;
  mrn: string;
  specimen: Specimen;
  specimen_label: string;
  container: string;
  /** "FBC, PCV" - what this tube was drawn for, as it was at the time. */
  panel_names: string;
  status: SampleStatus;
  status_label: string;
  collected_at: string;
  collection_note: string;
  received_at: string | null;
  rejected_at: string | null;
  rejection_reason: RejectionReason | "";
  rejection_note: string;
  /** "Clotted - drawn through a 25G needle", formatted by the server. */
  rejection: string;
  /** Needle to bench, in minutes. Null until the sample is received. */
  minutes_in_transit: number | null;
  /** The accession number this tube replaces, where it is a redraw. */
  replaces: string;
  label_print_count: number;
}

export interface LabRequestRow {
  id: string;
  order: string;
  patient: string;
  patient_name: string;
  mrn: string;
  panel: string;
  code: string;
  name: string;
  specimen: Specimen;
  specimen_label: string;
  container: string;
  priority: string;
  status: RequestStatus;
  status_label: string;
  sample: string | null;
  accession: string;
  requested_at: string;
  due_at: string;
  /** Decided by the server. See the note above. */
  overdue: boolean;
  minutes_late: number;
  /** How many tubes have already been rejected for this test. */
  rejection_count: number;
  is_send_out: boolean;
}

export interface Tube {
  specimen: Specimen;
  specimen_label: string;
  container: string;
  panels: { id: string; code: string; name: string }[];
}

export interface WorklistCounts {
  awaiting_collection: number;
  /** Tests waiting because a previous tube was rejected. */
  redraws: number;
  in_transit: number;
  in_laboratory: number;
  overdue: number;
}

export interface Worklist {
  awaiting_collection: LabRequestRow[];
  in_laboratory: LabRequestRow[];
  /** Tubes drawn but not yet booked in. The laboratory's inbox. */
  in_transit: Sample[];
  rejections: Sample[];
  counts: WorklistCounts;
  may_collect: boolean;
  may_receive: boolean;
}

export interface CollectInput {
  /** The request ids being drawn for. One patient only. */
  requests: string[];
  note?: string;
  /** The sample this draw replaces, when redrawing after a rejection. */
  replaces?: string;
}

export class LabWorklistApi {
  constructor(private readonly client: ApiClient) {}

  worklist(): Promise<Worklist> {
    return this.client.get<Worklist>("/lab/worklist/");
  }

  /** How many tubes to draw for this patient, and of what. */
  tubesFor(patientId: string): Promise<Tube[]> {
    return this.client.get<Tube[]>(`/lab/worklist/patient/${patientId}/tubes/`);
  }

  /** Draw the tubes. Returns one sample per specimen, numbered. */
  collect(input: CollectInput): Promise<{ samples: Sample[] }> {
    return this.client.post<{ samples: Sample[] }>("/lab/collect/", input);
  }

  receive(sampleId: string): Promise<Sample> {
    return this.client.post<Sample>(`/lab/samples/${sampleId}/receive/`, {});
  }

  reject(sampleId: string, reason: RejectionReason, note = ""): Promise<Sample> {
    return this.client.post<Sample>(`/lab/samples/${sampleId}/reject/`, { reason, note });
  }

  cancelRequest(requestId: string, reason: string): Promise<LabRequestRow> {
    return this.client.post<LabRequestRow>(`/lab/requests/${requestId}/cancel/`, { reason });
  }

  /**
   * The labels for these samples, as a PDF.
   *
   * By accession number rather than row id, so the address is one a
   * scientist can read and check against the tubes in their hand.
   */
  labels(accessions: string[]): Promise<Blob> {
    const query = encodeURIComponent(accessions.join(","));
    return this.client.blob(`/lab/labels.pdf?accessions=${query}`);
  }

  label(sampleId: string): Promise<Blob> {
    return this.client.blob(`/lab/samples/${sampleId}/label.pdf`);
  }
}

/** What the reasons are called on screen. The server holds the same list. */
export const REJECTION_REASONS: { value: RejectionReason; label: string }[] = [
  { value: "haemolysed", label: "Haemolysed" },
  { value: "clotted", label: "Clotted" },
  { value: "insufficient", label: "Not enough sample" },
  { value: "wrong_container", label: "Wrong container" },
  { value: "unlabelled", label: "No label" },
  { value: "mislabelled", label: "Label does not match the request" },
  { value: "leaked", label: "Leaked or broken in transit" },
  { value: "too_old", label: "Too long since collection" },
  { value: "contaminated", label: "Contaminated" },
  { value: "wrong_patient", label: "Taken from the wrong patient" },
  { value: "other", label: "Something else" },
];

/**
 * The two reasons that are a patient-safety matter rather than a redraw.
 *
 * The screen asks for a second confirmation on these, because a tube from
 * the wrong patient means somebody was mis-identified at the bedside and the
 * rejection is the smallest part of what has to happen next.
 */
export const SERIOUS_REJECTIONS: RejectionReason[] = ["wrong_patient", "mislabelled"];
