/**
 * The setup wizard's API, and the shapes it deals in.
 *
 * One GET gives the wizard everything: the steps, what is already done, the
 * templates for this hospital's size and modules, and what it already has.
 * That matters because people go backwards through wizards, and a step that
 * has to re-fetch its own options every time it is revisited feels broken on
 * a slow connection.
 */

import type { ApiClient } from "./client";

export interface SetupStep {
  key: string;
  title: string;
  blurb: string;
  required: boolean;
  done: boolean;
}

export interface DepartmentTemplate {
  code: string;
  name: string;
  kind: string;
  common: boolean;
}

export interface ServiceTemplate {
  code: string;
  name: string;
  category: string;
  price_kobo: number;
  common: boolean;
}

export interface PayerTemplate {
  code: string;
  name: string;
  kind: string;
  common: boolean;
}

export interface WardBedTemplate {
  department_code: string;
  beds: [string, number][];
}

export interface SetupBranch {
  id: string;
  name: string;
  code: string;
  state: string;
  lga: string;
  address: string;
  phone: string;
  is_main: boolean;
}

export interface SetupState {
  complete: boolean;
  next_step: string | null;
  steps: SetupStep[];
  templates: {
    departments: DepartmentTemplate[];
    services: ServiceTemplate[];
    payers: PayerTemplate[];
    ward_beds: WardBedTemplate[];
    bed_kinds: { value: string; label: string }[];
  };
  counts: Record<string, number>;
  organisation: {
    name: string;
    size_mode: string;
    cac_number: string;
    moh_licence_number: string;
    medical_director_mdcn: string;
    contact_email: string;
    contact_phone: string;
    share_records_across_branches: boolean;
  };
  branches: SetupBranch[];
}

export interface StepResult {
  created: number;
  updated: number;
  detail: string;
  complete: boolean;
  next_step: string | null;
}

export class SetupApi {
  constructor(private readonly client: ApiClient) {}

  state(): Promise<SetupState> {
    return this.client.get<SetupState>("/onboarding/setup/");
  }

  apply(step: string, payload: unknown): Promise<StepResult> {
    return this.client.post<StepResult>(`/onboarding/setup/${step}/`, payload);
  }

  skip(step: string): Promise<StepResult> {
    return this.client.post<StepResult>(`/onboarding/setup/${step}/skip/`, {});
  }
}
