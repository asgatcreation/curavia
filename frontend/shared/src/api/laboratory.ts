/**
 * The laboratory catalogue: what a test is made of, and what normal means.
 *
 * Two things the screen must not get wrong.
 *
 * `reference_ranges` on an analyte is the **whole set**, not the one that
 * applies to anybody in particular. Which one applies depends on the
 * patient's age and sex, and that is decided by the server. Never pick one
 * on this side — call `preview` and show what it says.
 *
 * `from_template` means "this came from Curavia and nobody here has
 * reviewed it". It is a prompt to a supervisor, not a warning: the shipped
 * values are ordinary intervals, and only the hospital's own laboratory
 * knows what its analyser reports.
 */

import type { ApiClient } from "./client";

export type Specimen =
  | "blood_edta"
  | "blood_citrate"
  | "blood_fluoride"
  | "serum"
  | "plasma_heparin"
  | "urine"
  | "stool"
  | "sputum"
  | "swab"
  | "csf"
  | "other";

export type ValueType = "numeric" | "coded" | "text";

/** Who a range applies to. "any" is a statement, not a missing value. */
export type RangeSex = "any" | "F" | "M";

/** What a coded answer means. Mirrors the levels a result can be flagged as. */
export type OptionFlag = "normal" | "abnormal" | "critical";

export interface CodedOption {
  label: string;
  flag: OptionFlag;
}

export interface ReferenceRange {
  id: string;
  analyte: string;
  sex: RangeSex;
  sex_label: string;
  age_min_days: number;
  age_max_days: number | null;
  low: string | null;
  high: string | null;
  critical_low: string | null;
  critical_high: string | null;
  note: string;
  /** "Female, 12 years and over" — built by the server, printed as given. */
  band: string;
  /** "11.5 - 16.5" — the normal band as a report prints it. */
  normal: string;
  /** "below 7 or above 20" — the telephone thresholds, already formatted. */
  critical: string;
  from_template: boolean;
}

export interface Analyte {
  id: string;
  panel: string;
  code: string;
  name: string;
  value_type: ValueType;
  value_type_label: string;
  unit: string;
  decimal_places: number;
  options: CodedOption[];
  loinc_code: string;
  sort_order: number;
  is_active: boolean;
  from_template: boolean;
  /** Every band on this analyte. Not the one that applies — see the note above. */
  reference_ranges: ReferenceRange[];
}

export interface LabPanel {
  id: string;
  service: string;
  code: string;
  name: string;
  specimen: Specimen;
  specimen_label: string;
  container: string;
  volume_ml: string | null;
  fasting_required: boolean;
  preparation: string;
  handling: string;
  turnaround_minutes: number;
  is_send_out: boolean;
  is_active: boolean;
  from_template: boolean;
  analytes: Analyte[];
}

export interface PanelSummary {
  id: string;
  service: string;
  code: string;
  name: string;
  specimen: Specimen;
  specimen_label: string;
  container: string;
  turnaround_minutes: number;
  fasting_required: boolean;
  is_send_out: boolean;
  is_active: boolean;
  from_template: boolean;
  analyte_count: number;
}

export interface PanelList {
  panels: PanelSummary[];
  /** How many ranges still carry Curavia's starting values. */
  unreviewed_ranges: number;
  /** Whether this person may change any of it. The server checks again. */
  may_edit: boolean;
}

/** What a result would be flagged as, for a patient of a given age and sex. */
export type Level =
  | "normal"
  | "low"
  | "high"
  | "critical_low"
  | "critical_high"
  | "abnormal"
  | "critical"
  | "unknown";

export interface Preview {
  /** The range that would be used, or null when none covers that patient. */
  applies: ReferenceRange | null;
  /** "11.5 - 16.5 g/dL", or empty where nothing applies. */
  describes: string;
  level: Level | "";
  /** "L", "HH" — what prints in the flag column. */
  marker: string;
}

export interface RangeInput {
  sex?: RangeSex;
  age_min_days?: number;
  age_max_days?: number | null;
  low?: string | null;
  high?: string | null;
  critical_low?: string | null;
  critical_high?: string | null;
  note?: string;
}

export interface AnalyteInput {
  code: string;
  name: string;
  value_type?: ValueType;
  unit?: string;
  decimal_places?: number;
  options?: CodedOption[];
  loinc_code?: string;
  sort_order?: number;
  is_active?: boolean;
}

export class LaboratoryApi {
  constructor(private readonly client: ApiClient) {}

  panels(): Promise<PanelList> {
    return this.client.get<PanelList>("/lab/panels/");
  }

  /** One panel with every analyte and every band on it. */
  panel(panelId: string): Promise<LabPanel> {
    return this.client.get<LabPanel>(`/lab/panels/${panelId}/`);
  }

  savePanel(panelId: string, changes: Partial<LabPanel>): Promise<LabPanel> {
    return this.client.patch<LabPanel>(`/lab/panels/${panelId}/`, changes);
  }

  addAnalyte(panelId: string, input: AnalyteInput): Promise<Analyte> {
    return this.client.post<Analyte>(`/lab/panels/${panelId}/analytes/`, input);
  }

  saveAnalyte(analyteId: string, changes: Partial<AnalyteInput>): Promise<Analyte> {
    return this.client.patch<Analyte>(`/lab/analytes/${analyteId}/`, changes);
  }

  /**
   * Which band a patient of this age and sex would be judged against.
   *
   * The only honest way to answer it: the age and sex rules live on the
   * server, and a second implementation here would drift from them.
   */
  preview(
    analyteId: string,
    options: { age_days?: number | null; sex?: RangeSex | ""; value?: string } = {},
  ): Promise<Preview> {
    return this.client.post<Preview>(`/lab/analytes/${analyteId}/preview/`, {
      age_days: options.age_days ?? null,
      sex: options.sex ?? "",
      value: options.value ?? "",
    });
  }

  addRange(analyteId: string, input: RangeInput): Promise<ReferenceRange> {
    return this.client.post<ReferenceRange>(`/lab/analytes/${analyteId}/ranges/`, input);
  }

  saveRange(rangeId: string, changes: RangeInput): Promise<ReferenceRange> {
    return this.client.patch<ReferenceRange>(`/lab/ranges/${rangeId}/`, changes);
  }

  removeRange(rangeId: string): Promise<void> {
    return this.client.delete<void>(`/lab/ranges/${rangeId}/`);
  }
}
