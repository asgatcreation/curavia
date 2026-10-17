/**
 * Formatting shared by the staff app and the patient portal.
 *
 * Money is the important one. The API only ever sends integer kobo, never a
 * decimal and never a pre-formatted string, so the two apps cannot disagree
 * about rounding and no amount is ever parsed from a float.
 */

const NAIRA = "₦";

/** 150050 -> "₦1,500.50". Negative amounts read "-₦1,500.50", as on a refund. */
export function formatNaira(kobo: number): string {
  const sign = kobo < 0 ? "-" : "";
  const absolute = Math.abs(Math.trunc(kobo));
  const naira = Math.floor(absolute / 100);
  const remainder = absolute % 100;
  return `${sign}${NAIRA}${naira.toLocaleString("en-NG")}.${String(remainder).padStart(2, "0")}`;
}

/** Everything is West Africa Time; the server sends ISO 8601 with an offset. */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-NG", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Lagos",
  });
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-NG", {
    dateStyle: "medium",
    timeZone: "Africa/Lagos",
  });
}

/**
 * How long a patient has been waiting, as a clerk would say it.
 *
 * The journey board is read under pressure, so "1h 20m" beats "80 minutes"
 * and both beat a timestamp the reader has to subtract from now.
 */
export function formatWait(since: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(since).getTime()) / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function patientAge(dateOfBirth: string, now: Date = new Date()): string {
  const born = new Date(dateOfBirth);
  let years = now.getFullYear() - born.getFullYear();
  const monthDelta = now.getMonth() - born.getMonth();
  if (monthDelta < 0 || (monthDelta === 0 && now.getDate() < born.getDate())) years -= 1;
  if (years >= 2) return `${years}y`;
  const months = years * 12 + monthDelta;
  return months >= 1 ? `${months}mo` : "newborn";
}
