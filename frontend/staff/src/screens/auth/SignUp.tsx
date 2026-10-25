/**
 * Starting a hospital.
 *
 * Six fields and a choice. Everything else a hospital needs — branches,
 * departments, services, prices, tariffs — is the setup wizard's job, after
 * the address is confirmed. Asking for it all on one form is how a sign-up
 * becomes an afternoon, and the person filling it in is usually a medical
 * director who has fifteen minutes.
 *
 * The size choice is here rather than in the wizard because it decides which
 * modules are switched on, which decides what the wizard even asks about. A
 * solo clinic should never be walked through a wards step.
 *
 * The success state is deliberate and not a redirect to the app: the account
 * exists but the address is unconfirmed, and saying so plainly beats dropping
 * somebody into a product they cannot use yet.
 */

import { useState } from "react";
import { ApiRequestError, Button, cn, inputClass } from "@curavia/shared";
import type { SignUpInput } from "@curavia/shared";

import { useAuth } from "../../auth/useAuth";
import { AuthLayout, FormError, Notice } from "./AuthLayout";

const SIZES: { value: SignUpInput["size_mode"]; label: string; blurb: string }[] = [
  { value: "solo", label: "Solo practice", blurb: "One doctor, one location" },
  { value: "clinic", label: "Clinic", blurb: "Own laboratory and pharmacy" },
  { value: "hospital", label: "Hospital", blurb: "Wards, theatre, maternity" },
];

export function SignUp() {
  const { auth } = useAuth();
  const [form, setForm] = useState<SignUpInput>({
    email: "",
    password: "",
    first_name: "",
    last_name: "",
    hospital_name: "",
    size_mode: "clinic",
    state: "",
  });
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  function set<K extends keyof SignUpInput>(key: K, value: SignUpInput[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    setBusy(true);
    try {
      await auth.signUp({ ...form, email: form.email.trim().toLowerCase() });
      setSent(true);
    } catch (caught) {
      if (caught instanceof ApiRequestError) {
        setFieldErrors(caught.fields);
        setError(caught.isValidation ? null : caught.message);
      } else {
        setError("Could not reach the server. Check your connection and try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <AuthLayout
        title="Check your email"
        blurb={`We have sent a confirmation link to ${form.email.trim().toLowerCase()}.`}
      >
        <Notice title="One more step">
          <p>
            Open the link to confirm the address, then sign in and we will walk you through
            setting up {form.hospital_name || "your hospital"}.
          </p>
          <p>
            The link works for one day. If it does not arrive within a few minutes, check your
            spam folder.
          </p>
          <p>
            <a
              className="font-semibold text-brand-700 underline dark:text-brand-300"
              href="/sign-in"
            >
              Go to sign in
            </a>
          </p>
        </Notice>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Set up your hospital"
      blurb="Thirty days free. No card."
      footer={
        <>
          Already have an account?{" "}
          <a className="font-semibold text-brand-700 underline dark:text-brand-300" href="/sign-in">
            Sign in
          </a>
        </>
      }
    >
      <form onSubmit={(event) => void submit(event)} className="space-y-4">
        <FormError message={error} />

        <Field label="Hospital or clinic name" errors={fieldErrors["hospital_name"]}>
          <input
            autoFocus
            value={form.hospital_name}
            onChange={(event) => set("hospital_name", event.target.value)}
            className={inputClass}
            placeholder="Oakfield Medical Centre"
          />
        </Field>

        <fieldset>
          <legend className="mb-1.5 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            What size are you?
          </legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {SIZES.map((size) => (
              <label
                key={size.value}
                className={cn(
                  "cursor-pointer rounded-[var(--radius-control)] p-3 ring-1 ring-inset transition-colors",
                  form.size_mode === size.value
                    ? "bg-brand-50 ring-brand-500 dark:bg-brand-900/40 dark:ring-brand-400"
                    : "bg-white ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:ring-slate-600 dark:hover:bg-slate-700",
                )}
              >
                <input
                  type="radio"
                  name="size_mode"
                  className="sr-only"
                  checked={form.size_mode === size.value}
                  onChange={() => set("size_mode", size.value)}
                />
                <span className="block text-sm font-semibold text-slate-900 dark:text-white">
                  {size.label}
                </span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">
                  {size.blurb}
                </span>
              </label>
            ))}
          </div>
          <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
            This decides which modules are switched on. You can change it later.
          </p>
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Your first name" errors={fieldErrors["first_name"]}>
            <input
              value={form.first_name}
              onChange={(event) => set("first_name", event.target.value)}
              autoComplete="given-name"
              className={inputClass}
            />
          </Field>
          <Field label="Your surname" errors={fieldErrors["last_name"]}>
            <input
              value={form.last_name}
              onChange={(event) => set("last_name", event.target.value)}
              autoComplete="family-name"
              className={inputClass}
            />
          </Field>
        </div>

        <Field label="Work email" errors={fieldErrors["email"]}>
          <input
            type="email"
            autoComplete="username"
            value={form.email}
            onChange={(event) => set("email", event.target.value)}
            className={inputClass}
          />
        </Field>

        <Field
          label="Password"
          errors={fieldErrors["password"]}
          hint="At least 12 characters. A phrase you will remember beats a word you will not."
        >
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(event) => set("password", event.target.value)}
            className={inputClass}
          />
        </Field>

        <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">
          {busy ? "Setting up…" : "Create my hospital"}
        </Button>

        <p className="text-xs text-slate-500 dark:text-slate-400">
          By continuing you agree to the{" "}
          <a className="underline" href="/legal/terms/">
            terms of service
          </a>{" "}
          and the{" "}
          <a className="underline" href="/legal/dpa/">
            data processing agreement
          </a>
          , under which your hospital is the data controller and Curavia is the processor.
        </p>
      </form>
    </AuthLayout>
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
