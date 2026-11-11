/**
 * Orders and referrals.
 *
 * `warnings` is the field that matters. It arrives with the order from the
 * moment it is drafted, and a `critical` one means the basket cannot be
 * signed — so the screen can put the reason against the drug rather than in
 * a dialog after the fact. A warning that appears after signing is a warning
 * that gets dismissed by reflex.
 *
 * The server re-runs every check when the basket is signed, so the client's
 * copy is for showing, never for deciding. A basket the screen believes is
 * fine can still be refused, and the refusal names what blocked it.
 */

import type { ApiClient } from "./client";

export type OrderKind =
  | "laboratory"
  | "radiology"
  | "medication"
  | "procedure"
  | "nursing"
  | "diet"
  | "other";

export type OrderPriority = "routine" | "urgent" | "stat";

export type OrderStatus = "draft" | "ordered" | "in_progress" | "completed" | "cancelled";

export interface OrderWarning {
  kind: string;
  /** "critical" stops the basket; "warning" is for the clinician to weigh. */
  tone: "critical" | "warning";
  message: string;
}

export interface Order {
  id: string;
  kind: OrderKind;
  kind_label: string;
  service: string | null;
  name: string;
  /** The one line a pharmacist or nurse reads: drug, dose, route, frequency. */
  summary: string;
  instructions: string;
  reason: string;
  priority: OrderPriority;
  priority_label: string;
  status: OrderStatus;
  status_label: string;
  dose: string;
  route: string;
  frequency: string;
  duration: string;
  quantity: number | null;
  /** What it is expected to cost. Null when the price list has nothing. */
  estimate_kobo: number | null;
  estimate_note: string;
  warnings: OrderWarning[];
  override_reason: string;
  ordered_at: string | null;
  ordered_by_name: string;
  cancel_reason: string;
  created_at: string;
}

export interface ServiceResult {
  id: string;
  code: string;
  name: string;
  category: string;
  price_kobo: number | null;
}

export type ReferralKind = "internal" | "external";
export type ReferralStatus =
  | "draft"
  | "sent"
  | "accepted"
  | "declined"
  | "completed"
  | "cancelled";

export interface Referral {
  id: string;
  kind: ReferralKind;
  kind_label: string;
  status: ReferralStatus;
  status_label: string;
  urgency: "routine" | "soon" | "urgent" | "emergency";
  urgency_label: string;
  to_department: string | null;
  department_name: string | null;
  to_facility: string;
  to_clinician: string;
  speciality: string;
  /** Where it is going, in the words the screen prints. */
  destination: string;
  reason: string;
  letter: string;
  referred_at: string | null;
  referred_by_name: string;
  responded_at: string | null;
  response_note: string;
  created_at: string;
}

export interface OrderInput {
  kind: OrderKind;
  name?: string;
  service_id?: string;
  priority?: OrderPriority;
  instructions?: string;
  reason?: string;
  dose?: string;
  route?: string;
  frequency?: string;
  duration?: string;
  quantity?: number;
}

export class OrdersApi {
  constructor(private readonly client: ApiClient) {}

  /** The hospital's own list, each with what it costs here today. */
  services(query: string, kind: OrderKind): Promise<{ results: ServiceResult[] }> {
    return this.client.get<{ results: ServiceResult[] }>(
      `/services/?q=${encodeURIComponent(query)}&kind=${kind}`,
    );
  }

  forEncounter(encounterId: string): Promise<{
    orders: Order[];
    basket_total_kobo: number;
    basket_count: number;
  }> {
    return this.client.get(`/encounters/${encounterId}/orders/`);
  }

  add(encounterId: string, input: OrderInput): Promise<Order> {
    return this.client.post<Order>(`/encounters/${encounterId}/orders/`, input);
  }

  /** Sends the whole basket. Refused, with a reason, if anything is blocked. */
  sign(encounterId: string, overrideReason = ""): Promise<{ orders: Order[] }> {
    return this.client.post(`/encounters/${encounterId}/orders/sign/`, {
      override_reason: overrideReason,
    });
  }

  cancel(orderId: string, reason: string): Promise<Order> {
    return this.client.post<Order>(`/orders/${orderId}/`, { reason });
  }

  forPatient(patientId: string): Promise<{ orders: Order[] }> {
    return this.client.get<{ orders: Order[] }>(`/patients/${patientId}/orders/`);
  }

  referrals(encounterId: string): Promise<{ referrals: Referral[] }> {
    return this.client.get<{ referrals: Referral[] }>(`/encounters/${encounterId}/referrals/`);
  }

  refer(
    encounterId: string,
    input: {
      kind: ReferralKind;
      reason: string;
      department_id?: string;
      to_facility?: string;
      to_clinician?: string;
      speciality?: string;
      urgency?: Referral["urgency"];
      letter?: string;
    },
  ): Promise<Referral> {
    return this.client.post<Referral>(`/encounters/${encounterId}/referrals/`, input);
  }

  answerReferral(
    referralId: string,
    status: "accepted" | "declined" | "completed" | "cancelled",
    note = "",
  ): Promise<Referral> {
    return this.client.post<Referral>(`/referrals/${referralId}/`, { status, note });
  }
}
