/**
 * Clinical and operational status, in one place.
 *
 * Every status carries a short label as well as a colour, and every component
 * that renders one renders the label too. That is the rule the colour tokens
 * were chosen under: a board that distinguishes "critical" from "stable" only
 * by hue fails for a colour-blind reader at exactly the moment it matters.
 */

export type StatusTone = "critical" | "urgent" | "warning" | "stable" | "info" | "neutral";

/**
 * Nigerian triage practice follows the South African Triage Scale in most
 * teaching hospitals: red, orange, yellow, green, blue. The numeral is kept
 * because nurses call them by number on the radio and in handover.
 */
export const TRIAGE = {
  red: { tone: "critical" as StatusTone, label: "Red", meaning: "Immediate", target: "0 min" },
  orange: { tone: "urgent" as StatusTone, label: "Orange", meaning: "Very urgent", target: "10 min" },
  yellow: { tone: "warning" as StatusTone, label: "Yellow", meaning: "Urgent", target: "60 min" },
  green: { tone: "stable" as StatusTone, label: "Green", meaning: "Routine", target: "240 min" },
  blue: { tone: "info" as StatusTone, label: "Blue", meaning: "Non-urgent", target: "—" },
} as const;

export type TriageCode = keyof typeof TRIAGE;

/**
 * NEWS2 aggregate score to a tone.
 *
 * The thresholds are the Royal College of Physicians' published escalation
 * bands, which Nigerian private hospitals that run early-warning scores
 * generally adopt wholesale. Written as a function rather than a lookup so
 * the boundaries are visible and arguable.
 */
export function news2Tone(score: number): StatusTone {
  if (score >= 7) return "critical";
  if (score >= 5) return "urgent";
  if (score >= 1) return "warning";
  return "stable";
}

export function news2Label(score: number): string {
  if (score >= 7) return "Emergency response";
  if (score >= 5) return "Urgent review";
  if (score >= 1) return "Ward-based response";
  return "Routine monitoring";
}

/**
 * How long a wait is allowed to be before it is worth flagging.
 *
 * Not clinical — operational. The front desk and the journey board use it to
 * decide what turns amber, so the number lives here rather than being typed
 * into two screens that then drift apart.
 */
export const WAIT_WARNING_MINUTES = 30;
export const WAIT_CRITICAL_MINUTES = 60;

export function waitTone(minutes: number): StatusTone {
  if (minutes >= WAIT_CRITICAL_MINUTES) return "critical";
  if (minutes >= WAIT_WARNING_MINUTES) return "warning";
  return "stable";
}
