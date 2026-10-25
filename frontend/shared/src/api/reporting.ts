/**
 * Reporting across branches.
 *
 * `pending` is the unusual field and the important one: the measures the
 * system cannot take yet, each with the milestone it arrives at. The screen
 * renders them as tiles that say so, rather than the server inventing a
 * number to fill them. See apps/core/api/group_views.py.
 */

import type { ApiClient } from "./client";

export interface GroupBranch {
  id: string;
  name: string;
  code: string;
  is_main: boolean;
  state: string;
  departments: number;
  wards: number;
  beds: number;
  staff_active: number;
}

export interface GroupAlert {
  tone: "critical" | "warning" | "info";
  branch: string;
  message: string;
}

export interface PendingMeasure {
  key: string;
  label: string;
  milestone: string;
  note: string;
}

export interface GroupSummary {
  as_of: string;
  organisation: {
    name: string;
    size_mode: string;
    status: string;
    setup_complete: boolean;
  };
  totals: {
    branches: number;
    departments: number;
    beds: number;
    staff_active: number;
    staff_without_mfa: number;
    services_priced: number;
    payers: number;
    invitations_open: number;
  };
  branches: GroupBranch[];
  alerts: GroupAlert[];
  pending: PendingMeasure[];
}

export class ReportingApi {
  constructor(private readonly client: ApiClient) {}

  group(): Promise<GroupSummary> {
    return this.client.get<GroupSummary>("/reporting/group/");
  }
}
