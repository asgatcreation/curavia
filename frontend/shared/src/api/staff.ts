/**
 * Staff, roles and invitations.
 *
 * The token never appears here, in either direction. It is created on the
 * server, emailed, and comes back only in the URL of the acceptance page —
 * which is why `preview` and `accept` take it from the caller rather than
 * this client ever holding one.
 */

import type { ApiClient } from "./client";

export interface StaffRole {
  id: string;
  name: string;
  slug: string;
  description: string;
  permission_count: number;
  is_protected: boolean;
}

export interface StaffBranch {
  id: string;
  name: string;
  code: string;
}

export interface StaffMember {
  id: string;
  full_name: string;
  email: string;
  job_title: string;
  is_active: boolean;
  started_on: string;
  has_mfa: boolean;
  roles: StaffRole[];
  branches: StaffBranch[];
}

export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired";

export interface StaffInvitation {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  job_title: string;
  status: InvitationStatus;
  is_open: boolean;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
  roles: StaffRole[];
  branches: StaffBranch[];
  invited_by_name: string;
}

export interface InviteInput {
  email: string;
  first_name?: string;
  last_name?: string;
  job_title?: string;
  role_ids: string[];
  branch_ids: string[];
}

export interface InvitationPreview {
  hospital_name: string;
  email: string;
  first_name: string;
  job_title: string;
  roles: string[];
  invited_by: string;
  expires_at: string;
  has_account: boolean;
  needs_name: boolean;
}

export class StaffApi {
  constructor(private readonly client: ApiClient) {}

  roles(): Promise<StaffRole[]> {
    return this.client.get<StaffRole[]>("/roles/");
  }

  members(): Promise<StaffMember[]> {
    return this.client.get<StaffMember[]>("/staff/");
  }

  invitations(): Promise<StaffInvitation[]> {
    return this.client.get<StaffInvitation[]>("/invitations/");
  }

  invite(input: InviteInput): Promise<StaffInvitation> {
    return this.client.post<StaffInvitation>("/invitations/", input);
  }

  resend(id: string): Promise<StaffInvitation> {
    return this.client.post<StaffInvitation>(`/invitations/${id}/resend/`, {});
  }

  revoke(id: string): Promise<StaffInvitation> {
    return this.client.post<StaffInvitation>(`/invitations/${id}/revoke/`, {});
  }

  deactivate(membershipId: string): Promise<StaffMember> {
    return this.client.post<StaffMember>(`/staff/${membershipId}/deactivate/`, {});
  }

  /** Public: what the acceptance page shows before anybody types anything. */
  preview(token: string): Promise<InvitationPreview> {
    return this.client.get<InvitationPreview>(
      `/auth/invitation/?token=${encodeURIComponent(token)}`,
    );
  }

  /** Public: the only call in the system that creates a staff membership. */
  accept(input: {
    token: string;
    password: string;
    first_name?: string;
    last_name?: string;
  }): Promise<{ detail: string; code: string; email: string }> {
    return this.client.post("/auth/invitation/accept/", input);
  }
}
