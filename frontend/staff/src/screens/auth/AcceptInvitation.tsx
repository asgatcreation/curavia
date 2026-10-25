/**
 * Accepting an invitation.
 *
 * The page a new member of staff opens from their email. It is the first
 * thing they ever see of Curavia, and it is also exactly the shape of a
 * phishing page — an unfamiliar site asking them to choose a password. So the
 * first thing it does is show them what it knows: the hospital's name, who
 * invited them, and what they were invited as. Those are the three facts a
 * recipient can check against what they already know, and they are fetched
 * from the server using the token rather than taken from the URL, so they
 * cannot be forged by editing the link.
 *
 * It asks for a name only when the invitation did not carry one. The server
 * says which case it is, rather than the page guessing and failing on submit.
 */

import { useEffect, useRef, useState } from "react";
import { ApiRequestError, Badge, Button, StaffApi, inputClass } from "@curavia/shared";
import type { InvitationPreview } from "@curavia/shared";

import { api } from "../../api";
import { AuthLayout, FormError, Notice } from "./AuthLayout";

const staffApi = new StaffApi(api);

export function AcceptInvitation() {
  const [token] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("token"),
  );
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [state, setState] = useState<"checking" | "ready" | "bad" | "joined">(() =>
    token ? "checking" : "bad",
  );
  const [error, setError] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [busy, setBusy] = useState(false);

  const started = useRef(false);

  useEffect(() => {
    if (started.current || !token) return;
    started.current = true;

    staffApi
      .preview(token)
      .then((next) => {
        setPreview(next);
        setFirstName(next.first_name);
        setState("ready");
      })
      .catch(() => setState("bad"));
  }, [token]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!token) return;
    setError(null);
    setBusy(true);
    try {
      await staffApi.accept({
        token,
        password,
        first_name: firstName.trim(),
        last_name: lastName.trim(),
      });
      setState("joined");
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not reach the server. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (state === "checking") {
    return (
      <AuthLayout title="Checking your invitation">
        <p className="text-slate-600 dark:text-slate-300">One moment…</p>
      </AuthLayout>
    );
  }

  if (state === "bad") {
    return (
      <AuthLayout
        title="That invitation is no longer valid"
        blurb="It may have expired, been withdrawn, or already been used."
      >
        <Notice title="What to do">
          <p>
            Ask whoever invited you to send a new one — invitations last seven days, and
            resending replaces the old link.
          </p>
          <p>
            If you have already joined,{" "}
            <a className="font-semibold text-brand-700 underline dark:text-brand-300" href="/sign-in">
              sign in instead
            </a>
            .
          </p>
        </Notice>
      </AuthLayout>
    );
  }

  if (state === "joined") {
    return (
      <AuthLayout title="You have joined" blurb={`Welcome to ${preview?.hospital_name ?? ""}.`}>
        <Notice title="One more step">
          <p>
            Sign in with your email and the password you just set. You will be asked to set up
            two-factor authentication — Curavia requires it for every member of staff, because
            your account can open patient records.
          </p>
          <p>
            <a className="font-semibold text-brand-700 underline dark:text-brand-300" href="/sign-in">
              Go to sign in
            </a>
          </p>
        </Notice>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={`Join ${preview?.hospital_name ?? "your hospital"}`}
      blurb={
        preview?.invited_by
          ? `${preview.invited_by} invited you. Choose a password to finish.`
          : "Choose a password to finish."
      }
    >
      {preview && (
        <div className="mb-5 rounded-[var(--radius-control)] bg-slate-50 p-4 text-sm dark:bg-slate-800/60">
          <p className="text-slate-600 dark:text-slate-300">
            Signing in as <strong className="text-slate-900 dark:text-white">{preview.email}</strong>
            {preview.job_title ? `, as ${preview.job_title}` : ""}.
          </p>
          {preview.roles.length > 0 && (
            <p className="mt-2 flex flex-wrap gap-1.5">
              {preview.roles.map((role) => (
                <Badge key={role} tone="info">
                  {role}
                </Badge>
              ))}
            </p>
          )}
        </div>
      )}

      <form onSubmit={(event) => void submit(event)} className="space-y-4">
        <FormError message={error} />

        {preview?.needs_name && (
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                First name
              </span>
              <input
                required
                autoComplete="given-name"
                value={firstName}
                onChange={(event) => setFirstName(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
                Surname
              </span>
              <input
                required
                autoComplete="family-name"
                value={lastName}
                onChange={(event) => setLastName(event.target.value)}
                className={inputClass}
              />
            </label>
          </div>
        )}

        <label className="block">
          <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
            {preview?.has_account ? "Your existing Curavia password" : "Choose a password"}
          </span>
          <input
            type="password"
            required
            autoComplete={preview?.has_account ? "current-password" : "new-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={inputClass}
          />
          <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
            {preview?.has_account
              ? "You already have a Curavia account for this address — it stays as it is, and this hospital is added to it."
              : "At least 12 characters. A phrase you will remember beats a word you will not."}
          </span>
        </label>

        <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">
          {busy ? "Joining…" : "Join this hospital"}
        </Button>
      </form>
    </AuthLayout>
  );
}
