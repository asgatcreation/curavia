/**
 * Registering a patient.
 *
 * Two fields are required and the rest are not, which is the whole design.
 * A date of birth is often approximate, a phone number may belong to a
 * neighbour, and a form that refuses to save without them produces invented
 * data — worse than missing data, because nobody can tell which is which.
 *
 * **Duplicates stop the first attempt and never block the second.** When the
 * server finds a strong match it comes back with what it found; the clerk
 * either opens that record or says "no, this is somebody else" and sends the
 * same form again. A system that refused outright would produce a
 * registration under a slightly misspelled name to get past it — the
 * duplicate it was trying to prevent, plus a lie.
 *
 * The allergy question has three answers, not two. "None known" is a real
 * answer and is recorded as one; leaving it alone means nobody asked, and the
 * patient banner shows those differently to the doctor who prescribes later.
 */

import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  ApiRequestError,
  Badge,
  Button,
  Card,
  CardHeader,
  PatientsApi,
  SectionTitle,
  cn,
  inputClass,
} from "@curavia/shared";
import type { DuplicateMatch, RegisterPatientInput, Sex } from "@curavia/shared";

import { api } from "../api";
import { IconWarning } from "../icons";

const patients = new PatientsApi(api);

type AllergyAnswer = "unasked" | "none" | "some";

