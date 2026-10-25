/**
 * The page the link in the confirmation email opens.
 *
 * It verifies on mount rather than showing a "Confirm" button. The person has
 * already confirmed, by clicking a link in their own inbox; making them click
 * again proves nothing and is one more place to lose them.
 *
 * Three outcomes, each with a next step rather than a dead end: confirmed →
 * sign in; bad or expired link → ask for a new one from here; and the case
 * where somebody opens the page with no token at all, which is usually a
 * truncated link in an email client.
 */

import { useEffect, useRef, useState } from "react";
import { ApiRequestError, Button, inputClass } from "@curavia/shared";

import { useAuth } from "../../auth/useAuth";
import { AuthLayout, FormError, Notice } from "./AuthLayout";

type State = "checking" | "verified" | "failed" | "no-token";

export function VerifyEmail() {
  const { auth } = useAuth();
  // The token is in the URL and cannot change while this page is open, so it
  // is read once at first render rather than in an effect. That also means
  // the "no token" case never renders a spinner it will never finish.
  const [token] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("token"),
  );
  const [state, setState] = useState<State>(() => (token ? "checking" : "no-token"));
  const [error, setError] = useState<string | null>(null);

  // Strict Mode mounts effects twice in development. The second call would
  // be harmless — verification is idempotent — but it would show a failure
  // if the first had already consumed the state, so it is guarded.
  const started = useRef(false);

  useEffect(() => {
    if (started.current || !token) return;
    started.current = true;

    auth
      .verifyEmail(token)
      .then(() => setState("verified"))
      .catch((caught: unknown) => {
        setError(
          caught instanceof ApiRequestError
            ? caught.message
            : "Could not reach the server. Try the link again.",
        );
        setState("failed");
      });
  }, [auth, token]);

  if (state === "checking") {
    return (
      <AuthLayout title="Confirming your email">
        <p className="text-slate-600 dark:text-slate-300">One moment…</p>
      </AuthLayout>
    );
  }

  if (state === "verified") {
    return (
      <AuthLayout title="Email confirmed" blurb="That is the address we will use for your account.">
        <Notice title="You are ready to sign in">
          <p>
            Sign in and we will walk you through setting up your hospital — branches,
            departments, services and prices.
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
      title={state === "no-token" ? "That link looks incomplete" : "That link has expired"}
      blurb="Ask for a new one and we will send it straight away."
    >
      <FormError message={error} />
      <ResendForm />
    </AuthLayout>
  );
}

function ResendForm() {
  const { auth } = useAuth();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await auth.resendVerification(email.trim().toLowerCase());
    } catch {
      // The endpoint says the same thing either way by design, so there is
      // nothing useful to tell the person about a failure here.
    } finally {
      setSent(true);
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <Notice title="Check your email">
        <p>
          If that address can be used, a new confirmation link is on its way to it. It works for
          one day.
        </p>
      </Notice>
    );
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-4">
      <label className="block">
        <span className="mb-1 block text-xs font-semibold tracking-wide text-slate-600 uppercase dark:text-slate-400">
          Your email
        </span>
        <input
          autoFocus
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className={inputClass}
        />
      </label>
      <Button type="submit" variant="primary" size="lg" disabled={busy} className="w-full">
        {busy ? "Sending…" : "Send me a new link"}
      </Button>
    </form>
  );
}
