/**
 * The patient banner.
 *
 * This is the single most important component in the system. It is pinned to
 * the top of every screen that is about one patient, it never scrolls away,
 * and it always carries the same six things in the same order:
 *
 *     name · MRN · age and sex · allergies · payer · encounter
 *
 * The order is fixed because the whole point is that a doctor who moves from
 * the consultation screen to the laboratory screen does not have to re-read
 * the banner to know whose chart is open. Wrong-patient errors are the most
 * common serious error in hospital software, and they happen when the
 * identifier moves.
 *
 * Allergies are rendered in the critical tone and never truncated. "No known
 * allergies" is shown explicitly rather than left blank — a blank space is
 * ambiguous between "none" and "nobody has asked yet", and those two mean
 * very different things when a doctor is about to prescribe.
 */

import { patientAge } from "../format";
import { Avatar, Badge } from "./primitives";

export interface BannerPatient {
  full_name: string;
  mrn: string;
  date_of_birth: string;
  sex: "F" | "M";
  allergies: string[];
  /** Null when nobody has taken an allergy history yet — not the same as none. */
  allergies_checked: boolean;
  payer: string;
  encounter_label: string;
}

export function PatientBanner({ patient }: { patient: BannerPatient }) {
  const sex = patient.sex === "F" ? "Female" : "Male";

  return (
    <section
      aria-label={`Patient: ${patient.full_name}, record number ${patient.mrn}`}
      className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur dark:border-slate-800 dark:bg-slate-900/95"
    >
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={patient.full_name} className="size-11" />
          <div className="min-w-0">
            <h1 className="truncate text-base font-bold text-slate-900 dark:text-white sm:text-lg">
              {patient.full_name}
            </h1>
            <p className="numeric text-xs text-slate-500 dark:text-slate-400">
              {patient.mrn} · {patientAge(patient.date_of_birth)} · {sex}
            </p>
          </div>
        </div>

        {/* Allergies sit immediately beside the name, before anything
            administrative. On a phone they wrap to their own line rather
            than being pushed off the end. */}
        <div className="flex min-w-0 flex-1 basis-full items-center gap-2 sm:basis-auto">
          <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
            Allergies
          </span>
          {!patient.allergies_checked ? (
            <Badge tone="warning">⚠ Not yet asked</Badge>
          ) : patient.allergies.length === 0 ? (
            <Badge tone="stable">No known allergies</Badge>
          ) : (
            <span className="flex flex-wrap gap-1.5">
              {patient.allergies.map((allergy) => (
                <Badge key={allergy} tone="critical">
                  ⚠ {allergy}
                </Badge>
              ))}
            </span>
          )}
        </div>

        <dl className="flex items-center gap-5 text-xs sm:ml-auto">
          <div>
            <dt className="font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              Payer
            </dt>
            <dd className="mt-0.5 font-medium text-slate-800 dark:text-slate-200">
              {patient.payer}
            </dd>
          </div>
          <div>
            <dt className="font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              Encounter
            </dt>
            <dd className="mt-0.5 font-medium text-slate-800 dark:text-slate-200">
              {patient.encounter_label}
            </dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