export function RegisterPatient() {
  const navigate = useNavigate();

  const [form, setForm] = useState<RegisterPatientInput>({
    first_name: "",
    last_name: "",
    other_names: "",
    sex: "U",
    date_of_birth: "",
    phone: "",
    address: "",
    state: "",
    lga: "",
    occupation: "",
  });
  const [nin, setNin] = useState("");
  const [kinName, setKinName] = useState("");
  const [kinRelationship, setKinRelationship] = useState("");
  const [kinPhone, setKinPhone] = useState("");
  const [allergyAnswer, setAllergyAnswer] = useState<AllergyAnswer>("unasked");
  const [allergyText, setAllergyText] = useState("");

  const [duplicates, setDuplicates] = useState<DuplicateMatch[] | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function set<K extends keyof RegisterPatientInput>(key: K, value: RegisterPatientInput[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function payload(confirmed: boolean): RegisterPatientInput {
    const allergies =
      allergyAnswer === "some"
        ? allergyText
            .split(",")
            .map((entry) => entry.trim())
            .filter(Boolean)
            .map((substance) => ({ substance }))
        : [];

    return {
      ...form,
      date_of_birth: form.date_of_birth || null,
      ...(nin.trim() ? { identifiers: [{ kind: "nin", value: nin.trim() }] } : {}),
      ...(kinName.trim() && kinPhone.trim()
        ? {
            next_of_kin: [
              {
                name: kinName.trim(),
                relationship: kinRelationship.trim(),
                phone: kinPhone.trim(),
              },
            ],
          }
        : {}),
      allergies,
      allergies_checked: allergyAnswer !== "unasked",
      confirmed_not_duplicate: confirmed,
    };
  }

  async function submit(confirmed: boolean) {
    setError(null);
    setFieldErrors({});
    setBusy(true);
    try {
      const created = await patients.register(payload(confirmed));
      await navigate({ to: "/", search: { registered: created.mrn } as never });
    } catch (caught) {
      if (caught instanceof ApiRequestError) {
        if (caught.code === "possible_duplicate") {
          setDuplicates((caught.body["matches"] as DuplicateMatch[] | undefined) ?? []);
        } else {
          setFieldErrors(caught.fields);
          setError(caught.isValidation ? null : caught.message);
        }
      } else {
        setError("Could not reach the server. Check your connection and try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (duplicates) {
    return (
      <DuplicateDecision
        matches={duplicates}
        busy={busy}
        onBack={() => setDuplicates(null)}
        onConfirm={() => void submit(true)}
      />
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-5 p-4 sm:p-6">
      <div>
        <SectionTitle>Register a new patient</SectionTitle>
        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
          Only the name is required. Fill in what the patient actually knows.
        </p>
      </div>

      {error && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-[var(--radius-control)] bg-critical-soft p-3 text-sm font-medium text-critical-ink dark:bg-critical/15 dark:text-critical"
        >
          <IconWarning className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(false);
        }}
        className="space-y-5"
      >
        <Card className="overflow-hidden">
          <CardHeader title="Who they are" />
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            <Field label="First name" errors={fieldErrors["first_name"]}>
              <input
                autoFocus
                required
                value={form.first_name}
                onChange={(event) => set("first_name", event.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Surname" errors={fieldErrors["last_name"]}>
              <input
                required
                value={form.last_name}
                onChange={(event) => set("last_name", event.target.value)}
                className={inputClass}
              />
            </Field>
            <Field
              label="Other names"
              hint="Middle or family names. Often what a relative will give at the desk."
            >
              <input
                value={form.other_names ?? ""}
                onChange={(event) => set("other_names", event.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Sex">
              <div className="flex gap-2">
                {(
                  [
                    ["F", "Female"],
                    ["M", "Male"],
                    ["U", "Not stated"],
                  ] as [Sex, string][]
                ).map(([value, label]) => (
                  <label
                    key={value}
                    className={cn(
                      "flex min-h-11 flex-1 cursor-pointer items-center justify-center rounded-[var(--radius-control)] px-2 text-sm font-semibold ring-1 ring-inset",
                      form.sex === value
                        ? "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                        : "bg-white ring-slate-300 dark:bg-slate-800 dark:ring-slate-600",
                    )}
                  >
                    <input
                      type="radio"
                      name="sex"
                      className="sr-only"
                      checked={form.sex === value}
                      onChange={() => set("sex", value)}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </Field>
            <Field
              label="Date of birth"
              hint="Leave empty if they do not know it. Never guess into this field."
              errors={fieldErrors["date_of_birth"]}
            >
              <input
                type="date"
                value={form.date_of_birth ?? ""}
                onChange={(event) => set("date_of_birth", event.target.value)}
                className={inputClass}
              />
            </Field>
            <Field label="Phone" errors={fieldErrors["phone"]}>
              <input
                inputMode="tel"
                value={form.phone ?? ""}
                onChange={(event) => set("phone", event.target.value)}
                className={inputClass}
                placeholder="08031234567"
              />
            </Field>
          </div>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader title="Where they live, and who to call" />
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            <Field label="Address">
              <input
                value={form.address ?? ""}
                onChange={(event) => set("address", event.target.value)}
                className={inputClass}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="State">
                <input
                  value={form.state ?? ""}
                  onChange={(event) => set("state", event.target.value)}
                  className={inputClass}
                />
              </Field>
              <Field label="LGA">
                <input
                  value={form.lga ?? ""}
                  onChange={(event) => set("lga", event.target.value)}
                  className={inputClass}
                />
              </Field>
            </div>
            <Field label="NIN" hint="11 digits. Helps us spot a duplicate record later.">
              <input
                inputMode="numeric"
                value={nin}
                onChange={(event) => setNin(event.target.value)}
                className={cn(inputClass, "numeric")}
                maxLength={11}
              />
            </Field>
            <Field label="Occupation">
              <input
                value={form.occupation ?? ""}
                onChange={(event) => set("occupation", event.target.value)}
                className={inputClass}
              />
            </Field>

            <div className="sm:col-span-2">
              <p className="mb-2 text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                Next of kin
              </p>
              <div className="grid gap-3 sm:grid-cols-3">
                <input
                  value={kinName}
                  onChange={(event) => setKinName(event.target.value)}
                  className={inputClass}
                  placeholder="Name"
                />
                <input
                  value={kinRelationship}
                  onChange={(event) => setKinRelationship(event.target.value)}
                  className={inputClass}
                  placeholder="Relationship"
                />
                <input
                  inputMode="tel"
                  value={kinPhone}
                  onChange={(event) => setKinPhone(event.target.value)}
                  className={inputClass}
                  placeholder="Phone"
                />
              </div>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Often the only reliable number on the record.
              </p>
            </div>
          </div>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader title="Allergies" />
          <div className="space-y-3 p-4">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Three answers, not two. <strong>&ldquo;None known&rdquo;</strong> is an answer and
              is recorded as one; leaving this alone records that nobody asked. The doctor who
              prescribes sees the difference.
            </p>
            <div className="grid gap-2 sm:grid-cols-3">
              {(
                [
                  ["unasked", "Not asked yet"],
                  ["none", "No known allergies"],
                  ["some", "Yes — list them"],
                ] as [AllergyAnswer, string][]
              ).map(([value, label]) => (
                <label
                  key={value}
                  className={cn(
                    "flex min-h-11 cursor-pointer items-center justify-center rounded-[var(--radius-control)] px-3 text-sm font-semibold ring-1 ring-inset",
                    allergyAnswer === value
                      ? value === "some"
                        ? "bg-critical-soft ring-critical dark:bg-critical/15"
                        : "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                      : "bg-white ring-slate-300 dark:bg-slate-800 dark:ring-slate-600",
                  )}
                >
                  <input
                    type="radio"
                    name="allergies"
                    className="sr-only"
                    checked={allergyAnswer === value}
                    onChange={() => setAllergyAnswer(value)}
                  />
                  {label}
                </label>
              ))}
            </div>
            {allergyAnswer === "some" && (
              <Field label="Which ones" hint="Separate with commas. Penicillin, peanuts…">
                <input
                  value={allergyText}
                  onChange={(event) => setAllergyText(event.target.value)}
                  className={inputClass}
                  placeholder="Penicillin, Sulfonamides"
                />
              </Field>
            )}
          </div>
        </Card>

        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" size="lg" disabled={busy}>
            {busy ? "Registering…" : "Register patient"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="lg"
            onClick={() => void navigate({ to: "/" })}
          >
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}

// --------------------------------------------------------------------------

function DuplicateDecision({
  matches,
  busy,
  onBack,
  onConfirm,
}: {
  matches: DuplicateMatch[];
  busy: boolean;
  onBack: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-5 p-4 sm:p-6">
      <div>
        <SectionTitle>This may already be a patient here</SectionTitle>
        <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
          Look at these before you create a second record. A split history is the most
          expensive mistake this system can make.
        </p>
      </div>

      <Card className="overflow-hidden">
        <CardHeader title="Possible matches" count={matches.length} />
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {matches.map((match) => (
            <li key={match.patient.id} className="flex flex-wrap items-center gap-3 p-4">
              <span className="min-w-0 flex-1 basis-48">
                <span className="block font-semibold text-slate-900 dark:text-white">
                  {match.patient.full_name}
                </span>
                <span className="numeric block text-xs text-slate-500 dark:text-slate-400">
                  {match.patient.mrn}
                  {match.patient.age ? ` · ${match.patient.age}` : ""}
                  {match.patient.phone ? ` · ${match.patient.phone}` : ""}
                </span>
              </span>
              <span className="flex flex-wrap gap-1.5">
                {match.reasons.map((reason) => (
                  <Badge key={reason} tone={match.is_strong ? "critical" : "warning"}>
                    {reason}
                  </Badge>
                ))}
              </span>
              <Button
                size="sm"
                variant="secondary"
                className="ml-auto"
                onClick={() => window.location.assign(`/patients/${match.patient.id}`)}
              >
                Open this record
              </Button>
            </li>
          ))}
        </ul>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="lg" onClick={onBack} disabled={busy}>
          Go back and check
        </Button>
        <Button variant="danger" size="lg" onClick={onConfirm} disabled={busy}>
          {busy ? "Registering…" : "No — this is somebody else"}
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  errors,
  children,
}: {
  label: string;
  hint?: string;
  errors?: string[] | undefined;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
        {label}
      </span>
      {children}
      {hint && !errors && (
        <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">{hint}</span>
      )}
      {errors?.map((message) => (
        <span
          key={message}
          className="mt-1 block text-xs font-medium text-critical-ink dark:text-critical"
        >
          {message}
        </span>
      ))}
    </label>
  );
}
